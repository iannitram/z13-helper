"""Unified-memory and compute-health report (#26). Stdlib only: Decky imports a copy.

Strix Halo has one pool of physical RAM. Firmware carves a fixed part out as
"VRAM" before the OS boots; the rest is what Linux reports as MemTotal, and the
GPU may map part of that dynamically as GTT. So the pools are not additive:
VRAM is outside MemTotal, GTT is inside it. `memory_report` keeps that straight.

Discovery is read-only. The only thing that allocates GPU memory is
`smoke_test`, which runs on request, with a stated memory budget and time limit.
"""

import json
import os
import platform

import host

DRM_ROOT = "/sys/class/drm"
MEMINFO = "/proc/meminfo"
PRESSURE = "/proc/pressure/memory"
KFD_NODES = "/sys/class/kfd/kfd/topology/nodes"

MIB = 1024 * 1024
SMOKE_DEFAULT_MIB = 256
SMOKE_MAX_MIB = 1024
SMOKE_TIMEOUT_S = 20          # the allocation step only
DISCOVER_TIMEOUT_S = 15       # runtime discovery, which runs first when no library is given
SMOKE_BUDGET_TEXT = (f"Allocates {SMOKE_DEFAULT_MIB} MiB of GPU memory (never more than {SMOKE_MAX_MIB} MiB, "
                     f"and never more than a quarter of available RAM), then frees it. "
                     f"Finding the runtime takes up to {DISCOVER_TIMEOUT_S} s; the allocation gives up after "
                     f"{SMOKE_TIMEOUT_S} s. Runs only when you press the button.")

NOTES = (
    "Firmware reserves VRAM out of physical RAM before Linux starts; MemTotal is what is left.",
    "GTT is shared system memory the GPU can map on demand. It lives inside MemTotal, so it is "
    "not extra RAM and is not added to anything here.",
)


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_int(path):
    try:
        return int(_read(path))
    except (TypeError, ValueError):
        return None


def parse_meminfo(text):
    """{"MemTotal": bytes, ...} from /proc/meminfo text."""
    out = {}
    for line in (text or "").splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts and parts[0].isdigit():
            out[key.strip()] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
    return out


def memory_pressure(text):
    """The `some avg10` percentage of /proc/pressure/memory, or None."""
    for line in (text or "").splitlines():
        if line.startswith("some"):
            for field in line.split():
                if field.startswith("avg10="):
                    try:
                        return float(field[6:])
                    except ValueError:
                        return None
    return None


def gpu_device():
    """The sysfs device dir of the integrated amdgpu (boot_vga, else lowest PCI address)."""
    found = []
    try:
        entries = sorted(os.listdir(DRM_ROOT))
    except OSError:
        return None
    for entry in entries:
        if not entry.startswith("card") or "-" in entry:
            continue
        dev = os.path.join(DRM_ROOT, entry, "device")
        if _read(os.path.join(dev, "mem_info_vram_total")) is None:
            continue                                   # not amdgpu
        found.append(dev)
    for dev in found:
        if _read(os.path.join(dev, "boot_vga")) == "1":
            return dev
    return min(found, key=os.path.realpath) if found else None


def memory_report():
    """RAM, firmware-reserved VRAM and GTT as separate, non-additive figures (bytes).

    Any figure that cannot be read is None.
    """
    mi = parse_meminfo(_read(MEMINFO))
    total, avail = mi.get("MemTotal"), mi.get("MemAvailable")
    dev = gpu_device()
    g = (lambda n: _read_int(os.path.join(dev, n))) if dev else (lambda n: None)
    vram_total = g("mem_info_vram_total")
    return {
        "ram_total": total,                                 # what Linux sees
        "ram_available": avail,
        "ram_used": total - avail if total is not None and avail is not None else None,
        "vram_reserved": vram_total,                        # firmware carve-out, outside ram_total
        "vram_used": g("mem_info_vram_used"),
        "physical_total": total + vram_total if total is not None and vram_total is not None else None,
        "gtt_limit": g("mem_info_gtt_total"),               # inside ram_total
        "gtt_used": g("mem_info_gtt_used"),
        "pressure_avg10": memory_pressure(_read(PRESSURE)),
        "notes": list(NOTES),
    }


def gfx_name(target_version):
    """110501 -> "gfx1151" (decimal MMmmss, stepping in hex), or None."""
    if not isinstance(target_version, int) or target_version <= 0:
        return None
    major, minor, step = target_version // 10000, target_version // 100 % 100, target_version % 100
    return f"gfx{major}{minor}{step:x}"


def _pci_id(props):
    """"1002:1586" from the KFD vendor_id/device_id (decimal), or None. The file has no name key."""
    try:
        return f"{int(props['vendor_id']):04x}:{int(props['device_id']):04x}"
    except (KeyError, ValueError):
        return None


def _json_line(text):
    """The last line of `text` that is a JSON object; host.run mixes stderr into out."""
    lines = [l for l in (text or "").splitlines() if l.lstrip().startswith("{")]
    return json.loads(lines[-1])


def gpu_identity():
    """GPU agents the kernel's KFD lists: [{"gfx": "gfx1151", "name": ...}]. Empty without KFD."""
    agents = []
    try:
        nodes = sorted(os.listdir(KFD_NODES))
    except OSError:
        return agents
    for n in nodes:
        props = {}
        for line in (_read(os.path.join(KFD_NODES, n, "properties")) or "").splitlines():
            k, _, v = line.partition(" ")
            props[k] = v.strip()
        try:
            gfx = gfx_name(int(props.get("gfx_target_version", "0")))
        except ValueError:
            gfx = None
        if gfx:
            agents.append({"gfx": gfx, "name": _pci_id(props)})
    return agents


# Runs on the host (the Flatpak cannot see host libraries). Prints one JSON object.
DISCOVER_SCRIPT = r'''
import glob, json, os, shutil
libs = []
for pat in ("/opt/rocm*/lib/libamdhip64.so*", "/opt/rocm*/lib64/libamdhip64.so*",
            "/usr/lib/libamdhip64.so*", "/usr/lib64/libamdhip64.so*"):
    libs += glob.glob(pat)
rp = os.environ.get("ROCM_PATH")
if rp:
    libs += glob.glob(os.path.join(rp, "lib", "libamdhip64.so*"))
libs = sorted({os.path.realpath(p) for p in libs if os.path.isfile(p)})
icds = []
for d in ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d"):
    icds += [p for p in glob.glob(d + "/*.json") if "radeon" in os.path.basename(p).lower()
             or "amd" in os.path.basename(p).lower()]
ver = None
for p in glob.glob("/opt/rocm*/.info/version"):
    try:
        ver = open(p).read().strip()
    except OSError:
        pass
print(json.dumps({"hip_libs": libs, "rocm_version": ver,
                  "rocminfo": shutil.which("rocminfo"), "vulkaninfo": shutil.which("vulkaninfo"),
                  "vulkan_icds": sorted(icds),
                  "kfd_access": os.access("/dev/kfd", os.R_OK | os.W_OK),
                  "render_access": any(os.access(p, os.R_OK | os.W_OK)
                                       for p in glob.glob("/dev/dri/renderD*"))}))
'''

# The allocation smoke test: HIP through ctypes, no third-party package. argv: lib, MiB.
SMOKE_SCRIPT = r'''
import ctypes, json, sys
def done(ok, msg):
    print(json.dumps({"ok": ok, "detail": msg})); sys.exit(0)
try:
    hip = ctypes.CDLL(sys.argv[1])
except OSError as e:
    done(False, f"cannot load {sys.argv[1]}: {e}")
size = int(sys.argv[2]) * 1048576
count = ctypes.c_int(0)
rc = hip.hipGetDeviceCount(ctypes.byref(count))
if rc != 0 or count.value < 1:
    done(False, f"hipGetDeviceCount rc={rc}, devices={count.value}")
ptr = ctypes.c_void_p()
rc = hip.hipMalloc(ctypes.byref(ptr), ctypes.c_size_t(size))
if rc != 0:
    done(False, f"hipMalloc of {sys.argv[2]} MiB failed (rc={rc})")
try:
    rc = hip.hipMemset(ptr, 0xA5, ctypes.c_size_t(size))
    sync = hip.hipDeviceSynchronize()
    buf = (ctypes.c_ubyte * 4096)()
    back = hip.hipMemcpy(buf, ptr, ctypes.c_size_t(4096), 2)   # device to host
    good = rc == 0 and sync == 0 and back == 0 and all(b == 0xA5 for b in buf)
finally:
    hip.hipFree(ptr)
done(good, f"{sys.argv[2]} MiB allocated, written, read back and freed" if good
     else f"memset/sync/readback failed (rc={rc}/{sync}/{back})")
'''


def discover():
    """Runtime discovery on the host: ROCm/HIP libraries, Vulkan ICDs, device access."""
    res = host.run(["python3", "-I", "-c", DISCOVER_SCRIPT], timeout=DISCOVER_TIMEOUT_S)
    info = {}
    if res.rc == 0:
        try:
            info = _json_line(res.out)
        except (ValueError, IndexError):
            info = {}
    return {
        "ok": bool(info),
        "hip_libs": info.get("hip_libs") or [],
        "rocm_version": info.get("rocm_version"),
        "rocminfo": info.get("rocminfo"),
        "vulkaninfo": info.get("vulkaninfo"),
        "vulkan_icds": info.get("vulkan_icds") or [],
        "kfd_access": info.get("kfd_access"),
        "render_access": info.get("render_access"),
    }


def health_report():
    """Everything the panel shows. Read-only; allocates nothing."""
    agents = gpu_identity()
    return {
        "memory": memory_report(),
        "gpu_agents": agents,
        "gfx1151": any(a["gfx"] == "gfx1151" for a in agents),
        "kernel": platform.release(),
        "runtime": discover(),
        "smoke_budget": SMOKE_BUDGET_TEXT,
    }


def clamp_budget(mib, available_bytes):
    """The budget the smoke test will really use, or None when RAM is too tight.

    Clamped to 16..SMOKE_MAX_MIB and to a quarter of available RAM; None when that leaves under 16 MiB.
    """
    try:
        mib = int(mib)
    except (TypeError, ValueError):
        mib = SMOKE_DEFAULT_MIB
    mib = max(16, min(mib, SMOKE_MAX_MIB))
    if available_bytes is not None:
        mib = min(mib, available_bytes // 4 // MIB)
    return mib if mib >= 16 else None


def smoke_test(budget_mib=SMOKE_DEFAULT_MIB, hip_lib=None):
    """Allocate, fill, read back and free `budget_mib` of GPU memory through HIP.

    Only called on request. Returns {"ok": bool, "detail": str, "budget_mib": int|None}.
    Never raises; a timeout or a missing runtime is a not-ok result with the reason.
    """
    avail = parse_meminfo(_read(MEMINFO)).get("MemAvailable")
    budget = clamp_budget(budget_mib, avail)
    if budget is None:
        return {"ok": False, "budget_mib": None,
                "detail": "Not enough available RAM for a safe test allocation."}
    if hip_lib is None:
        libs = discover()["hip_libs"]
        if not libs:
            return {"ok": False, "budget_mib": budget,
                    "detail": "No ROCm/HIP runtime (libamdhip64) found on this system."}
        hip_lib = libs[0]
    res = host.run(["python3", "-I", "-c", SMOKE_SCRIPT, hip_lib, str(budget)],
                   timeout=SMOKE_TIMEOUT_S)
    if res.timed_out:
        return {"ok": False, "budget_mib": budget,
                "detail": f"Timed out after {SMOKE_TIMEOUT_S} s."}
    try:
        data = _json_line(res.out)
        return {"ok": bool(data["ok"]), "budget_mib": budget, "detail": str(data["detail"])}
    except (ValueError, IndexError, KeyError, TypeError):
        return {"ok": False, "budget_mib": budget,
                "detail": f"Test did not complete (rc={res.rc}): {res.out[-200:]}"}

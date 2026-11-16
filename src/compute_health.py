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
KFD_ROOT = "/sys/class/kfd/kfd"
AMDGPU_MODULE = "/sys/module/amdgpu"

MIB = 1024 * 1024
SMOKE_DEFAULT_MIB = 256
SMOKE_MAX_MIB = 1024
SMOKE_TIMEOUT_S = 20          # the allocation step only
DISCOVER_TIMEOUT_S = 15       # runtime discovery, which runs first when no library is given
RUNTIMES = ("hip", "vulkan")
VULKAN_LIB = "libvulkan.so.1"
VULKANINFO_TIMEOUT_S = 10
# First ROCm release that lists gfx1151 (Strix Halo) targets at all. Support is per component and
# changes between releases, so this is a hint, not a guarantee (see COMPAT_NOTES).
MIN_ROCM_GFX1151 = (6, 4, 1)
COMPAT_NOTES = (
    "ROCm support for gfx1151 varies by component and by release; for example hipSPARSELt is "
    "excluded for gfx1151. Finding a library is not proof that every component works.",
    "Nothing here sets HSA_OVERRIDE_GFX_VERSION, changes GTT limits or installs packages.",
)
SMOKE_BUDGET_TEXT = (f"Allocates {SMOKE_DEFAULT_MIB} MiB of GPU memory (never more than {SMOKE_MAX_MIB} MiB, "
                     f"and never more than a quarter of available RAM), then frees it. "
                     f"Finding the runtime takes up to {DISCOVER_TIMEOUT_S} s; the allocation gives up after "
                     f"{SMOKE_TIMEOUT_S} s. Choose HIP or Vulkan. Runs only when you press the button.")

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


# The Vulkan allocation smoke test: libvulkan through ctypes, no third-party package and no
# shader. It creates an instance and a device, allocates a host-visible block, maps it, fills it,
# reads it back and frees everything. argv: library, MiB.
VULKAN_SMOKE_SCRIPT = r'''
import ctypes as C, json, sys
def done(ok, msg):
    print(json.dumps({"ok": ok, "detail": msg})); sys.exit(0)
try:
    vk = C.CDLL(sys.argv[1])
except OSError as e:
    done(False, f"cannot load {sys.argv[1]}: {e}")
mib = int(sys.argv[2]); size = mib * 1048576
P, U32, I32, U64 = C.c_void_p, C.c_uint32, C.c_int32, C.c_uint64
class AppInfo(C.Structure):
    _fields_ = [("sType", I32), ("pNext", P), ("name", C.c_char_p), ("appVer", U32), ("engName", C.c_char_p),
                ("engVer", U32), ("apiVersion", U32)]
class InstInfo(C.Structure):
    _fields_ = [("sType", I32), ("pNext", P), ("flags", U32), ("app", C.POINTER(AppInfo)), ("nLayers", U32),
                ("layers", P), ("nExt", U32), ("ext", P)]
class QInfo(C.Structure):
    _fields_ = [("sType", I32), ("pNext", P), ("flags", U32), ("family", U32), ("count", U32),
                ("prio", C.POINTER(C.c_float))]
class DevInfo(C.Structure):
    _fields_ = [("sType", I32), ("pNext", P), ("flags", U32), ("nQ", U32), ("q", C.POINTER(QInfo)),
                ("nLayers", U32), ("layers", P), ("nExt", U32), ("ext", P), ("features", P)]
class QFam(C.Structure):
    _fields_ = [("flags", U32), ("count", U32), ("ts", U32), ("gran", U32 * 3)]
class MType(C.Structure):
    _fields_ = [("flags", U32), ("heap", U32)]
class MHeap(C.Structure):
    _fields_ = [("size", U64), ("flags", U32)]
class MemProps(C.Structure):
    _fields_ = [("nTypes", U32), ("types", MType * 32), ("nHeaps", U32), ("heaps", MHeap * 16)]
class AllocInfo(C.Structure):
    _fields_ = [("sType", I32), ("pNext", P), ("size", U64), ("type", U32)]
for n, a in (("vkCreateInstance", [P, P, P]), ("vkEnumeratePhysicalDevices", [P, P, P]),
             ("vkGetPhysicalDeviceProperties", [P, P]), ("vkGetPhysicalDeviceQueueFamilyProperties", [P, P, P]),
             ("vkGetPhysicalDeviceMemoryProperties", [P, P]), ("vkCreateDevice", [P, P, P, P]),
             ("vkAllocateMemory", [P, P, P, P]), ("vkMapMemory", [P, P, U64, U64, U32, P]),
             ("vkUnmapMemory", [P, P]), ("vkFreeMemory", [P, P, P]), ("vkDestroyDevice", [P, P]),
             ("vkDestroyInstance", [P, P])):
    f = getattr(vk, n); f.argtypes = a; f.restype = I32 if n.startswith(("vkCreate", "vkEnum", "vkAlloc", "vkMap")) else None
inst, dev, mem, mapped = P(), P(), P(), P()
res, good = "not started", False
try:
    app = AppInfo(0, None, b"z13-helper", 1, b"none", 1, 1 << 22)
    rc = vk.vkCreateInstance(C.byref(InstInfo(1, None, 0, C.pointer(app), 0, None, 0, None)), None, C.byref(inst))
    if rc != 0:
        done(False, f"vkCreateInstance failed (rc={rc})")
    n = U32(0)
    vk.vkEnumeratePhysicalDevices(inst, C.byref(n), None)
    if n.value < 1:
        done(False, "no Vulkan physical device")
    devs = (P * n.value)()
    vk.vkEnumeratePhysicalDevices(inst, C.byref(n), devs)
    pick, name = None, ""
    for d in devs:
        props = (C.c_ubyte * 2048)()
        vk.vkGetPhysicalDeviceProperties(d, props)
        dtype = int.from_bytes(bytes(props[16:20]), "little")     # 1 integrated, 2 discrete
        if dtype in (1, 2) and pick is None:
            pick, name = d, bytes(props[20:276]).split(b"\0")[0].decode("utf-8", "replace")
    if pick is None:
        done(False, "no GPU among the Vulkan devices (software renderers are ignored)")
    nf = U32(0)
    vk.vkGetPhysicalDeviceQueueFamilyProperties(pick, C.byref(nf), None)
    fams = (QFam * max(nf.value, 1))()
    vk.vkGetPhysicalDeviceQueueFamilyProperties(pick, C.byref(nf), fams)
    fam = next((i for i in range(nf.value) if fams[i].flags & 2), 0)
    prio = C.c_float(1.0)
    q = QInfo(2, None, 0, fam, 1, C.pointer(prio))
    rc = vk.vkCreateDevice(pick, C.byref(DevInfo(3, None, 0, 1, C.pointer(q), 0, None, 0, None, None)), None, C.byref(dev))
    if rc != 0:
        done(False, f"vkCreateDevice failed on {name} (rc={rc})")
    mp = MemProps()
    vk.vkGetPhysicalDeviceMemoryProperties(pick, C.byref(mp))
    want = 0x6                                                  # host visible + host coherent
    best = None
    for i in range(mp.nTypes):
        t = mp.types[i]
        if t.flags & want == want and mp.heaps[t.heap].size >= size:
            if best is None or (t.flags & 1):                    # prefer device local too
                best = i
    if best is None:
        done(False, f"no host-visible memory type with {mib} MiB on {name}")
    rc = vk.vkAllocateMemory(dev, C.byref(AllocInfo(5, None, size, best)), None, C.byref(mem))
    if rc != 0:
        done(False, f"vkAllocateMemory of {mib} MiB failed on {name} (rc={rc})")
    rc = vk.vkMapMemory(dev, mem, 0, size, 0, C.byref(mapped))
    if rc != 0:
        done(False, f"vkMapMemory failed on {name} (rc={rc})")
    C.memset(mapped, 0xA5, size)
    head = C.string_at(mapped.value, 4096)
    tail = C.string_at(mapped.value + size - 4096, 4096)
    good = head == b"\xa5" * 4096 and tail == b"\xa5" * 4096
    vk.vkUnmapMemory(dev, mem)
    res = (f"{mib} MiB allocated, mapped, written, read back and freed on {name}" if good
           else f"readback mismatch on {name}")
finally:
    if mem.value and dev.value:
        vk.vkFreeMemory(dev, mem, None)
    if dev.value:
        vk.vkDestroyDevice(dev, None)
    if inst.value:
        vk.vkDestroyInstance(inst, None)
done(good, res)
'''


def parse_vulkaninfo(text):
    """Instance version and GPUs from `vulkaninfo --summary` text."""
    out = {"instance_version": None, "devices": []}
    cur = None
    for line in (text or "").splitlines():
        key, _, val = line.strip().partition("=")
        key, val = key.strip(), val.strip()
        if line.startswith("Vulkan Instance Version:"):
            out["instance_version"] = line.partition(":")[2].strip()
        elif key.startswith("GPU") and not val and key[3:].rstrip(":").isdigit():
            cur = {"name": None, "api_version": None, "driver": None, "type": None}
            out["devices"].append(cur)
        elif cur is not None and val:
            if key == "deviceName":
                cur["name"] = val
            elif key == "apiVersion":
                cur["api_version"] = val.split()[0]
            elif key == "driverName":
                cur["driver"] = val
            elif key == "deviceType":
                cur["type"] = val.replace("PHYSICAL_DEVICE_TYPE_", "")
    return out


def vulkan_info(vulkaninfo_path):
    """Device and version report from `vulkaninfo --summary`; read-only, no workload."""
    if not vulkaninfo_path:
        return {"ok": False, "instance_version": None, "devices": []}
    res = host.run([vulkaninfo_path, "--summary"], timeout=VULKANINFO_TIMEOUT_S)
    info = parse_vulkaninfo(res.out) if res.rc == 0 else {"instance_version": None, "devices": []}
    info["ok"] = bool(info["devices"])
    return info


def parse_rocm_version(text):
    """"6.4.1-123" -> (6, 4, 1); None when it does not start with numbers."""
    parts = []
    for piece in (text or "").strip().split("-")[0].split("."):
        if not piece.isdigit():
            break
        parts.append(int(piece))
    return tuple(parts[:3]) + (0,) * (3 - len(parts[:3])) if parts else None


def lib_version(path):
    """"/opt/rocm/lib/libamdhip64.so.6.4.60401" -> "6.4.60401"; None for a bare .so."""
    base = os.path.basename(path or "")
    _, sep, tail = base.partition(".so.")
    return tail if sep and tail else None


def compat_report(kernel, agents, runtime, amdgpu_loaded, kfd_present):
    """Host/ROCm compatibility rows [{"label", "status", "detail"}], status ok|warn|info.

    Pure: all inputs are already collected. A warning is a hint to look, not a verdict.
    """
    rows = [{"label": "Kernel", "status": "info", "detail": kernel or "unknown"}]
    rows.append({"label": "amdgpu driver", "status": "ok" if amdgpu_loaded else "warn",
                 "detail": "loaded" if amdgpu_loaded else "module not loaded"})
    rows.append({"label": "KFD (compute interface)", "status": "ok" if kfd_present else "warn",
                 "detail": "present" if kfd_present else "/sys/class/kfd missing"})
    has = any(a["gfx"] == "gfx1151" for a in agents)
    rows.append({"label": "GPU target", "status": "ok" if has else "warn",
                 "detail": ", ".join(a["gfx"] for a in agents) or "no GPU agent from KFD"})
    ver = parse_rocm_version(runtime.get("rocm_version"))
    want = ".".join(map(str, MIN_ROCM_GFX1151))
    if not runtime.get("ok"):
        rows.append({"label": "ROCm", "status": "info", "detail": "runtime discovery unavailable"})
    elif not runtime.get("hip_libs"):
        rows.append({"label": "ROCm", "status": "info", "detail": "no ROCm/HIP runtime found"})
    elif ver is None:
        rows.append({"label": "ROCm", "status": "info",
                     "detail": f"version not recorded; gfx1151 needs {want} or newer"})
    elif ver < MIN_ROCM_GFX1151:
        rows.append({"label": "ROCm", "status": "warn" if has else "info",
                     "detail": f"{'.'.join(map(str, ver))} predates gfx1151 support ({want} or newer)"})
    else:
        rows.append({"label": "ROCm", "status": "ok",
                     "detail": f"{'.'.join(map(str, ver))} (gfx1151 targets exist from {want})"})
    libs = [lib_version(p) for p in runtime.get("hip_libs") or []]
    rows.append({"label": "HIP library", "status": "info",
                 "detail": ", ".join(v for v in libs if v) or "no versioned library"})
    rows.append({"label": "Access", "status": "ok" if runtime.get("kfd_access") and runtime.get("render_access")
                 else "warn" if runtime.get("ok") else "info",
                 "detail": f"/dev/kfd {'yes' if runtime.get('kfd_access') else 'no'}, "
                           f"render node {'yes' if runtime.get('render_access') else 'no'}"})
    return rows


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
    runtime = discover()
    kernel = platform.release()
    return {
        "memory": memory_report(),
        "gpu_agents": agents,
        "gfx1151": any(a["gfx"] == "gfx1151" for a in agents),
        "kernel": kernel,
        "runtime": runtime,
        "vulkan": vulkan_info(runtime["vulkaninfo"]),
        "compat": compat_report(kernel, agents, runtime, os.path.isdir(AMDGPU_MODULE),
                                os.path.isdir(KFD_ROOT)),
        "compat_notes": list(COMPAT_NOTES),
        "runtimes": list(RUNTIMES),
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


def smoke_test(budget_mib=SMOKE_DEFAULT_MIB, hip_lib=None, runtime="hip", vulkan_lib=None):
    """Allocate, fill, read back and free `budget_mib` of GPU memory through HIP or Vulkan.

    Only called on request. Returns {"ok", "detail", "budget_mib", "runtime"}.
    Never raises; a timeout or a missing runtime is a not-ok result with the reason.
    """
    if runtime not in RUNTIMES:
        return {"ok": False, "budget_mib": None, "runtime": runtime,
                "detail": f"Unknown runtime {runtime!r}; choose one of {', '.join(RUNTIMES)}."}
    avail = parse_meminfo(_read(MEMINFO)).get("MemAvailable")
    budget = clamp_budget(budget_mib, avail)
    if budget is None:
        return {"ok": False, "budget_mib": None, "runtime": runtime,
                "detail": "Not enough available RAM for a safe test allocation."}
    if runtime == "hip":
        script, lib = SMOKE_SCRIPT, hip_lib
        if lib is None:
            libs = discover()["hip_libs"]
            if not libs:
                return {"ok": False, "budget_mib": budget, "runtime": runtime,
                        "detail": "No ROCm/HIP runtime (libamdhip64) found on this system."}
            lib = libs[0]
    else:
        script, lib = VULKAN_SMOKE_SCRIPT, vulkan_lib
        if lib is None:
            if not discover()["vulkan_icds"]:
                return {"ok": False, "budget_mib": budget, "runtime": runtime,
                        "detail": "No AMD Vulkan driver file found on this system."}
            lib = VULKAN_LIB
    res = host.run(["python3", "-I", "-c", script, lib, str(budget)], timeout=SMOKE_TIMEOUT_S)
    if res.timed_out:
        return {"ok": False, "budget_mib": budget, "runtime": runtime,
                "detail": f"Timed out after {SMOKE_TIMEOUT_S} s."}
    try:
        data = _json_line(res.out)
        return {"ok": bool(data["ok"]), "budget_mib": budget, "runtime": runtime,
                "detail": str(data["detail"])}
    except (ValueError, IndexError, KeyError, TypeError):
        return {"ok": False, "budget_mib": budget, "runtime": runtime,
                "detail": f"Test did not complete (rc={res.rc}): {res.out[-200:]}"}

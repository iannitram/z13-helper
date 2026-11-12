"""compute_health: memory breakdown, gfx identification, runtime discovery, bounded smoke test (#26)."""

import json
import sys

import pytest

import compute_health as ch

MEMINFO = "MemTotal:       98304000 kB\nMemFree: 1000 kB\nMemAvailable:   60000000 kB\nHugePages_Total: 0\n"
GIB = 1024 ** 3


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    drm, kfd = tmp_path / "drm", tmp_path / "kfd"
    drm.mkdir(), kfd.mkdir()
    (tmp_path / "meminfo").write_text(MEMINFO)
    (tmp_path / "pressure").write_text("some avg10=1.50 avg60=0.00 avg300=0.00 total=1\nfull avg10=0.00\n")
    monkeypatch.setattr(ch, "DRM_ROOT", str(drm))
    monkeypatch.setattr(ch, "KFD_NODES", str(kfd))
    monkeypatch.setattr(ch, "MEMINFO", str(tmp_path / "meminfo"))
    monkeypatch.setattr(ch, "PRESSURE", str(tmp_path / "pressure"))
    return tmp_path


def card(sysfs, name, vram, used, gtt, gtt_used, boot_vga=None):
    d = sysfs / "drm" / name / "device"
    d.mkdir(parents=True)
    for k, v in (("mem_info_vram_total", vram), ("mem_info_vram_used", used),
                 ("mem_info_gtt_total", gtt), ("mem_info_gtt_used", gtt_used)):
        (d / k).write_text(f"{v}\n")
    if boot_vga is not None:
        (d / "boot_vga").write_text(f"{boot_vga}\n")


def is_smoke(argv):
    return ch.SMOKE_SCRIPT in argv


def test_parse_meminfo_in_bytes():
    mi = ch.parse_meminfo(MEMINFO)
    assert mi["MemTotal"] == 98304000 * 1024 and mi["HugePages_Total"] == 0


def test_pools_are_not_added_together(sysfs):
    card(sysfs, "card1", 16 * GIB, 1 * GIB, 60 * GIB, 2 * GIB, boot_vga=1)
    r = ch.memory_report()
    assert r["ram_total"] == 98304000 * 1024
    assert r["physical_total"] == r["ram_total"] + 16 * GIB       # VRAM is outside MemTotal
    assert r["gtt_limit"] == 60 * GIB and r["gtt_used"] == 2 * GIB   # GTT is inside it, not extra
    assert r["ram_used"] == r["ram_total"] - r["ram_available"]
    assert r["pressure_avg10"] == 1.5
    assert any("GTT" in n for n in r["notes"])


def test_igpu_chosen_by_boot_vga(sysfs):
    card(sysfs, "card0", 24 * GIB, 0, 0, 0, boot_vga=0)           # eGPU
    card(sysfs, "card1", 16 * GIB, 0, 0, 0, boot_vga=1)
    assert ch.memory_report()["vram_reserved"] == 16 * GIB


def test_unreadable_is_none_not_zero(sysfs):
    (sysfs / "meminfo").write_text("")
    (sysfs / "pressure").unlink()
    r = ch.memory_report()
    assert all(r[k] is None for k in ("ram_total", "ram_used", "vram_reserved", "physical_total",
                                      "gtt_limit", "pressure_avg10"))


def test_gfx_name():
    assert ch.gfx_name(110501) == "gfx1151"
    assert ch.gfx_name(90010) == "gfx90a"
    assert ch.gfx_name(0) is None and ch.gfx_name("x") is None


def test_gpu_identity_skips_cpu_nodes(sysfs):
    for i, gfx in enumerate(("0", "110501")):
        n = sysfs / "kfd" / str(i)
        n.mkdir()
        (n / "properties").write_text(f"cpu_cores_count 32\ngfx_target_version {gfx}\nvendor_id 4098\ndevice_id 5510\n")
    assert ch.gpu_identity() == [{"gfx": "gfx1151", "name": "1002:1586"}]


def test_no_kfd_means_no_agents(sysfs):
    (sysfs / "kfd").rmdir()
    assert ch.gpu_identity() == []


def test_discover_script_runs_and_prints_json(real_subprocess):
    out = real_subprocess([sys.executable, "-I", "-c", ch.DISCOVER_SCRIPT], capture_output=True,
                         text=True, timeout=30).stdout
    data = json.loads(out.splitlines()[-1])
    assert {"hip_libs", "vulkan_icds", "kfd_access", "render_access"} <= set(data)


def test_discover_parses_host_answer(fake_host):
    payload = {"hip_libs": ["/opt/rocm/lib/libamdhip64.so.6"], "rocm_version": "6.4.1",
               "rocminfo": "/opt/rocm/bin/rocminfo", "vulkaninfo": None, "vulkan_icds": [],
               "kfd_access": True, "render_access": False}
    fake_host.fail("python3", rc=0, out="warn\n" + json.dumps(payload))
    r = ch.discover()
    assert r["ok"] and r["hip_libs"] == payload["hip_libs"] and r["kfd_access"] is True


def test_discover_failure_is_not_ok(fake_host):
    fake_host.fail("python3", rc=127, out="missing")
    r = ch.discover()
    assert r["ok"] is False and r["hip_libs"] == []


def test_clamp_budget():
    assert ch.clamp_budget(256, 64 * GIB) == 256
    assert ch.clamp_budget(10 ** 6, 64 * GIB) == ch.SMOKE_MAX_MIB
    assert ch.clamp_budget(1024, 2 * GIB) == 512                 # a quarter of available RAM
    assert ch.clamp_budget(256, 32 * 1024 ** 2) is None          # too tight
    assert ch.clamp_budget("junk", 64 * GIB) == ch.SMOKE_DEFAULT_MIB
    assert ch.clamp_budget(1, 64 * GIB) == 16


def test_smoke_never_runs_without_a_runtime(sysfs, fake_host):
    fake_host.fail("python3", rc=0, out=json.dumps({"hip_libs": []}))
    r = ch.smoke_test()
    assert r["ok"] is False and "No ROCm" in r["detail"]
    assert not any(is_smoke(a) for a in fake_host.argvs)


def test_smoke_runs_bounded(sysfs, fake_host):
    fake_host.fail(is_smoke, rc=0, out=json.dumps({"ok": True, "detail": "fine"}))
    r = ch.smoke_test(10 ** 6, hip_lib="/opt/rocm/lib/libamdhip64.so")
    call = fake_host.calls[-1]
    assert r == {"ok": True, "budget_mib": ch.SMOKE_MAX_MIB, "runtime": "hip", "detail": "fine"}
    assert call.timeout == ch.SMOKE_TIMEOUT_S
    assert call.argv[-2:] == ["/opt/rocm/lib/libamdhip64.so", str(ch.SMOKE_MAX_MIB)]
    assert "HSA_OVERRIDE_GFX_VERSION" not in ch.SMOKE_SCRIPT


def test_smoke_timeout_and_garbage(sysfs, fake_host):
    fake_host.fail(is_smoke, rc=124, out="timed out", timed_out=True, times=1)
    assert "Timed out" in ch.smoke_test(hip_lib="x")["detail"]
    fake_host.fail(is_smoke, rc=1, out="Segmentation fault")
    r = ch.smoke_test(hip_lib="x")
    assert r["ok"] is False and "Segmentation" in r["detail"]


def test_smoke_script_reports_unloadable_library(real_subprocess):
    out = real_subprocess([sys.executable, "-I", "-c", ch.SMOKE_SCRIPT, "/nonexistent/libamdhip64.so", "16"],
                         capture_output=True, text=True, timeout=30).stdout
    data = json.loads(out.splitlines()[-1])
    assert data["ok"] is False and "cannot load" in data["detail"]


def test_report_text_keeps_pools_apart_and_marks_unknowns():
    pytest.importorskip("PyQt6.QtWidgets")          # compute_dialog imports Qt
    import compute_dialog as cd
    r = {
        "memory": {"physical_total": 112 * GIB, "ram_total": 96 * GIB, "ram_available": 60 * GIB,
                   "ram_used": 36 * GIB, "vram_reserved": 16 * GIB, "vram_used": None,
                   "gtt_limit": 48 * GIB, "gtt_used": 2 * GIB, "pressure_avg10": None,
                   "notes": ["note-a"]},
        "gpu_agents": [{"gfx": "gfx1151", "name": "1002:1586"}], "gfx1151": True, "kernel": "7.2",
        "runtime": {"ok": True, "hip_libs": [], "rocm_version": None, "vulkan_icds": [],
                    "kfd_access": True, "render_access": None, "rocminfo": None, "vulkaninfo": None},
    }
    text = cd.format_report(r)
    assert "not additive" in text
    assert "GTT (shared, inside Linux RAM): up to 48.0 GiB, in use 2.0 GiB" in text
    assert "Firmware-reserved VRAM: 16.0 GiB, in use unavailable" in text
    assert "Memory pressure (10 s): unavailable" in text
    assert "gfx1151 (Strix Halo): yes" in text
    assert "ROCm/HIP: not found" in text
    assert "render node access: unavailable" in text


# ---- Vulkan verification, runtime choice, compatibility diagnostic (#26) ----

VULKANINFO = """Vulkan Instance Version: 1.4.350

Devices:
========
GPU0:
\tapiVersion         = 1.4.350
\tdeviceType         = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
\tdeviceName         = AMD Radeon 8060S Graphics (RADV STRIX_HALO)
\tdriverName         = radv
GPU1:
\tapiVersion         = 1.3.0
\tdeviceType         = PHYSICAL_DEVICE_TYPE_CPU
\tdeviceName         = llvmpipe
\tdriverName         = llvmpipe
"""


def is_vk_smoke(argv):
    return ch.VULKAN_SMOKE_SCRIPT in argv


def test_parse_vulkaninfo_lists_devices_and_version():
    r = ch.parse_vulkaninfo(VULKANINFO)
    assert r["instance_version"] == "1.4.350"
    assert [d["name"] for d in r["devices"]] == ["AMD Radeon 8060S Graphics (RADV STRIX_HALO)", "llvmpipe"]
    assert r["devices"][0] == {"name": "AMD Radeon 8060S Graphics (RADV STRIX_HALO)", "api_version": "1.4.350",
                               "driver": "radv", "type": "INTEGRATED_GPU"}


def test_vulkan_info_runs_summary_only(fake_host):
    fake_host.fail(lambda a: a[-1] == "--summary", rc=0, out=VULKANINFO)
    r = ch.vulkan_info("/usr/bin/vulkaninfo")
    assert r["ok"] and r["instance_version"] == "1.4.350"
    assert fake_host.calls[-1].timeout == ch.VULKANINFO_TIMEOUT_S
    assert ch.vulkan_info(None)["ok"] is False


def test_vulkan_info_failure_is_not_ok(fake_host):
    fake_host.fail(lambda a: a[-1] == "--summary", rc=1, out="boom")
    assert ch.vulkan_info("/usr/bin/vulkaninfo") == {"ok": False, "instance_version": None, "devices": [],}


def test_smoke_rejects_unknown_runtime_without_running(sysfs, fake_host):
    r = ch.smoke_test(runtime="cuda")
    assert r["ok"] is False and "Unknown runtime" in r["detail"]
    assert not fake_host.argvs


def test_smoke_hip_is_still_the_default(sysfs, fake_host):
    fake_host.fail(is_smoke, rc=0, out=json.dumps({"ok": True, "detail": "fine"}))
    r = ch.smoke_test(hip_lib="/opt/rocm/lib/libamdhip64.so")
    assert r["runtime"] == "hip" and r["ok"] and not any(is_vk_smoke(a) for a in fake_host.argvs)


def test_smoke_vulkan_runs_bounded(sysfs, fake_host):
    fake_host.fail(is_vk_smoke, rc=0, out=json.dumps({"ok": True, "detail": "fine"}))
    r = ch.smoke_test(10 ** 6, runtime="vulkan", vulkan_lib="libvulkan.so.1")
    call = fake_host.calls[-1]
    assert r == {"ok": True, "budget_mib": ch.SMOKE_MAX_MIB, "runtime": "vulkan", "detail": "fine"}
    assert call.timeout == ch.SMOKE_TIMEOUT_S
    assert call.argv[-2:] == ["libvulkan.so.1", str(ch.SMOKE_MAX_MIB)]
    assert "HSA_OVERRIDE_GFX_VERSION" not in ch.VULKAN_SMOKE_SCRIPT


def test_smoke_vulkan_needs_an_amd_driver(sysfs, fake_host):
    fake_host.fail("python3", rc=0, out=json.dumps({"hip_libs": ["/x"], "vulkan_icds": []}))
    r = ch.smoke_test(runtime="vulkan")
    assert r["ok"] is False and "Vulkan driver" in r["detail"]
    assert not any(is_vk_smoke(a) for a in fake_host.argvs)


def test_smoke_vulkan_timeout(sysfs, fake_host):
    fake_host.fail(is_vk_smoke, rc=124, out="", timed_out=True)
    assert "Timed out" in ch.smoke_test(runtime="vulkan", vulkan_lib="x")["detail"]


def test_vulkan_script_reports_unloadable_library(real_subprocess):
    out = real_subprocess([sys.executable, "-I", "-c", ch.VULKAN_SMOKE_SCRIPT, "/nonexistent/libvulkan.so.1", "16"],
                         capture_output=True, text=True, timeout=30).stdout
    data = json.loads(out.splitlines()[-1])
    assert data["ok"] is False and "cannot load" in data["detail"]


def test_vulkan_script_compiles():
    compile(ch.VULKAN_SMOKE_SCRIPT, "vulkan-smoke", "exec")


def test_parse_rocm_version_and_lib_version():
    assert ch.parse_rocm_version("6.4.1-123") == (6, 4, 1)
    assert ch.parse_rocm_version("7.0") == (7, 0, 0)
    assert ch.parse_rocm_version("junk") is None and ch.parse_rocm_version(None) is None
    assert ch.lib_version("/opt/rocm/lib/libamdhip64.so.6.4.60401") == "6.4.60401"
    assert ch.lib_version("/opt/rocm/lib/libamdhip64.so") is None


def _rows(rt, agents=None, amdgpu=True, kfd=True):
    agents = [{"gfx": "gfx1151", "name": None}] if agents is None else agents
    return {r["label"]: r for r in ch.compat_report("7.2.0", agents, rt, amdgpu, kfd)}


RT_OK = {"ok": True, "hip_libs": ["/opt/rocm/lib/libamdhip64.so.6.4.60401"], "rocm_version": "6.4.1",
         "kfd_access": True, "render_access": True}


def test_compat_all_good():
    rows = _rows(RT_OK)
    assert all(r["status"] in ("ok", "info") for r in rows.values())
    assert rows["ROCm"]["status"] == "ok" and rows["HIP library"]["detail"] == "6.4.60401"
    assert rows["Kernel"]["detail"] == "7.2.0"


def test_compat_old_rocm_warns_on_gfx1151():
    rows = _rows({**RT_OK, "rocm_version": "6.2.4"})
    assert rows["ROCm"]["status"] == "warn" and "predates" in rows["ROCm"]["detail"]


def test_compat_missing_pieces():
    rows = _rows({"ok": True, "hip_libs": [], "rocm_version": None, "kfd_access": False, "render_access": True},
                 agents=[], amdgpu=False, kfd=False)
    assert rows["amdgpu driver"]["status"] == "warn" and rows["KFD (compute interface)"]["status"] == "warn"
    assert rows["GPU target"]["status"] == "warn"
    assert rows["ROCm"]["status"] == "info" and rows["Access"]["status"] == "warn"


def test_compat_discovery_failed_is_info_not_warn():
    rows = _rows({"ok": False, "hip_libs": []})
    assert rows["ROCm"]["status"] == "info" and rows["Access"]["status"] == "info"


def test_health_report_has_vulkan_compat_and_choices(sysfs, fake_host, monkeypatch):
    monkeypatch.setattr(ch, "AMDGPU_MODULE", str(sysfs))
    monkeypatch.setattr(ch, "KFD_ROOT", str(sysfs / "kfd"))
    fake_host.fail(lambda a: a[-1] == "--summary", rc=0, out=VULKANINFO)
    fake_host.fail("python3", rc=0, out=json.dumps({"hip_libs": [], "vulkaninfo": "/usr/bin/vulkaninfo"}))
    r = ch.health_report()
    assert r["runtimes"] == ["hip", "vulkan"]
    assert r["vulkan"]["devices"][0]["driver"] == "radv"
    assert any(row["label"] == "ROCm" for row in r["compat"])
    assert any("hipSPARSELt" in n for n in r["compat_notes"])
    assert not any(is_smoke(a) or is_vk_smoke(a) for a in fake_host.argvs)   # reporting allocates nothing


def test_report_text_shows_vulkan_and_compat():
    pytest.importorskip("PyQt6.QtWidgets")
    import compute_dialog as cd
    r = {
        "memory": {"physical_total": None, "ram_total": None, "ram_available": None, "ram_used": None,
                   "vram_reserved": None, "vram_used": None, "gtt_limit": None, "gtt_used": None,
                   "pressure_avg10": None, "notes": []},
        "gpu_agents": [], "gfx1151": False, "kernel": "7.2",
        "runtime": {"ok": False},
        "vulkan": ch.parse_vulkaninfo(VULKANINFO) | {"ok": True},
        "compat": [{"label": "ROCm", "status": "warn", "detail": "too old"}],
        "compat_notes": ["note-c"],
    }
    text = cd.format_report(r)
    assert "Vulkan 1.4.350: AMD Radeon 8060S Graphics (RADV STRIX_HALO) [radv, 1.4.350]" in text
    assert "[warn] ROCm: too old" in text and "note-c" in text

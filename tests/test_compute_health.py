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
    assert r == {"ok": True, "budget_mib": ch.SMOKE_MAX_MIB, "detail": "fine"}
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

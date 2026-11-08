"""Has upstream fixed it? probes (issue #71, recipe 7): pure checks on sample outputs."""

import pytest

from post_update.probes import (
    ProbeResult,
    evaluate_upstream_probes,
    probe_kernel_version,
    probe_mes_firmware,
    probe_mt7925_bt,
    probe_ryzen_smu,
    probe_wmi_write_hang,
    upstream_probes_recipe,
)

SAMPLE_DMESG_MES_OLD = "[    1.234567] amdgpu: [drm] MES fw version: 0x00000078\n"
SAMPLE_DMESG_MES_FIXED = "[    1.234567] amdgpu: [drm] MES fw version: 0x0000007f\n"
SAMPLE_DMESG_MES_NEWER = "[    1.234567] amdgpu: [drm] MES fw version: 0x00000085\n"

SAMPLE_DMESG_BT_OLD = "Bluetooth: hci0: BT_RAM_CODE_MT7925_1_1_hdr.bin build: 20250305102030\n"
SAMPLE_DMESG_BT_FIXED = "Bluetooth: hci0: BT_RAM_CODE_MT7925_1_1_hdr.bin build: 20250601090000\n"
SAMPLE_DMESG_BT_OLDER = "Bluetooth: hci0: BT_RAM_CODE_MT7925_1_1_hdr.bin build: 20241215080000\n"

SAMPLE_UNAME_STEAMOS_395 = "7.2.7-valve2-2-neptune-72-g31504582a073\n"
SAMPLE_UNAME_KERNEL_618 = "6.18.50-valve1-1-neptune\n"
SAMPLE_UNAME_KERNEL_730 = "7.3.0-valve1-1-neptune\n"
SAMPLE_UNAME_KERNEL_800 = "8.0.1-arch1-1\n"

SAMPLE_MODULES_WITHOUT_SMU = "amdgpu 12345 1 - Live 0x0\nsnd_hda_intel 1200 0 - Live 0x0\n"
SAMPLE_MODULES_WITH_SMU = "amdgpu 12345 1 - Live 0x0\nryzen_smu 54321 0 - Live 0x0\n"
SAMPLE_MODULES_WITH_SMU_DRV = "ryzen_smu_drv 65432 0 - Live 0x0\namdgpu 12345 1 - Live 0x0\n"


# 1. MES firmware version probe (>= 0x7f)

def test_mes_firmware_needed_below_0x7f():
    r = probe_mes_firmware(SAMPLE_DMESG_MES_OLD)
    assert r == ProbeResult("mes_fw", False, "needed", "MES FW 0x78 < 0x7f: cwsr_enable=0 workaround required")


def test_mes_firmware_fixed_at_0x7f():
    r = probe_mes_firmware(SAMPLE_DMESG_MES_FIXED)
    assert r == ProbeResult("mes_fw", True, "fixed", "MES FW 0x7f >= 0x7f: cwsr_enable=0 can be retired")


def test_mes_firmware_fixed_above_0x7f():
    r = probe_mes_firmware(SAMPLE_DMESG_MES_NEWER)
    assert r == ProbeResult("mes_fw", True, "fixed", "MES FW 0x85 >= 0x7f: cwsr_enable=0 can be retired")


@pytest.mark.parametrize("bad_input", ["", None, "   \n", "amdgpu: initialized successfully\n"])
def test_mes_firmware_unreadable(bad_input):
    r = probe_mes_firmware(bad_input)
    assert r == ProbeResult("mes_fw", False, "unreadable", "MES firmware version not found in log")


# 2. MT7925 Bluetooth firmware build date probe (> 20250305)

def test_mt7925_bt_needed_equal_to_threshold():
    r = probe_mt7925_bt(SAMPLE_DMESG_BT_OLD)
    assert r == ProbeResult("mt7925_bt", False, "needed", "MT7925 BT FW build 20250305 <= 20250305: BT workarounds required")


def test_mt7925_bt_needed_older_than_threshold():
    r = probe_mt7925_bt(SAMPLE_DMESG_BT_OLDER)
    assert r == ProbeResult("mt7925_bt", False, "needed", "MT7925 BT FW build 20241215 <= 20250305: BT workarounds required")


def test_mt7925_bt_fixed_newer_than_threshold():
    r = probe_mt7925_bt(SAMPLE_DMESG_BT_FIXED)
    assert r == ProbeResult("mt7925_bt", True, "fixed", "MT7925 BT FW build 20250601 > 20250305: BT workarounds can be retired")


@pytest.mark.parametrize("bad_input", ["", None, "   \n", "Bluetooth: hci0: Device setup done\n"])
def test_mt7925_bt_unreadable(bad_input):
    r = probe_mt7925_bt(bad_input)
    assert r == ProbeResult("mt7925_bt", False, "unreadable", "MT7925 BT firmware build date not found in log")


# 3. Kernel version probe (>= 7.3.0 floor)

def test_kernel_version_needed_steamos_395_kernel_72():
    r = probe_kernel_version(SAMPLE_UNAME_STEAMOS_395)
    assert r == ProbeResult("kernel_version", False, "needed", "Kernel 7.2.7 < 7.3.0: dcdebugmask workaround required")


def test_kernel_version_needed_kernel_618():
    r = probe_kernel_version(SAMPLE_UNAME_KERNEL_618)
    assert r == ProbeResult("kernel_version", False, "needed", "Kernel 6.18.50 < 7.3.0: dcdebugmask workaround required")


def test_kernel_version_fixed_at_730():
    r = probe_kernel_version(SAMPLE_UNAME_KERNEL_730)
    assert r == ProbeResult("kernel_version", True, "fixed", "Kernel 7.3.0 >= 7.3.0: dcdebugmask=0x20e10 can be retired")


def test_kernel_version_fixed_above_730():
    r = probe_kernel_version(SAMPLE_UNAME_KERNEL_800)
    assert r == ProbeResult("kernel_version", True, "fixed", "Kernel 8.0.1 >= 7.3.0: dcdebugmask=0x20e10 can be retired")


@pytest.mark.parametrize("bad_input", ["", None, "   \n", "custom-kernel-no-numbers\n"])
def test_kernel_version_unreadable(bad_input):
    r = probe_kernel_version(bad_input)
    assert r.probe_id == "kernel_version" and not r.fixed and r.finding == "unreadable"


# 4. ryzen_smu kernel module probe

def test_ryzen_smu_needed_when_absent():
    r = probe_ryzen_smu(SAMPLE_MODULES_WITHOUT_SMU)
    assert r == ProbeResult("ryzen_smu", False, "needed", "ryzen_smu absent: APU undervolt remains disabled on stock SteamOS")


def test_ryzen_smu_fixed_when_loaded():
    r = probe_ryzen_smu(SAMPLE_MODULES_WITH_SMU)
    assert r == ProbeResult("ryzen_smu", True, "fixed", "ryzen_smu loaded (ryzen_smu): APU undervolt support enabled")


def test_ryzen_smu_fixed_when_drv_variant_loaded():
    r = probe_ryzen_smu(SAMPLE_MODULES_WITH_SMU_DRV)
    assert r == ProbeResult("ryzen_smu", True, "fixed", "ryzen_smu loaded (ryzen_smu_drv): APU undervolt support enabled")


@pytest.mark.parametrize("bad_input", ["", None, "   \n"])
def test_ryzen_smu_unreadable(bad_input):
    r = probe_ryzen_smu(bad_input)
    assert r == ProbeResult("ryzen_smu", False, "unreadable", "/proc/modules text is unreadable or empty")


# 5. asus-nb-wmi write hang probe (5000 ms threshold)

def test_wmi_write_hang_fixed_fast_write_and_verified_readback():
    r = probe_wmi_write_hang(elapsed_ms=45.0, write_rc=0, read_val="25\n", expected_w=25)
    assert r == ProbeResult("wmi_hang", True, "fixed", "asus-nb-wmi write responsive: 45 ms (< 5000 ms) with verified 25 W read-back")


def test_wmi_write_hang_needed_on_timeout_delay():
    r = probe_wmi_write_hang(elapsed_ms=60000.0, write_rc=0, read_val="25\n", expected_w=25)
    assert r == ProbeResult("wmi_hang", False, "needed", "asus-nb-wmi write hang detected: took 60000 ms (>= 5000 ms threshold)")


def test_wmi_write_hang_needed_at_threshold():
    r = probe_wmi_write_hang(elapsed_ms=5000, write_rc=0, read_val="25", expected_w=25)
    assert r == ProbeResult("wmi_hang", False, "needed", "asus-nb-wmi write hang detected: took 5000 ms (>= 5000 ms threshold)")


def test_wmi_write_hang_needed_on_write_error():
    r = probe_wmi_write_hang(elapsed_ms=120, write_rc=5, read_val=None, expected_w=25)
    assert r == ProbeResult("wmi_hang", False, "needed", "asus-nb-wmi write failed with rc=5 (EIO or error)")


def test_wmi_write_hang_needed_on_readback_mismatch():
    r = probe_wmi_write_hang(elapsed_ms=60, write_rc=0, read_val="15\n", expected_w=25)
    assert r == ProbeResult("wmi_hang", False, "needed", "asus-nb-wmi read-back mismatch: expected 25 W, read 15 W")


@pytest.mark.parametrize("args", [
    (40, 0, "not-a-number", 25),
    (40, 0, None, 25),
    (-10, 0, "25", 25),
    ("bad", 0, "25", 25),
])
def test_wmi_write_hang_unreadable(args):
    r = probe_wmi_write_hang(elapsed_ms=args[0], write_rc=args[1], read_val=args[2], expected_w=args[3])
    assert r.probe_id == "wmi_hang" and not r.fixed and r.finding == "unreadable"


# 6. evaluate_upstream_probes aggregator and Recipe registration

def test_evaluate_upstream_probes_returns_all_five_in_order():
    results = evaluate_upstream_probes(
        mes_text=SAMPLE_DMESG_MES_FIXED,
        bt_text=SAMPLE_DMESG_BT_FIXED,
        uname_r=SAMPLE_UNAME_KERNEL_730,
        modules_text=SAMPLE_MODULES_WITH_SMU,
        wmi_timing=(45.0, 0, "25", 25),
    )
    assert len(results) == 5
    assert [x.probe_id for x in results] == ["mes_fw", "mt7925_bt", "kernel_version", "ryzen_smu", "wmi_hang"]
    assert all(x.fixed for x in results)


def test_upstream_probes_recipe_structure():
    recipe = upstream_probes_recipe()
    assert recipe.id == "upstream-probes"
    assert recipe.title == "Has upstream fixed it? (info only)"
    assert recipe.fixes == "knowing when a workaround can be retired"
    assert recipe.check() == "info"
    assert recipe.apply() is None
    assert (recipe.sudo, recipe.reboot, recipe.pacman, recipe.network, recipe.default_on) == (False, False, False, False, False)

"""sensor_reader.SensorReader against a fake /sys/class/hwmon tree."""

import pytest

import sensor_reader
from conftest import curve_files, make_hwmon
from sensor_reader import SensorReader

CURVE = [(40, 50), (50, 80), (60, 120), (70, 160), (75, 200), (80, 230), (85, 255), (90, 255)]


def make_amdgpu(root, index, files=None):
    path = make_hwmon(root, index, "amdgpu", files)
    device = path / "device"
    device.mkdir()
    (device / "vendor").write_text("0x1002\n")
    (device / "device").write_text("0x1586\n")
    (device / "class").write_text("0x038000\n")
    return path


@pytest.fixture
def hwmon(tmp_path, monkeypatch):
    root = tmp_path / "hwmon"
    root.mkdir()
    monkeypatch.setattr(sensor_reader, "HWMON_ROOT", str(root))
    return root


@pytest.fixture
def machine(hwmon):
    """A plausible Z13: indices deliberately not the ones in the docstring."""
    make_hwmon(hwmon, 0, "AC")                       # unrelated device
    make_hwmon(hwmon, 1, "acpitz", {"temp1_input": 45500})
    make_hwmon(hwmon, 3, "asus", {"fan1_input": 8700, "fan2_input": 8600})
    make_hwmon(hwmon, 7, "asus_custom_fan_curve", curve_files(CURVE))
    make_amdgpu(hwmon, 12, {"temp1_input": 51000})
    return hwmon


def test_discovery_by_name(machine):
    s = SensorReader()
    assert s.get_fan_speeds() == (8700, 8600)
    assert s.get_apu_temp() == 45.5
    assert s.get_gpu_temp() == 51.0
    assert s._find_hwmon("asus").endswith("hwmon3")
    assert s._find_hwmon("asus_custom_fan_curve").endswith("hwmon7")


def test_name_must_match_exactly(hwmon):
    make_hwmon(hwmon, 0, "asus_custom_fan_curve", curve_files(CURVE))
    s = SensorReader()
    assert s.get_fan_speeds() == (None, None)       # "asus" != "asus_custom_fan_curve"


def test_dirs_without_name_are_skipped(hwmon):
    (hwmon / "hwmon0").mkdir()
    make_hwmon(hwmon, 1, "asus", {"fan1_input": 100, "fan2_input": 200})
    assert SensorReader().get_fan_speeds() == (100, 200)


def test_missing_devices_return_none(hwmon):
    s = SensorReader()
    assert s.get_fan_speeds() == (None, None)
    assert s.get_apu_temp() is None
    assert s.get_gpu_temp() is None
    assert s.custom_fan_curve_enabled() is None
    assert s.custom_fan_curve_points() is None
    assert s.expected_fan_pwm(60) is None


def test_missing_root_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(sensor_reader, "HWMON_ROOT", str(tmp_path / "nope"))
    assert SensorReader().get_fan_speeds() == (None, None)


def test_unreadable_values_are_none(hwmon):
    make_hwmon(hwmon, 0, "asus", {"fan1_input": "garbage"})
    assert SensorReader().get_fan_speeds() == (None, None)


def test_cache_rediscovers_when_index_shifts(machine):
    s = SensorReader()
    assert s.get_fan_speeds() == (8700, 8600)
    (machine / "hwmon3").rename(machine / "hwmon20")
    assert s.get_fan_speeds() == (8700, 8600)
    assert s._find_hwmon("asus").endswith("hwmon20")


# ----------------------------------------------------------------------
# custom curve
# ----------------------------------------------------------------------

@pytest.mark.parametrize("enable,expected", [((1, 1), True), ((2, 1), False), ((1, 2), False),
                                             ((0, 0), False), ((2, 2), False)])
def test_custom_fan_curve_enabled(hwmon, enable, expected):
    make_hwmon(hwmon, 5, "asus_custom_fan_curve", curve_files(CURVE, enable=enable))
    assert SensorReader().custom_fan_curve_enabled() is expected


def test_custom_fan_curve_enabled_unreadable(hwmon):
    files = curve_files(CURVE)
    del files["pwm2_enable"]
    make_hwmon(hwmon, 5, "asus_custom_fan_curve", files)
    assert SensorReader().custom_fan_curve_enabled() is None


def test_custom_fan_curve_points(machine):
    assert SensorReader().custom_fan_curve_points() == [list(p) for p in CURVE]


def test_custom_fan_curve_points_incomplete(hwmon):
    files = curve_files(CURVE)
    del files["pwm1_auto_point8_pwm"]
    make_hwmon(hwmon, 5, "asus_custom_fan_curve", files)
    assert SensorReader().custom_fan_curve_points() is None


def test_custom_fan_curve_points_reads_fan1(hwmon):
    files = curve_files(CURVE)
    files["pwm2_auto_point1_pwm"] = 0                # fan 2 differs; fan 1 is the reference
    make_hwmon(hwmon, 5, "asus_custom_fan_curve", files)
    assert SensorReader().custom_fan_curve_points()[0] == [40, 50]


@pytest.mark.parametrize("temp,pwm", [
    (-10, 0), (39.9, 0),          # below the first point: firmware idles the fans
    (40, 50), (40.0, 50),         # exactly on a point
    (49.99, 50),                  # step curve: no interpolation up
    (50, 80), (59, 80),
    (77.5, 200),
    (90, 255), (150, 255),        # past the last point: last value
])
def test_expected_fan_pwm_steps(machine, temp, pwm):
    assert SensorReader().expected_fan_pwm(temp) == pwm


def test_expected_fan_pwm_max_fans_curve(hwmon):
    pts = [(t, 255) for t in (20, 30, 40, 50, 60, 70, 80, 90)]
    make_hwmon(hwmon, 5, "asus_custom_fan_curve", curve_files(pts))
    s = SensorReader()
    assert s.expected_fan_pwm(19) == 0
    assert s.expected_fan_pwm(20) == 255
    assert s.expected_fan_pwm(36 - 3) == 255


# --- labeled CPU / GPU temperatures (#25) ---

def test_cpu_temp_falls_back_to_labeled_acpi(machine):
    assert SensorReader().get_cpu_temp() == (45.5, "ACPI")


def test_cpu_temp_prefers_k10temp_tdie_over_tctl(machine):
    make_hwmon(machine, 4, "k10temp", {"temp1_input": 71000, "temp1_label": "Tctl",
                                       "temp2_input": 62000, "temp2_label": "Tdie"})
    assert SensorReader().get_cpu_temp() == (62.0, "k10temp Tdie")


def test_cpu_temp_uses_tctl_when_no_tdie(machine):
    make_hwmon(machine, 4, "k10temp", {"temp1_input": 71000, "temp1_label": "Tctl"})
    assert SensorReader().get_cpu_temp() == (71.0, "k10temp Tctl")


def test_cpu_temp_ignores_unlabeled_k10temp(machine):
    make_hwmon(machine, 4, "k10temp", {"temp1_input": 99000})
    assert SensorReader().get_cpu_temp() == (45.5, "ACPI")


def test_cpu_temp_unavailable_is_none_not_zero(hwmon):
    make_hwmon(hwmon, 1, "acpitz", {})                 # node present, value unreadable
    assert SensorReader().get_cpu_temp() is None
    assert SensorReader().get_apu_temp() is None


def test_gpu_temp_labeled_names_the_sensor(machine):
    assert SensorReader().get_gpu_temp_labeled() == (51.0, "amdgpu edge")
    make_hwmon(machine, 12, "amdgpu", {"temp1_input": 52000, "temp1_label": "junction"})
    assert SensorReader().get_gpu_temp_labeled() == (52.0, "amdgpu junction")


def test_gpu_unreadable_is_none(hwmon):
    make_amdgpu(hwmon, 2)
    assert SensorReader().get_gpu_temp_labeled() is None
    assert SensorReader().get_gpu_temp() is None


@pytest.mark.parametrize("apu_index,egpu_index", [(5, 1), (1, 5)])
def test_gpu_picks_strix_halo_without_boot_vga(hwmon, tmp_path, apu_index, egpu_index):
    # USB4 eGPU has the lower PCI address; hwmon order must not matter either.
    for index, addr, device_id, temp in (
        (egpu_index, "0000:03:00.0", "0x73bf", 80000),
        (apu_index, "0000:c4:00.0", "0x1586", 44000),
    ):
        path = make_amdgpu(hwmon, index, {"temp1_input": temp})
        device = path / "device"
        (device / "device").write_text(device_id + "\n")
        device.rename(tmp_path / addr)
        device.symlink_to(tmp_path / addr)
        assert not (device / "boot_vga").exists()
    assert SensorReader().get_gpu_temp_labeled() == (44.0, "amdgpu edge")


@pytest.mark.parametrize("vendor,device_id", [
    ("0x1002", "0x73bf"), ("0x1234", "0x1586"), (None, None),
])
def test_gpu_unidentified_is_unavailable(hwmon, vendor, device_id):
    path = make_hwmon(hwmon, 1, "amdgpu", {"temp1_input": 80000})
    device = path / "device"
    device.mkdir()
    for name, value in (("vendor", vendor), ("device", device_id)):
        if value is not None:
            (device / name).write_text(value + "\n")
    (device / "boot_vga").write_text("1\n")
    assert SensorReader().get_gpu_temp() is None

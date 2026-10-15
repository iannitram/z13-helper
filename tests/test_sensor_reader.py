"""sensor_reader.SensorReader against a fake /sys/class/hwmon tree."""

import pytest

import sensor_reader
from conftest import curve_files, make_hwmon
from sensor_reader import SensorReader

CURVE = [(40, 50), (50, 80), (60, 120), (70, 160), (75, 200), (80, 230), (85, 255), (90, 255)]


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
    make_hwmon(hwmon, 12, "amdgpu", {"temp1_input": 51000})
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

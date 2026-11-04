"""Platform support and capability discovery (#22)."""

import platform_support as ps


def _w(root, rel, text=""):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)


def _z13(root, **drop):
    _w(root, "sys/class/dmi/id/product_name", "ROG Flow Z13 GZ302EA_GZ302EA\n")
    _w(root, "sys/firmware/acpi/platform_profile", "balanced")
    _w(root, "sys/class/power_supply/BAT0/charge_control_end_threshold", "80")
    _w(root, "sys/class/leds/asus::kbd_backlight/brightness", "1")
    _w(root, "sys/class/hwmon/hwmon7/name", "asus_custom_fan_curve")
    (root / "sys/class/firmware-attributes/asus-armoury/attributes").mkdir(parents=True)


def test_z13_reports_capabilities_without_undervolt(tmp_path):
    _z13(tmp_path)
    r = ps.detect(str(tmp_path), system="linux")
    assert r["supported"] and r["reason"] == ""
    caps = r["capabilities"]
    assert all(caps[k]["available"] for k in
               ("platform_profile", "battery_limit", "keyboard_backlight", "fan_curve", "tdp"))
    assert not caps["undervolt"]["available"] and "ryzen_smu" in caps["undervolt"]["reason"]


def test_missing_node_disables_only_that_control(tmp_path):
    _z13(tmp_path)
    (tmp_path / "sys/class/power_supply/BAT0/charge_control_end_threshold").unlink()
    caps = ps.detect(str(tmp_path), system="linux")["capabilities"]
    assert not caps["battery_limit"]["available"] and caps["battery_limit"]["reason"]
    assert caps["platform_profile"]["available"]


def test_other_device_is_unsupported_and_all_controls_off(tmp_path):
    _z13(tmp_path)
    _w(tmp_path, "sys/class/dmi/id/product_name", "Steam Deck\n")
    r = ps.detect(str(tmp_path), system="linux")
    assert not r["supported"] and "GZ302EA" in r["reason"] and "Steam Deck" in r["reason"]
    assert not any(c["available"] for c in r["capabilities"].values())
    assert all(c["reason"] == r["reason"] for c in r["capabilities"].values())


def test_non_linux_is_unsupported(tmp_path):
    _z13(tmp_path)
    r = ps.detect(str(tmp_path), system="darwin")
    assert not r["supported"] and "Linux" in r["reason"]


def test_empty_tree_does_not_raise(tmp_path):
    r = ps.detect(str(tmp_path), system="linux")
    assert not r["supported"] and "not identified" in r["reason"]

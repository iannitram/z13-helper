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


def test_capability_object_has_discovery_fields(tmp_path):
    _z13(tmp_path)
    _w(tmp_path, "sys/class/power_supply/BAT0/type", "Battery")
    _w(tmp_path, "sys/class/power_supply/AC0/type", "Mains")
    _w(tmp_path, "sys/class/power_supply/AC0/online", "1")
    _w(tmp_path, "sys/firmware/acpi/platform_profile_choices", "low-power balanced performance")
    _w(tmp_path, "sys/class/hwmon/hwmon7/fan1_input", "0")
    _w(tmp_path, "sys/class/hwmon/hwmon7/fan2_input", "0")
    a = "sys/class/firmware-attributes/asus-armoury/attributes/ppt_pl1_spl/"
    _w(tmp_path, a + "min_value", "5")
    _w(tmp_path, a + "max_value", "120")
    _w(tmp_path, a + "default_value", "70")
    _w(tmp_path, a + "current_value", "99")
    r = ps.detect(str(tmp_path), system="linux")
    assert r["battery_present"] and r["power_source"] == "ac"
    assert r["profile_choices"] == ["low-power", "balanced", "performance"]
    assert r["fan_count"] == 2 and "asus_custom_fan_curve" in r["sensors"]
    assert r["rails"]["ppt_pl1_spl"] == {"min": "5", "max": "120", "default": "70"}
    assert "tdp" in r["operations"] and "undervolt" not in r["operations"]
    assert "undervolt" in r["unsupported"]
    assert r["backend_version"] == ps.UNKNOWN
    assert r["session"]["runtime_dir"].startswith("/run/user/")


def test_unknowns_are_explicit_on_unsupported_machine(tmp_path):
    r = ps.detect(str(tmp_path), system="linux")
    assert r["fan_count"] == ps.UNKNOWN and r["power_source"] == ps.UNKNOWN
    assert r["operations"] == [] and set(r["unsupported"]) == set(ps.CAPABILITIES)


def test_batteryless_is_distinct_from_missing_threshold(tmp_path):
    _z13(tmp_path)
    (tmp_path / "sys/class/power_supply/BAT0/charge_control_end_threshold").unlink()
    r = ps.detect(str(tmp_path), system="linux")
    assert r["battery_present"] and "charge limit" in r["capabilities"]["battery_limit"]["reason"]
    import shutil
    shutil.rmtree(tmp_path / "sys/class/power_supply")
    r = ps.detect(str(tmp_path), system="linux")
    assert not r["battery_present"]
    assert r["capabilities"]["battery_limit"] == {"available": False, "reason": "no battery present"}


def test_session_resolves_actual_user_not_deck(tmp_path):
    (tmp_path / "run/user/1234").mkdir(parents=True)
    s = ps.session(environ={}, euid=0, root=str(tmp_path))
    assert s["uid"] == 1234 and s["runtime_dir"] == "/run/user/1234"
    assert s["bus"] == "unix:path=/run/user/1234/bus"
    assert ps.session(environ={"SUDO_UID": "4321"}, euid=0, root=str(tmp_path))["uid"] == 4321
    assert ps.session(environ={}, euid=0, root=str(tmp_path / "none"))["uid"] == 1000
    assert ps.session(environ={}, euid=2000)["runtime_dir"] == "/run/user/2000"


def test_host_uses_resolved_session(monkeypatch):
    import host
    sess = {"user": "alice", "uid": 1234, "home": "/home/alice",
            "runtime_dir": "/run/user/1234", "bus": "unix:path=/run/user/1234/bus"}
    monkeypatch.setattr(ps, "session", lambda *a, **k: sess)
    argv, env = host.command(["x"], as_deck=True, mode="root", environ={})
    assert argv[:4] == ["runuser", "-u", "alice", "--"]
    assert "HOME=/home/alice" in argv and "XDG_RUNTIME_DIR=/run/user/1234" in argv
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1234"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1234/bus"


def test_host_falls_back_to_deck_for_uid_1000(monkeypatch):
    import host
    monkeypatch.setattr(ps, "session", lambda *a, **k: ps._uid_session(1000))
    monkeypatch.setattr(ps.pwd, "getpwuid", lambda uid: (_ for _ in ()).throw(KeyError(uid)))
    argv, env = host.command(["x"], as_deck=True, mode="root", environ={})
    assert argv[:4] == ["runuser", "-u", "deck", "--"] and "HOME=/home/deck" in argv
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"


# --- backend_version: read-only, time-bounded z13ctl probe (#22) ---

def test_backend_version_is_parsed_from_the_probe():
    seen = []

    def run(argv, timeout):
        seen.append((argv, timeout))
        return 0, "z13ctl 1.4.2\n"

    assert ps.backend_version(run) == "1.4.2"
    argv, timeout = seen[0]
    assert argv[1:] == ["--version"] and argv[0].endswith("z13ctl")
    assert 0 < timeout <= 5


def test_backend_version_failures_are_unknown_and_never_raise():
    assert ps.backend_version(lambda a, t: (127, "")) == "unknown"
    assert ps.backend_version(lambda a, t: (124, "")) == "unknown"
    assert ps.backend_version(lambda a, t: (0, "no number here")) == "unknown"

    def boom(a, t):
        raise OSError("gone")

    assert ps.backend_version(boom) == "unknown"


def test_detect_fills_backend_version_only_when_supported(tmp_path):
    _z13(tmp_path)
    r = ps.detect(str(tmp_path), system="linux", version_runner=lambda a, t: (0, "1.4.2"))
    assert r["backend_version"] == "1.4.2"
    r = ps.detect(str(tmp_path), system="darwin", version_runner=lambda a, t: (0, "1.4.2"))
    assert r["backend_version"] == "unknown"


def test_detect_on_a_fake_tree_never_runs_the_probe(tmp_path, monkeypatch):
    _z13(tmp_path)

    def trap(*a, **k):
        raise AssertionError("probe ran for a test tree")

    monkeypatch.setattr(ps, "backend_version", trap)
    assert ps.detect(str(tmp_path), system="linux")["backend_version"] == "unknown"


def test_default_probe_goes_through_host_run_with_a_timeout(monkeypatch):
    import host
    seen = []

    def fake(argv, timeout=None, **k):
        seen.append((argv, timeout))
        return host.Result(0, "z13ctl version 1.5.0", False)

    monkeypatch.setattr(host, "run", fake)
    ps.reset_version_cache()
    assert ps.backend_version() == "1.5.0"
    assert ps.backend_version() == "1.5.0"      # cached: no second spawn
    assert len(seen) == 1 and seen[0][1] == ps.VERSION_TIMEOUT_S

"""screen_brightness: percent mapping, backlight discovery, the sysfs fallback."""

import threading

import screen_brightness as sb


def test_to_percent_and_back():
    assert sb.to_percent(62451, 62451) == 100
    assert sb.to_percent(0, 62451) == 0
    assert sb.to_percent(5000, 10000) == 50
    assert sb.to_percent(5, 0) == 0
    assert sb.to_raw(50, 10000) == 5000
    assert sb.to_raw(100, 62451) == 62451


def test_to_raw_never_blacks_out():
    assert sb.to_raw(0, 62451) == sb.to_raw(sb.MIN_PERCENT, 62451) > 0
    assert sb.to_raw(-20, 10000) > 0
    assert sb.to_raw(1, 50) == 1       # rounds to 0 without the floor
    assert sb.to_raw(250, 10000) == 10000


def _backlight(root, name, cur, mx):
    d = root / name
    d.mkdir(parents=True)
    (d / "brightness").write_text(f"{cur}\n")
    (d / "max_brightness").write_text(f"{mx}\n")
    return d


def test_find_backlight_prefers_panel_driver(tmp_path):
    assert sb.find_backlight(str(tmp_path / "missing")) is None
    _backlight(tmp_path, "acpi_video0", 5, 10)
    amd = _backlight(tmp_path, "amdgpu_bl0", 100, 200)
    assert sb.find_backlight(str(tmp_path)) == str(amd)


def test_sysfs_fallback_reads_and_writes_latest(tmp_path, fake_host, monkeypatch):
    bl = _backlight(tmp_path, "amdgpu_bl0", 31225, 62451)
    done = threading.Event()
    run = fake_host.run
    monkeypatch.setattr(sb.host, "run", lambda *a, **kw: (run(*a, **kw), done.set())[0])

    s = sb.ScreenBrightness(bus=None, backlight_root=str(tmp_path))
    assert s.available() and s.backend.name == "sysfs"
    assert s.get() == 50
    s.set(0)                                   # clamped to MIN_PERCENT, not 0
    assert done.wait(2)
    call = fake_host.calls[-1]
    assert call.argv == ["tee", str(bl / "brightness")]
    assert call.input == f"{sb.to_raw(sb.MIN_PERCENT, 62451)}\n"


def test_no_backlight_at_all(tmp_path):
    s = sb.ScreenBrightness(bus=None, backlight_root=str(tmp_path))
    assert not s.available()
    assert s.get() is None
    try:
        s.set(50)
    except RuntimeError:
        pass
    else:
        raise AssertionError("set() without a backend should raise")

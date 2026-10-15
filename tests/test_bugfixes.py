"""Regression tests for the 2026-10-12 bug pass (one per fix; see the PR body)."""

import hashlib
import json
import re
import threading
from pathlib import Path

import pytest

import profiles

REPO = Path(__file__).resolve().parent.parent


# ---- profiles.delete_profile: legacy current-profile follows the fallback ----

def test_delete_active_updates_legacy_current_profile(profiles_env):
    profiles.upsert_profile({"name": "Mine", "base": "quiet", "tdp": None, "fan_curve": None,
                             "undervolt": None, "brightness": None, "battery_limit": None,
                             "hooks": {"enter": "", "exit": ""}})
    profiles.set_active("Turbo")
    assert profiles_env.legacy.read_text().strip() == "maximum-throughput"
    profiles.set_active("Mine")
    profiles.delete_profile("Mine")
    assert profiles.active_name() == "Balanced"
    assert profiles_env.legacy.read_text().strip() == "balanced-daily-driver"


def test_delete_inactive_leaves_legacy_alone(profiles_env):
    profiles.upsert_profile({"name": "Mine", "base": "quiet", "tdp": None, "fan_curve": None,
                             "undervolt": None, "brightness": None, "battery_limit": None,
                             "hooks": {"enter": "", "exit": ""}})
    profiles.set_active("Silent")
    profiles.delete_profile("Mine")
    assert profiles_env.legacy.read_text().strip() == "longevity-cool-stable"


# ---- profiles.validate: bad shapes raise ValueError (the UI catches only that) ----

GOOD = {"name": "X", "base": "balanced"}


@pytest.mark.parametrize("bad", [
    {"name": 5, "base": "balanced"},
    {"name": None, "base": "balanced"},
    {"name": ["a"], "base": "balanced"},
    dict(GOOD, tdp=[20, 30, 40]),
    dict(GOOD, tdp="20"),
    dict(GOOD, fan_curve="40:50%"),
    dict(GOOD, fan_curve=[[40, 50]] * 7 + [[90]]),
    dict(GOOD, fan_curve=[[40, 50]] * 7 + [None]),
    dict(GOOD, fan_curve=[[40, 50]] * 7 + [["hot", 50]]),
    dict(GOOD, fan_curve=[[40, 50, 1]] * 8),
    "not a dict",
])
def test_validate_raises_valueerror(bad):
    with pytest.raises(ValueError):
        profiles.validate(bad)


def test_validate_still_accepts_defaults():
    for p in profiles.default_profiles():
        profiles.validate(p)


# ---- fan watchdog: a sensor dropout resets the stall counter ----

def test_watchdog_dropout_resets_stall_counter(clock):
    import fan_watchdog
    from test_fan_watchdog import FakeController, FakeSensors

    s, c = FakeSensors(fans=(0, 0), temp=60.0), FakeController()
    wd = fan_watchdog.FanWatchdog(s, c, max_fans_on=lambda: False, clock=clock)
    for _ in range(fan_watchdog.STALL_TICKS - 1):
        clock.advance(1.5)
        assert wd.tick() is None
    s.fans = (None, None)                     # hwmon node gone for a tick
    clock.advance(1.5)
    assert wd.tick() is None
    s.fans = (0, 0)
    clock.advance(1.5)
    assert wd.tick() is None                  # chain broken: not a stall yet
    assert c.calls == []
    for _ in range(fan_watchdog.STALL_TICKS - 1):
        clock.advance(1.5)
        msg = wd.tick()
    assert msg and c.calls[0][0] == "rewrite_profile"


def test_decky_runs_the_shared_watchdog_not_a_copy():
    src = (REPO / "decky" / "main.py").read_text()
    assert re.search(r"from fan_watchdog import .*\bFanWatchdog\b", src)
    for copied in ("STALL_TICKS", "FROZEN_S", "STALL_MIN_PWM", "def _expected_pwm"):
        assert copied not in src, copied


# ---- installers._fetch_release: temp dir removed when the version check fails ----

def test_fetch_release_cleans_tmp_on_version_mismatch(monkeypatch, tmp_path):
    import installers
    from test_installers import TAR, fake_fetch, make_tarball
    monkeypatch.setattr(installers, "CACHE", str(tmp_path / "cache"))
    tar = make_tarball()
    monkeypatch.setattr(installers, "fetch", fake_fetch(tar, f"{hashlib.sha256(tar).hexdigest()}  {TAR}\n"))
    monkeypatch.setattr(installers, "_z13ctl_installed_version", lambda path: "1.3.0")
    extract = tmp_path / "extract"
    extract.mkdir()
    monkeypatch.setattr(installers.tempfile, "mkdtemp", lambda prefix: str(extract))
    with pytest.raises(RuntimeError, match="expected"):
        installers._fetch_release()
    assert not extract.exists()


def test_version_probe_timeout_counts_as_not_runnable(monkeypatch):
    import subprocess
    import installers

    def hang(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 10)
    monkeypatch.setattr(installers.subprocess, "run", hang)
    assert installers._z13ctl_installed_version("/x/z13ctl") is None


# ---- config_manager: atomic, thread-safe writes ----

def test_config_manager_atomic_and_threadsafe(tmp_path):
    from config_manager import ConfigManager
    path = tmp_path / "cfg" / "config.json"
    cm = ConfigManager(str(path))

    def writer(k):
        for i in range(50):
            cm.set(k, i)
    ts = [threading.Thread(target=writer, args=(f"k{n}",)) for n in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    data = json.loads(path.read_text())
    assert data == {"k0": 49, "k1": 49, "k2": 49, "k3": 49}
    assert sorted(p.name for p in path.parent.iterdir()) == ["config.json"]   # no tmp left


# ---- principle 5: no UI code reads TDP back through z13ctl ----

def test_ui_never_reads_tdp_live():
    for f in ("fans_window.py", "dashboard_ui.py", "profile_control.py"):
        assert "get_tdp(" not in (REPO / "src" / f).read_text(), f


# ---- boondock: profile writes through z13ctl, never sysfs / sudo z13ctl ----

def test_boondock_uses_z13ctl_for_device_control():
    src = (REPO / "tooling" / "boondock").read_text()
    assert 'sudo tee "$PROFILE_SYSFS"' not in src
    assert 'sudo "$Z"' not in src


# ---- z13-button-watcher: the game-mode probe can't hang or kill the loop ----

def test_button_watcher_gamemode_probe_has_timeout():
    src = (REPO / "tooling" / "z13-button-watcher").read_text()
    i = src.index("def in_gamemode")
    body = src[i:src.index("def handle_press")]
    assert "timeout=" in body and "except" in body

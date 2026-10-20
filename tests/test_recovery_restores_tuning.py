"""Fan recovery rewrites the firmware profile, which resets undervolt and hands
power limits back to firmware (z13ctl docs, "firmware profiles"). So the
recovery must put the active app profile's TDP and undervolt back, through the
same steps profiles.apply uses, and read them back."""

import pytest

import profiles
from fan_watchdog import FanWatchdog
from hardware_controller import HardwareController
from test_fan_watchdog import FakeSensors

Z = "z13ctl"
CURVE = [[40, 20], [50, 40], [60, 60], [70, 100], [75, 110], [80, 140], [85, 200], [90, 255]]


def store_with(profile):
    store = profiles.load_store()
    store["profiles"].append(profile)
    store["active"] = profile["name"]
    profiles.save_store(store)


def user_profile(**over):
    p = {"name": "Quiet Work", "builtin": False, "base": "quiet",
         "tdp": {"pl1": 18, "pl2": 22, "pl3": 25, "force": False},
         "fan_curve": [list(pt) for pt in CURVE], "undervolt": -12,
         "brightness": None, "battery_limit": None, "hooks": {"enter": "", "exit": ""}}
    p.update(over)
    return p


@pytest.fixture
def ctl(monkeypatch):
    c = HardwareController(z13ctl_path=Z)
    monkeypatch.setattr(c, "get_platform_profile", lambda: "quiet")
    return c


def make_wd(ctl):
    return FanWatchdog(FakeSensors(curve=CURVE), ctl, max_fans_on=lambda: False)


def verbs(fake_host):
    return [a[1] for a in fake_host.argvs if a[1] != "--device"]


def test_recovery_restores_tdp_and_undervolt_after_profile_write(profiles_env, fake_host, ctl):
    store_with(user_profile())
    msg = make_wd(ctl)._recover(False)
    lines = fake_host.lines
    # profile write, then tdp, curve, undervolt (profiles.apply's order)
    assert lines[0] == f"{Z} profile --set quiet"
    assert f"{Z} tdp --set 18 --pl2 22 --pl3 25" in lines
    assert f"{Z} undervolt --set -12" in lines
    writes = [l.split()[1] for l in lines if " --set " in l]
    assert writes == ["profile", "tdp", "fancurve", "undervolt"], writes
    assert "re-applied" in msg


def test_recovery_reads_back_undervolt_but_never_tdp(profiles_env, fake_host, ctl):
    store_with(user_profile())
    make_wd(ctl)._recover(False)
    lines = fake_host.lines
    assert f"{Z} tdp --get" not in lines
    assert lines[-1] == f"{Z} undervolt --get"


def test_failed_tdp_is_reported(profiles_env, fake_host, ctl):
    store_with(user_profile())
    fake_host.fail("tdp --set", out="nope")
    msg = make_wd(ctl)._recover(False)
    assert "TDP not restored" in msg
    assert any(" fancurve --set " in l for l in fake_host.lines)


def test_failed_undervolt_does_not_block_the_curve_and_is_reported(profiles_env, fake_host, ctl):
    store_with(user_profile())
    fake_host.fail("undervolt --set", out="ryzen_smu not loaded")
    msg = make_wd(ctl)._recover(False)
    assert any(" fancurve --set " in l for l in fake_host.lines)
    assert "undervolt" in msg


def test_profile_without_tdp_or_undervolt_adds_no_writes(profiles_env, fake_host, ctl):
    store_with(user_profile(tdp=None, undervolt=None))
    make_wd(ctl)._recover(False)
    assert "tdp" not in verbs(fake_host) and "undervolt" not in verbs(fake_host)

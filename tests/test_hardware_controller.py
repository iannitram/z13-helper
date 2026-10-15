"""hardware_controller.HardwareController: per-device brightness + wedged-lightbar recovery."""

import subprocess

import pytest

import hardware_controller
import profiles
from hardware_controller import HardwareController

WEDGED = "z13ctl command failed: init packet 1: write /dev/hidraw5: connection timed out"
STALE = "z13ctl command failed: init packet 1: write /dev/hidraw5: no such device"


class Fake(HardwareController):
    """_run_command replaced by a scripted fake; records (args, timeout)."""

    def __init__(self, fail):
        super().__init__(z13ctl_path="z13ctl")
        self.calls = []
        self._fail = fail      # fn(args, n_calls_so_far) -> error text or None

    def _run_command(self, args, quiet=False, timeout=120):
        self.calls.append((list(args), timeout))
        err = self._fail(args, self.calls)
        if err:
            raise RuntimeError(err)
        return ""


@pytest.fixture
def recover(monkeypatch):
    seen = []

    def fake(runner=None, **kw):
        seen.append(runner)
        return fake.result
    fake.result = (True, "lightbar reset")
    monkeypatch.setattr(profiles, "recover_lightbar", fake)
    fake.seen = seen
    return fake


@pytest.fixture
def recover_stale(monkeypatch):
    seen = []

    def fake(runner=None, **kw):
        seen.append(runner)
        return fake.result
    fake.result = (True, "daemon restarted")
    monkeypatch.setattr(profiles, "recover_lightbar_stale", fake)
    fake.seen = seen
    return fake


def devs(c):
    return [a[1] for a, _ in c.calls]


def test_keyboard_first_and_short_timeout(recover):
    c = Fake(lambda a, calls: None)
    assert c.set_brightness("low", devices=("lightbar", "keyboard")) == []
    assert devs(c) == ["keyboard", "lightbar"]
    assert all(t == 30 for _, t in c.calls)
    assert recover.seen == []


def test_wedged_lightbar_one_reset_one_retry(recover):
    # Lightbar times out on the first write only.
    c = Fake(lambda a, calls: WEDGED if a[1] == "lightbar" and len(calls) == 2 else None)
    assert c.set_brightness("high") == []
    assert devs(c) == ["keyboard", "lightbar", "lightbar"]
    assert len(recover.seen) == 1


def test_still_wedged_reports_lightbar_failed(recover):
    c = Fake(lambda a, calls: WEDGED if a[1] == "lightbar" else None)
    assert c.set_brightness("high") == ["lightbar"]
    assert devs(c) == ["keyboard", "lightbar", "lightbar"]      # never more than one retry
    assert len(recover.seen) == 1


def test_rate_limited_reset_means_no_retry(recover):
    recover.result = (False, "lightbar reset skipped (last one 10 s ago)")
    c = Fake(lambda a, calls: WEDGED if a[1] == "lightbar" else None)
    assert c.set_brightness("high") == ["lightbar"]
    assert devs(c) == ["keyboard", "lightbar"]


def test_non_timeout_failure_no_reset(recover):
    c = Fake(lambda a, calls: "z13ctl command failed: no device" if a[1] == "lightbar" else None)
    assert c.set_brightness("high") == ["lightbar"]
    assert recover.seen == []


def test_stale_lightbar_one_daemon_restart_one_retry(recover, recover_stale):
    # ENODEV on the first write only.
    c = Fake(lambda a, calls: STALE if a[1] == "lightbar" and len(calls) == 2 else None)
    assert c.set_brightness("high") == []
    assert devs(c) == ["keyboard", "lightbar", "lightbar"]
    assert len(recover_stale.seen) == 1
    assert recover.seen == []            # no USB reset for a stale handle


def test_still_stale_after_daemon_restart_reports_lightbar_failed(recover, recover_stale):
    c = Fake(lambda a, calls: STALE if a[1] == "lightbar" else None)
    assert c.set_brightness("high") == ["lightbar"]
    assert devs(c) == ["keyboard", "lightbar", "lightbar"]      # never more than one retry
    assert len(recover_stale.seen) == 1


def test_stale_daemon_restart_failure_means_no_retry(recover, recover_stale):
    recover_stale.result = (False, "daemon restart failed (Unit z13ctl.service not loaded.)")
    c = Fake(lambda a, calls: STALE if a[1] == "lightbar" else None)
    assert c.set_brightness("high") == ["lightbar"]
    assert devs(c) == ["keyboard", "lightbar"]
    assert recover.seen == []


def test_keyboard_timeout_never_resets(recover):
    c = Fake(lambda a, calls: WEDGED if a[1] == "keyboard" else None)
    assert c.set_brightness("high") == ["keyboard"]
    assert recover.seen == []


def test_all_failed_raises(recover):
    recover.result = (False, "skipped")
    c = Fake(lambda a, calls: WEDGED)
    with pytest.raises(RuntimeError):
        c.set_brightness("off")


def test_run_command_timeout_becomes_runtime_error(monkeypatch):
    def slow(*a, **kw):
        assert kw.get("timeout") == 7
        raise subprocess.TimeoutExpired(a[0], 7)
    monkeypatch.setattr(hardware_controller.subprocess, "run", slow)
    c = HardwareController(z13ctl_path="z13ctl")
    c._flatpak = False
    with pytest.raises(RuntimeError, match="timed out"):
        c._run_command(["status"], quiet=True, timeout=7)

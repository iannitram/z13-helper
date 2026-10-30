"""hardware_controller.HardwareController: per-device brightness + wedged-lightbar recovery."""

import pytest

import profiles
from hardware_controller import HardwareController, Z13CTL_WRITE_TIMEOUT_S

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

    def fake(**kw):
        seen.append(kw)
        return fake.result
    fake.result = (True, "lightbar reset")
    monkeypatch.setattr(profiles, "recover_lightbar", fake)
    fake.seen = seen
    return fake


@pytest.fixture
def recover_stale(monkeypatch):
    seen = []

    def fake(**kw):
        seen.append(kw)
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
    assert all(t == Z13CTL_WRITE_TIMEOUT_S for _, t in c.calls)
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


@pytest.mark.parametrize("device", ["keyboard", "lightbar"])
def test_write_timeout_brightness_stops_and_propagates_exact_message(
        device, recover, recover_stale):
    message = "z13ctl write timed out after 20 s (kernel asus-wmi hang?)"
    c = Fake(lambda a, calls: message if a[1] == device else None)
    with pytest.raises(RuntimeError) as err:
        c.set_brightness("high")
    assert str(err.value) == message
    assert devs(c) == (["keyboard"] if device == "keyboard" else ["keyboard", "lightbar"])
    assert recover.seen == [] and recover_stale.seen == []


def test_all_failed_raises(recover):
    recover.result = (False, "skipped")
    c = Fake(lambda a, calls: WEDGED)
    with pytest.raises(RuntimeError):
        c.set_brightness("off")


def test_run_command_returns_output(fake_host):
    fake_host.fail("--get", rc=0, out="80")
    assert HardwareController(z13ctl_path="z13ctl").get_battery_limit() == 80
    assert fake_host.calls[0].argv == ["z13ctl", "batterylimit", "--get"]


def test_run_command_timeout_becomes_runtime_error(fake_host):
    fake_host.fail("profile --set", rc=124, out="timed out after 20 s", timed_out=True)
    c = HardwareController(z13ctl_path="z13ctl")
    with pytest.raises(RuntimeError, match="^z13ctl write timed out after 20 s \\(kernel asus-wmi hang\\?\\)$"):
        c._run_command(["profile", "--set", "balanced"], quiet=True)
    assert fake_host.calls[0].timeout == Z13CTL_WRITE_TIMEOUT_S


def test_run_command_failure_carries_output(fake_host):
    fake_host.fail("lightbar", out="write /dev/hidraw5: connection timed out")
    with pytest.raises(RuntimeError, match="z13ctl command failed: write /dev/hidraw5"):
        HardwareController(z13ctl_path="z13ctl")._run_command(["--device", "lightbar"], quiet=True)


def test_run_command_missing_binary(fake_host):
    fake_host.fail("z13ctl", rc=127, out="No such file or directory: 'z13ctl'")
    with pytest.raises(RuntimeError, match="not found"):
        HardwareController(z13ctl_path="z13ctl")._run_command(["status"])


def test_apply_tdp_takes_a_profiles_tdp_dict(fake_host):
    c = HardwareController(z13ctl_path="z13ctl")
    c.apply_tdp({"pl1": 20, "pl2": 30, "pl3": 40, "force": False})
    c.apply_tdp({"pl1": 80, "pl2": 85, "pl3": 90, "force": True})
    assert fake_host.argvs == [
        ["z13ctl", "tdp", "--set", "20", "--pl2", "30", "--pl3", "40"],
        ["z13ctl", "tdp", "--set", "80", "--pl2", "85", "--pl3", "90", "--force"],
    ]


@pytest.mark.parametrize("device", ["keyboard", "lightbar"])
@pytest.mark.parametrize("mode", profiles.LIGHTING_MODES)
def test_set_lighting_builds_per_device_argv(device, mode):
    c = Fake(lambda _args, _calls: None)

    c.set_lighting(device, {"mode": mode, "color": "cyan"}, brightness="low")

    assert c.calls == [(["--device", device, "apply", "--mode", mode,
                         "--color", "cyan", "--brightness", "low"],
                        Z13CTL_WRITE_TIMEOUT_S)]

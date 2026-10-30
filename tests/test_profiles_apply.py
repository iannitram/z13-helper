"""profiles.apply(): write order, step independence, hooks, Max Fans, undervolt."""

import json

import pytest

import host
import max_fans_state
import profiles
from fan_watchdog import FanWatchdog
from fake_host import FakeHost

Z = "z13ctl"
CURVE = [[30, 10], [40, 20], [50, 40], [60, 60], [70, 80], [80, 100], [90, 100], [100, 100]]


def full_profile(name="Custom", **over):
    """A profile that exercises every step."""
    p = {
        "name": name, "builtin": False, "base": "performance",
        "tdp": {"pl1": 50, "pl2": 60, "pl3": 70, "force": False},
        "fan_curve": [list(pt) for pt in CURVE],
        "undervolt": -15,
        "brightness": "medium",
        "battery_limit": 80,
        "hooks": {"enter": "enter-cmd", "exit": "exit-cmd-of-custom"},
    }
    p.update(over)
    return p


def bare_profile(name="Bare", base="balanced", **over):
    p = {"name": name, "base": base, "tdp": None, "fan_curve": None, "undervolt": None,
         "brightness": None, "battery_limit": None, "hooks": {"enter": "", "exit": ""}}
    p.update(over)
    return p


def names(results):
    return [s for s, _, _ in results]


EXPECTED_ORDER = ["exit hook", "profile", "tdp", "fans", "undervolt",
                  "brightness (keyboard)", "brightness (lightbar)", "battery limit", "enter hook"]


# ----------------------------------------------------------------------
# order
# ----------------------------------------------------------------------

def test_write_order(profiles_env, fake_host):
    prev = full_profile("Previous", hooks={"enter": "", "exit": "exit-cmd"})
    results = profiles.apply(full_profile(), previous=prev, z13ctl=Z)

    assert names(results) == EXPECTED_ORDER
    assert all(ok for _, ok, _ in results)
    # The fake_host saw the same order, with the right argv for each step.
    assert fake_host.argvs == [
        ["bash", "-lc", "exit-cmd"],
        [Z, "profile", "--set", "performance"],
        [Z, "tdp", "--set", "50", "--pl2", "60", "--pl3", "70"],
        [Z, "fancurve", "--set", profiles.curve_spec(CURVE)],
        [Z, "undervolt", "--set", "-15"],
        [Z, "--device", "keyboard", "brightness", "medium"],
        [Z, "--device", "lightbar", "brightness", "medium"],
        [Z, "batterylimit", "--set", "80"],
        ["bash", "-lc", "enter-cmd"],
    ]


def test_curve_spec_format():
    assert profiles.curve_spec([[40, 50], [50.0, "75"]]) == "40:50%,50:75%"


def test_tdp_force_flag(profiles_env, fake_host):
    p = bare_profile(tdp={"pl1": 80, "pl2": 85, "pl3": 90, "force": True})
    profiles.apply(p, z13ctl=Z)
    tdp = [a for a in fake_host.argvs if a[1] == "tdp"]
    assert tdp == [[Z, "tdp", "--set", "80", "--pl2", "85", "--pl3", "90", "--force"]]


def test_null_fields_are_left_alone(profiles_env, fake_host):
    results = profiles.apply(bare_profile(), z13ctl=Z)
    # Only the platform profile is written; fans report firmware auto without a call.
    assert fake_host.argvs == [[Z, "profile", "--set", "balanced"]]
    assert names(results) == ["profile", "fans"]
    assert results[1] == ("fans", True, "firmware auto")


def test_curve_without_tdp_clears_only_default_custom_tdp_after_base_profile(
        profiles_env, fake_host):
    """A bare z13ctl curve edit activates `custom`, so old custom TDP must be cleared first."""
    fake_host.fail("tdp --help", rc=0, out="Usage: z13ctl tdp [flags]\nFlags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\n"
                       "Custom profiles:\n   custom              fan curve, 20W\n")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert fake_host.argvs == [
        [Z, "profile", "--set", "performance"],
        [Z, "tdp", "--help"],
        [Z, "profile", "--list"],
        [Z, "tdp", "--reset", "--profile", "custom"],
        [Z, "fancurve", "--set", profiles.curve_spec(CURVE)],
    ]
    assert ("tdp", True, "cleared saved z13ctl custom TDP") in results


def test_curve_without_tdp_does_not_clear_custom_tdp_if_base_switch_fails(
        profiles_env, fake_host):
    fake_host.fail("profile --set", rc=1, out="profile switch failed")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--help"] not in fake_host.argvs
    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] not in fake_host.argvs
    assert ("fans", False,
            "saved custom TDP could not be safely cleared; fan curve was not applied") in results


def test_curve_without_tdp_is_skipped_when_custom_tdp_cleanup_fails(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\n"
                       "Custom profiles:\n   custom              fan curve, 20W\n")
    fake_host.fail("tdp --reset --profile custom", rc=1, out="daemon rejected reset")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] not in fake_host.argvs
    assert ("fans", False,
            "saved custom TDP could not be safely cleared; fan curve was not applied") in results


def test_failed_base_switch_restores_initial_max_fans_flag(profiles_env, fake_host):
    fake_host.fail("profile --set", rc=1, out="profile switch failed")
    p = bare_profile(name="Turbo", base="performance", max_fans=True)

    results = profiles.apply(p, z13ctl=Z)

    assert not max_fans_state.is_enabled()
    assert [Z, "fancurve", "--set", profiles.MAX_FANS_SPEC] not in fake_host.argvs
    assert ("fans", False,
            "saved custom TDP could not be safely cleared; fan curve was not applied") in results


def test_failed_cleanup_disables_max_fans_before_watchdog_tick(profiles_env, fake_host):
    max_fans_state.save(enabled=True)
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\n"
                       "Custom profiles:\n   custom              fan curve, 20W\n")
    fake_host.fail("tdp --reset --profile custom", rc=1, out="daemon rejected reset")
    p = bare_profile(name="Turbo", base="performance", max_fans=True)

    results = profiles.apply(p, z13ctl=Z)

    assert not max_fans_state.is_enabled()
    assert ("fans", False,
            "saved custom TDP could not be safely cleared; fan curve was not applied") in results
    assert next(call for call in fake_host.calls if "tdp --reset" in " ".join(call.argv)).timeout == 20

    class Sensors:
        def custom_fan_curve_enabled(self):
            return False  # the successful base-profile write left firmware auto active

        def custom_fan_curve_points(self):
            return None

        def get_apu_temp(self):
            return 40.0

        def get_fan_speeds(self):
            return 0, 0

    class Controller:
        calls = []

        def rewrite_profile(self):
            self.calls.append("rewrite_profile")

        def set_fans_max(self):
            self.calls.append("set_fans_max")

        def set_fancurve_pwm(self, curve):
            self.calls.append(("set_fancurve_pwm", curve))

        def apply_tdp(self, tdp):
            self.calls.append(("apply_tdp", tdp))

        def reset_fancurve(self):
            self.calls.append("reset_fancurve")

    ctl = Controller()
    watchdog = FanWatchdog(Sensors(), ctl, max_fans_on=max_fans_state.is_enabled)

    assert watchdog.tick() is None
    assert ctl.calls == []


def test_leaving_turbo_disables_watchdog_before_base_profile_write(
        profiles_env, fake_host, monkeypatch):
    max_fans_state.save(enabled=True, saved_curve=[[40, 128]])

    class Sensors:
        def custom_fan_curve_enabled(self):
            return False  # the platform-profile write just released the curve

        def custom_fan_curve_points(self):
            return None

        def get_apu_temp(self):
            return 40.0

        def get_fan_speeds(self):
            return 0, 0

    class Controller:
        def __init__(self):
            self.calls = []

        def rewrite_profile(self):
            self.calls.append("rewrite_profile")

        def set_fans_max(self):
            self.calls.append("set_fans_max")

        def set_fancurve_pwm(self, curve):
            self.calls.append(("set_fancurve_pwm", curve))

        def apply_tdp(self, tdp):
            self.calls.append(("apply_tdp", tdp))

        def reset_fancurve(self):
            self.calls.append("reset_fancurve")

    ctl = Controller()
    watchdog = FanWatchdog(Sensors(), ctl, max_fans_on=max_fans_state.is_enabled)
    real_run = host.run

    def tick_during_base_write(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if argv == [Z, "profile", "--set", "balanced"]:
            assert not max_fans_state.is_enabled()
            watchdog.tick()
        return result

    monkeypatch.setattr(host, "run", tick_during_base_write)
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\nCustom profiles:\n")
    defaults = {p["name"]: p for p in profiles.default_profiles()}

    profiles.apply(defaults["Balanced"], previous=defaults["Turbo"], z13ctl=Z)

    assert not max_fans_state.is_enabled()
    assert ctl.calls == []
    assert [Z, "fancurve", "--set", profiles.curve_spec(defaults["Balanced"]["fan_curve"])] \
        in fake_host.argvs


def test_max_fans_reapply_suspends_flag_through_base_and_cleanup(
        profiles_env, fake_host, monkeypatch):
    max_fans_state.save(enabled=True)
    hardware = {"curve_enabled": False}

    class Sensors:
        def custom_fan_curve_enabled(self):
            return hardware["curve_enabled"]

        def custom_fan_curve_points(self):
            return None

        def get_apu_temp(self):
            return 40.0

        def get_fan_speeds(self):
            return 0, 0

    class Controller:
        def __init__(self):
            self.calls = []

        def rewrite_profile(self):
            self.calls.append("rewrite_profile")

        def set_fans_max(self):
            self.calls.append("set_fans_max")

        def set_fancurve_pwm(self, curve):
            self.calls.append(("set_fancurve_pwm", curve))

        def apply_tdp(self, tdp):
            self.calls.append(("apply_tdp", tdp))

        def reset_fancurve(self):
            self.calls.append("reset_fancurve")

    ctl = Controller()
    watchdog = FanWatchdog(Sensors(), ctl, max_fans_on=max_fans_state.is_enabled)
    real_run = host.run

    def tick_during_apply(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if argv[1:3] == ["profile", "--set"] or argv[1:3] in (
                ["tdp", "--help"], ["profile", "--list"]):
            assert not max_fans_state.is_enabled()
            watchdog.tick()
        if argv == [Z, "fancurve", "--set", profiles.MAX_FANS_SPEC]:
            hardware["curve_enabled"] = result[0] == 0
            assert not max_fans_state.is_enabled()
        return result

    monkeypatch.setattr(host, "run", tick_during_apply)
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\nCustom profiles:\n")
    turbo = next(p for p in profiles.default_profiles() if p["name"] == "Turbo")

    profiles.apply(turbo, previous=turbo, z13ctl=Z)

    assert not ctl.calls
    assert max_fans_state.is_enabled()
    assert [Z, "fancurve", "--set", profiles.MAX_FANS_SPEC] in fake_host.argvs


def test_entering_turbo_enables_flag_only_after_successful_curve_write(
        profiles_env, fake_host, monkeypatch):
    real_run = host.run

    def inspect_fan_write(argv, **kwargs):
        result = real_run(argv, **kwargs)
        if argv == [Z, "fancurve", "--set", profiles.MAX_FANS_SPEC]:
            assert not max_fans_state.is_enabled()
        return result

    monkeypatch.setattr(host, "run", inspect_fan_write)
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\nCustom profiles:\n")
    defaults = {p["name"]: p for p in profiles.default_profiles()}

    profiles.apply(defaults["Turbo"], previous=defaults["Balanced"], z13ctl=Z)

    assert max_fans_state.is_enabled()


def test_failed_max_fans_curve_write_does_not_publish_enabled_flag(
        profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\nCustom profiles:\n")
    fake_host.fail("fancurve --set", rc=1, out="curve write failed")
    defaults = {p["name"]: p for p in profiles.default_profiles()}

    results = profiles.apply(defaults["Turbo"], previous=defaults["Balanced"], z13ctl=Z)

    assert not max_fans_state.is_enabled()
    assert ("fans", False, "curve write failed") in results


def test_curveless_max_fans_profile_clears_stale_custom_tdp(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\n"
                       "Custom profiles:\n   custom              20W\n")
    p = bare_profile(name="Turbo", base="performance", max_fans=True)

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.MAX_FANS_SPEC] in fake_host.argvs
    assert ("tdp", True, "cleared saved z13ctl custom TDP") in results


def test_default_custom_without_tdp_is_not_created_or_reset(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\n"
                       "Custom profiles:\n   custom              (empty)\n")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] in fake_host.argvs


def test_empty_custom_profile_list_allows_curve_for_fresh_user(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\nCustom profiles:\n")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] in fake_host.argvs


def test_malformed_profile_list_blocks_curve_write(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0, out="unexpected output")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] not in fake_host.argvs
    assert ("fans", False,
            "saved custom TDP could not be safely cleared; fan curve was not applied") in results


def test_curve_without_tdp_skips_for_daemon_without_profile_list(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=1, out="unknown command: profile-list")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] not in fake_host.argvs
    assert ("fans", False,
            "saved custom TDP could not be safely cleared; fan curve was not applied") in results


def test_curve_without_tdp_refuses_reset_if_custom_remains_active(profiles_env, fake_host):
    fake_host.fail("tdp --help", rc=0, out="Flags: --profile string")
    fake_host.fail("profile --list", rc=0,
                   out="Firmware profiles: quiet, balanced, performance\n"
                       "Custom profiles:\n * custom             20W\n")
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert [Z, "fancurve", "--set", profiles.curve_spec(CURVE)] not in fake_host.argvs
    assert ("tdp", False,
            "z13ctl custom profile is still active; saved TDP was left unchanged") in results


def test_old_z13ctl_without_named_tdp_reset_leaves_saved_custom_tdp_unchanged(
        profiles_env, fake_host):
    p = bare_profile(base="performance", fan_curve=[list(pt) for pt in CURVE])

    results = profiles.apply(p, z13ctl=Z)

    assert [Z, "tdp", "--help"] in fake_host.argvs
    assert [Z, "profile", "--list"] not in fake_host.argvs
    assert [Z, "tdp", "--reset", "--profile", "custom"] not in fake_host.argvs
    assert all(step != "tdp" for step, _, _ in results)


def test_tdp_without_curve_resets_fans_after_tdp(profiles_env, fake_host):
    """z13ctl's `tdp --set` re-applies the last curve its "custom" profile stored
    (Boondock came back with Max Fans' 100% curve); the reset after it clears that."""
    p = bare_profile(base="quiet", tdp={"pl1": 20, "pl2": 30, "pl3": 40, "force": False})
    results = profiles.apply(p, z13ctl=Z)
    assert fake_host.argvs == [
        [Z, "profile", "--set", "quiet"],
        [Z, "tdp", "--set", "20", "--pl2", "30", "--pl3", "40"],
        [Z, "fancurve", "--reset"],
    ]
    assert ("fans", True, "firmware auto") in results


def test_brightness_has_short_timeout(profiles_env, fake_host):
    profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    seen = [(c.argv[1:3], c.timeout) for c in fake_host.calls]
    assert (["--device", "keyboard"], 30) in seen
    assert (["--device", "lightbar"], 30) in seen


# ----------------------------------------------------------------------
# independence
# ----------------------------------------------------------------------

@pytest.mark.parametrize("failing", ["profile", "tdp", "fancurve", "undervolt", "keyboard",
                                     "batterylimit", "enter-cmd"])
def test_one_failing_step_does_not_stop_the_rest(profiles_env, failing, fake_host):
    fake_host.fail(lambda a: failing in a, rc=1, out=f"{failing} broke")
    prev = full_profile("Previous", hooks={"enter": "", "exit": "exit-cmd"})
    results = profiles.apply(full_profile(), previous=prev, z13ctl=Z)

    assert names(results) == EXPECTED_ORDER          # every step still ran
    assert len(fake_host.argvs) == len(EXPECTED_ORDER)
    bad = [(s, msg) for s, ok, msg in results if not ok]
    assert len(bad) == 1
    assert "broke" in bad[0][1]


def test_failure_message_falls_back_to_rc(profiles_env, fake_host):
    fake_host.fail(lambda a: "batterylimit" in a, rc=3, out="")
    results = profiles.apply(bare_profile(battery_limit=60), z13ctl=Z)
    assert ("battery limit", False, "rc=3") in results


def test_lightbar_failure_does_not_block_keyboard(profiles_env, fake_host):
    fake_host.fail(lambda a: "lightbar" in a, rc=124, out="timed out")
    results = profiles.apply(bare_profile(brightness="high"), z13ctl=Z)
    assert ("brightness (keyboard)", True, "high") in results
    lb = [r for r in results if r[0] == "brightness (lightbar)"]
    assert len(lb) == 1 and lb[0][1] is False
    assert "timed out" in lb[0][2] and "still failing" in lb[0][2]


# ----------------------------------------------------------------------
# wedged lightbar recovery (ROADMAP §3)
# ----------------------------------------------------------------------

WEDGED = "init packet 1: write to /dev/hidraw5: write /dev/hidraw5: connection timed out"
STALE = "init packet 1: write /dev/hidraw5: no such device"
RESET = ["systemctl", "start", "--no-ask-password", "z13-lightbar-reset.service"]
RESTART = ["systemctl", "--user", "restart", "--no-ask-password", "z13ctl.service"]


def wedged_until_reset(fake_host):
    """The lightbar times out once; the reset (and the retry after it) then succeed."""
    return fake_host.fail(lambda a: "lightbar" in a, out=WEDGED, times=1)


def stale_until_daemon_restart(fake_host):
    """The lightbar reports ENODEV once; the retry after the daemon restart succeeds."""
    return fake_host.fail(lambda a: "lightbar" in a, out=STALE, times=1)


def test_wedged_lightbar_recovers_once_and_retries_once(profiles_env, fake_host):
    wedged_until_reset(fake_host)
    results = profiles.apply(bare_profile(brightness="low", battery_limit=80), z13ctl=Z)
    assert fake_host.argvs == [
        [Z, "profile", "--set", "balanced"],
        [Z, "--device", "keyboard", "brightness", "low"],     # keyboard first, before any reset
        [Z, "--device", "lightbar", "brightness", "low"],
        RESET,
        RESTART,                                              # daemon cached the old hidraw path
        [Z, "--device", "lightbar", "brightness", "low"],     # exactly one retry
        [Z, "batterylimit", "--set", "80"],
    ]
    assert ("brightness (keyboard)", True, "low") in results
    assert ("brightness (lightbar)", True, "low (lightbar recovered)") in results
    assert profiles_env.lightbar_stamp.exists()


def test_stale_lightbar_recovers_with_daemon_restart_only(profiles_env, fake_host):
    stale_until_daemon_restart(fake_host)
    results = profiles.apply(bare_profile(brightness="low", battery_limit=80), z13ctl=Z)
    assert fake_host.argvs == [
        [Z, "profile", "--set", "balanced"],
        [Z, "--device", "keyboard", "brightness", "low"],     # keyboard first, before any recovery
        [Z, "--device", "lightbar", "brightness", "low"],
        RESTART,                                              # no reset unit: just a stale handle
        [Z, "--device", "lightbar", "brightness", "low"],     # exactly one retry
        [Z, "batterylimit", "--set", "80"],
    ]
    assert RESET not in fake_host.argvs
    assert ("brightness (keyboard)", True, "low") in results
    assert ("brightness (lightbar)", True, "low (lightbar recovered)") in results
    assert profiles_env.lightbar_daemon_stamp.exists()


def test_wedged_daemon_restart_failure_is_reported_but_still_retries_once(profiles_env, fake_host):
    (fake_host
              .fail(lambda a: "lightbar" in a, rc=1, out=WEDGED)
              .fail(lambda a: a == RESTART, rc=1, out="Unit z13ctl.service not loaded."))
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert fake_host.argvs.count(RESET) == 1
    assert fake_host.argvs.count(RESTART) == 1
    lightbar_writes = [a for a in fake_host.argvs if "lightbar" in a]
    assert len(lightbar_writes) == 2          # the write, plus exactly one retry
    msg = dict((s, m) for s, _, m in results)["brightness (lightbar)"]
    assert "daemon restart failed" in msg


def test_stale_daemon_restart_failure_is_reported_without_retry(profiles_env, fake_host):
    (fake_host
              .fail(lambda a: "lightbar" in a, rc=1, out=STALE)
              .fail(lambda a: a == RESTART, rc=1, out="Unit z13ctl.service not loaded."))
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert RESET not in fake_host.argvs
    assert fake_host.argvs.count(RESTART) == 1
    lightbar_writes = [a for a in fake_host.argvs if "lightbar" in a]
    assert len(lightbar_writes) == 1          # recovery itself failed: no retry
    msg = dict((s, m) for s, _, m in results)["brightness (lightbar)"]
    assert "daemon restart failed" in msg


def test_still_wedged_after_reset_is_not_retried_again(profiles_env, fake_host):
    fake_host.fail(lambda a: "lightbar" in a, rc=1, out=WEDGED)
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    lightbar_writes = [a for a in fake_host.argvs if "lightbar" in a]
    assert len(lightbar_writes) == 2
    assert fake_host.argvs.count(RESET) == 1
    assert fake_host.argvs.count(RESTART) == 1
    assert ("brightness (lightbar)", False) == results[-1][:2]


def test_still_stale_after_daemon_restart_is_not_retried_again(profiles_env, fake_host):
    fake_host.fail(lambda a: "lightbar" in a, rc=1, out=STALE)
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    lightbar_writes = [a for a in fake_host.argvs if "lightbar" in a]
    assert len(lightbar_writes) == 2
    assert RESET not in fake_host.argvs
    assert fake_host.argvs.count(RESTART) == 1
    assert ("brightness (lightbar)", False) == results[-1][:2]


def test_unrecognised_lightbar_failure_triggers_no_recovery(profiles_env, fake_host):
    fake_host.fail(lambda a: "lightbar" in a, rc=1, out="permission denied")
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert RESET not in fake_host.argvs
    assert RESTART not in fake_host.argvs
    assert ("brightness (lightbar)", False, "permission denied") in results


def test_keyboard_timeout_never_resets_anything(profiles_env, fake_host):
    fake_host.fail(lambda a: "keyboard" in a, rc=1, out=WEDGED)
    profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert RESET not in fake_host.argvs
    assert len([a for a in fake_host.argvs if "keyboard" in a]) == 1


def test_reset_is_rate_limited(profiles_env, fake_host, monkeypatch):
    r1 = wedged_until_reset(fake_host)
    profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert r1.argvs.count(RESET) == 1
    # Wedged again a minute later: no second reset, no retry.
    r2 = FakeHost().install(monkeypatch.setattr).fail(lambda a: "lightbar" in a, rc=1, out=WEDGED)
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert RESET not in r2.argvs
    assert len([a for a in r2.argvs if "lightbar" in a]) == 1
    msg = dict((s, m) for s, _, m in results)["brightness (lightbar)"]
    assert "skipped" in msg


def test_stale_daemon_restart_is_rate_limited(profiles_env, fake_host, monkeypatch):
    r1 = stale_until_daemon_restart(fake_host)
    profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert r1.argvs.count(RESTART) == 1
    # Stale again a minute later: no second daemon restart, no retry.
    r2 = FakeHost().install(monkeypatch.setattr).fail(lambda a: "lightbar" in a, rc=1, out=STALE)
    results = profiles.apply(bare_profile(brightness="low"), z13ctl=Z)
    assert RESTART not in r2.argvs
    assert len([a for a in r2.argvs if "lightbar" in a]) == 1
    msg = dict((s, m) for s, _, m in results)["brightness (lightbar)"]
    assert "skipped" in msg


def test_recover_lightbar_rate_limit_window(profiles_env, fake_host):
    slept = []
    assert profiles.recover_lightbar(sleep=slept.append, now=lambda: 5000.0)[0]
    stamp = profiles_env.lightbar_stamp
    import os
    os.utime(stamp, (5000.0, 5000.0))
    ok, msg = profiles.recover_lightbar(sleep=slept.append,
                                        now=lambda: 5000.0 + profiles.LIGHTBAR_RESET_MIN_GAP_S - 1)
    assert not ok and "skipped" in msg
    assert profiles.recover_lightbar(sleep=slept.append,
                                     now=lambda: 5000.0 + profiles.LIGHTBAR_RESET_MIN_GAP_S + 1)[0]
    assert fake_host.argvs == [RESET, RESTART, RESET, RESTART]
    assert slept == [profiles.LIGHTBAR_RESET_SETTLE_S, profiles.LIGHTBAR_DAEMON_RESTART_WAIT_S] * 2


def test_recover_lightbar_stale_rate_limit_window(profiles_env, fake_host):
    slept = []
    assert profiles.recover_lightbar_stale(sleep=slept.append, now=lambda: 5000.0)[0]
    stamp = profiles_env.lightbar_daemon_stamp
    import os
    os.utime(stamp, (5000.0, 5000.0))
    ok, msg = profiles.recover_lightbar_stale(sleep=slept.append,
                                              now=lambda: 5000.0 + profiles.LIGHTBAR_RESET_MIN_GAP_S - 1)
    assert not ok and "skipped" in msg
    assert profiles.recover_lightbar_stale(sleep=slept.append,
                                           now=lambda: 5000.0 + profiles.LIGHTBAR_RESET_MIN_GAP_S + 1)[0]
    assert fake_host.argvs == [RESTART, RESTART]
    assert slept == [profiles.LIGHTBAR_DAEMON_RESTART_WAIT_S] * 2


def test_recover_lightbar_reports_unit_failure(profiles_env, fake_host):
    fake_host.fail(lambda a: a[0] == "systemctl", rc=1, out="Interactive authentication required.")
    ok, msg = profiles.recover_lightbar(sleep=lambda s: None)
    assert not ok and "Interactive authentication required" in msg


def test_recover_lightbar_daemon_restart_failure_is_folded_into_message(profiles_env, fake_host):
    fake_host.fail(lambda a: a == RESTART, rc=1, out="Unit z13ctl.service not loaded.")
    ok, msg = profiles.recover_lightbar(sleep=lambda s: None)
    assert ok      # the USB reset itself succeeded; only the daemon restart failed
    assert "daemon restart failed" in msg


def test_recover_lightbar_stale_reports_daemon_restart_failure(profiles_env, fake_host):
    fake_host.fail(lambda a: a == RESTART, rc=1, out="Unit z13ctl.service not loaded.")
    ok, msg = profiles.recover_lightbar_stale(sleep=lambda s: None)
    assert not ok
    assert "daemon restart failed" in msg


@pytest.mark.parametrize("out,expected", [
    (WEDGED, True), ("timed out", True), ("context deadline exceeded (Client.Timeout)", True),
    ("no such device", False), ("", False), (None, False)])
def test_lightbar_timed_out(out, expected):
    assert profiles.lightbar_timed_out(out) is expected


@pytest.mark.parametrize("out,expected", [
    (WEDGED, "wedged"), ("timed out", "wedged"),
    ("context deadline exceeded (Client.Timeout)", "wedged"),
    (STALE, "stale"), ("no such device", "stale"), ("write: ENODEV", "stale"),
    ("permission denied", None), ("", None), (None, None)])
def test_lightbar_failure_kind(out, expected):
    assert profiles.lightbar_failure_kind(out) == expected


def test_stamp_lives_in_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.delenv("FLATPAK_ID", raising=False)
    assert profiles.lightbar_stamp_path() == str(tmp_path / "z13-lightbar-reset.stamp")
    shared = tmp_path / "app" / "io.github.iannitram.Z13Helper"
    shared.mkdir(parents=True)
    monkeypatch.setenv("FLATPAK_ID", "io.github.iannitram.Z13Helper")
    assert profiles.lightbar_stamp_path() == str(shared / "z13-lightbar-reset.stamp")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "missing"))
    assert profiles.lightbar_stamp_path() == "/tmp/z13-lightbar-reset.stamp"


# ----------------------------------------------------------------------
# hooks
# ----------------------------------------------------------------------

def hook_calls(fake_host):
    return [a[-1] for a in fake_host.argvs if a[0] == "bash"]


def test_exit_hook_runs_first_when_switching(profiles_env, fake_host):
    prev = bare_profile("Boondock", hooks={"enter": "boondock on", "exit": "boondock off"})
    nxt = bare_profile("Balanced", hooks={"enter": "say hi", "exit": ""})
    profiles.apply(nxt, previous=prev, z13ctl=Z)
    assert fake_host.argvs[0] == ["bash", "-lc", "boondock off"]
    assert hook_calls(fake_host) == ["boondock off", "say hi"]


def test_exit_hook_skipped_when_reapplying_same_profile(profiles_env, fake_host):
    p = bare_profile("Boondock", hooks={"enter": "boondock on", "exit": "boondock off"})
    profiles.apply(p, previous=dict(p), z13ctl=Z)
    assert hook_calls(fake_host) == ["boondock on"]


def test_no_previous_no_exit_hook(profiles_env, fake_host):
    p = bare_profile(hooks={"enter": "", "exit": "never"})
    profiles.apply(p, previous=None, z13ctl=Z)
    assert hook_calls(fake_host) == []


@pytest.mark.parametrize("hooks", [{"enter": "", "exit": "   "}, {}, None])
def test_blank_or_missing_hooks_are_not_run(profiles_env, fake_host, hooks):
    prev = bare_profile("Prev", hooks=hooks)
    profiles.apply(bare_profile("Next", hooks=hooks), previous=prev, z13ctl=Z)
    assert hook_calls(fake_host) == []


def test_skip_hooks_skips_both(profiles_env, fake_host):
    prev = bare_profile("Boondock", hooks={"enter": "boondock on", "exit": "boondock off"})
    nxt = bare_profile("Balanced", hooks={"enter": "say hi", "exit": ""})
    results = profiles.apply(nxt, previous=prev, z13ctl=Z, skip_hooks=True)
    assert hook_calls(fake_host) == []
    assert "exit hook" not in names(results) and "enter hook" not in names(results)


# ----------------------------------------------------------------------
# Max Fans
# ----------------------------------------------------------------------

def write_max_fans(paths, **state):
    paths.max_fans.parent.mkdir(parents=True, exist_ok=True)
    paths.max_fans.write_text(json.dumps(state))


def read_max_fans(paths):
    return json.loads(paths.max_fans.read_text())


def test_max_fans_on_keeps_100_percent_and_saves_profile_curve(profiles_env, fake_host):
    write_max_fans(profiles_env, enabled=True, saved_curve=[[1, 2]] * 8, other="kept")
    results = profiles.apply(full_profile(), z13ctl=Z)

    fan_calls = [a for a in fake_host.argvs if a[1] == "fancurve"]
    assert fan_calls == [[Z, "fancurve", "--set", profiles.MAX_FANS_SPEC]]
    assert ("fans", True, "Max Fans stays ON (100%)") in results

    state = read_max_fans(profiles_env)
    assert state["enabled"] is True
    assert state["other"] == "kept"                  # merged, not replaced
    # percent -> 0..255, temps unchanged
    assert state["saved_curve"] == [[t, round(p * 255 / 100)] for t, p in CURVE]
    assert state["saved_curve"][0] == [30, 26]       # 10% -> 25.5 -> 26
    assert state["saved_curve"][-1] == [100, 255]


def test_max_fans_spec_is_all_100_from_20c():
    pts = profiles.MAX_FANS_SPEC.split(",")
    assert len(pts) == 8
    assert pts[0] == "20:100%"
    assert all(p.endswith(":100%") for p in pts)


def test_max_fans_on_without_profile_curve_still_pins_100(profiles_env, fake_host):
    write_max_fans(profiles_env, enabled=True)
    profiles.apply(bare_profile(), z13ctl=Z)
    assert [a for a in fake_host.argvs if a[1] == "fancurve"] == [[Z, "fancurve", "--set", profiles.MAX_FANS_SPEC]]


def test_max_fans_off_applies_profile_curve(profiles_env, fake_host):
    write_max_fans(profiles_env, enabled=False)
    results = profiles.apply(full_profile(), z13ctl=Z)
    assert [a for a in fake_host.argvs if a[1] == "fancurve"] == [[Z, "fancurve", "--set", profiles.curve_spec(CURVE)]]
    assert ("fans", True, "custom curve") in results
    assert "saved_curve" not in read_max_fans(profiles_env)


def test_max_fans_file_missing_means_off(profiles_env, fake_host):
    profiles.apply(full_profile(), z13ctl=Z)
    assert [a for a in fake_host.argvs if a[1] == "fancurve"][0][-1] == profiles.curve_spec(CURVE)
    assert not profiles_env.max_fans.exists()       # nothing written when OFF


# ----------------------------------------------------------------------
# undervolt
# ----------------------------------------------------------------------

def test_undervolt_ok(profiles_env, fake_host):
    results = profiles.apply(bare_profile(undervolt=-20), z13ctl=Z)
    assert ("undervolt", True, "-20") in results


def test_undervolt_zero_is_still_written(profiles_env, fake_host):
    profiles.apply(bare_profile(undervolt=0), z13ctl=Z)
    assert [Z, "undervolt", "--set", "0"] in fake_host.argvs


def test_undervolt_without_ryzen_smu_is_unavailable(profiles_env, fake_host):
    fake_host.fail(lambda a: "undervolt" in a, rc=1,
                               out="error: ryzen_smu kernel module is not loaded")
    results = profiles.apply(bare_profile(undervolt=-10), z13ctl=Z)
    assert ("undervolt", False, "unavailable (ryzen_smu not loaded)") in results


def test_undervolt_other_failure_passes_message_through(profiles_env, fake_host):
    fake_host.fail(lambda a: "undervolt" in a, rc=2, out="value out of range")
    results = profiles.apply(bare_profile(undervolt=-10), z13ctl=Z)
    assert ("undervolt", False, "value out of range") in results

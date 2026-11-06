"""Resume restore skips only when daemon state and live fan state are complete."""

import json

import pytest
import profiles
import host


class FakeClient:
    def __init__(self, state=None, error=None):
        self.state = state
        self.error = error
        self.reads = 0

    def get_state(self):
        self.reads += 1
        if self.error:
            raise self.error
        return self.state


def setup_restore(profiles_env, monkeypatch, *, backend_profile=None, mode="manual",
                  max_fans=False, points=None, state_error=None, base="balanced",
                  live_base="balanced", live_tdp=None, with_tdp=False):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Balanced")
    profile["base"] = base
    if base != "balanced":
        profile["fan_curve"] = [[t, pct] for t, pct in zip((20, 30, 40, 50, 60, 70, 80, 90),
                                                           (20, 20, 35, 55, 75, 90, 100, 100))]
    if with_tdp:
        profile["tdp"] = {"pl1": 30, "pl2": 40, "pl3": 50}
    profiles.save_store({"version": 1, "active": "Balanced", "profiles": [profile]})
    profiles_env.max_fans.write_text(json.dumps({"enabled": max_fans}))
    client = FakeClient({"profile": ""}, state_error)
    # Match through the public mapping helper used by the production guard.
    from z13ctl_client import Z13CtlClient
    active = profiles.get_profile("Balanced")
    mapped = Z13CtlClient.mapped_profile_name(active)
    client.state.update({
        "profile": backend_profile or mapped,
        "battery_limit": 80,
        "custom_profiles": {mapped: {"tdp": ({"pl1_spl": 30, "pl2_sppt": 40,
                                                "fppt": 50, "apu_sppt": 50,
                                                "platform_sppt": 50} if with_tdp else None)}},
    })
    monkeypatch.setattr(profiles.authority, "named_client", lambda: client)
    monkeypatch.setattr(profiles.host, "run", lambda argv, **kwargs: host.Result(
        0, f"2026/11/06 08:58:19 INFO resume: restoring custom profile profile={mapped}\n"
           f"2026/11/06 08:58:20 INFO profile set={mapped}", False))

    if points is None:
        points = [(t, round(pct * 255 / 100), pct) for t, pct in profile["fan_curve"]]
    monkeypatch.setattr("hardware_controller.HardwareController.get_fancurve",
                        lambda self: {"mode": mode, "points": points})
    monkeypatch.setattr("hardware_controller.HardwareController.get_tdp",
                        lambda self: pytest.fail("resume guard must not read PPT/TDP hardware"))
    monkeypatch.setattr("sensor_reader.SensorReader.get_platform_profile", lambda self: live_base)
    applies = []
    monkeypatch.setattr(profiles, "apply_by_name", lambda name, **kwargs: applies.append(name) or [])
    return client, applies


def check(monkeypatch, *extra):
    return profiles.main(["restore", "--if-needed", *extra, "--since-epoch", "1793955496"])


def test_restore_if_needed_skips_when_all_guards_hold(profiles_env, monkeypatch, capsys):
    client, applies = setup_restore(profiles_env, monkeypatch)

    assert check(monkeypatch) == profiles.RESTORE_SKIPPED

    assert client.reads == 1
    assert applies == []
    assert "already restored by z13ctl; skipped" in capsys.readouterr().out


def test_restore_if_needed_applies_when_named_profile_differs(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, backend_profile="z13h-some-other-profile")

    assert check(monkeypatch) == 0

    assert applies == ["Balanced"]


def test_restore_if_needed_applies_when_curve_is_auto(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, mode="auto")

    assert check(monkeypatch) == 0

    assert applies == ["Balanced"]


def test_manual_different_points_apply(profiles_env, monkeypatch):
    points = [(t, 255, 100) for t in (20, 30, 40, 50, 60, 70, 80, 90)]
    _, applies = setup_restore(profiles_env, monkeypatch, points=points)
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_matching_points_skip_and_one_pwm_rounding_skips(profiles_env, monkeypatch):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Balanced")
    points = [(t, round(pct * 255 / 100), pct) for t, pct in profile["fan_curve"]]
    points[5] = (points[5][0], points[5][1] - 1, points[5][2])
    setup_restore(profiles_env, monkeypatch, points=points)
    assert check(monkeypatch) == profiles.RESTORE_SKIPPED


def test_profile_without_curve_and_max_fans_off_always_applies(profiles_env, monkeypatch):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Silent")
    profile["fan_curve"] = None
    profiles.save_store({"version": 1, "active": "Silent", "profiles": [profile]})
    profiles_env.max_fans.write_text(json.dumps({"enabled": False}))
    from z13ctl_client import Z13CtlClient
    client = FakeClient({"profile": Z13CtlClient.mapped_profile_name(profile)})
    monkeypatch.setattr(profiles.authority, "named_client", lambda: client)
    monkeypatch.setattr(profiles.host, "run", lambda argv, **kwargs: host.Result(
        0, f"resume: restoring custom profile profile={Z13CtlClient.mapped_profile_name(profile)}\n"
           f"profile set={Z13CtlClient.mapped_profile_name(profile)}", False))
    monkeypatch.setattr("sensor_reader.SensorReader.get_platform_profile", lambda self: "quiet")
    monkeypatch.setattr("hardware_controller.HardwareController.get_fancurve",
                        lambda self: {"mode": "manual", "points": [(t, 255, 100) for t in range(8)]})
    applies = []
    monkeypatch.setattr(profiles, "apply_by_name", lambda name, **kwargs: applies.append(name) or [])
    assert check(monkeypatch) == 0
    assert applies == ["Silent"]


def test_restore_if_needed_applies_when_max_fans_curve_is_not_full(profiles_env, monkeypatch):
    points = [(t, 254 if i == 4 else 255, 99 if i == 4 else 100)
              for i, t in enumerate(range(8))]
    _, applies = setup_restore(profiles_env, monkeypatch, max_fans=True, points=points)

    assert check(monkeypatch) == 0

    assert applies == ["Balanced"]


def test_max_fans_point_at_254_pwm_never_skips(profiles_env, monkeypatch):
    points = [(t, 254 if i == 3 else 255, 100) for i, t in enumerate(profiles.MAX_FAN_TEMPS)]
    _, applies = setup_restore(profiles_env, monkeypatch, max_fans=True, points=points)
    assert check(monkeypatch, "--check-only") == 0
    assert applies == []


def test_restore_if_needed_applies_when_daemon_state_cannot_be_read(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, state_error=OSError("offline"))

    assert check(monkeypatch) == 0

    assert applies == ["Balanced"]


def test_restore_if_needed_restores_when_max_fans_state_is_unreadable(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch)
    profiles_env.max_fans.unlink()

    assert check(monkeypatch) == 0

    assert applies == ["Balanced"]


@pytest.mark.parametrize("enabled", ["true", 1, None])
def test_restore_if_needed_restores_when_enabled_is_not_bool(profiles_env, monkeypatch, enabled):
    _, applies = setup_restore(profiles_env, monkeypatch)
    profiles_env.max_fans.write_text(json.dumps({"enabled": enabled}))
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_manual_mode_with_fewer_than_eight_points_applies(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, points=[(20, 50, 20)] * 7)
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_max_fans_requires_pwm_and_pct_full(profiles_env, monkeypatch):
    points = [(t, 255, 100) for t in profiles.MAX_FAN_TEMPS]
    points[-1] = (points[-1][0], 255, 99)
    _, applies = setup_restore(profiles_env, monkeypatch, max_fans=True, points=points)
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_named_client_none_applies(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch)
    monkeypatch.setattr(profiles.authority, "named_client", lambda: None)
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_base_mismatch_applies(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, live_base="quiet")
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_unreadable_base_applies(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, live_base=None)
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


@pytest.mark.parametrize("key,value", [("pl1_spl", 31), ("pl2_sppt", 41), ("fppt", 51)])
def test_tdp_mismatch_applies(profiles_env, monkeypatch, key, value):
    client, applies = setup_restore(profiles_env, monkeypatch, with_tdp=True)
    client.state["custom_profiles"][client.state["profile"]]["tdp"][key] = value
    assert check(monkeypatch) == 0
    assert applies == ["Balanced"]


def test_check_only_returns_skip_code_without_applying(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch)
    assert check(monkeypatch, "--check-only") == profiles.RESTORE_SKIPPED
    assert applies == []


def test_matching_tdp_and_base_skip(profiles_env, monkeypatch):
    _, applies = setup_restore(profiles_env, monkeypatch, with_tdp=True)
    assert check(monkeypatch) == profiles.RESTORE_SKIPPED
    assert applies == []


def test_daemon_tdp_must_be_empty_when_profile_has_no_tdp(profiles_env, monkeypatch):
    client, _ = setup_restore(profiles_env, monkeypatch)
    client.state["custom_profiles"][client.state["profile"]]["tdp"] = {"pl1_spl": 30}
    assert check(monkeypatch, "--check-only") == 0


@pytest.mark.parametrize("offset", [-2, 2])
def test_profile_curve_two_pwm_off_does_not_skip(profiles_env, monkeypatch, offset):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Balanced")
    points = [(t, round(pct * 255 / 100), pct) for t, pct in profile["fan_curve"]]
    points[2] = (points[2][0], points[2][1] + offset, points[2][2])
    setup_restore(profiles_env, monkeypatch, points=points)
    assert check(monkeypatch, "--check-only") == 0


def test_stored_none_curve_is_plain_mismatch(profiles_env, monkeypatch):
    _, _ = setup_restore(profiles_env, monkeypatch)
    store = profiles.load_store()
    store["profiles"][0]["fan_curve"] = None
    profiles.save_store(store)
    assert profiles._resume_restore_is_current(1793955496) is False
    assert check(monkeypatch, "--check-only") == 0


def test_duplicate_temperatures_do_not_skip(profiles_env, monkeypatch):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Balanced")
    points = [(t, round(pct * 255 / 100), pct) for t, pct in profile["fan_curve"]]
    points[-1] = (points[-2][0], points[-1][1], points[-1][2])
    setup_restore(profiles_env, monkeypatch, points=points)
    assert check(monkeypatch, "--check-only") == 0


@pytest.mark.parametrize("enabled", ["true", 1])
def test_max_fans_enabled_non_bool_does_not_skip(profiles_env, monkeypatch, enabled):
    setup_restore(profiles_env, monkeypatch, max_fans=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": enabled}))
    assert check(monkeypatch, "--check-only") == 0


@pytest.mark.parametrize("journal", [
    "2026/11/06 08:58:20 INFO profile set=z13h-balanced-5386ea5d",
    "2026/11/06 08:58:19 INFO resume: restoring custom profile profile=wrong\n2026/11/06 08:58:20 INFO profile set=z13h-balanced-5386ea5d",
    "2026/11/06 08:58:19 INFO resume: restoring custom profile profile=z13h-balanced-5386ea5d\n2026/11/06 08:58:20 INFO profile set=z13h-balanced-5386ea5d\n2026/11/06 08:58:21 WARN command failed cmd=x",
    "2026/11/06 08:58:19 INFO resume: restoring custom profile profile=z13h-balanced-5386ea5d",
])
def test_insufficient_journal_evidence_does_not_skip(profiles_env, monkeypatch, journal):
    setup_restore(profiles_env, monkeypatch)
    monkeypatch.setattr(profiles.host, "run", lambda argv, **kwargs: host.Result(0, journal, False))
    assert check(monkeypatch, "--check-only") == 0


def test_journal_timeout_is_read_error(profiles_env, monkeypatch):
    setup_restore(profiles_env, monkeypatch)
    monkeypatch.setattr(profiles.host, "run", lambda argv, **kwargs: host.Result(124, "", True))
    assert profiles.main(["restore", "--if-needed", "--check-only", "--since-epoch", "1"]) == profiles.RESTORE_CHECK_ERROR


def test_missing_since_epoch_never_skips(profiles_env, monkeypatch):
    setup_restore(profiles_env, monkeypatch)
    assert profiles.main(["restore", "--if-needed", "--check-only"]) == 0


def test_battery_limit_mismatch_does_not_skip(profiles_env, monkeypatch):
    client, _ = setup_restore(profiles_env, monkeypatch)
    client.state["battery_limit"] = 100
    assert check(monkeypatch, "--check-only") == 0


def test_battery_limit_restore_fallback_is_checked(profiles_env, monkeypatch):
    client, _ = setup_restore(profiles_env, monkeypatch)
    store = profiles.load_store()
    store["profiles"][0]["battery_limit"] = None
    profiles.save_store(store)
    assert check(monkeypatch, "--check-only") == profiles.RESTORE_SKIPPED
    client.state["battery_limit"] = 70
    assert check(monkeypatch, "--check-only") == 0


def test_epp_mismatch_does_not_skip(profiles_env, monkeypatch):
    profile = next(p for p in profiles.default_profiles() if p["name"] == "Balanced")
    profile["epp"] = "balance_performance"
    setup_restore(profiles_env, monkeypatch)
    store = profiles.load_store()
    store["profiles"][0]["epp"] = "balance_performance"
    profiles.save_store(store)
    monkeypatch.setattr("hardware_controller.HardwareController.get_epp", lambda self: "performance")
    assert check(monkeypatch, "--check-only") == 0

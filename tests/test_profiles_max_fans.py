"""Per-profile Max Fans (`max_fans`, Turbo ships ON; added 2026-10-11).

Switching TO a max_fans profile turns Max Fans ON; switching AWAY turns it OFF
(explicit `fancurve --reset` when the new profile has no curve); re-applying the
same profile (login/resume restore) leaves the user's toggle alone.
"""

import json

import profiles

Z = profiles.Z13CTL


def by_name(name):
    return profiles.get_profile(name, {"profiles": profiles.default_profiles()})


def mf(paths):
    try:
        return json.loads(paths.max_fans.read_text())
    except FileNotFoundError:
        return {}


def fan_calls(fake_host):
    return [a[1:] for a in fake_host.argvs if a[1] == "fancurve"]


def test_turbo_ships_with_max_fans():
    assert {p["name"]: p["max_fans"] for p in profiles.default_profiles()} == \
        {"Silent": False, "Balanced": False, "Turbo": True, "Boondock": False}


def test_switching_to_turbo_turns_max_fans_on(profiles_env, fake_host):
    profiles.apply(by_name("Turbo"), previous=by_name("Balanced"))
    assert mf(profiles_env)["enabled"] is True
    assert fan_calls(fake_host) == [["fancurve", "--set", profiles.MAX_FANS_SPEC]]
    # Turbo's own curve is what Max Fans OFF goes back to.
    assert mf(profiles_env)["saved_curve"] == [
        [temp, round(pwm * 255 / 100)] for temp, pwm in by_name("Turbo")["fan_curve"]]


def test_leaving_turbo_for_a_curve_profile_turns_max_fans_off(profiles_env, fake_host):
    profiles_env.max_fans.parent.mkdir(parents=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True}))
    profiles.apply(by_name("Balanced"), previous=by_name("Turbo"))
    assert mf(profiles_env)["enabled"] is False
    assert fan_calls(fake_host) == [["fancurve", "--set", profiles.curve_spec(by_name("Balanced")["fan_curve"])]]


def test_leaving_turbo_for_no_curve_resets_to_firmware_auto(profiles_env, fake_host):
    profiles_env.max_fans.parent.mkdir(parents=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True}))
    res = profiles.apply(by_name("Silent"), previous=by_name("Turbo"))
    assert mf(profiles_env)["enabled"] is False
    assert fan_calls(fake_host) == [["fancurve", "--reset"]]
    assert ("fans", True, "Max Fans OFF, firmware auto") in res


def test_restore_keeps_users_manual_off(profiles_env, fake_host):
    """Max Fans switched OFF by hand while in Turbo: a restore must not re-enable it."""
    profiles_env.max_fans.parent.mkdir(parents=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": False}))
    turbo = by_name("Turbo")
    profiles.apply(turbo, previous=turbo)
    assert mf(profiles_env)["enabled"] is False
    assert fan_calls(fake_host) == [["fancurve", "--set", profiles.curve_spec(turbo["fan_curve"])]]


def test_manual_max_fans_survives_switch_between_plain_profiles(profiles_env, fake_host):
    profiles_env.max_fans.parent.mkdir(parents=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True}))
    profiles.apply(by_name("Silent"), previous=by_name("Balanced"))
    assert mf(profiles_env)["enabled"] is True
    assert fan_calls(fake_host) == [["fancurve", "--set", profiles.MAX_FANS_SPEC]]


def test_saved_curve_follows_a_profile_without_curve(profiles_env, fake_host):
    """Bug found 2026-10-11: Max Fans ON, Balanced -> Silent, then OFF restored
    Balanced's curve on Silent. saved_curve must track the applied profile."""
    profiles_env.max_fans.parent.mkdir(parents=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True, "saved_curve": [[40, 51]] * 8}))
    profiles.apply(by_name("Silent"), previous=by_name("Balanced"))
    assert mf(profiles_env)["saved_curve"] is None


def test_print_results_always_two_spaces_after_step(capsys):
    long = "x" * 30
    profiles._print_results([(long, True, "msg"), ("fans", False, "boom")])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].endswith(f"{long}  msg")
    assert "fans" + " " * 18 + "  boom" in lines[1]

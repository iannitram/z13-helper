"""Max Fans restore snapshot: "firmware auto" is not "unknown" (issue #18).

Max Fans ON -> untick "Apply Custom Fan Curve" -> Max Fans OFF must end in
firmware auto, not in the curve that was snapshotted while it was still ticked.
The restore decision lives in max_fans_state so it is testable without Qt.
"""

import json

import max_fans_state as mfs

CURVE_PCT = [[30 + 10 * i, 10 * (i + 1)] for i in range(8)]


def write(env, **state):
    env.max_fans.parent.mkdir(parents=True, exist_ok=True)
    env.max_fans.write_text(json.dumps(state))


def off_calls(env, profile_curve):
    """What Max Fans OFF would send (mirrors dashboard_ui off())."""
    kind, pts = mfs.restore_plan()
    if kind == "curve":
        return ("set_pwm", pts)
    if kind == "auto":
        return ("reset", None)
    return ("set", profile_curve) if profile_curve else ("reset", None)


def test_on_disable_curve_off_ends_in_firmware_auto(profiles_env):
    write(profiles_env, enabled=True)
    mfs.record_profile_curve(CURVE_PCT)           # edit while ON: snapshot kept
    assert mfs.restore_plan()[0] == "curve"
    mfs.record_profile_curve(None)                # untick the custom curve while ON
    assert mfs.restore_plan() == ("auto", None)
    assert off_calls(profiles_env, None) == ("reset", None)


def test_explicit_null_means_auto_even_if_profile_has_a_curve(profiles_env):
    write(profiles_env, enabled=True, saved_curve=None)
    assert off_calls(profiles_env, CURVE_PCT) == ("reset", None)


def test_missing_or_unreadable_snapshot_keeps_profile_fallback(profiles_env):
    assert mfs.restore_plan() == ("unknown", None)          # no file
    write(profiles_env, enabled=True)                       # no key
    assert off_calls(profiles_env, CURVE_PCT) == ("set", CURVE_PCT)
    profiles_env.max_fans.write_text("{not json")
    assert mfs.restore_plan() == ("unknown", None)


def test_legacy_migration_without_snapshot_is_unknown_not_auto(profiles_env):
    mfs.migrate_legacy({"fans_max": True})
    assert mfs.restore_plan() == ("unknown", None)

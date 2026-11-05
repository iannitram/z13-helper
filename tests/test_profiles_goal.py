"""Phase 15b: the optional profile `goal`, and max_fans as the Max goal."""

import json

import pytest

import profiles

Z = profiles.Z13CTL


def custom(name="Mine", **over):
    p = {"name": name, "base": "balanced", "tdp": None, "fan_curve": None, "undervolt": None,
         "brightness": None, "battery_limit": None, "hooks": {"enter": "", "exit": ""}}
    p.update(over)
    return p


def seed(paths, profiles_list, active="Mine"):
    paths.store.parent.mkdir(parents=True, exist_ok=True)
    paths.store.write_text(json.dumps(
        {"version": 1, "active": active, "profiles": profiles_list}))


def stored(paths):
    return {p["name"]: p for p in json.loads(paths.store.read_text())["profiles"]}


def by_name(store, name):
    return next(p for p in store["profiles"] if p["name"] == name)


# ---- schema ----------------------------------------------------------

@pytest.mark.parametrize("goal", [
    {"kind": "quiet", "max_rpm": 4400}, {"kind": "comfort", "target_c": 40},
    {"kind": "fps", "target_fps": 60}, {"kind": "max"}, None])
def test_valid_goals_pass(goal):
    profiles.validate(custom(goal=goal))


@pytest.mark.parametrize("goal", [
    "quiet", {}, {"kind": "turbo"}, {"kind": "quiet"}, {"kind": "quiet", "max_rpm": "4400"},
    {"kind": "quiet", "max_rpm": 50}, {"kind": "quiet", "max_rpm": True},
    {"kind": "fps", "target_fps": 1000}])
def test_bad_goals_are_rejected(goal):
    with pytest.raises(ValueError):
        profiles.validate(custom(goal=goal))


@pytest.mark.parametrize("kind", [[], {}])
def test_unhashable_goal_kind_is_a_value_error_not_a_type_error(kind):
    # Locked for the review finding on #124/#126/#127: a list or dict kind must be
    # rejected as a bad goal (ValueError), not crash the GOAL_TARGETS lookup.
    with pytest.raises(ValueError):
        profiles.validate(custom(goal={"kind": kind}))


# ---- old files load unchanged; goal key only when set ----------------

def test_old_store_loads_and_saves_without_a_goal_key(profiles_env):
    seed(profiles_env, [custom("Mine")])
    store = profiles.load_store()
    assert "goal" not in by_name(store, "Mine")
    profiles.save_store(store)
    assert "goal" not in stored(profiles_env)["Mine"]


def test_quiet_goal_round_trips_and_clears_max_fans(profiles_env):
    seed(profiles_env, [custom("Mine", goal={"kind": "quiet", "max_rpm": 4400}, max_fans=True)])
    store = profiles.load_store()
    p = by_name(store, "Mine")
    assert p["goal"] == {"kind": "quiet", "max_rpm": 4400}
    assert p["max_fans"] is False
    profiles.save_store(store)
    assert stored(profiles_env)["Mine"]["goal"] == {"kind": "quiet", "max_rpm": 4400}


def test_invalid_goal_in_file_is_dropped_not_the_profile(profiles_env):
    seed(profiles_env, [custom("Mine", goal={"kind": "warp"}), custom("Other")])
    store = profiles.load_store()
    assert "goal" not in by_name(store, "Mine")
    assert by_name(store, "Other")


# ---- max_fans <-> goal max -------------------------------------------

def test_max_fans_profile_migrates_to_goal_max_and_keeps_max_fans(profiles_env):
    seed(profiles_env, [custom("Mine", max_fans=True)])
    store = profiles.load_store()
    p = by_name(store, "Mine")
    assert p["goal"] == {"kind": "max"} and p["max_fans"] is True
    profiles.save_store(store)
    saved = stored(profiles_env)["Mine"]
    assert saved["goal"] == {"kind": "max"} and saved["max_fans"] is True


def test_goal_max_without_max_fans_is_seen_by_back_compat_readers(profiles_env):
    seed(profiles_env, [custom("Mine", goal={"kind": "max"})])
    assert by_name(profiles.load_store(), "Mine")["max_fans"] is True


def test_builtins_migrate_turbo_only(profiles_env):
    store = profiles.load_store()
    goals = {p["name"]: p.get("goal") for p in store["profiles"]}
    assert goals == {"Silent": None, "Balanced": None, "Turbo": {"kind": "max"}, "Boondock": None}
    assert by_name(store, "Turbo")["max_fans"] is True
    assert "goal" not in stored(profiles_env)["Silent"]


def test_edited_profile_turning_max_fans_off_drops_goal_max(profiles_env):
    seed(profiles_env, [custom("Mine", max_fans=True)])
    p = by_name(profiles.load_store(), "Mine")
    p["max_fans"] = False
    p.pop("goal")
    profiles.upsert_profile(p)
    assert "goal" not in stored(profiles_env)["Mine"]
    assert stored(profiles_env)["Mine"]["max_fans"] is False


def test_upsert_keeps_a_goal_profile(profiles_env):
    profiles.upsert_profile(custom("Mine", goal={"kind": "fps", "target_fps": 60}))
    assert stored(profiles_env)["Mine"]["goal"] == {"kind": "fps", "target_fps": 60}


# ---- apply -----------------------------------------------------------

def mf(paths):
    return json.loads(paths.max_fans.read_text())


def fan_calls(fake_host):
    return [a[1:] for a in fake_host.argvs if a[1] == "fancurve"]


def test_apply_goal_max_without_max_fans_flag_pins_fans(profiles_env, fake_host):
    profiles.apply(custom("Mine", goal={"kind": "max"}), previous=custom("Other"))
    assert mf(profiles_env)["enabled"] is True
    assert fan_calls(fake_host) == [["fancurve", "--set", profiles.MAX_FANS_SPEC]]


def test_apply_quiet_goal_applies_fixed_values_for_now(profiles_env, fake_host):
    curve = profiles.default_profiles()[1]["fan_curve"]
    res = profiles.apply(custom("Mine", fan_curve=curve, goal={"kind": "quiet", "max_rpm": 4400}),
                         previous=custom("Other"))
    assert all(ok for _, ok, _ in res)
    assert fan_calls(fake_host) == [["fancurve", "--set", profiles.curve_spec(curve)]]


def test_leaving_goal_max_profile_turns_max_fans_off(profiles_env, fake_host):
    profiles_env.max_fans.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True}))
    profiles.apply(custom("Mine"), previous=custom("Old", goal={"kind": "max"}))
    assert mf(profiles_env)["enabled"] is False


def test_cli_apply_accepts_a_goal_profile(profiles_env, fake_host, capsys):
    seed(profiles_env, [custom("Mine", goal={"kind": "quiet", "max_rpm": 4400})])
    assert profiles.main(["apply", "Mine"]) == 0


# ---- null Max Fans snapshot ------------------------------------------

def test_null_snapshot_with_goal_max_profile_still_loads_and_applies(profiles_env, fake_host):
    profiles_env.max_fans.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.max_fans.write_text(json.dumps({"enabled": True, "saved_curve": None}))
    seed(profiles_env, [custom("Mine", max_fans=True), custom("Plain")], active="Plain")
    store = profiles.load_store()
    assert by_name(store, "Mine")["goal"] == {"kind": "max"}
    assert mf(profiles_env) == {"enabled": True, "saved_curve": None}   # untouched by load
    profiles.apply(by_name(store, "Plain"), previous=by_name(store, "Mine"))
    assert mf(profiles_env)["enabled"] is False
    assert fan_calls(fake_host) == [["fancurve", "--reset"]]

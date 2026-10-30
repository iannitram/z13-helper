"""profiles.py store, validation, apply_by_name and the z13-profile CLI."""

import json

import pytest

import profiles

GOOD_CURVE = [[40, 20], [50, 35], [60, 55], [70, 75], [75, 90], [80, 100], [85, 100], [90, 100]]


def custom(name="Mine", **over):
    p = {"name": name, "base": "balanced", "tdp": None, "fan_curve": None, "undervolt": None,
         "brightness": None, "battery_limit": None, "hooks": {"enter": "", "exit": ""}}
    p.update(over)
    return p


def stored(paths):
    return json.loads(paths.store.read_text())


# ----------------------------------------------------------------------
# load_store / seeding
# ----------------------------------------------------------------------

def test_load_store_create_false_writes_nothing(profiles_env):
    store = profiles.load_store(create=False)
    assert not profiles_env.store.exists()
    assert not profiles_env.store.parent.exists()
    assert store["active"] == "Balanced"
    assert [p["name"] for p in store["profiles"]] == ["Silent", "Balanced", "Turbo", "Boondock"]


def test_load_store_creates_default_file(profiles_env):
    store = profiles.load_store()
    assert profiles_env.store.exists()
    assert stored(profiles_env) == store
    assert not list(profiles_env.store.parent.glob("*.tmp"))   # atomic write cleaned up


def test_existing_store_is_returned_unchanged(profiles_env):
    profiles_env.store.parent.mkdir(parents=True)
    mine = {"version": 1, "active": "Mine", "profiles": [custom(max_fans=False)]}
    profiles_env.store.write_text(json.dumps(mine))
    assert profiles.load_store() == mine


def test_store_without_max_fans_key_gets_shipped_values(profiles_env):
    """Stores seeded before 2026-10-11 lack `max_fans`: built-ins take the shipped
    value (Turbo ON), custom profiles default to OFF. Nothing is written."""
    profiles_env.store.parent.mkdir(parents=True)
    old = {"version": 1, "active": "Turbo",
           "profiles": [{k: v for k, v in p.items() if k != "max_fans"} for p in profiles.default_profiles()]
                       + [custom()]}
    profiles_env.store.write_text(json.dumps(old))
    got = {p["name"]: p["max_fans"] for p in profiles.load_store()["profiles"]}
    assert got == {"Silent": False, "Balanced": False, "Turbo": True, "Boondock": False, "Mine": False}
    assert "max_fans" not in profiles_env.store.read_text()


@pytest.mark.parametrize("content", ["{not json", "[]", '{"profiles": "nope"}'])
def test_corrupt_store_is_kept_aside_before_reseeding(profiles_env, content):
    profiles_env.store.parent.mkdir(parents=True)
    profiles_env.store.write_text(content)
    store = profiles.load_store()
    assert store["active"] == "Balanced"
    kept = list(profiles_env.store.parent.glob("profiles.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text() == content


@pytest.mark.parametrize("content", ["{not json", "[]", '{"profiles": "nope"}'])
def test_unusable_store_falls_back_to_defaults_read_only(profiles_env, content):
    profiles_env.store.parent.mkdir(parents=True)
    profiles_env.store.write_text(content)
    store = profiles.load_store(create=False)
    assert store["active"] == "Balanced"
    assert profiles_env.store.read_text() == content   # create=False never overwrites


@pytest.mark.parametrize("legacy,active", [
    ("maximum-throughput", "Turbo"),
    ("balanced-daily-driver", "Balanced"),
    ("longevity-cool-stable", "Silent"),
    ("longevity-cool-stable\n", "Silent"),
    ("something-else", "Balanced"),
])
def test_seed_active_from_legacy_current_profile(profiles_env, legacy, active):
    profiles_env.legacy.parent.mkdir(parents=True)
    profiles_env.legacy.write_text(legacy)
    assert profiles.load_store(create=False)["active"] == active


def test_default_profiles_are_builtin_and_valid():
    defaults = profiles.default_profiles()
    assert {p["name"] for p in defaults} == {"Silent", "Balanced", "Turbo", "Boondock"}
    for p in defaults:
        assert p["builtin"] is True
        profiles.validate(p)
    assert next(p for p in defaults if p["name"] == "Turbo")["fan_curve"] == [
        [20, 50], [40, 50], [50, 75], [60, 90], [70, 100], [75, 100], [80, 100], [90, 100]]
    assert next(p for p in defaults if p["name"] == "Balanced")["fan_curve"] == [
        [20, 20], [40, 20], [50, 35], [60, 55], [70, 75], [75, 90], [80, 100], [90, 100]]
    boondock = next(p for p in defaults if p["name"] == "Boondock")
    assert boondock["hooks"]["enter"].endswith("boondock session-on")
    assert boondock["hooks"]["exit"].endswith("boondock session-off")


def _curve_pwm_at(curve, temp):
    if temp <= curve[0][0]:
        return curve[0][1]
    for (low_t, low_pwm), (high_t, high_pwm) in zip(curve, curve[1:]):
        if temp <= high_t:
            fraction = (temp - low_t) / (high_t - low_t)
            return low_pwm + fraction * (high_pwm - low_pwm)
    return curve[-1][1]


def test_builtin_curve_change_preserves_the_response_from_40c_up():
    for old_curve, new_curve in (
            (profiles.LEGACY_TURBO_CURVE, profiles.TURBO_CURVE),
            (profiles.LEGACY_BALANCED_CURVE, profiles.BALANCED_CURVE)):
        for temp in (40, 45, 50, 55, 60, 65, 70, 74, 75, 77, 80, 82, 85, 87, 90):
            assert _curve_pwm_at(new_curve, temp) == _curve_pwm_at(old_curve, temp)


def test_builtin_names_follow_ghelper():
    assert profiles.BUILTIN_FOR_BASE == {"quiet": "Silent", "balanced": "Balanced",
                                         "performance": "Turbo"}
    assert set(profiles.LEGACY_NAMES_REV) == {"Turbo", "Balanced", "Silent"}


def _legacy_builtin_store():
    builtins = profiles.default_profiles()
    for p in builtins:
        if p["name"] == "Turbo":
            p["fan_curve"] = [point[:] for point in profiles.LEGACY_TURBO_CURVE]
        elif p["name"] == "Balanced":
            p["fan_curve"] = [point[:] for point in profiles.LEGACY_BALANCED_CURVE]
    return {"version": 1, "active": "Turbo", "profiles": builtins}


def test_migrates_exact_legacy_builtin_curves_and_keeps_backup(profiles_env):
    old = _legacy_builtin_store()
    user_curve = [point[:] for point in profiles.LEGACY_TURBO_CURVE]
    old["profiles"].append(custom("My Turbo", fan_curve=user_curve))
    original = json.dumps(old, indent=2)
    _seed(profiles_env, original)

    store = profiles.load_store()
    by_name = {p["name"]: p for p in store["profiles"]}

    assert by_name["Turbo"]["fan_curve"] == profiles.TURBO_CURVE
    assert by_name["Balanced"]["fan_curve"] == profiles.BALANCED_CURVE
    assert by_name["My Turbo"]["fan_curve"] == user_curve
    assert store["active"] == "Turbo"
    backups = list(profiles_env.store.parent.glob("profiles.json.corrupt-*"))
    assert len(backups) == 1 and backups[0].read_text() == original
    saved = stored(profiles_env)
    assert [p["fan_curve"] for p in saved["profiles"][:4]] == [
        None, profiles.BALANCED_CURVE, profiles.TURBO_CURVE, None]
    assert saved["profiles"][-1]["fan_curve"] == user_curve


def test_curve_migration_is_idempotent(profiles_env):
    _seed(profiles_env, json.dumps(_legacy_builtin_store()))

    first = profiles.load_store()
    second = profiles.load_store()

    assert first == second
    assert len(list(profiles_env.store.parent.glob("profiles.json.corrupt-*"))) == 1


def test_curve_migration_preserves_edited_builtin_curve(profiles_env):
    old = _legacy_builtin_store()
    turbo = next(p for p in old["profiles"] if p["name"] == "Turbo")
    turbo["fan_curve"][0][1] = 55
    _seed(profiles_env, json.dumps(old))

    store = profiles.load_store()
    by_name = {p["name"]: p for p in store["profiles"]}

    assert by_name["Turbo"]["fan_curve"] == turbo["fan_curve"]
    assert by_name["Balanced"]["fan_curve"] == profiles.BALANCED_CURVE
    assert profiles.TURBO_CURVE == [
        [20, 50], [40, 50], [50, 75], [60, 90], [70, 100], [75, 100], [80, 100], [90, 100]]


@pytest.mark.parametrize("name,builtin,base", [
    ("Turbo", False, "performance"),
    ("Turbo", True, "balanced"),
    ("User Turbo", True, "performance"),
])
def test_curve_migration_ignores_nonmatching_builtin_identity(profiles_env, name, builtin, base):
    old = _legacy_builtin_store()
    turbo = next(p for p in old["profiles"] if p["name"] == "Turbo")
    turbo.update(name=name, builtin=builtin, base=base)
    expected = [point[:] for point in profiles.LEGACY_TURBO_CURVE]
    _seed(profiles_env, json.dumps(old))

    store = profiles.load_store()

    migrated = next(p for p in store["profiles"] if p["name"] == name)
    assert migrated["fan_curve"] == expected


def test_curve_migration_create_false_does_not_write(profiles_env):
    old = _legacy_builtin_store()
    original = json.dumps(old)
    _seed(profiles_env, original)

    store = profiles.load_store(create=False)

    assert next(p for p in store["profiles"] if p["name"] == "Turbo")["fan_curve"] == profiles.TURBO_CURVE
    assert profiles_env.store.read_text() == original
    assert not list(profiles_env.store.parent.glob("profiles.json.corrupt-*"))


def _raw_curve(curve):
    return [[temp, round(pwm * 255 / 100)] for temp, pwm in curve]


@pytest.mark.parametrize("name,old_curve,new_curve", [
    ("Turbo", profiles.LEGACY_TURBO_CURVE, profiles.TURBO_CURVE),
    ("Balanced", profiles.LEGACY_BALANCED_CURVE, profiles.BALANCED_CURVE),
])
def test_migrates_enabled_max_fans_snapshot_for_active_builtin(
        profiles_env, name, old_curve, new_curve):
    _seed(profiles_env, json.dumps({"version": 1, "active": name,
                                    "profiles": profiles.default_profiles()}))
    old_state = {"enabled": True, "saved_curve": _raw_curve(old_curve),
                 "other_setting": "preserved"}
    original = json.dumps(old_state, indent=2)
    profiles_env.max_fans.write_text(original)

    profiles.load_store()

    migrated = json.loads(profiles_env.max_fans.read_text())
    assert migrated == {"enabled": True, "saved_curve": _raw_curve(new_curve),
                        "other_setting": "preserved"}
    backups = list(profiles_env.max_fans.parent.glob("max-fans.json.before-idle-curve-*"))
    assert len(backups) == 1 and backups[0].read_text() == original
    profiles.load_store()
    assert len(list(profiles_env.max_fans.parent.glob("max-fans.json.before-idle-curve-*"))) == 1


def test_already_migrated_snapshot_keeps_store_load_lock_free(profiles_env, monkeypatch):
    _seed(profiles_env, json.dumps({"version": 1, "active": "Turbo",
                                    "profiles": profiles.default_profiles()}))
    profiles_env.max_fans.write_text(json.dumps(
        {"enabled": True, "saved_curve": _raw_curve(profiles.TURBO_CURVE)}))

    def unexpected_store_lock():
        raise AssertionError("steady-state profile loads should not take the store lock")

    monkeypatch.setattr(profiles, "store_lock", unexpected_store_lock)

    profiles.load_store()


@pytest.mark.parametrize("enabled,snapshot", [
    (False, _raw_curve(profiles.LEGACY_TURBO_CURVE)),
    (True, None),
    (True, [[25, 200]]),
])
def test_max_fans_snapshot_migration_preserves_off_null_and_unrelated_state(
        profiles_env, enabled, snapshot):
    _seed(profiles_env, json.dumps({"version": 1, "active": "Turbo",
                                    "profiles": profiles.default_profiles()}))
    original_state = {"enabled": enabled, "saved_curve": snapshot, "marker": 7}
    original = json.dumps(original_state, indent=2)
    profiles_env.max_fans.write_text(original)

    profiles.load_store()

    assert profiles_env.max_fans.read_text() == original
    assert not list(profiles_env.max_fans.parent.glob("max-fans.json.before-idle-curve-*"))


@pytest.mark.parametrize("profile_kind", ["custom", "edited_builtin"])
def test_max_fans_snapshot_migration_preserves_custom_or_edited_active_profile(
        profiles_env, profile_kind):
    store = {"version": 1, "active": "Turbo", "profiles": profiles.default_profiles()}
    turbo = next(p for p in store["profiles"] if p["name"] == "Turbo")
    if profile_kind == "custom":
        turbo["builtin"] = False
    else:
        turbo["fan_curve"] = [point[:] for point in turbo["fan_curve"]]
        turbo["fan_curve"][0][1] = 51
    _seed(profiles_env, json.dumps(store))
    original_state = {"enabled": True, "saved_curve": _raw_curve(profiles.LEGACY_TURBO_CURVE)}
    original = json.dumps(original_state, indent=2)
    profiles_env.max_fans.write_text(original)

    profiles.load_store()

    assert profiles_env.max_fans.read_text() == original
    assert not list(profiles_env.max_fans.parent.glob("max-fans.json.before-idle-curve-*"))


def test_max_fans_snapshot_migration_create_false_does_not_write(profiles_env):
    _seed(profiles_env, json.dumps({"version": 1, "active": "Turbo",
                                    "profiles": profiles.default_profiles()}))
    original_state = {"enabled": True, "saved_curve": _raw_curve(profiles.LEGACY_TURBO_CURVE)}
    original = json.dumps(original_state, indent=2)
    profiles_env.max_fans.write_text(original)

    profiles.load_store(create=False)

    assert profiles_env.max_fans.read_text() == original
    assert not list(profiles_env.max_fans.parent.glob("max-fans.json.before-idle-curve-*"))


# ----------------------------------------------------------------------
# set_active / upsert / delete
# ----------------------------------------------------------------------

def test_set_active_mirrors_legacy_name(profiles_env):
    profiles.set_active("Turbo")
    assert stored(profiles_env)["active"] == "Turbo"
    assert profiles_env.legacy.read_text().strip() == "maximum-throughput"


def test_set_active_non_legacy_name_leaves_legacy_file(profiles_env):
    profiles.set_active("Silent")
    profiles.set_active("Boondock")
    assert profiles.active_name() == "Boondock"
    assert profiles_env.legacy.read_text().strip() == "longevity-cool-stable"


def test_upsert_adds_custom_profile(profiles_env):
    store = profiles.upsert_profile(custom("Mine", brightness="low"))
    mine = profiles.get_profile("Mine", store)
    assert mine["builtin"] is False
    assert profiles.get_profile("Mine")["brightness"] == "low"     # persisted
    assert len(stored(profiles_env)["profiles"]) == 5


def test_upsert_replaces_by_name(profiles_env):
    profiles.upsert_profile(custom("Mine", battery_limit=60))
    profiles.upsert_profile(custom("Mine", battery_limit=90))
    mine = [p for p in profiles.list_profiles() if p["name"] == "Mine"]
    assert len(mine) == 1 and mine[0]["battery_limit"] == 90


def test_upsert_over_builtin_keeps_builtin_flag(profiles_env):
    edited = custom("Turbo", base="performance", battery_limit=70, builtin=False)
    profiles.upsert_profile(edited)
    turbo = profiles.get_profile("Turbo")
    assert turbo["builtin"] is True
    assert turbo["battery_limit"] == 70
    with pytest.raises(ValueError):
        profiles.delete_profile("Turbo")


def test_upsert_rejects_invalid_and_leaves_store_alone(profiles_env):
    profiles.load_store()
    before = profiles_env.store.read_text()
    with pytest.raises(ValueError):
        profiles.upsert_profile(custom("Bad", base="ludicrous"))
    assert profiles_env.store.read_text() == before


@pytest.mark.parametrize("name", ["Silent", "Balanced", "Turbo", "Boondock"])
def test_builtins_cannot_be_deleted(profiles_env, name):
    with pytest.raises(ValueError):
        profiles.delete_profile(name)
    assert profiles.get_profile(name) is not None


def test_delete_unknown_raises_keyerror(profiles_env):
    with pytest.raises(KeyError):
        profiles.delete_profile("Nope")


def test_delete_active_falls_back_to_balanced(profiles_env):
    profiles.upsert_profile(custom("Mine"))
    profiles.set_active("Mine")
    store = profiles.delete_profile("Mine")
    assert store["active"] == "Balanced"
    assert profiles.active_name() == "Balanced"
    assert profiles.get_profile("Mine") is None


def test_delete_inactive_keeps_active(profiles_env):
    profiles.upsert_profile(custom("Mine"))
    profiles.set_active("Turbo")
    profiles.delete_profile("Mine")
    assert profiles.active_name() == "Turbo"


# ----------------------------------------------------------------------
# validate / validate_curve
# ----------------------------------------------------------------------

@pytest.mark.parametrize("over", [
    {},
    {"tdp": {"pl1": 5, "pl2": 93, "pl3": 93, "force": False}},
    {"tdp": {"pl1": 75, "pl2": 80, "pl3": 90}},
    {"tdp": {"pl1": 93, "pl2": 93, "pl3": 93, "force": True}},
    {"fan_curve": GOOD_CURVE},
    {"undervolt": 0}, {"undervolt": -40},
    {"brightness": "off"}, {"brightness": "high"},
    {"battery_limit": 40}, {"battery_limit": 100},
])
def test_validate_accepts(over):
    profiles.validate(custom(**over))


@pytest.mark.parametrize("over,msg", [
    ({"name": ""}, "name"),
    ({"name": "   "}, "name"),
    ({"base": "custom"}, "base"),
    ({"base": None}, "base"),
    ({"tdp": {"pl1": 4, "pl2": 10, "pl3": 10}}, "pl1"),
    ({"tdp": {"pl1": 10, "pl2": 94, "pl3": 10}}, "pl2"),
    ({"tdp": {"pl1": 10, "pl2": 10}}, "pl3"),
    ({"tdp": {"pl1": 10.5, "pl2": 10, "pl3": 10}}, "pl1"),
    ({"tdp": {"pl1": 76, "pl2": 80, "pl3": 90}}, "force"),
    ({"tdp": {"pl1": 76, "pl2": 80, "pl3": 90, "force": False}}, "force"),
    ({"undervolt": 1}, "undervolt"),
    ({"undervolt": -41}, "undervolt"),
    ({"undervolt": "-10"}, "undervolt"),
    ({"brightness": "max"}, "brightness"),
    ({"battery_limit": 39}, "battery_limit"),
    ({"battery_limit": 101}, "battery_limit"),
    ({"battery_limit": "80"}, "battery_limit"),
    ({"fan_curve": GOOD_CURVE[:7]}, "8 points"),
])
def test_validate_rejects(over, msg):
    with pytest.raises(ValueError, match=msg):
        profiles.validate(custom(**over))


def test_validate_curve_none_is_fine():
    profiles.validate_curve(None)


@pytest.mark.parametrize("mutate,msg", [
    (lambda c: c.__setitem__(0, [19, 20]), "out of range"),
    (lambda c: c.__setitem__(7, [111, 100]), "out of range"),
    (lambda c: c.__setitem__(3, [70, 101]), "out of range"),
    (lambda c: c.__setitem__(0, [40, -1]), "out of range"),
    (lambda c: c.__setitem__(2, [45, 55]), "temps"),
    (lambda c: c.__setitem__(4, [75, 50]), "speeds"),
    (lambda c: c.append([95, 100]), "8 points"),
])
def test_validate_curve_rejects(mutate, msg):
    curve = [list(p) for p in GOOD_CURVE]
    mutate(curve)
    with pytest.raises(ValueError, match=msg):
        profiles.validate_curve(curve)


def test_validate_curve_allows_flat_segments():
    profiles.validate_curve([[50, 100]] * 8)


# ----------------------------------------------------------------------
# apply_by_name
# ----------------------------------------------------------------------

def test_apply_by_name_sets_active_and_runs_previous_exit_hook(profiles_env, fake_host):
    profiles.set_active("Boondock")
    profiles.apply_by_name("Balanced", z13ctl="z13ctl")
    assert fake_host.argvs[0][0] == "bash" and fake_host.argvs[0][-1].endswith("boondock session-off")
    assert profiles.active_name() == "Balanced"


def test_apply_by_name_keeps_active_when_profile_write_fails(profiles_env, fake_host):
    profiles.set_active("Silent")
    fake_host.fail(lambda a: "profile" in a)
    profiles.apply_by_name("Turbo", z13ctl="z13ctl")
    assert profiles.active_name() == "Silent"


def test_apply_by_name_unknown(profiles_env, fake_host):
    with pytest.raises(KeyError):
        profiles.apply_by_name("Nope")


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

@pytest.fixture
def cli_host(profiles_env, monkeypatch, fake_host):
    """main() with the fake host and a plain `z13ctl` path (wraps profiles.apply)."""
    real_apply = profiles.apply
    monkeypatch.setattr(profiles, "apply",
                        lambda p, **kw: real_apply(p, z13ctl="z13ctl", **kw))
    return fake_host


def bash_calls(fake_host):
    return [a[-1] for a in fake_host.argvs if a[0] == "bash"]


def test_cli_list(profiles_env, capsys):
    profiles.set_active("Turbo")
    assert profiles.main(["list"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "* Turbo  (base: performance)" in lines
    assert "  Silent  (base: quiet)" in lines


def test_cli_list_json(profiles_env, capsys):
    assert profiles.main(["list", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["active"] == "Balanced"
    assert len(data["profiles"]) == 4


def test_cli_active(profiles_env, capsys):
    profiles.set_active("Silent")
    assert profiles.main(["active"]) == 0
    assert capsys.readouterr().out.strip() == "Silent"


def test_cli_show(profiles_env, capsys):
    assert profiles.main(["show", "Turbo"]) == 0
    p = json.loads(capsys.readouterr().out)
    assert p["name"] == "Turbo" and p["base"] == "performance"


def test_cli_show_unknown(profiles_env, capsys):
    assert profiles.main(["show", "Nope"]) == 1
    assert "Nope" in capsys.readouterr().err


def test_cli_apply_prints_one_line_per_step(profiles_env, cli_host, capsys):
    assert profiles.main(["apply", "Silent"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Applying profile: Silent"
    assert any(line.lstrip().startswith("ok ") and "profile" in line for line in out[1:])
    assert profiles.active_name() == "Silent"


def test_cli_apply_returns_1_on_any_failed_step(profiles_env, cli_host, capsys):
    cli_host.fail(lambda a: "lightbar" in a)
    assert profiles.main(["apply", "Silent"]) == 1
    assert any(line.lstrip().startswith("ERR") for line in capsys.readouterr().out.splitlines())


def test_cli_apply_unknown(profiles_env, cli_host, capsys):
    assert profiles.main(["apply", "Nope"]) == 1
    assert cli_host.argvs == []


def test_cli_apply_runs_hooks(profiles_env, cli_host):
    profiles.main(["apply", "Boondock"])
    assert len(bash_calls(cli_host)) == 1 and bash_calls(cli_host)[0].endswith("boondock session-on")


def test_cli_apply_no_hooks(profiles_env, cli_host):
    profiles.main(["apply", "Boondock", "--no-hooks"])
    assert bash_calls(cli_host) == []
    assert profiles.active_name() == "Boondock"


def test_cli_restore_reapplies_active_without_hooks(profiles_env, cli_host, capsys):
    profiles.set_active("Boondock")
    assert profiles.main(["restore"]) == 0
    assert "Applying profile: Boondock" in capsys.readouterr().out
    assert bash_calls(cli_host) == []               # no `boondock on` at login
    assert ["z13ctl", "profile", "--set", "quiet"] in cli_host.argvs


def test_cli_restore_with_no_active_falls_back_to_a_profile(profiles_env, cli_host, capsys):
    """A store with no usable active name (or no profiles) is repaired at load, so
    restore always has a profile to apply (#14)."""
    profiles_env.store.parent.mkdir(parents=True)
    profiles_env.store.write_text(json.dumps({"version": 1, "active": None, "profiles": []}))
    assert profiles.main(["restore"]) == 0
    captured = capsys.readouterr()
    assert "no active profile" not in captured.err
    assert "Applying profile: Balanced" in captured.out
    assert profiles.active_name() == "Balanced"


def test_cli_requires_a_command(profiles_env):
    with pytest.raises(SystemExit):
        profiles.main([])


def test_print_results_format_parsed_by_install_check(capsys):
    """install_check.check_restore parses this output; keep the formats in step."""
    import re
    rc = profiles._print_results([("brightness (lightbar)", False, "timed out"),
                                  ("profile", True, "quiet")])
    assert rc == 1
    pat = re.compile(r"\s*(ok|ERR)\s+(.+?)\s{2,}(.*)$")
    rows = [pat.match(line).groups() for line in capsys.readouterr().out.splitlines()]
    assert rows == [("ERR", "brightness (lightbar)", "timed out"), ("ok", "profile", "quiet")]


def test_cli_restore_profile_without_limit_falls_back_to_80(profiles_env, cli_host, capsys):
    # Boondock sets no battery_limit. Login must still write the 80% fallback, since
    # the 80% unit that used to cover it is retired.
    profiles.set_active("Boondock")
    assert profiles.main(["restore"]) == 0
    assert ["z13ctl", "batterylimit", "--set", "80"] in cli_host.argvs
    assert "battery limit" in capsys.readouterr().out


# ----------------------------------------------------------------------
# well-formed JSON of the wrong shape (#14)
# ----------------------------------------------------------------------

def _seed(paths, content):
    paths.store.parent.mkdir(parents=True, exist_ok=True)
    paths.store.write_text(content)
    return content


def _backups(paths):
    return list(paths.store.parent.glob("profiles.json.corrupt-*"))


@pytest.mark.parametrize("content", ['{"profiles":[null]}', '{"profiles":[]}',
                                     '{"profiles":[1,"x",{"name":""}]}'])
def test_structurally_empty_store_is_kept_aside_and_reseeded(profiles_env, content):
    _seed(profiles_env, content)
    store = profiles.load_store()
    assert [p["name"] for p in store["profiles"]] == ["Silent", "Balanced", "Turbo", "Boondock"]
    assert store["active"] == "Balanced"
    kept = _backups(profiles_env)
    assert len(kept) == 1 and kept[0].read_text() == content
    assert stored(profiles_env) == store


def test_null_entry_among_good_profiles_is_dropped_others_kept(profiles_env):
    content = _seed(profiles_env, json.dumps(
        {"version": 1, "active": "Mine", "profiles": [custom(max_fans=False), None, custom("Bad", base="nope")]}))
    store = profiles.load_store()
    assert [p["name"] for p in store["profiles"]] == ["Mine"]
    assert store["active"] == "Mine"
    kept = _backups(profiles_env)
    assert len(kept) == 1 and kept[0].read_text() == content
    assert [p["name"] for p in stored(profiles_env)["profiles"]] == ["Mine"]


def test_active_name_that_does_not_exist_falls_back(profiles_env):
    content = _seed(profiles_env, json.dumps(
        {"version": 1, "active": "Ghost", "profiles": [custom("Mine"), custom("Balanced")]}))
    store = profiles.load_store()
    assert store["active"] == "Balanced"
    assert profiles.get_profile(store["active"], store) is not None
    assert _backups(profiles_env)[0].read_text() == content


def test_active_fallback_without_balanced_uses_first_profile(profiles_env):
    _seed(profiles_env, json.dumps({"active": 7, "profiles": [custom("Mine")]}))
    assert profiles.load_store()["active"] == "Mine"


def test_non_string_hook_drops_that_profile(profiles_env):
    _seed(profiles_env, json.dumps({"active": "Mine", "profiles": [
        custom("Mine"), custom("Hooky", hooks={"enter": 5, "exit": ""})]}))
    assert [p["name"] for p in profiles.load_store()["profiles"]] == ["Mine"]


def test_duplicate_names_keep_first(profiles_env):
    _seed(profiles_env, json.dumps({"active": "Mine", "profiles": [custom("Mine"), custom("Mine", base="quiet")]}))
    got = profiles.load_store()["profiles"]
    assert len(got) == 1 and got[0]["base"] == "balanced"


def test_unsorted_fan_curve_drops_that_profile(profiles_env):
    bad = [[90, 100]] + GOOD_CURVE[:7]
    _seed(profiles_env, json.dumps({"active": "Mine", "profiles": [custom("Mine"), custom("Curvy", fan_curve=bad)]}))
    assert [p["name"] for p in profiles.load_store()["profiles"]] == ["Mine"]


def test_wrong_shape_read_only_writes_nothing(profiles_env):
    content = _seed(profiles_env, '{"profiles":[null]}')
    store = profiles.load_store(create=False)
    assert store["active"] == "Balanced"
    assert profiles_env.store.read_text() == content and not _backups(profiles_env)


def test_clean_store_makes_no_backup(profiles_env):
    _seed(profiles_env, json.dumps({"version": 1, "active": "Mine", "profiles": [custom(max_fans=False)]}))
    profiles.load_store()
    assert not _backups(profiles_env)


@pytest.mark.parametrize("bad", [[{}] * 8, [[float("inf"), 1]] + GOOD_CURVE[1:]])
def test_exotic_bad_fan_curve_drops_entry_without_raising(profiles_env, bad):
    _seed(profiles_env, json.dumps({"active": "Mine", "profiles": [custom("Mine"), custom("Odd", fan_curve=bad)]}))
    assert [p["name"] for p in profiles.load_store()["profiles"]] == ["Mine"]


def test_backups_never_overwrite_each_other(profiles_env, monkeypatch):
    monkeypatch.setattr(profiles.time, "strftime", lambda fmt: "20260101-000000")
    monkeypatch.setattr(profiles.time, "time_ns", lambda: 1_000_000)
    monkeypatch.setattr(profiles.os, "getpid", lambda: 1)
    for content in ('{"profiles":[null]}', '{"profiles":[1]}'):
        _seed(profiles_env, content)
        profiles.load_store()
    assert sorted(b.read_text() for b in _backups(profiles_env)) == ['{"profiles":[1]}', '{"profiles":[null]}']

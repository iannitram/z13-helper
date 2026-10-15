"""profiles.py store, validation, apply_by_name and the z13-profile CLI."""

import json

import pytest

import profiles
from conftest import FakeRunner

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
    boondock = next(p for p in defaults if p["name"] == "Boondock")
    assert boondock["hooks"]["enter"].endswith("boondock on")
    assert boondock["hooks"]["exit"].endswith("boondock off")


def test_builtin_names_follow_ghelper():
    assert profiles.BUILTIN_FOR_BASE == {"quiet": "Silent", "balanced": "Balanced",
                                         "performance": "Turbo"}
    assert set(profiles.LEGACY_NAMES_REV) == {"Turbo", "Balanced", "Silent"}


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

def test_apply_by_name_sets_active_and_runs_previous_exit_hook(profiles_env):
    profiles.set_active("Boondock")
    runner = FakeRunner()
    profiles.apply_by_name("Balanced", runner=runner, z13ctl="z13ctl")
    assert runner.calls[0][0] == "bash" and runner.calls[0][-1].endswith("boondock off")
    assert profiles.active_name() == "Balanced"


def test_apply_by_name_keeps_active_when_profile_write_fails(profiles_env):
    profiles.set_active("Silent")
    runner = FakeRunner().fail(lambda a: "profile" in a)
    profiles.apply_by_name("Turbo", runner=runner, z13ctl="z13ctl")
    assert profiles.active_name() == "Silent"


def test_apply_by_name_unknown(profiles_env):
    with pytest.raises(KeyError):
        profiles.apply_by_name("Nope", runner=FakeRunner())


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

@pytest.fixture
def cli_runner(profiles_env, monkeypatch):
    """main() takes no runner; inject one by wrapping profiles.apply."""
    runner = FakeRunner()
    real_apply = profiles.apply
    monkeypatch.setattr(profiles, "apply",
                        lambda p, **kw: real_apply(p, runner=runner, z13ctl="z13ctl", **kw))
    return runner


def bash_calls(runner):
    return [a[-1] for a in runner.calls if a[0] == "bash"]


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


def test_cli_apply_prints_one_line_per_step(profiles_env, cli_runner, capsys):
    assert profiles.main(["apply", "Silent"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "Applying profile: Silent"
    assert any(line.lstrip().startswith("ok ") and "profile" in line for line in out[1:])
    assert profiles.active_name() == "Silent"


def test_cli_apply_returns_1_on_any_failed_step(profiles_env, cli_runner, capsys):
    cli_runner.fail(lambda a: "lightbar" in a)
    assert profiles.main(["apply", "Silent"]) == 1
    assert any(line.lstrip().startswith("ERR") for line in capsys.readouterr().out.splitlines())


def test_cli_apply_unknown(profiles_env, cli_runner, capsys):
    assert profiles.main(["apply", "Nope"]) == 1
    assert cli_runner.calls == []


def test_cli_apply_runs_hooks(profiles_env, cli_runner):
    profiles.main(["apply", "Boondock"])
    assert len(bash_calls(cli_runner)) == 1 and bash_calls(cli_runner)[0].endswith("boondock on")


def test_cli_apply_no_hooks(profiles_env, cli_runner):
    profiles.main(["apply", "Boondock", "--no-hooks"])
    assert bash_calls(cli_runner) == []
    assert profiles.active_name() == "Boondock"


def test_cli_restore_reapplies_active_without_hooks(profiles_env, cli_runner, capsys):
    profiles.set_active("Boondock")
    assert profiles.main(["restore"]) == 0
    assert "Applying profile: Boondock" in capsys.readouterr().out
    assert bash_calls(cli_runner) == []               # no `boondock on` at login
    assert ["z13ctl", "profile", "--set", "quiet"] in cli_runner.calls


def test_cli_restore_with_no_active(profiles_env, cli_runner, capsys):
    profiles_env.store.parent.mkdir(parents=True)
    profiles_env.store.write_text(json.dumps({"version": 1, "active": None, "profiles": []}))
    assert profiles.main(["restore"]) == 1
    assert "no active profile" in capsys.readouterr().err


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

"""Boondock's TDP and the never-report-what-did-not-apply rule (#203).

The built-in used to ask for 20/30/40 W, below the asus-armoury floors
(28/32/45 W on the GZ302EA). The firmware clamped the write while the app went
on showing 20/30/40 W. Now every TDP write is clamped into the detected ranges
before it is sent, the written values are what gets reported, and stores and
named profiles saved by older builds are migrated. No hardware is touched.
"""

import json
from pathlib import Path

import pytest

import power_range
import profiles
import state_authority as authority
import z13ctl_client
from test_state_authority import Daemon
from z13ctl_client import Z13CtlClient

Z = "z13ctl"
FLOORS = power_range.ARMOURY_RANGES          # PL1 28-80, PL2 32-92, PL3 45-93 W
LEGACY = {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}
BOON = Z13CtlClient.backend_profile_name("Boondock")


@pytest.fixture
def armoury(monkeypatch):
    """The ranges this device reports (metadata only), for both write paths."""
    monkeypatch.setattr(profiles, "_tdp_ranges", lambda: dict(FLOORS))
    monkeypatch.setattr(z13ctl_client, "_tdp_ranges", lambda: dict(FLOORS))


def boondock(store=None):
    return next(p for p in (store or profiles.load_store())["profiles"]
                if p["name"] == "Boondock")


# ---- the built-in ----

def test_builtin_boondock_is_within_the_firmware_ranges():
    tdp = boondock({"profiles": profiles.default_profiles()})["tdp"]
    assert tdp == {"pl1": 28, "pl2": 32, "pl3": 45, "force": False}
    assert power_range.fit_tdp(tdp, FLOORS) == (tdp, "")


def test_builtin_and_boondock_script_agree():
    script = (Path(__file__).resolve().parent.parent / "tooling" / "boondock").read_text()
    t = profiles.BOONDOCK_TDP
    assert f"TDP_PL1={t['pl1']}; TDP_PL2={t['pl2']}; TDP_PL3={t['pl3']}" in script


# ---- clamping ----

@pytest.mark.parametrize("asked, written", [
    ((20, 30, 40), (28, 32, 45)),       # the old built-in: every rail up to its floor
    ((50, 30, 40), (50, 50, 50)),       # a lower rail pushes the higher ones up
    ((80, 92, 93), (80, 92, 93)),       # the ceiling is accepted as is
    ((5, 93, 93), (28, 92, 93)),        # PL2 above its maximum comes down
])
def test_fit_tdp_clamps_and_keeps_rail_order(asked, written):
    tdp = dict(zip(("pl1", "pl2", "pl3"), asked), force=False)
    fitted, note = power_range.fit_tdp(tdp, FLOORS)
    assert (fitted["pl1"], fitted["pl2"], fitted["pl3"]) == written
    assert bool(note) == (asked != written)
    if note:
        assert note == f"requested {asked[0]}/{asked[1]}/{asked[2]} W, outside the firmware range"


def test_fit_tdp_keeps_force_only_above_75_w():
    assert power_range.fit_tdp({"pl1": 90, "pl2": 92, "pl3": 93, "force": True},
                               FLOORS)[0]["force"] is True
    assert power_range.fit_tdp({"pl1": 70, "pl2": 92, "pl3": 93, "force": True},
                               FLOORS)[0]["force"] is False


def test_fit_tdp_leaves_the_stored_dict_alone():
    tdp = dict(LEGACY)
    power_range.fit_tdp(tdp, FLOORS)
    assert tdp == LEGACY


def test_legacy_apply_writes_and_reports_the_clamped_limits(profiles_env, fake_host, armoury):
    p = {"name": "Low", "base": "quiet", "tdp": dict(LEGACY)}
    results = profiles.apply_tdp(p, z13ctl=Z)
    assert fake_host.argvs == [[Z, "tdp", "--set", "28", "--pl2", "32", "--pl3", "45"]]
    assert results == [("tdp", True,
                        "28/32/45 W (requested 20/30/40 W, outside the firmware range)")]


def test_legacy_apply_in_range_reports_plain_values(profiles_env, fake_host, armoury):
    results = profiles.apply_tdp({"tdp": {"pl1": 30, "pl2": 40, "pl3": 50}}, z13ctl=Z)
    assert fake_host.argvs == [[Z, "tdp", "--set", "30", "--pl2", "40", "--pl3", "50"]]
    assert results == [("tdp", True, "30/40/50 W")]


def test_legacy_apply_failure_reports_no_limit(profiles_env, fake_host, armoury):
    fake_host.fail("tdp --set", rc=1, out="rejected")
    assert profiles.apply_tdp({"tdp": dict(LEGACY)}, z13ctl=Z) == [("tdp", False, "rejected")]


@pytest.fixture
def daemon(monkeypatch, profiles_env):
    d = Daemon()
    profiles.save_store({"version": 1, "active": "Balanced",
                         "profiles": profiles.default_profiles()})
    monkeypatch.setattr(authority, "named_client", lambda: d)
    return d


def test_named_apply_stores_and_reports_what_applied(daemon, fake_host, armoury):
    """A user profile below the floors: the named profile gets the clamped
    limits, the apply says so, and the daemon snapshot mirrors them back into
    the store, so every view shows 28/32/45 W."""
    low = {"name": "Low", "builtin": False, "base": "quiet", "tdp": dict(LEGACY),
           "fan_curve": None, "undervolt": None, "brightness": None,
           "battery_limit": None, "hooks": {"enter": "", "exit": ""}}
    profiles.upsert_profile(low)
    results = profiles.apply(low, skip_hooks=True)

    name = Z13CtlClient.backend_profile_name("Low")
    assert daemon.state["custom_profiles"][name]["tdp"] == {
        "pl1_spl": 28, "pl2_sppt": 32, "fppt": 45}
    assert ("tdp", True,
            "28/32/45 W (requested 20/30/40 W, outside the firmware range)") in results

    profiles.sync_active_from_backend(daemon.get_state())
    assert profiles.get_profile("Low")["tdp"] == {"pl1": 28, "pl2": 32, "pl3": 45,
                                                  "force": False}


def test_named_apply_in_range_adds_no_tdp_step(daemon, fake_host, armoury):
    results = profiles.apply(profiles.get_profile("Boondock"), skip_hooks=True)
    assert "tdp" not in [step for step, _, _ in results]
    assert daemon.state["custom_profiles"][BOON]["tdp"] == {
        "pl1_spl": 28, "pl2_sppt": 32, "fppt": 45}


# ---- migration of saved stores ----

def _seed(profiles_env, tdp):
    store = {"version": 1, "active": "Balanced", "profiles": profiles.default_profiles()}
    boondock(store)["tdp"] = tdp
    profiles_env.store.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.store.write_text(json.dumps(store))


def test_store_with_the_old_builtin_is_migrated_with_a_backup(profiles_env):
    _seed(profiles_env, dict(LEGACY))
    assert boondock()["tdp"] == profiles.BOONDOCK_TDP
    on_disk = json.loads(profiles_env.store.read_text())
    assert boondock(on_disk)["tdp"] == profiles.BOONDOCK_TDP
    backups = list(profiles_env.store.parent.glob("profiles.json.corrupt-*"))
    assert len(backups) == 1
    assert boondock(json.loads(backups[0].read_text()))["tdp"] == LEGACY


def test_user_edited_boondock_tdp_is_left_alone(profiles_env):
    mine = {"pl1": 35, "pl2": 40, "pl3": 50, "force": False}
    _seed(profiles_env, dict(mine))
    assert boondock()["tdp"] == mine
    assert not list(profiles_env.store.parent.glob("profiles.json.corrupt-*"))


def test_custom_profile_with_the_old_values_is_not_migrated(profiles_env):
    store = {"version": 1, "active": "Balanced", "profiles": profiles.default_profiles()}
    store["profiles"].append({"name": "Mine", "builtin": False, "base": "quiet",
                              "tdp": dict(LEGACY), "hooks": {"enter": "", "exit": ""}})
    profiles_env.store.parent.mkdir(parents=True, exist_ok=True)
    profiles_env.store.write_text(json.dumps(store))
    assert profiles.get_profile("Mine")["tdp"] == LEGACY


# ---- migration of the mirrored z13ctl named profile ----

def _old_mirror(daemon):
    daemon.state["custom_profiles"][BOON] = {
        "name": BOON, "tdp": {"pl1_spl": 20, "pl2_sppt": 30, "fppt": 40}}


def test_inactive_old_mirror_is_rebuilt_save_only(daemon, fake_host, armoury):
    _old_mirror(daemon)
    authority.snapshot(daemon)
    assert daemon.state["custom_profiles"][BOON]["tdp"] == {
        "pl1_spl": 28, "pl2_sppt": 32, "fppt": 45}
    assert daemon.state["profile"] != BOON
    assert "profile" not in [cmd for cmd, _ in daemon.calls]   # nothing selected
    assert fake_host.argvs == [["cat", "/sys/firmware/acpi/platform_profile"]]


def test_current_mirror_is_not_rewritten(daemon, fake_host, armoury):
    daemon.state["custom_profiles"][BOON] = {
        "name": BOON, "tdp": {"pl1_spl": 28, "pl2_sppt": 32, "fppt": 45}}
    authority.snapshot(daemon)
    assert [cmd for cmd, _ in daemon.calls] == ["get-state"]


def test_active_old_mirror_is_left_but_the_store_keeps_the_new_builtin(
        daemon, fake_host, armoury):
    _old_mirror(daemon)
    daemon.state["profile"] = BOON
    authority.snapshot(daemon)
    assert [cmd for cmd, _ in daemon.calls] == ["get-state"]   # never rebuilds an active one
    store = profiles.load_store()
    assert store["active"] == "Boondock"
    assert boondock(store)["tdp"] == profiles.BOONDOCK_TDP


def test_stale_mirrors_is_empty_without_a_daemon_snapshot(profiles_env):
    assert profiles.stale_mirrors(None) == []
    assert profiles.stale_mirrors({"profile": "quiet"}) == []

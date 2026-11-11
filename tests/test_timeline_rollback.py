"""Timeline rollback (#135): the allowlist, and the desktop rollback's write path.

No hardware: every z13ctl call goes to a FakeHost, every file to tmp_path, and the
Qt dialog is never built (DashboardUI methods run against a small stand-in).
"""

import types

import pytest

import max_fans_state
import profiles
import timeline
from test_timeline_r4 import dashboard  # noqa: F401  (fixture: dashboard_ui, Qt stubbed if absent)


# ----------------------------------------------------------------------
# rollback_spec: what the persisted record may ask the app to do
# ----------------------------------------------------------------------

def _rb(**kw):
    return {"rollback": kw}


def _snap(name="Silent"):
    return {"name": name}


@pytest.mark.parametrize("entry", [
    _rb(kind="profile", profile="Silent", snapshot=_snap("Silent")),
    _rb(kind="goal", goal="max"),
    _rb(kind="goal", goal="custom"),
    _rb(kind="tdp", state={"tdp": None}),
    _rb(kind="tdp", state={"tdp": {"pl1": 5, "pl2": 5, "pl3": 5}}),
    _rb(kind="tdp", state={"tdp": {"pl1": 20, "pl2": 30, "pl3": 93, "force": True}}),
    _rb(kind="battery_limit", state={"battery_limit": 40}),
    _rb(kind="battery_limit", state={"battery_limit": 100}),
    _rb(kind="brightness", state={"brightness": "off"}),
    _rb(kind="brightness", state={"brightness": "high"}),
])
def test_spec_accepts_valid_records(entry):
    assert timeline.rollback_spec(entry) is not None


@pytest.mark.parametrize("entry", [
    None, [], "profile", 5,                       # not an entry
    {}, {"rollback": None}, {"rollback": []},     # no record
    _rb(kind="files", backup_dir="/etc"),         # backup records are never run
    _rb(kind="shell", cmd="rm -rf /"),
    _rb(),
    _rb(kind="profile", profile="Silent", snapshot=_snap("Other")),   # name mismatch
    _rb(kind="profile", profile="Silent"),                            # no snapshot
    _rb(kind="profile", profile="Silent", snapshot="Silent"),
    _rb(kind="profile", profile="-Silent", snapshot=_snap("-Silent")),  # option-like name
    _rb(kind="profile", profile="", snapshot=_snap("")),
    _rb(kind="profile", profile=7, snapshot=_snap(7)),
    _rb(kind="goal", goal="turbo"),
    _rb(kind="goal", goal=None),
    _rb(kind="tdp", state={"tdp": {"pl1": 4, "pl2": 30, "pl3": 40}}),     # below 5
    _rb(kind="tdp", state={"tdp": {"pl1": 20, "pl2": 30, "pl3": 94}}),    # above 93
    _rb(kind="tdp", state={"tdp": {"pl1": 40, "pl2": 30, "pl3": 50}}),    # pl1 > pl2
    _rb(kind="tdp", state={"tdp": {"pl1": 20, "pl2": 50, "pl3": 40}}),    # pl2 > pl3
    _rb(kind="tdp", state={"tdp": {"pl1": 20, "pl2": 30}}),               # missing rail
    _rb(kind="tdp", state={"tdp": {"pl1": 20.0, "pl2": 30, "pl3": 40}}),  # not int
    _rb(kind="tdp", state={"tdp": {"pl1": True, "pl2": 30, "pl3": 40}}),
    _rb(kind="tdp", state={"tdp": {"pl1": 20, "pl2": 30, "pl3": 40, "extra": 1}}),
    _rb(kind="tdp", state={"tdp": {"pl1": 20, "pl2": 30, "pl3": 40, "force": "yes"}}),
    _rb(kind="tdp", state={"tdp": 25}),
    _rb(kind="tdp", state={}),
    _rb(kind="tdp", state={"tdp": None, "battery_limit": 80}),            # extra key
    _rb(kind="tdp", state=None),
    _rb(kind="battery_limit", state={"battery_limit": 39}),
    _rb(kind="battery_limit", state={"battery_limit": 101}),
    _rb(kind="battery_limit", state={"battery_limit": "80"}),
    _rb(kind="battery_limit", state={"battery_limit": True}),
    _rb(kind="battery_limit", state={"battery_limit": None}),
    _rb(kind="battery_limit", state={"tdp": None}),                       # wrong key for kind
    _rb(kind="brightness", state={"brightness": "max"}),
    _rb(kind="brightness", state={"brightness": None}),
    _rb(kind="brightness", state={"brightness": 3}),
])
def test_spec_rejects_unsupported_or_malformed_records(entry):
    assert timeline.rollback_spec(entry) is None


def test_spec_returns_only_the_allowlisted_fields():
    spec = timeline.rollback_spec(_rb(kind="profile", profile="Silent", snapshot=_snap("Silent"),
                                      cmd="rm -rf /", backup_dir="/etc"))
    assert spec == {"kind": "profile", "profile": "Silent"}


def test_spec_tdp_defaults_force_false_and_keeps_it_when_set():
    base = {"pl1": 20, "pl2": 30, "pl3": 40}
    assert timeline.rollback_spec(_rb(kind="tdp", state={"tdp": base}))["value"] == {**base, "force": False}
    forced = timeline.rollback_spec(_rb(kind="tdp", state={"tdp": {**base, "force": True}}))
    assert forced["value"] == {**base, "force": True}


def test_a_real_recorded_tdp_change_is_rollbackable(tmp_path):
    """Stored profile TDPs carry "force"; the record built from one must pass the allowlist."""
    path = str(tmp_path / "t.jsonl")
    rec = timeline.record_setting_change(
        kind="tdp", summary="TDP 25/30/40 W",
        before={"tdp": {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}},
        after={"tdp": {"pl1": 25, "pl2": 30, "pl3": 40, "force": False}}, path=path)
    assert timeline.rollback_spec(rec)["value"] == {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}


# ----------------------------------------------------------------------
# DashboardUI._rollback_timeline_entry
# ----------------------------------------------------------------------

class _FakeControl:
    """Runs submitted work inline, like the hardware worker would, and keeps the outcome."""

    def __init__(self, allowed=True):
        self.allowed = allowed
        self.submitted = []
        self.results = []

    def writes_allowed(self):
        return self.allowed

    def _submit(self, label, fn):
        self.submitted.append(label)
        try:
            self.results.append(fn())
        except Exception as exc:  # the worker hands the exception to the status bar
            self.results.append(exc)


def _fake_ui(allowed=True):
    return types.SimpleNamespace(control=_FakeControl(allowed))


def _store(profiles_env, active="Balanced"):
    store = {"version": 1, "active": active, "profiles": profiles.default_profiles()}
    for p in store["profiles"]:
        if p["name"] == "Balanced":
            p["tdp"] = {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}
            p["battery_limit"] = 80
            p["brightness"] = "medium"
    profiles.save_store(store)


def _rollback(dashboard, ui, entry):
    out = dashboard.DashboardUI._rollback_timeline_entry(ui, entry)
    timeline.flush(10)
    return out


def _entries():
    timeline.flush(10)
    return timeline.read_entries()


def _setting_entry(kind, value):
    return {"id": "old", "rollback": {"kind": kind, "state": {kind: value}}}


def test_unsupported_record_is_refused_and_nothing_is_queued(dashboard, profiles_env, fake_host):
    ui = _fake_ui()
    ok, message = _rollback(dashboard, ui, {"rollback": {"kind": "shell", "cmd": "reboot"}})
    assert not ok and "not supported" in message
    assert ui.control.submitted == [] and fake_host.calls == []


def test_unsupported_machine_is_refused_before_any_work(dashboard, profiles_env, fake_host):
    ui = _fake_ui(allowed=False)
    ok, message = _rollback(dashboard, ui, _setting_entry("battery_limit", 80))
    assert not ok and "Unsupported machine" in message
    assert ui.control.submitted == [] and fake_host.calls == []


def test_rollback_is_queued_on_the_hardware_worker_not_run_in_the_caller(dashboard, profiles_env, fake_host):
    """The dialog callback returns at once; the apply runs where the worker runs it."""
    _store(profiles_env)

    class Deferred(_FakeControl):
        def _submit(self, label, fn):
            self.submitted.append(label)   # keep the work, do not run it

    ui = types.SimpleNamespace(control=Deferred())
    ok, message = dashboard.DashboardUI._rollback_timeline_entry(ui, _setting_entry("battery_limit", 85))
    assert ok and "queued" in message
    assert ui.control.submitted == ["Timeline rollback"]
    assert fake_host.calls == [], "no hardware call before the worker runs the job"


def test_setting_rollback_writes_and_adds_its_own_entry(dashboard, profiles_env, fake_host):
    _store(profiles_env)
    ui = _fake_ui()
    ok, _ = _rollback(dashboard, ui, _setting_entry("battery_limit", 85))
    assert ok and not isinstance(ui.control.results[0], Exception)
    assert any("battery" in " ".join(c.argv) for c in fake_host.calls)
    (entry,) = _entries()
    assert entry["kind"] == "battery_limit" and entry["ok"] is True
    assert entry["after"] == {"battery_limit": 85} and entry["before"] == {"battery_limit": 80}
    assert profiles.get_profile("Balanced")["battery_limit"] == 85


def test_tdp_rollback_restores_the_rails_and_adds_its_own_entry(dashboard, profiles_env, fake_host):
    _store(profiles_env)
    ui = _fake_ui()
    old = {"pl1": 15, "pl2": 25, "pl3": 35, "force": False}
    ok, _ = _rollback(dashboard, ui, _setting_entry("tdp", old))
    assert ok and not isinstance(ui.control.results[0], Exception)
    assert any(c.argv[:1] and "tdp" in c.argv for c in fake_host.calls)
    (entry,) = _entries()
    assert entry["kind"] == "tdp" and entry["after"] == {"tdp": old}
    assert entry["before"] == {"tdp": {"pl1": 20, "pl2": 30, "pl3": 40, "force": False}}


def test_brightness_rollback_adds_its_own_entry(dashboard, profiles_env, fake_host):
    _store(profiles_env)
    ui = _fake_ui()
    ok, _ = _rollback(dashboard, ui, _setting_entry("brightness", "low"))
    assert ok and not isinstance(ui.control.results[0], Exception)
    (entry,) = _entries()
    assert entry["kind"] == "brightness" and entry["after"] == {"brightness": "low"}


def test_goal_rollback_sets_max_fans_and_adds_its_own_entry(dashboard, profiles_env, fake_host):
    _store(profiles_env)
    ui = _fake_ui()
    ok, _ = _rollback(dashboard, ui, {"id": "old", "rollback": {"kind": "goal", "goal": "max"}})
    assert ok and ui.control.results == ["Max Fans ON"]
    assert max_fans_state.is_enabled()
    (entry,) = _entries()
    assert entry["kind"] == "goal" and entry["after"]["goal"] == "max"


def test_profile_rollback_applies_the_saved_profile_and_adds_its_own_entry(dashboard, profiles_env, fake_host):
    _store(profiles_env, active="Balanced")
    ui = _fake_ui()
    entry_in = {"id": "old", "rollback": {"kind": "profile", "profile": "Silent",
                                            "snapshot": {"name": "Silent"}}}
    ok, _ = _rollback(dashboard, ui, entry_in)
    assert ok and not isinstance(ui.control.results[0], Exception)
    assert profiles.active_name() == "Silent"
    (entry,) = _entries()
    assert entry["kind"] == "profile" and entry["summary"] == "Profile Balanced -> Silent"
    # Hooks never run from a rollback.
    assert not any("boondock" in " ".join(c.argv) for c in fake_host.calls)


def test_profile_rollback_of_a_deleted_profile_fails_without_writing(dashboard, profiles_env, fake_host):
    _store(profiles_env)
    ui = _fake_ui()
    ok, _ = _rollback(dashboard, ui, {"id": "old", "rollback": {
        "kind": "profile", "profile": "Gone", "snapshot": {"name": "Gone"}}})
    assert ok   # queued; the worker reports the failure in the status bar
    assert isinstance(ui.control.results[0], RuntimeError)
    assert "no longer exists" in str(ui.control.results[0])
    assert fake_host.calls == [] and _entries() == []


def test_setting_rollback_without_an_active_profile_fails_without_writing(dashboard, profiles_env, fake_host, monkeypatch):
    _store(profiles_env)
    monkeypatch.setattr(profiles, "get_profile", lambda *a, **k: None)
    ui = _fake_ui()
    ok, _ = _rollback(dashboard, ui, _setting_entry("battery_limit", 85))
    assert ok and isinstance(ui.control.results[0], RuntimeError)
    assert "no active profile" in str(ui.control.results[0])
    assert fake_host.calls == []


def test_a_failed_hardware_write_surfaces_and_records_a_failed_entry(dashboard, profiles_env, fake_host):
    _store(profiles_env)
    fake_host.fail("battery", out="write /dev/hidraw5: connection timed out")
    ui = _fake_ui()
    ok, _ = _rollback(dashboard, ui, _setting_entry("battery_limit", 85))
    assert ok
    assert isinstance(ui.control.results[0], Exception), "the worker must see the failure"
    (entry,) = _entries()
    assert entry["ok"] is False and entry["after"] == entry["before"]

"""Locked tests for the #134 review round-4 findings (Codex review of build/134).

Each test is named for the finding it locks (F1-F11 in the Qwen brief). Findings
that no longer apply on the merged code are NOT locked here (see the brief):
F1 (Max Fans restore metadata lookup is already inside _before_snapshot) and
F8 (brightness timeouts already record a failed entry through _edit_field and the
dashboard's record(ok=False)). F3 is locked only for the non-dict state.json read.

No hardware, no real HOME, no real Qt: every z13ctl call is a FakeHost or a stub,
and dashboard_ui is imported against a permissive PyQt6 stub when PyQt6 is absent.
"""

import json
import os
import pwd
import sys
import types

import pytest

import host
import profiles
import state_authority as authority
import timeline


# ----------------------------------------------------------------------
# Shared helpers
# ----------------------------------------------------------------------

def _raw(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _entries(path):
    return timeline.read_entries(str(path))


def _write_store(profiles_env, active="Balanced"):
    profiles.save_store({"version": 1, "active": active, "profiles": profiles.default_profiles()})


@pytest.fixture
def decky_m(main, profiles_env, tmp_path, monkeypatch):
    """decky/main.py with every file it reads or writes pointed into tmp_path."""
    monkeypatch.setattr(main, "TIMELINE_PATH", str(timeline.TIMELINE_PATH))
    monkeypatch.setattr(main, "PROFILES_STORE", str(profiles.STORE_PATH))
    monkeypatch.setattr(main, "MAX_FANS_STATE", str(profiles_env.max_fans))
    monkeypatch.setattr(main, "INSTALL_JSON", str(tmp_path / "install.json"))
    monkeypatch.setattr(main, "OS_RELEASE_PATH", str(tmp_path / "os-release"))
    return main


# ----------------------------------------------------------------------
# F4: timeline directory handling when running as root
# ----------------------------------------------------------------------

def test_f4_symlinked_ancestor_directory_is_refused(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(OSError):
        timeline.append({"kind": "profile", "summary": "x"},
                        path=str(link / "state" / "timeline.jsonl"), journal=False)
    assert not (real / "state").exists(), "nothing may be created through an ancestor symlink"


def test_f4_root_directory_handover_uses_fchown_not_path_chown(tmp_path, monkeypatch):
    d = tmp_path / "state"
    d.mkdir()
    real_lstat = os.lstat
    chowns, fchowns = [], []

    # Pretend the existing folder is root-owned, as after a root-run first write.
    def lstat(p, *a, **k):
        st = real_lstat(p, *a, **k)
        if os.path.abspath(str(p)) == str(d):
            return types.SimpleNamespace(st_mode=st.st_mode, st_uid=0, st_gid=0)
        return st

    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "lstat", lstat)
    monkeypatch.setattr(os, "chown", lambda p, u, g: chowns.append(str(p)))
    monkeypatch.setattr(os, "fchown", lambda fd, u, g: fchowns.append((u, g)))

    timeline.append({"kind": "profile", "summary": "root write"},
                    path=str(d / "timeline.jsonl"), journal=False, owner=(1000, 1000))
    assert chowns == [], "the folder must be handed over by descriptor (fchown), not by path"
    assert fchowns, "the folder handover must go through fchown"


# ----------------------------------------------------------------------
# F5: corrupt timeline records must not break steamos dedup or startup
# ----------------------------------------------------------------------

def test_f5_steamos_dedup_skips_corrupt_record_shape(tmp_path):
    p = tmp_path / "timeline.jsonl"
    p.write_text(json.dumps({"id": "bad", "kind": "steamos_update", "after": [1]}) + "\n")

    rec = timeline.record_steamos_update(
        before_build_id="A", after_build_id="B", path=str(p), journal_fn=lambda t, m: None)
    assert isinstance(rec, dict), "a corrupt earlier record must not stop a new BUILD_ID entry"
    assert rec["after"]["build_id"] == "B"


def test_f5_startup_build_id_check_survives_corrupt_timeline(decky_m, tmp_path, monkeypatch):
    (tmp_path / "install.json").write_text(json.dumps({"build_id": "A"}))
    (tmp_path / "os-release").write_text('BUILD_ID="B"\nVERSION_ID="3.8"\n')
    p = str(timeline.TIMELINE_PATH)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write(json.dumps({"id": "bad", "kind": "steamos_update", "after": [1]}) + "\n")
    monkeypatch.setattr(decky_m, "logging_enabled", lambda: True)

    # _main awaits this before scheduling the Max Fans watchdog; it must not raise.
    decky_m._check_steamos_build_id()


# ----------------------------------------------------------------------
# F6: profile hook commands must not reach the timeline
# ----------------------------------------------------------------------

def test_f6_recorded_profile_steps_scrub_hook_command_text(tmp_path):
    p = tmp_path / "timeline.jsonl"
    secret = "curl https://example.invalid -H 'Authorization: SECRET-TOKEN-XYZ'"
    timeline.record_profile_apply(
        profile={"name": "B", "hooks": {"enter": "", "exit": ""}},
        previous={"name": "A", "hooks": {"exit": secret}},
        results=[("exit hook", True, f"ran: {secret}"), ("profile", True, "ok")],
        path=str(p),
    )
    raw = _raw(p)
    assert "SECRET-TOKEN-XYZ" not in raw
    assert "example.invalid" not in raw
    assert "ran:" not in raw


def test_f6_profile_apply_never_writes_hook_command_to_timeline(profiles_env, monkeypatch):
    secret_exit = "echo SECRET-EXIT-HOOK"
    secret_enter = "echo SECRET-ENTER-HOOK"
    monkeypatch.setattr(authority, "run",
                        lambda argv, timeout=30, **k: host.Result(0, "", False))
    previous = {"name": "A", "base": "balanced", "hooks": {"enter": "", "exit": secret_exit}}
    profile = {"name": "B", "base": "balanced", "hooks": {"enter": secret_enter, "exit": ""}}

    profiles.apply(profile, previous=previous, skip_hooks=False)

    assert timeline.flush()   # apply() queues its entry; it is never written inline
    raw = _raw(timeline.TIMELINE_PATH)
    assert "SECRET-EXIT-HOOK" not in raw
    assert "SECRET-ENTER-HOOK" not in raw


# ----------------------------------------------------------------------
# F7: logging off means no folder, no lock file, before any preference-less work
# ----------------------------------------------------------------------

def test_f7_steamos_record_with_logging_off_touches_no_disk(tmp_path, monkeypatch):
    install = tmp_path / "install.json"
    install.write_text(json.dumps({"logging": False}))
    monkeypatch.setattr(timeline, "INSTALL_JSON", str(install))
    target = tmp_path / "new-folder" / "timeline.jsonl"

    timeline.record_steamos_update(before_build_id="A", after_build_id="B",
                                   path=str(target), journal_fn=lambda t, m: None)
    assert not (tmp_path / "new-folder").exists(), "logging off must not create the state folder"


# ----------------------------------------------------------------------
# F9: the recovery newline counts toward the segment cap
# ----------------------------------------------------------------------

def test_f9_partial_line_separator_counts_toward_segment_cap(tmp_path):
    target = tmp_path / "timeline.jsonl"
    line = b"{" + b"a" * 37 + b"}\n"          # 40 bytes, a complete record
    cap = 100
    target.write_bytes(b"x" * (cap - len(line)))  # no trailing newline: a crashed writer

    timeline._append_locked(line, str(target), cap, 3, None)

    segments = [p for p in [target, tmp_path / "timeline.jsonl.1"] if p.exists()]
    assert all(p.stat().st_size <= cap for p in segments), \
        "the recovery newline plus the record must not push a segment past the cap"


# ----------------------------------------------------------------------
# F10: one timeline entry per targeted TDP / battery write, in the shared owner
# ----------------------------------------------------------------------

class _FakeHardware:
    def __init__(self, *a, **k):
        self.calls = []

    def set_battery_limit(self, value):
        self.calls.append(("battery_limit", value))
        return ""

    def apply_tdp(self, value):
        self.calls.append(("tdp", value))
        return ""


@pytest.fixture
def fake_hardware(monkeypatch):
    import hardware_controller
    made = []

    def factory(*a, **k):
        hw = _FakeHardware()
        made.append(hw)
        return hw
    monkeypatch.setattr(hardware_controller, "HardwareController", factory)
    return made


def test_f10_battery_write_through_authority_records_exactly_one_entry(profiles_env, fake_hardware):
    _write_store(profiles_env)
    authority.set_field("battery_limit", 80, "Balanced")

    got = _entries(timeline.TIMELINE_PATH)
    assert [e["kind"] for e in got] == ["battery_limit"]


def test_f10_tdp_edit_through_authority_records_exactly_one_entry(profiles_env, fake_hardware):
    _write_store(profiles_env)
    profile = profiles.get_profile("Balanced")
    profile["tdp"] = {"pl1": 40, "pl2": 50, "pl3": 60}
    authority.edit(profile, "tdp")

    got = _entries(timeline.TIMELINE_PATH)
    assert [e["kind"] for e in got] == ["tdp"]


def test_f10_decky_battery_write_still_records_exactly_one_entry(decky_m, profiles_env, fake_hardware):
    _write_store(profiles_env)
    result = decky_m._edit_field_recorded("battery_limit", 80, "Balanced")
    assert result["ok"] is True
    got = _entries(timeline.TIMELINE_PATH)
    assert [e["kind"] for e in got] == ["battery_limit"], "the write must be recorded once, not twice"


# ----------------------------------------------------------------------
# F11: the production post-update Env records a timeline entry
# ----------------------------------------------------------------------

def test_f11_post_update_env_is_built_with_timeline_recorder(tmp_path):
    from post_update import driver
    env = driver.make_env(str(tmp_path), run=lambda argv: (0, ""), tool_name=lambda n: n)
    assert env.timeline is not None, "make_env must pass timeline=make_timeline_recorder(...)"


# ----------------------------------------------------------------------
# F3: a non-object state.json is treated as empty, not as state
# ----------------------------------------------------------------------

@pytest.mark.parametrize("payload", ["[]", "null", "42"])
def test_f3_plugin_state_read_ignores_non_object_json(decky_m, tmp_path, monkeypatch, payload):
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "state.json").write_text(payload)
    monkeypatch.setattr(decky_m.decky, "DECKY_PLUGIN_SETTINGS_DIR", str(settings))

    state = decky_m._load_state()
    assert isinstance(state, dict)
    assert state.get("brightness") is None


# ----------------------------------------------------------------------
# F2: the dashboard Max Fans toggle must not create/migrate the store before the write
# ----------------------------------------------------------------------

def _install_qt_stub(monkeypatch):
    """Permissive PyQt6 stand-in so dashboard_ui can be imported without Qt installed."""
    class _Any:
        def __init__(self, *a, **k):
            pass

        def __call__(self, *a, **k):
            if len(a) == 1 and not k and isinstance(a[0], types.FunctionType):
                return a[0]          # @pyqtSlot(...) used as a decorator
            return _Any()

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return _Any()

    class _Meta(type):
        def __getattr__(cls, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return _Any()

    class _Base(metaclass=_Meta):
        def __init__(self, *a, **k):
            pass

        def __getattr__(self, name):
            if name.startswith("__"):
                raise AttributeError(name)
            return _Any()

    def _module(name):
        mod = types.ModuleType(name)
        cache = {}

        def __getattr__(attr):
            if attr.startswith("__"):
                raise AttributeError(attr)
            if attr not in cache:
                if attr[:1].isupper() and attr not in ("QT_VERSION_STR",):
                    cache[attr] = _Meta(attr, (_Base,), {})
                else:
                    cache[attr] = _Any()
            return cache[attr]
        mod.__getattr__ = __getattr__
        return mod

    root = types.ModuleType("PyQt6")
    root.__path__ = []
    monkeypatch.setitem(sys.modules, "PyQt6", root)
    for sub in ("QtWidgets", "QtCore", "QtDBus", "QtGui", "QtTest", "QtNetwork", "QtSvg"):
        monkeypatch.setitem(sys.modules, f"PyQt6.{sub}", _module(f"PyQt6.{sub}"))


@pytest.fixture
def dashboard(monkeypatch):
    try:
        import PyQt6.QtWidgets  # noqa: F401
    except ImportError:
        _install_qt_stub(monkeypatch)
        for name in list(sys.modules):
            if name in ("dashboard_ui", "profile_control", "fans_window", "theme", "updates_dialog",
                        "post_update_view", "display_modes", "screen_brightness", "apply_worker"):
                monkeypatch.delitem(sys.modules, name, raising=False)
    import dashboard_ui
    return dashboard_ui


class _FakeControl:
    """Real ProfileControl read methods bound to a fake object; _submit records the work."""

    def __init__(self):
        self.submitted = []

    def _submit(self, label, fn):
        self.submitted.append((label, fn))

    def store(self):
        from profile_control import ProfileControl
        return ProfileControl.store(self)

    def active_name(self):
        from profile_control import ProfileControl
        return ProfileControl.active_name(self)


@pytest.mark.parametrize("mode", ["strict", "broken"])
def test_f2_max_fans_toggle_is_submitted_even_when_store_cannot_be_read(
        dashboard, profiles_env, monkeypatch, mode):
    real_load = profiles.load_store

    def load(create=True):
        if mode == "strict" and create:
            raise AssertionError("the toggle must not create or migrate profiles before the write")
        if mode == "broken":
            raise OSError("profiles.json is unreadable")
        return real_load(create=False)

    monkeypatch.setattr(profiles, "load_store", load)
    calls = []
    monkeypatch.setattr(authority, "set_max_fans", lambda enabled: calls.append(enabled) or "Max Fans ON")

    fake = types.SimpleNamespace(control=_FakeControl())
    dashboard.DashboardUI._toggle_max_fans(fake, True)

    assert len(fake.control.submitted) == 1, "the toggle must reach the worker"
    label, fn = fake.control.submitted[0]
    assert fn() == "Max Fans ON"
    assert calls == [True]

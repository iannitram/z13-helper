"""Atomic writers that run as root must not trust the directory they write in.

The directories are writable by the deck user, so a temp file named after the pid
could be pre-planted (or be a symlink). The writers now create an unpredictable temp
file with O_EXCL, set mode/owner on the fd, and refuse a symlink at the target.
Root is simulated by patching geteuid and recording chown/fchown calls.
"""

import importlib.util
import json
import logging
import os
import sys
import types
from pathlib import Path

import pytest

import profiles

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def dmain(monkeypatch):
    stub = types.ModuleType("decky")
    stub.logger = logging.getLogger("decky.test")
    stub.DECKY_PLUGIN_SETTINGS_DIR = "/nonexistent-settings-dir"
    monkeypatch.setitem(sys.modules, "decky", stub)
    spec = importlib.util.spec_from_file_location("decky_main_atomic", REPO / "decky" / "main.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _decky_store(tmp, dm):
    dm.write_store_atomic({"active": "A", "profiles": []}, str(tmp / "profiles.json"))


def _decky_fans(tmp, dm):
    dm.MAX_FANS_STATE = str(tmp / "max-fans.json")
    dm._save_max_fans({"enabled": True})


def _decky_stamp(tmp, dm):
    dm._stamp_rate_limited(str(tmp / "stamp"), lambda: 100.0, lambda a: "x")


def _decky_state(tmp, dm):
    dm.decky.DECKY_PLUGIN_SETTINGS_DIR = str(tmp)
    dm._save_plugin_state(brightness=5)


def _prof_json(tmp, dm):
    profiles._write_json(str(tmp / "x.json"), {"k": 1})


def _prof_stamp(tmp, dm):
    profiles._stamp_rate_limited(str(tmp / "stamp"), lambda: 100.0, lambda a: "x")


# id, writer, target filename, legacy predictable temp-name templates
CASES = [
    ("decky-store", _decky_store, "profiles.json", ["{p}.{pid}.decky.tmp"]),
    ("decky-fans", _decky_fans, "max-fans.json", ["{p}.{pid}.tmp"]),
    ("decky-stamp", _decky_stamp, "stamp", ["{p}.{pid}.tmp"]),
    ("decky-state", _decky_state, "state.json", ["{p}.tmp"]),
    ("profiles-json", _prof_json, "x.json", ["{p}.{pid}.tmp"]),
    ("profiles-stamp", _prof_stamp, "stamp", ["{p}.{pid}.tmp"]),
]


@pytest.fixture(params=CASES, ids=[c[0] for c in CASES])
def case(request):
    return request.param


def test_preplanted_symlink_at_old_name_untouched(case, dmain, tmp_path):
    _, write, name, templates = case
    target = tmp_path / name
    victim = tmp_path / "victim"
    victim.write_text("precious")
    planted = []
    for t in templates:
        old = Path(t.format(p=target, pid=os.getpid()))
        old.symlink_to(victim)
        planted.append(old)
    write(tmp_path, dmain)
    assert victim.read_text() == "precious"
    for old in planted:
        assert old.is_symlink() and os.readlink(old) == str(victim)
    assert target.exists() and not target.is_symlink()


def test_preplanted_file_at_old_name_untouched(case, dmain, tmp_path):
    _, write, name, templates = case
    target = tmp_path / name
    planted = []
    for t in templates:
        old = Path(t.format(p=target, pid=os.getpid()))
        old.write_text("planted")
        os.chmod(old, 0o600)
        planted.append(old)
    write(tmp_path, dmain)
    for old in planted:
        assert old.read_text() == "planted"
        assert oct(old.stat().st_mode & 0o777) == "0o600"
    assert target.read_text() != "planted"


def test_symlink_at_target_is_refused(case, dmain, tmp_path):
    _, write, name, _ = case
    target = tmp_path / name
    victim = tmp_path / "victim"
    victim.write_text("precious")
    target.symlink_to(victim)
    try:
        write(tmp_path, dmain)   # the stamp writers swallow OSError by design
    except OSError:
        pass
    assert target.is_symlink() and os.readlink(target) == str(victim)
    assert victim.read_text() == "precious"
    assert not [p for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_fake_root_chowns_the_fd_never_the_path(case, dmain, tmp_path, monkeypatch):
    cid, write, _, _ = case
    calls = {"fchown": [], "chown": []}
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda fd, u, g: calls["fchown"].append((u, g)))
    monkeypatch.setattr(os, "chown", lambda *a, **k: calls["chown"].append(a))
    write(tmp_path, dmain)
    assert calls["chown"] == []
    # decky-state and profiles-stamp are not re-owned (no owner given), the rest are
    assert len(calls["fchown"]) == (0 if cid in ("decky-state", "profiles-stamp") else 1)


def test_temp_name_is_not_predictable(dmain, tmp_path, monkeypatch):
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append(os.path.basename(a)), real(a, b)))
    for _ in range(3):
        dmain.write_store_atomic({"active": "A", "profiles": []}, str(tmp_path / "p.json"))
    assert len(set(seen)) == 3
    assert not any(str(os.getpid()) in n for n in seen)


def test_write_failure_leaves_no_temp(dmain, tmp_path, monkeypatch):
    def boom(a, b):
        raise OSError("replace failed")
    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        dmain.write_store_atomic({"active": "A", "profiles": []}, str(tmp_path / "p.json"))
    with pytest.raises(OSError):
        profiles._write_json(str(tmp_path / "q.json"), {"a": 1})
    assert list(tmp_path.iterdir()) == []


def test_content_round_trips(dmain, tmp_path):
    store = {"active": "A", "profiles": [{"name": "A"}]}
    p = tmp_path / "p.json"
    dmain.write_store_atomic(store, str(p))
    assert json.loads(p.read_text()) == store
    assert oct(p.stat().st_mode & 0o777) == "0o644"
    q = tmp_path / "q.json"
    profiles._write_json(str(q), store)
    assert json.loads(q.read_text()) == store



def test_symlinked_store_dir_is_refused(dmain, tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    with pytest.raises(OSError):
        dmain.write_store_atomic({"active": "A", "profiles": []}, str(link / "profiles.json"))
    assert list(real.iterdir()) == []


def test_store_owner_comes_from_the_dir_fd(dmain, tmp_path, monkeypatch):
    got = []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(os, "fchown", lambda fd, u, g: got.append((u, g)))
    monkeypatch.setattr(os, "chown", lambda *a, **k: pytest.fail("path chown"))
    dmain.write_store_atomic({"active": "A", "profiles": []}, str(tmp_path / "p.json"))
    assert got == [(os.getuid(), os.getgid())]


def test_symlink_at_trace_dir_is_not_chowned(dmain, tmp_path, monkeypatch):
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "keep").write_text("x")
    link = tmp_path / "tracedir"
    link.symlink_to(victim)
    monkeypatch.setattr(dmain, "logging_enabled", lambda: True)
    calls = []
    monkeypatch.setattr(os, "fchown", lambda *a: calls.append(a))
    monkeypatch.setattr(os, "chown", lambda *a, **k: calls.append(a))
    assert dmain.make_fan_trace(str(link / "trace.log"), owner=(1000, 1000)) is None
    assert calls == []
    assert [p.name for p in victim.iterdir()] == ["keep"]


def test_new_trace_dir_is_chowned_by_fd(dmain, tmp_path, monkeypatch):
    monkeypatch.setattr(dmain, "logging_enabled", lambda: True)
    fch, ch = [], []
    monkeypatch.setattr(os, "fchown", lambda fd, u, g: fch.append((u, g)))
    monkeypatch.setattr(os, "chown", lambda *a, **k: ch.append(a))
    dmain.make_fan_trace(str(tmp_path / "new" / "trace.log"), owner=(1000, 1000))
    assert (tmp_path / "new").is_dir() and ch == [] and (1000, 1000) in fch

"""Two writers read-modify-writing the same store must not lose each other's update.

Atomic rename keeps the JSON whole, but a second writer that read before the first
one saved would write its older snapshot back over it. The interleaving is forced:
each writer's load parks at a barrier so both have read before either saves. Under
the store lock the second writer never reaches the barrier until the first is done,
so the barrier times out and the writers simply run one after the other.
"""

import threading

import max_fans_state
import profiles


def _both_read_before_either_writes(module, name, monkeypatch):
    barrier = threading.Barrier(2)
    real = getattr(module, name)

    def parked(*a, **kw):
        out = real(*a, **kw)
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return out

    monkeypatch.setattr(module, name, parked)


def _run_together(monkeypatch, *fns):
    # Threads stand in for processes; their tmp files must not collide (pid-named).
    monkeypatch.setattr(profiles.os, "getpid", threading.get_ident)
    errors = []

    def wrap(fn):
        def go():
            try:
                fn()
            except Exception as e:  # noqa: BLE001
                errors.append(e)
        return go

    threads = [threading.Thread(target=wrap(fn)) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert not errors, errors


def test_profile_store_transactions_do_not_lose_updates(profiles_env, monkeypatch):
    profiles.load_store()
    _both_read_before_either_writes(profiles, "load_store", monkeypatch)
    new = profiles._profile("Added", "balanced")

    _run_together(monkeypatch, lambda: profiles.upsert_profile(new),
                         lambda: profiles.set_active("Silent"))

    store = profiles._read_json(profiles_env.store)
    assert "Added" in [p["name"] for p in store["profiles"]]
    assert store["active"] == "Silent"


def test_max_fans_state_saves_do_not_lose_updates(profiles_env, monkeypatch):
    max_fans_state.save(enabled=False)
    _both_read_before_either_writes(max_fans_state, "load", monkeypatch)

    _run_together(monkeypatch, lambda: max_fans_state.save(enabled=True),
                         lambda: max_fans_state.save(saved_curve=[[40, 10]]))

    state = max_fans_state.load()
    assert state["enabled"] is True and state["saved_curve"] == [[40, 10]]


def test_profiles_max_fans_save_does_not_lose_updates(profiles_env, monkeypatch):
    profiles._save_max_fans(enabled=False)
    _both_read_before_either_writes(profiles, "_max_fans_state", monkeypatch)

    _run_together(monkeypatch, lambda: profiles._save_max_fans(enabled=True),
                         lambda: profiles._save_max_fans(saved_curve=[[40, 10]]))

    state = profiles._read_json(profiles_env.max_fans)
    assert state["enabled"] is True and state["saved_curve"] == [[40, 10]]


def test_lock_is_reentrant_and_not_the_json_file(profiles_env):
    with profiles.store_lock():
        with profiles.store_lock():
            profiles.set_active("Silent")   # takes the lock again, same thread
    assert (profiles_env.store.parent / "profiles.json.lock").exists()
    assert profiles._read_json(profiles_env.store)["active"] == "Silent"


# ---- hostile / stuck lock file (the Decky copy runs as root in a deck-writable dir) ----

import os  # noqa: E402
import stat  # noqa: E402

import pytest  # noqa: E402

REFUSED = (profiles.StoreLockError, max_fans_state.StoreLockError)
LOCKS = [
    pytest.param(lambda: profiles.file_lock, lambda env: str(env.store), id="profiles"),
    pytest.param(lambda: max_fans_state.file_lock, lambda env: str(env.max_fans), id="max_fans_state"),
]


@pytest.mark.parametrize("lock,store", LOCKS)
def test_symlinked_lock_file_is_refused_and_target_untouched(profiles_env, tmp_path, lock, store):
    victim = tmp_path / "victim"
    victim.write_text("root-owned")
    victim.chmod(0o600)
    before = os.stat(victim)
    path = store(profiles_env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.symlink(victim, path + ".lock")
    with pytest.raises(REFUSED):
        with lock()(path):
            pass
    after = os.stat(victim)
    assert (after.st_mode, after.st_uid, after.st_gid) == (before.st_mode, before.st_uid, before.st_gid)
    assert victim.read_text() == "root-owned"


@pytest.mark.parametrize("lock,store", LOCKS)
def test_hardlinked_lock_file_is_refused(profiles_env, tmp_path, lock, store):
    victim = tmp_path / "victim"
    victim.write_text("x")
    victim.chmod(0o600)
    path = store(profiles_env)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.link(victim, path + ".lock")
    with pytest.raises(REFUSED):
        with lock()(path):
            pass
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o600


@pytest.mark.parametrize("lock,store", LOCKS)
def test_held_lock_times_out_instead_of_hanging(profiles_env, monkeypatch, lock, store):
    monkeypatch.setattr(profiles, "LOCK_TIMEOUT", 0.2)
    monkeypatch.setattr(max_fans_state, "LOCK_TIMEOUT", 0.2)
    path = store(profiles_env)
    holder_in, release = threading.Event(), threading.Event()

    def hold():
        with lock()(path):
            holder_in.set()
            release.wait(5)

    t = threading.Thread(target=hold)
    t.start()
    assert holder_in.wait(5)
    try:
        with pytest.raises(REFUSED, match="timed out"):
            with lock()(path):
                pass
    finally:
        release.set()
        t.join(5)


# --- load-time repair runs under the store lock -------------------------------

import json

import pytest


def _bad_store(profiles_env, kind):
    """A store that needs repair: one bad entry among good ones, or unparseable."""
    profiles_env.store.parent.mkdir(parents=True, exist_ok=True)
    if kind == "bad-entry":
        good = profiles.default_profiles()
        body = {"version": 1, "active": "Balanced",
                "profiles": good + [{"name": "Broken", "base": "nope"}]}
        profiles_env.store.write_text(json.dumps(body))
    else:
        profiles_env.store.write_text("{not json at all")


def _backups(profiles_env):
    return sorted(profiles_env.store.parent.glob("profiles.json.corrupt-*"))


def _park_first_read_per_thread(monkeypatch, barrier):
    """Every thread parks after its first unlocked-or-locked read of the store."""
    real = profiles._read_json
    seen = set()

    def parked(path):
        out = real(path)
        me = threading.get_ident()
        if me not in seen:
            seen.add(me)
            try:
                barrier.wait(timeout=0.5)
            except threading.BrokenBarrierError:
                pass
        return out

    monkeypatch.setattr(profiles, "_read_json", parked)


@pytest.mark.parametrize("kind", ["bad-entry", "unparseable"])
def test_concurrent_loaders_repair_a_bad_store_once(profiles_env, monkeypatch, kind):
    _bad_store(profiles_env, kind)
    original = profiles_env.store.read_text()
    _park_first_read_per_thread(monkeypatch, threading.Barrier(2))
    # Both loaders also reach the backup step together (under the lock the second
    # one can't get there until the first is done, so this just times out).
    both_at_backup = threading.Barrier(2)
    real_aside = profiles._keep_aside

    def aside(copy):
        try:
            both_at_backup.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return real_aside(copy)

    monkeypatch.setattr(profiles, "_keep_aside", aside)

    _run_together(monkeypatch, profiles.load_store, profiles.load_store)

    backups = _backups(profiles_env)
    assert len(backups) == 1, backups
    assert backups[0].read_text() == original
    assert profiles._sanitize_store(profiles._read_json(profiles_env.store))[1] is False


def test_loader_repair_does_not_drop_a_locked_writers_edit(profiles_env, monkeypatch):
    _bad_store(profiles_env, "bad-entry")
    parked, release = threading.Event(), threading.Event()
    real = profiles._read_json
    loader = {}

    def hook(path):
        out = real(path)
        if threading.get_ident() == loader.get("id") and not loader.get("done"):
            loader["done"] = True
            parked.set()                      # loader has its (old) snapshot
            release.wait(5)
        return out

    monkeypatch.setattr(profiles, "_read_json", hook)

    def run_loader():
        loader["id"] = threading.get_ident()
        profiles.load_store()

    t = threading.Thread(target=run_loader)
    t.start()
    loader.setdefault("id", None)
    assert parked.wait(5)
    profiles.upsert_profile(profiles._profile("Added", "balanced"))   # locked writer
    release.set()
    t.join(10)

    store = profiles._read_json(profiles_env.store)
    assert "Added" in [p["name"] for p in store["profiles"]]

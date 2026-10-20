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

"""load_store's rewrite paths must hold the store lock (PR #39 x #40)."""
import json

from src import profiles


def _spy(monkeypatch):
    held = []
    real = profiles._write_json

    def spy(path, data):
        held.append(profiles._lock_state.__dict__.get("held", {}).get(profiles.STORE_PATH, 0) > 0)
        return real(path, data)

    monkeypatch.setattr(profiles, "_write_json", spy)
    return held


def test_first_run_creation_holds_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(profiles, "STORE_PATH", str(tmp_path / "profiles.json"))
    held = _spy(monkeypatch)
    profiles.load_store()
    assert held and all(held)


def test_sanitize_rewrite_holds_lock(tmp_path, monkeypatch):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps({"version": 1, "active": "x", "profiles": "garbage"}))
    monkeypatch.setattr(profiles, "STORE_PATH", str(path))
    held = _spy(monkeypatch)
    profiles.load_store()
    assert held and all(held)

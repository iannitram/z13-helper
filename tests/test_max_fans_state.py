"""max_fans_state: the shared Max Fans flag file."""

import json

import pytest

import max_fans_state as mfs


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    p = tmp_path / "deep" / "config" / "max-fans.json"
    monkeypatch.setattr(mfs, "STATE_PATH", str(p))
    return p


def test_missing_file(state_path):
    assert mfs.load() is None
    assert mfs.is_enabled() is False
    assert mfs.saved_curve() is None


@pytest.mark.parametrize("content", ["", "{oops", "[1, 2]", '"on"', "null"])
def test_corrupt_or_wrong_type_is_none(state_path, content):
    state_path.parent.mkdir(parents=True)
    state_path.write_text(content)
    assert mfs.load() is None
    assert mfs.is_enabled() is False


def test_save_creates_dirs_and_merges(state_path):
    mfs.save(enabled=True)
    curve = [[20, 255]] * 8
    mfs.save(saved_curve=curve)
    assert json.loads(state_path.read_text()) == {"enabled": True, "saved_curve": curve}
    assert mfs.is_enabled() is True
    assert mfs.saved_curve() == curve
    mfs.save(enabled=False)
    assert mfs.load() == {"enabled": False, "saved_curve": curve}


def test_save_over_corrupt_file_starts_fresh(state_path):
    state_path.parent.mkdir(parents=True)
    state_path.write_text("{oops")
    mfs.save(enabled=True)
    assert mfs.load() == {"enabled": True}


def test_save_leaves_no_tmp_files(state_path):
    mfs.save(enabled=True)
    mfs.save(enabled=False)
    assert [p.name for p in state_path.parent.iterdir()] == ["max-fans.json"]


def test_save_is_atomic(state_path, monkeypatch):
    """A crash mid-write must leave the previous file intact (tmp + rename)."""
    mfs.save(enabled=True, saved_curve=[[40, 128]] * 8)
    before = state_path.read_text()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(mfs.json, "dump", boom)
    with pytest.raises(OSError):
        mfs.save(enabled=False)
    assert state_path.read_text() == before
    assert mfs.is_enabled() is True


def test_truthiness_of_enabled(state_path):
    state_path.parent.mkdir(parents=True)
    state_path.write_text(json.dumps({"enabled": 1}))
    assert mfs.is_enabled() is True
    state_path.write_text(json.dumps({"saved_curve": None}))
    assert mfs.is_enabled() is False


def test_migrate_legacy_seeds_once(state_path):
    curve = [[30, 100]] * 8
    mfs.migrate_legacy({"fans_max": True, "fan_curve_saved": curve})
    assert mfs.load() == {"enabled": True, "saved_curve": curve}
    # Second call (or another frontend's) never overwrites the live state.
    mfs.save(enabled=False)
    mfs.migrate_legacy({"fans_max": True, "fan_curve_saved": [[1, 1]]})
    assert mfs.load() == {"enabled": False, "saved_curve": curve}


def test_migrate_legacy_from_empty_config(state_path):
    mfs.migrate_legacy({})
    assert mfs.load() == {"enabled": False, "saved_curve": None}


def test_profiles_and_max_fans_state_share_the_file(profiles_env):
    """profiles.py keeps its own copy of the read/merge logic; they must agree."""
    import profiles
    mfs.save(enabled=True, saved_curve=[[1, 2]])
    assert profiles._max_fans_state() == {"enabled": True, "saved_curve": [[1, 2]]}
    profiles._save_max_fans(saved_curve=[[3, 4]])
    assert mfs.load() == {"enabled": True, "saved_curve": [[3, 4]]}

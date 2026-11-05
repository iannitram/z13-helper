"""After SteamOS update detection: BUILD_ID, kernel major, state file, armoury check (issue #70)."""

import json

import pytest

from post_update import steamos_update as su

REL = 'NAME="SteamOS"\nBUILD_ID=20261023.100\n'
REL2 = 'NAME="SteamOS"\nBUILD_ID=20261101.1\n'


@pytest.mark.parametrize("text, major", [("7.2.7-valve2-2-neptune", 7), ("6.18.0", 6), ("", None), ("x", None)])
def test_kernel_major(text, major):
    assert su.kernel_major(text) == major


def test_first_run_records_without_claiming_an_update():
    s = su.detect(REL, "6.18.1", {})
    assert s == {"build_id": "20261023.100", "kernel_major": 6, "reverify": False, "reason": ""}


def test_same_build_and_kernel_is_quiet():
    rec = su.detect(REL, "6.18.1", {})
    assert su.detect(REL, "6.18.9", rec)["reverify"] is False


def test_build_id_change_marks_reverify():
    rec = su.detect(REL, "6.18.1", {})
    s = su.detect(REL2, "6.18.1", rec)
    assert s["reverify"] and "20261023.100 to 20261101.1" in s["reason"]
    assert s["build_id"] == "20261101.1"


def test_kernel_major_change_alone_marks_reverify():
    rec = su.detect(REL, "6.18.1", {})
    s = su.detect(REL, "7.2.7-valve2", rec)
    assert s["reverify"] and "kernel 6.x to 7.x" in s["reason"]


def test_minor_kernel_change_does_not():
    rec = su.detect(REL, "6.18.1", {})
    assert su.detect(REL, "6.19.0", rec)["reverify"] is False


def test_reverify_stays_until_acknowledged():
    s = su.detect(REL2, "6.18.1", su.detect(REL, "6.18.1", {}))
    s = su.detect(REL2, "6.18.1", s)          # next look: no further change
    assert s["reverify"] is True
    cleared = su.acknowledge(s)
    assert cleared["reverify"] is False and cleared["build_id"] == "20261101.1"
    assert su.detect(REL2, "6.18.1", cleared)["reverify"] is False


def test_missing_readings_keep_the_recorded_values():
    rec = su.detect(REL, "6.18.1", {})
    s = su.detect("", "", rec)
    assert s["build_id"] == "20261023.100" and s["kernel_major"] == 6 and not s["reverify"]


def test_state_roundtrip_is_atomic_and_tolerates_junk(tmp_path):
    path = su.state_path(str(tmp_path))
    assert su.load_state(path) == {}
    su.save_state(path, {"build_id": "1"})
    assert su.load_state(path) == {"build_id": "1"}
    assert [p.name for p in (tmp_path / ".local/share/z13-helper").iterdir()] == ["post-update-state.json"]
    open(path, "w").write("[1]")
    assert su.load_state(path) == {}
    open(path, "w").write("{broken")
    assert su.load_state(path) == {}


def test_save_failure_leaves_the_old_file(tmp_path, monkeypatch):
    path = su.state_path(str(tmp_path))
    su.save_state(path, {"build_id": "old"})
    monkeypatch.setattr(su.os, "replace", lambda a, b: (_ for _ in ()).throw(OSError("full")))
    with pytest.raises(OSError):
        su.save_state(path, {"build_id": "new"})
    assert json.load(open(path)) == {"build_id": "old"}
    assert len(list((tmp_path / ".local/share/z13-helper").iterdir())) == 1


ALL = ["ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt", "fan_curve_enable"]


def test_armoury_check():
    assert su.armoury_check(lambda p: ALL) == {"state": "ok", "missing": []}
    assert su.armoury_check(lambda p: []) == {"state": "na", "missing": []}
    r = su.armoury_check(lambda p: ["ppt_pl1_spl", "ppt_pl2_sppt", "fan_x"])
    assert r == {"state": "needs", "missing": ["ppt_pl3_fppt"]}
    r = su.armoury_check(lambda p: ALL[:3])
    assert r["missing"] == ["fan attributes"]

"""Per-rail power-limit ranges: chosen by which sysfs tree exists, never by reading a ppt value.

The armoury ranges are the documented ones (z13ctl `tdp --help`), not measured here;
the nb-wmi + armoury-present layout mirrors this Z13 (asus-nb-wmi in use, armoury
tree present with ppt_* answering ENODEV).
"""

import pytest

import power_range as pr


def _tree(tmp_path, armoury, nbwmi):
    a, n = tmp_path / "armoury", tmp_path / "nbwmi"
    a.mkdir(); n.mkdir()
    if armoury:
        (a / "ppt_pl1_spl").mkdir()
    if nbwmi:
        (n / "ppt_pl1_spl").write_text("")
    return str(a), str(n)


def test_legacy_range_is_5_93(tmp_path):
    a, n = _tree(tmp_path, armoury=False, nbwmi=True)
    assert pr.detect_ranges(a, n) == {"pl1": (5, 93), "pl2": (5, 93), "pl3": (5, 93)}


def test_armoury_range_is_per_rail(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=False)
    assert pr.detect_ranges(a, n) == {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}


def test_both_trees_present_means_nbwmi(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    assert pr.detect_ranges(a, n) == pr.DEFAULT_RANGES


def test_no_tree_falls_back_to_default(tmp_path):
    a, n = _tree(tmp_path, armoury=False, nbwmi=False)
    assert pr.detect_ranges(a, n) == pr.DEFAULT_RANGES


def test_detection_never_opens_a_file(tmp_path, monkeypatch):
    a, n = _tree(tmp_path, armoury=True, nbwmi=False)
    import builtins
    monkeypatch.setattr(builtins, "open", lambda *x, **k: pytest.fail("read a file"))
    pr.detect_ranges(a, n)


def test_stored_20w_pl1_on_armoury_needs_attention_not_silently_applied():
    st = pr.tdp_status({"pl1": 20, "pl2": 30, "pl3": 40, "force": False}, pr.ARMOURY_RANGES)
    assert st["needs_attention"]
    assert st["requested"] == {"pl1": 20, "pl2": 30, "pl3": 40}
    assert st["accepted"] == {"pl1": 28, "pl2": 32, "pl3": 45}
    assert st["adjusted"] == ["pl1", "pl2", "pl3"]
    assert "20" in st["note"] and "28" in st["note"]


def test_stored_20w_is_fine_on_legacy():
    st = pr.tdp_status({"pl1": 20, "pl2": 30, "pl3": 40, "force": False}, pr.DEFAULT_RANGES)
    assert not st["needs_attention"] and st["note"] == ""

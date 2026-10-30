"""Per-rail power range selection from backend permissions and range metadata.

The fixtures model z13ctl's Armoury preference, coexisting legacy attributes,
and the non-reading current-value checks used to protect the EC.
"""

import pytest

import power_range as pr
import host


def _tree(tmp_path, armoury, nbwmi):
    a, n = tmp_path / "armoury", tmp_path / "nbwmi"
    a.mkdir(); n.mkdir()
    if armoury:
        limits = {
            "ppt_pl1_spl": (28, 80),
            "ppt_pl2_sppt": (32, 92),
            "ppt_pl3_fppt": (45, 93),
        }
        for attr, (lo, hi) in limits.items():
            d = a / attr
            d.mkdir()
            (d / "current_value").write_text("")
            (d / "min_value").write_text(str(lo))
            (d / "max_value").write_text(str(hi))
    if nbwmi:
        (n / "ppt_pl1_spl").write_text("")
    return str(a), str(n)


def test_legacy_range_is_5_93(tmp_path):
    a, n = _tree(tmp_path, armoury=False, nbwmi=True)
    assert pr.detect_ranges(a, n) == {"pl1": (5, 93), "pl2": (5, 93), "pl3": (5, 93)}


def test_armoury_range_is_per_rail_with_z13ctl_attribute_names(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=False)
    assert pr.detect_ranges(a, n) == {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}


def test_armoury_wins_when_both_backends_are_present(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    assert pr.detect_ranges(a, n) == pr.ARMOURY_RANGES


def test_armoury_backend_requires_usable_pl1_metadata(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    (tmp_path / "armoury" / "ppt_pl1_spl" / "min_value").unlink()
    assert pr.detect_ranges(a, n) == pr.DEFAULT_RANGES


@pytest.mark.parametrize("lo,hi", [(0, 80), (80, 28), ("bad", 80)])
def test_invalid_armoury_bounds_fall_back(tmp_path, lo, hi):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    d = tmp_path / "armoury" / "ppt_pl1_spl"
    (d / "min_value").write_text(str(lo))
    (d / "max_value").write_text(str(hi))
    assert pr.detect_ranges(a, n) == pr.DEFAULT_RANGES


def test_armoury_backend_requires_writable_current_value(tmp_path, monkeypatch):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    real_access = pr.os.access

    def access(path, mode):
        if str(path).endswith("ppt_pl1_spl/current_value") and mode == pr.os.W_OK:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(pr.os, "access", access)
    assert pr.detect_ranges(a, n) == pr.DEFAULT_RANGES


def test_armoury_rail_presence_uses_armoury_pl3_name(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    (tmp_path / "armoury" / "ppt_pl3_fppt" / "current_value").unlink()
    assert pr.detect_ranges(a, n) == {
        "pl1": (28, 80), "pl2": (32, 92), "pl3": (5, 93),
    }


def test_armoury_uses_actual_per_rail_bounds(tmp_path):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    bounds = {
        "ppt_pl1_spl": (31, 68),
        "ppt_pl2_sppt": (37, 77),
        "ppt_pl3_fppt": (42, 88),
    }
    for attr, (lo, hi) in bounds.items():
        d = tmp_path / "armoury" / attr
        (d / "min_value").write_text(str(lo))
        (d / "max_value").write_text(str(hi))
    assert pr.detect_ranges(a, n) == {
        "pl1": (31, 68), "pl2": (37, 77), "pl3": (42, 88),
    }


def test_host_probe_reads_range_metadata_without_reading_current_values(
        tmp_path, monkeypatch, capsys):
    a, n = _tree(tmp_path, armoury=True, nbwmi=True)
    values = {
        "ppt_pl1_spl": (30, 66),
        "ppt_pl2_sppt": (36, 76),
        "ppt_pl3_fppt": (41, 87),
    }
    for attr, (lo, hi) in values.items():
        d = tmp_path / "armoury" / attr
        (d / "min_value").write_text(str(lo))
        (d / "max_value").write_text(str(hi))
    import builtins
    import sys
    real_open = builtins.open

    def guarded_open(path, *args, **kwargs):
        if str(path).endswith("current_value"):
            pytest.fail("host probe read a live PPT value")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(sys, "argv", ["probe", a, n])
    exec(pr._HOST_PROBE, {})
    assert __import__("json").loads(capsys.readouterr().out) == {
        "pl1": [30, 66], "pl2": [36, 76], "pl3": [41, 87],
    }


def test_flatpak_uses_host_permission_and_range_probe(monkeypatch):
    expected = {"pl1": (29, 71), "pl2": (35, 79), "pl3": (43, 89)}
    calls = []
    monkeypatch.setattr(pr.host, "in_flatpak", lambda: True)

    def run(argv, timeout):
        calls.append((argv, timeout))
        assert argv[:3] == ["python3", "-c", pr._HOST_PROBE]
        return host.Result(0, '{"pl1": [29, 71], "pl2": [35, 79], "pl3": [43, 89]}', False)

    monkeypatch.setattr(pr.host, "run", run)
    assert pr.detect_ranges() == expected
    assert len(calls) == 1 and calls[0][1] == 5


def test_flatpak_probe_failure_uses_documented_fallback(monkeypatch):
    monkeypatch.setattr(pr.host, "in_flatpak", lambda: True)
    monkeypatch.setattr(pr.host, "run", lambda *a, **k: host.Result(1, "unavailable", False))
    assert pr.detect_ranges() == pr.DEFAULT_RANGES


def test_no_tree_falls_back_to_default(tmp_path):
    a, n = _tree(tmp_path, armoury=False, nbwmi=False)
    assert pr.detect_ranges(a, n) == pr.DEFAULT_RANGES


def test_detection_never_reads_live_power_values(tmp_path, monkeypatch):
    a, n = _tree(tmp_path, armoury=True, nbwmi=False)
    import builtins
    real_open = builtins.open

    def guarded_open(path, *args, **kwargs):
        if str(path).endswith("current_value"):
            pytest.fail("read a live PPT value")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", guarded_open)
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

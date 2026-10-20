"""Per-rail power-limit ranges from `z13ctl tdp --get` (parse, fall back, clamp for display).

The fixtures are assumed `tdp --get` shapes: PLn (label): <watts> W (range lo-hi W).
The armoury sample uses the documented 28-80 / 32-92 / 45-93 W ranges.
"""

from pathlib import Path

import pytest

import power_range as pr
from hardware_controller import HardwareController

FIX = Path(__file__).parent / "fixtures"
LEGACY = (FIX / "tdp_get_legacy.txt").read_text()
ARMOURY = (FIX / "tdp_get_armoury.txt").read_text()


def test_legacy_range_is_5_93():
    r = pr.parse_tdp_ranges(LEGACY)
    assert r == {"pl1": (5, 93), "pl2": (5, 93), "pl3": (5, 93)}


def test_armoury_range_is_per_rail():
    r = pr.parse_tdp_ranges(ARMOURY)
    assert r == {"pl1": (28, 80), "pl2": (32, 92), "pl3": (45, 93)}


@pytest.mark.parametrize("text", ["", "garbage\n", "PL1 (sustained): 20 W\n"])
def test_unparseable_falls_back_to_default(text):
    assert pr.parse_tdp_ranges(text) == pr.DEFAULT_RANGES


def test_stored_20w_pl1_on_armoury_needs_attention_not_silently_applied():
    ranges = pr.parse_tdp_ranges(ARMOURY)
    st = pr.tdp_status({"pl1": 20, "pl2": 30, "pl3": 40, "force": False}, ranges)
    assert st["needs_attention"]
    assert st["requested"] == {"pl1": 20, "pl2": 30, "pl3": 40}
    assert st["accepted"] == {"pl1": 28, "pl2": 32, "pl3": 45}
    assert st["adjusted"] == ["pl1", "pl2", "pl3"]
    assert "20" in st["note"] and "28" in st["note"]


def test_stored_20w_is_fine_on_legacy():
    st = pr.tdp_status({"pl1": 20, "pl2": 30, "pl3": 40, "force": False},
                       pr.parse_tdp_ranges(LEGACY))
    assert not st["needs_attention"] and st["note"] == ""


def test_controller_reads_ranges_from_tdp_get(monkeypatch):
    c = HardwareController(z13ctl_path="z13ctl")
    monkeypatch.setattr(c, "_run_command", lambda args, **kw: ARMOURY)
    assert c.get_tdp_ranges()["pl1"] == (28, 80)


def test_controller_range_failure_falls_back(monkeypatch):
    c = HardwareController(z13ctl_path="z13ctl")

    def boom(args, **kw):
        raise RuntimeError("nope")
    monkeypatch.setattr(c, "_run_command", boom)
    assert c.get_tdp_ranges() == pr.DEFAULT_RANGES

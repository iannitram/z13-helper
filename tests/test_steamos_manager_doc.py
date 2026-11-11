"""docs/steamos-manager.md must carry the #29 feasibility answer, not just the measurement."""
from pathlib import Path

DOC = (Path(__file__).resolve().parent.parent / "docs" / "steamos-manager.md").read_text()


def test_feasibility_section_present():
    for needle in ("## Feasibility", "## Options", "## Recommendation"):
        assert needle in DOC


def test_remote_route_covers_the_three_interfaces():
    for iface in ("TdpLimit1", "BatteryChargeLimit1", "PerformanceProfile1", "remotes.d"):
        assert iface in DOC


def test_all_four_options_named():
    for row in ("| a |", "| b |", "| c |", "| d |"):
        assert row in DOC

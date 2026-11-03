"""The profile controller's event dispatch delegate (#23 part B)."""

import pytest

pytest.importorskip("PyQt6.QtCore")

from profile_control import dispatch_event


@pytest.mark.parametrize("gui", [True, False])
@pytest.mark.parametrize("name", ["state-changed", "power-source"])
def test_dispatch_event_refreshes(name, gui):
    assert dispatch_event(name, gui) == "refresh"


def test_dispatch_event_toggles_the_window_only_when_selected():
    assert dispatch_event("gui-toggle", True) == "toggle-window"
    assert dispatch_event("gui-toggle", False) == "ignore"


@pytest.mark.parametrize("gui", [True, False])
def test_dispatch_event_ignores_unknown_names(gui):
    assert dispatch_event("unknown", gui) == "ignore"

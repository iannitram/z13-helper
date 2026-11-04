"""The z13ctl event action table (#23 part B).

Tests the PyQt-free state_events.event_action directly, so they run in the
gate (which installs only pytest). profile_control.dispatch_event is a thin
delegate to the same function.
"""

import pytest

from state_events import event_action


@pytest.mark.parametrize("gui", [True, False])
@pytest.mark.parametrize("name", ["state-changed", "power-source"])
def test_event_action_refreshes(name, gui):
    assert event_action(name, gui) == "refresh"


def test_event_action_toggles_the_window_only_when_selected():
    assert event_action("gui-toggle", True) == "toggle-window"
    assert event_action("gui-toggle", False) == "ignore"


@pytest.mark.parametrize("gui", [True, False])
def test_event_action_ignores_unknown_names(gui):
    assert event_action("unknown", gui) == "ignore"

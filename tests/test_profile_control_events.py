"""The z13ctl event action table (#23 part B).

Tests the PyQt-free state_events.event_action directly, so they run in the
gate (which installs only pytest). The production signal handler uses the
same dispatch function and action table.
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


@pytest.mark.parametrize("name,gui,expected", [
    ("state-changed", False, ["refresh"]),
    ("power-source", False, ["refresh"]),
    ("gui-toggle", True, ["toggle"]),
    ("gui-toggle", False, []),
    ("unknown", True, []),
])
def test_dispatch_calls_only_the_selected_frontend_callback(name, gui, expected):
    from state_events import dispatch_event
    seen = []
    dispatch_event(name, gui, refresh=lambda: seen.append("refresh"),
                   toggle_window=lambda: seen.append("toggle"))
    assert seen == expected

"""z13ctl event dispatch and operation status (issue #23, part a)."""

import pytest

from state_events import event_action, op_status


@pytest.mark.parametrize("name", ["state-changed", "power-source"])
@pytest.mark.parametrize("gui", [True, False])
def test_state_events_refresh_whatever_the_button_path(name, gui):
    assert event_action(name, gui) == "refresh"


def test_gui_toggle_only_when_selected_as_the_button_path():
    assert event_action("gui-toggle", True) == "toggle-window"
    assert event_action("gui-toggle", False) == "ignore"


@pytest.mark.parametrize("name", ["", "State-Changed", "power-source ", "profile-changed", "unknown"])
def test_anything_else_is_ignored_never_toggles_a_window(name):
    assert event_action(name, True) == "ignore"


@pytest.mark.parametrize("saved, applied, expected", [
    (True, (True, True, True), "applied"),
    (True, (), "saved"),
    (False, (), "failed"),
    (True, (False, False), "saved-not-applied"),
    (True, (True, False), "partial"),
    (False, (True, True), "partial"),
    (False, (True, False), "partial"),
    (False, (False,), "failed"),
])
def test_op_status(saved, applied, expected):
    assert op_status(saved, applied) == expected

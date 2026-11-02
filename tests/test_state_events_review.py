"""Reviewer probe for z13ctl event dispatch (issue #120, R28).

The locked test file does not cover the Expectation's rule that names differing
in case or spacing are ignored when they name the GUI toggle, so this adds it.
"""

import pytest

from state_events import event_action


@pytest.mark.parametrize("name", ["Gui-Toggle", "GUI-TOGGLE", " gui-toggle", "gui-toggle ", "gui toggle"])
def test_gui_toggle_name_variants_never_toggle_a_window(name):
    assert event_action(name, True) == "ignore"
    assert event_action(name, False) == "ignore"


@pytest.mark.parametrize("name", ["State-Changed", " power-source", "Power-Source"])
def test_refresh_names_must_match_exactly(name):
    assert event_action(name, True) == "ignore"

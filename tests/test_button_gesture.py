"""Side-button gesture (issue #131): triple press cycles profiles; presses 1-2 keep their action."""

import pathlib

import evdev_listener as ev

REPO = pathlib.Path(__file__).resolve().parent.parent


def feed(times, **kw):
    d = ev.GestureDetector(**kw)
    return [d.press(t) for t in times]


def test_single_and_double_press_keep_their_actions():
    assert feed([0.0]) == ["press"]
    assert feed([0.0, 0.2]) == ["press", "press"]


def test_triple_press_cycles_on_the_third_and_replaces_its_action():
    assert feed([0.0, 0.2, 0.4]) == ["press", "press", "cycle"]


def test_slow_presses_never_cycle():
    assert feed([0.0, 1.0, 2.0, 3.0]) == ["press"] * 4


def test_fourth_quick_press_starts_a_new_chain_without_double_firing():
    assert feed([0.0, 0.2, 0.4, 0.6, 0.8]) == ["press", "press", "cycle", "press", "press"]
    assert feed([0.0, 0.2, 0.4, 0.6, 0.8, 1.0]) [-1] == "cycle"


def test_window_edge_counts_as_quick():
    assert feed([0.0, ev.GESTURE_WINDOW_S, 2 * ev.GESTURE_WINDOW_S])[-1] == "cycle"


def test_gesture_matches_the_game_mode_watcher_constants():
    src = (REPO / "tooling" / "z13-button-watcher").read_text()
    assert f"DOUBLE_TAP_S = {ev.GESTURE_WINDOW_S}" in src
    assert f"CYCLE_PRESSES = {ev.CYCLE_PRESSES}" in src


def test_watcher_cycles_instead_of_acting_and_stays_masked_from_inputplumber():
    src = (REPO / "tooling" / "z13-button-watcher").read_text()
    assert 'app_command("cycle")' in src and "handle_cycle()" in src
    # principle 2: no input emulation, no InputPlumber un-mask
    assert "uinput" not in src and "unmask" not in src

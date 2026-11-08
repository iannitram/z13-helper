"""Side-button gesture (issue #131): a triple press cycles profiles, and exactly one reader
(the app in Desktop Mode, the z13-button-watcher in Game Mode) owns it per session."""

import importlib.machinery
import importlib.util
import pathlib

import pytest

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


def test_fourth_quick_press_starts_a_new_chain():
    assert feed([0.0, 0.2, 0.4, 0.6, 0.8]) == ["press", "press", "cycle", "press", "press"]
    assert feed([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])[-1] == "cycle"


def test_window_edge_counts_as_quick():
    assert feed([0.0, ev.GESTURE_WINDOW_S, 2 * ev.GESTURE_WINDOW_S])[-1] == "cycle"


# ---- one owner per session ----

@pytest.fixture
def watcher(monkeypatch):
    path = str(REPO / "tooling" / "z13-button-watcher")
    loader = importlib.machinery.SourceFileLoader("z13_button_watcher_cycle", path)
    spec = importlib.util.spec_from_loader("z13_button_watcher_cycle", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def triple_press(watcher, monkeypatch, game_mode, times=(100.0, 100.2, 100.4)):
    """Run one triple press through BOTH readers; return (profile cycles, watcher menu presses).

    A "cycle" is the app's reader emitting CYCLE (-> DashboardUI.cycle_profile) or the watcher
    sending the `cycle` socket command (-> the same cycle_profile)."""
    cycles, presses = [], []
    commands = []
    monkeypatch.setattr(watcher, "in_gamemode", lambda: game_mode)
    monkeypatch.setattr(watcher, "app_command", lambda cmd: commands.append(cmd) or True)
    monkeypatch.setattr(watcher, "handle_press", lambda double: presses.append(double))
    app_side, watcher_side = ev.GestureDetector(), watcher.PressChain()
    for t in times:
        if ev.gesture_for_app(app_side, t, game_mode=lambda: game_mode) == "cycle":
            cycles.append("app")
        watcher_side.route(t)
    cycles += ["watcher"] * commands.count("cycle")
    return cycles, presses


def test_one_triple_press_cycles_exactly_once_in_game_mode(watcher, monkeypatch):
    cycles, presses = triple_press(watcher, monkeypatch, game_mode=True)
    assert cycles == ["watcher"]
    assert presses == [False, True]          # presses 1-2 keep their menus; 3 has no menu


def test_one_triple_press_cycles_exactly_once_in_desktop_mode(watcher, monkeypatch):
    cycles, presses = triple_press(watcher, monkeypatch, game_mode=False)
    assert cycles == ["app"]
    assert presses == [False, True, False]   # third is the usual "is the app running" press


@pytest.mark.parametrize("game_mode", [True, False])
def test_six_quick_presses_cycle_twice_not_four_times(watcher, monkeypatch, game_mode):
    times = [100.0 + 0.2 * i for i in range(6)]
    cycles, _ = triple_press(watcher, monkeypatch, game_mode, times)
    assert len(cycles) == 2


def test_app_only_asks_for_game_mode_when_a_gesture_completes():
    asked = []
    d = ev.GestureDetector()
    for t in (0.0, 0.2):
        assert ev.gesture_for_app(d, t, game_mode=lambda: asked.append(1)) == "press"
    assert asked == []
    assert ev.gesture_for_app(d, 0.4, game_mode=lambda: asked.append(1) or False) == "cycle"
    assert asked == [1]


def test_game_mode_app_reader_reports_the_third_press_as_a_plain_press():
    d = ev.GestureDetector()
    got = [ev.gesture_for_app(d, t, game_mode=lambda: True) for t in (0.0, 0.2, 0.4)]
    assert got == ["press"] * 3


def test_gesture_matches_the_game_mode_watcher_constants():
    src = (REPO / "tooling" / "z13-button-watcher").read_text()
    assert f"DOUBLE_TAP_S = {ev.GESTURE_WINDOW_S}" in src
    assert f"CYCLE_PRESSES = {ev.CYCLE_PRESSES}" in src


def test_no_input_emulation_or_inputplumber_unmask():
    src = (REPO / "tooling" / "z13-button-watcher").read_text()
    assert "uinput" not in src and "unmask" not in src


def test_main_handles_the_cycle_command():
    assert 'cmd == "cycle"' in (REPO / "main.py").read_text()

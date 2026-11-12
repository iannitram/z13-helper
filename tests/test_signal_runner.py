"""Desktop signal runner (issue #130): off switch, restore, shared rate limit, no wedge.

Drives DashboardUI's runner methods on a stand-in object, so no window and no hardware.
"""

import types

import pytest

pytest.importorskip("PyQt6.QtWidgets")

import hw_signals as hs
from dashboard_ui import DashboardUI

SAVED = {"mode": "static", "color": "blue"}
USER_WRITE = f"Lighting → {hs.DEVICE}"
PROFILE_OK = [("tdp", True, "")]


class Config(dict):
    def set(self, key, value):
        self[key] = value


class Ui:
    """Just the attributes the runner touches, with the real methods bound to it."""
    _tick_hardware_signals = DashboardUI._tick_hardware_signals
    _on_hardware_signal_source_done = DashboardUI._on_hardware_signal_source_done
    _set_hardware_signals_enabled = DashboardUI._set_hardware_signals_enabled

    def __init__(self, lighting=SAVED, enabled=True):
        self.config = Config(hardware_signals_enabled=enabled)
        self.writes = []
        self.submitted = []
        self.control = types.SimpleNamespace(
            active=lambda: {"lighting": {hs.DEVICE: lighting} if lighting else {}})
        self.worker = types.SimpleNamespace(submit=self._submit)
        self.controller = types.SimpleNamespace(
            set_lighting=lambda dev, setting, brightness: self.writes.append(
                (setting["mode"], setting["color"], brightness)))
        self._signals_active = {}
        self._signals_shown = None
        self._signals_shown_at = {}
        self._signals_last_write = None
        self._signals_pending = None

    def _submit(self, label, fn):
        self.submitted.append(label)
        fn()

    def finish(self, label="Hardware signal lightbar"):
        self._on_hardware_signal_source_done(label, None)


@pytest.fixture
def clock(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("time.monotonic", lambda: now[0])
    return now


def flash(ui, clock):
    """A profile apply succeeds; the apply itself may have written the bar, so the flash
    waits out the rate limit, then plays."""
    ui._on_hardware_signal_source_done("Profile → Turbo", PROFILE_OK)
    assert ui.writes == []
    clock[0] += hs.MIN_WRITE_INTERVAL_S + 0.1
    ui._tick_hardware_signals()
    assert ui.writes == [("static", "green", "high")]
    ui.finish()


def test_profile_change_flashes_then_restores_saved_lighting(clock):
    ui = Ui()
    flash(ui, clock)
    clock[0] += hs.ONE_SHOT_S + 0.1
    ui._tick_hardware_signals()
    ui.finish()
    assert ui.writes[-1] == ("static", "blue", "high") and len(ui.writes) == 2
    ui._tick_hardware_signals()
    assert len(ui.writes) == 2          # settled: nothing more to write


def test_off_switch_means_zero_lightbar_writes_for_a_profile_change(clock):
    ui = Ui(enabled=False)
    ui._on_hardware_signal_source_done("Profile → Turbo", PROFILE_OK)
    clock[0] += 10
    ui._tick_hardware_signals()
    assert ui.writes == [] and ui.submitted == []


def test_turning_off_mid_signal_restores_exactly_once(clock):
    ui = Ui()
    flash(ui, clock)
    clock[0] += 0.5   # the flash is still showing, last write over 0.5 s ago
    ui._set_hardware_signals_enabled(False)
    assert len(ui.writes) == 1                  # too soon after the flash: deferred
    clock[0] += hs.MIN_WRITE_INTERVAL_S
    ui._tick_hardware_signals()
    ui.finish()
    assert ui.writes == [("static", "green", "high"), ("static", "blue", "high")]
    for _ in range(3):
        clock[0] += 5
        ui._tick_hardware_signals()
    assert len(ui.writes) == 2


def test_rate_limit_counts_user_lightbar_writes(clock):
    ui = Ui()
    ui._on_hardware_signal_source_done(USER_WRITE, [])      # the user just set the bar
    ui._signals_active[hs.PROFILE_CHANGED] = clock[0]
    clock[0] += hs.MIN_WRITE_INTERVAL_S - 0.5
    ui._tick_hardware_signals()
    assert ui.writes == []                                   # deferred, not dropped
    clock[0] += 1.0
    ui._tick_hardware_signals()
    assert ui.writes == [("static", "green", "high")]


def test_signal_writes_are_spaced_by_the_rate_limit(clock):
    ui = Ui()
    flash(ui, clock)
    ui._on_hardware_signal_source_done("Profile → B", PROFILE_OK)   # burst of profile changes
    clock[0] += 1.0
    ui._tick_hardware_signals()
    assert len(ui.writes) == 1


def test_no_saved_lighting_turns_the_bar_off_after_the_flash(clock):
    ui = Ui(lighting=None)
    ui._on_hardware_signal_source_done("Profile → Turbo", PROFILE_OK)
    clock[0] += hs.MIN_WRITE_INTERVAL_S + 0.1
    ui._tick_hardware_signals()
    ui.finish()
    clock[0] += hs.ONE_SHOT_S + 0.1
    ui._tick_hardware_signals()
    assert ui.writes[-1] == ("static", "green", "off")

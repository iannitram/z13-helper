"""The dashboard remains usable when its window is shorter than its panels."""

from unittest.mock import MagicMock

import pytest


def test_dashboard_scrolls_without_squeezing_performance_tiles(monkeypatch):
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication
    import dashboard_ui
    from theme import SIZE

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(dashboard_ui.max_fans_state, "migrate_legacy", lambda _config: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_setup_tray", lambda self: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_refresh_profile_ui", lambda self: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_load_initial_state", lambda self: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_tick", lambda self: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_start_side_button_listener", lambda self: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "reprobe_capabilities", lambda self: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_check_updates", lambda *a, **k: None)
    monkeypatch.setattr(dashboard_ui.DashboardUI, "_check_post_update", lambda *a, **k: None)

    class Signal:
        def connect(self, *_args):
            pass

    class Worker:
        done = Signal()

        def __init__(self, *_args):
            pass

        def submit(self, *_args):
            pass

    class Control:
        message = changed = toggle_window = Signal()

        def __init__(self, *_args):
            self._pending = False
            self.writes_allowed = None

        def start_events(self):
            pass

        def close_events(self):
            pass

    monkeypatch.setattr(dashboard_ui, "ApplyWorker", Worker)
    monkeypatch.setattr(dashboard_ui, "ProfileControl", Control)
    monkeypatch.setattr(dashboard_ui, "FanWatchdog", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(dashboard_ui, "FanWatchdogRunner", lambda *_a, **_k: MagicMock())
    monkeypatch.setattr(dashboard_ui.platform_support, "detect", lambda: {
        "supported": False, "reason": "test", "battery_present": True,
        "capabilities": {name: {"available": False, "reason": "test"} for name in (
            "platform_profile", "tdp", "fan_curve", "undervolt", "keyboard_backlight", "battery_limit")},
    })
    controller, sensors, config = MagicMock(), MagicMock(), MagicMock()
    config.get.return_value = None
    window = dashboard_ui.DashboardUI(controller, sensors, config)
    window.setMinimumSize(0, 0)
    window.centralWidget().setMinimumSize(0, 0)
    window.resize(927, 450)
    window.show()
    app.processEvents()

    assert all(tile.height() >= SIZE["tile_h"] for tile in window._tiles.values())
    assert window.fans_tile.height() >= SIZE["tile_h"]
    assert window.perf.height() >= window.perf.sizeHint().height()
    assert window.scroll_area.verticalScrollBar().maximum() > 0
    window.timer.stop()
    window.hide()

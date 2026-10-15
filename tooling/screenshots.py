#!/usr/bin/env python3
"""Render the README screenshots offscreen, from the real app code.

Run inside the Flatpak (it has PyQt6), from the repo root:

    flatpak run --filesystem=$PWD --command=python3 io.github.iannitram.Z13Helper \\
        tooling/screenshots.py [out_dir]

Writes main-window.png, fans-power.png and fan-trace.png to out_dir
(default docs/screenshots/). Nothing on screen, nothing written to the hardware:
reads (sensors, profiles, z13ctl --get, the logs shown) are real; every z13ctl write,
screen-brightness write, the side-button reader, the fan watchdog and its trace file and
the upstream check are stubbed out. The resident app keeps running untouched.
"""

import os
import sys

os.environ["QT_QPA_PLATFORM"] = "offscreen"
REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "docs", "screenshots"))
SETTLE_MS = 4000   # let the telemetry timer fill in live readings

from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import dashboard_ui  # noqa: E402
import fan_watchdog  # noqa: E402
import screen_brightness  # noqa: E402
from config_manager import ConfigManager  # noqa: E402
from hardware_controller import HardwareController  # noqa: E402
from sensor_reader import SensorReader  # noqa: E402


class ReadOnlyController(HardwareController):
    """Real z13ctl reads; every write becomes a no-op."""

    def _run_command(self, args, *a, **kw):
        if "--get" in args or args[:1] == ["status"]:
            return super()._run_command(args, *a, **kw)
        return ""


def main():
    os.makedirs(OUT, exist_ok=True)
    dashboard_ui.DashboardUI._start_side_button_listener = lambda self: None
    dashboard_ui.DashboardUI._check_updates = lambda self, force=False: None
    fan_watchdog.FanWatchdog.tick = lambda self: None
    dashboard_ui.FanTrace.to_file = staticmethod(lambda *a, **kw: None)   # not the real trace file
    for name in dir(screen_brightness):
        cls = getattr(screen_brightness, name)
        if isinstance(cls, type) and hasattr(cls, "set"):
            cls.set = lambda self, *a, **kw: None

    app = QApplication(sys.argv)
    app.setApplicationName("Z13-Helper")
    window = dashboard_ui.DashboardUI(ReadOnlyController(), SensorReader(),
                                      ConfigManager(dashboard_ui._CONFIG_PATH))
    window.show()

    def shoot():
        window.grab().save(os.path.join(OUT, "main-window.png"))
        window._toggle_fans_window()
        QTimer.singleShot(1500, shoot_fans)

    def shoot_fans():
        window._fans_window.grab().save(os.path.join(OUT, "fans-power.png"))
        window._fans_window.hide()
        window._open_error_log()
        QTimer.singleShot(1000, shoot_log)

    def shoot_log():
        dlg = window._log_dialog
        if dlg is not None:
            dlg.resize(1180, 560)                # wide enough for a whole trace line
            dlg.tabs.setCurrentIndex(2)          # Fan trace
            dlg._fill_trace()
            dlg.grab().save(os.path.join(OUT, "fan-trace.png"))
        print("saved to", OUT)
        app.quit()

    QTimer.singleShot(SETTLE_MS, shoot)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

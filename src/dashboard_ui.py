"""Main window — G-Helper's Settings form, adapted to the Z13 on SteamOS.

A single stacked column (docs/ROADMAP.md §5): Performance Mode (Silent /
Balanced / Turbo / Fans + Power tiles + the profile dropdown), Keyboard &
Rear Light, Screen Brightness, Battery Charge Limit, Run on Startup, Version,
and a footer with Updates / Quit. Live sensors sit right-aligned in the panel title rows, as in
G-Helper. The Fans + Power window (fans_window.py) opens to the left.

Profiles (profiles.py) replace the old Boondock button: Boondock is now a
profile whose hooks run the boondock script.
"""

import logging
import os
import subprocess
import sys
import threading
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QSlider, QStatusBar, QSystemTrayIcon, QMenu, QMessageBox, QComboBox, QCheckBox, QFrame,
)
from PyQt6.QtCore import QTimer, Qt, pyqtSlot, pyqtSignal
from PyQt6.QtDBus import QDBusConnection
from PyQt6.QtGui import QFont, QIcon, QPainter, QColor, QPixmap, QAction

from hardware_controller import HardwareController
from sensor_reader import SensorReader
from config_manager import ConfigManager
import app_log
import host
import install_record
import max_fans_state
import profiles
import update_watch
from fan_watchdog import FanTrace, FanWatchdog, FanWatchdogRunner
from screen_brightness import ScreenBrightness, MIN_PERCENT
from apply_worker import ApplyWorker
from profile_control import ProfileControl
from fans_window import FansWindow
from updates_dialog import UpdatesDialog, UpdateCheckThread
from theme import (STYLESHEET, ModeTile, Panel, pill, tip, ECO, STANDARD, TURBO, CUSTOM,
                   ACCENT_FOR_BASE, BORDER)
from evdev_listener import find_side_button_device, EvdevListener

APP_VERSION = "0.5.0"

_INPUTPLUMBER_SERVICE      = "org.shadowblip.InputPlumber"
_INPUTPLUMBER_IFACE        = "org.shadowblip.Input.DBusDevice"
_COMPOSITE_DEV_PATH        = "/org/shadowblip/InputPlumber/CompositeDevice0"
_COMPOSITE_DEV_IFACE       = "org.shadowblip.Input.CompositeDevice"
# Empirically determined event name emitted by the Z13 InputPlumber profile
# when the Armoury Crate side button is pressed (legacy fallback path only).
_QUICK_ACCESS_EVENT        = "ui_quick"
_INTERCEPT_MODE_ALL        = 2
_INTERCEPT_MODE_NONE       = 0
_TARGET_DEVICES_WITH_DBUS  = ["xbox-elite", "touchpad", "keyboard", "dbus"]

# Built-in tiles: (profile name, accent) — G-Helper's Silent / Balanced / Turbo.
_TILES = [("Silent", ECO), ("Balanced", STANDARD), ("Turbo", TURBO)]
_BRIGHTNESS = [("Off", "off"), ("Low", "low"), ("Medium", "medium"), ("High", "high")]
_LIGHT_TARGETS = [("Both", ("keyboard", "lightbar")), ("Keyboard", ("keyboard",)), ("Rear light", ("lightbar",))]

# Unique marker on the host-side side-button reader's argv, so we can reap stale
# ones (flatpak-spawn --host children are NOT reliably killed when the app dies).
_SIDE_BUTTON_READER_TAG = "z13-side-button-reader"
# pkill pattern: matches ONLY the reader's full invocation. The bracketed last
# char keeps the regex from matching its own invoker.
_SIDE_BUTTON_READER_MATCH = "emit " + _SIDE_BUTTON_READER_TAG[:-1] + "[" + _SIDE_BUTTON_READER_TAG[-1] + "]"

_CONFIG_PATH = os.path.expanduser("~/.config/z13-helper/config.json")
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ICON_PATH    = os.path.join(_PROJECT_ROOT, "assets", "icon.svg")
_SERVICE = "z13-helper.service"

TICK_VISIBLE_MS, TICK_HIDDEN_MS = 2000, 6000     # G-Helper: 2 s shown, 6 s hidden
UPDATE_CHECK_MS = 24 * 3600 * 1000
SCREEN_WRITE_MS = 40            # coalesce slider steps into one backlight write
SCREEN_FOLLOW_GRACE_S = 1.5     # after our own write, don't snap the slider back to a stale read


class DashboardUI(QMainWindow):
    sideButtonPressed = pyqtSignal()
    # From app_log listeners, which run on whatever thread logged / crashed.
    _errorLogged = pyqtSignal()
    _crashReported = pyqtSignal(str, str)

    def __init__(self, controller: HardwareController, sensors: SensorReader, config: ConfigManager):
        super().__init__()
        self.controller = controller
        self.sensors = sensors
        self.config = config
        max_fans_state.migrate_legacy(config)

        self.setWindowTitle("Z13-Helper")
        self.setStyleSheet(STYLESHEET)
        # No fixed minimum: the layout's own minimum keeps the title row (profile
        # name + live sensors) from being clipped, which a hard 430 px allowed.
        self._app_icon = self._make_app_icon()
        self.setWindowIcon(self._app_icon)
        QApplication.instance().setWindowIcon(self._app_icon)

        self.worker = ApplyWorker(self)
        self.control = ProfileControl(controller, self.worker, self)
        self.control.message.connect(lambda m: self.status_bar.showMessage(m, 8000))
        self._log_dialog = None
        self._errors_unseen = 0
        self._last_crash_notice = 0.0
        self.control.changed.connect(self._refresh_profile_ui)
        # Not while an apply is queued or running: a profile write in progress drops
        # the curve for a moment, and the watchdog would race the worker with its
        # own profile + curve writes.
        self._sensors = sensors
        self._fan_watchdog = FanWatchdog(sensors, controller,
                                         max_fans_on=max_fans_state.is_enabled,
                                         may_act=lambda: not self.control._pending,
                                         trace=self._make_fan_trace())
        self._fan_runner = FanWatchdogRunner(self._fan_watchdog, self.worker.submit)
        self.worker.done.connect(self._on_fan_recovery_done)
        self._fans_window = None
        self._updates_dialog = None
        self._update_thread = None
        self._new_updates = []
        self.screen = ScreenBrightness(QDBusConnection.sessionBus())
        self._screen_last_write = 0.0

        self._build_menu_bar()
        self._build_ui()
        self._setup_tray()
        self._refresh_profile_ui()
        self._load_initial_state()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK_HIDDEN_MS)
        self._tick()

        QTimer.singleShot(8000, lambda: self._check_updates(force=False))
        self._update_timer = QTimer(self)
        self._update_timer.timeout.connect(lambda: self._check_updates(force=False))
        self._update_timer.start(UPDATE_CHECK_MS)

        self._intercept_mode_set = False
        self._target_devices_set = False
        self._host_reader_proc = None
        self._evdev_listener = None
        self.sideButtonPressed.connect(self._toggle_window)
        self._start_side_button_listener()

        # Error log: badge on errors, notice on crashes (app_log.py).
        self._errorLogged.connect(self._on_error_logged)
        self._crashReported.connect(self._on_crash_reported)
        if app_log.ring() is not None:
            app_log.ring().add_listener(
                lambda item: self._errorLogged.emit() if item[0] >= logging.ERROR else None)
            errors = sum(1 for lv, _c, _t in list(app_log.ring().records) if lv >= logging.ERROR)
            if errors:
                QTimer.singleShot(0, lambda: self._bump_error_badge(errors))
        app_log.on_crash(lambda path, summary: self._crashReported.emit(path or "", summary))
        if app_log.previous_fatal():
            QTimer.singleShot(4000, lambda: self._notify_crash(
                "Z13-Helper crashed last time. The report is in Help → Error log."))

    # ------------------------------------------------------------------
    # Icon, menu bar, tray
    # ------------------------------------------------------------------

    def _make_app_icon(self) -> QIcon:
        if os.path.isfile(_ICON_PATH):
            icon = QIcon(_ICON_PATH)
            if not icon.isNull():
                return icon
        px = QPixmap(64, 64)
        px.fill(QColor(0, 0, 0, 0))
        p = QPainter(px)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor("#1a1d21"))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(0, 0, 64, 64, 12, 12)
        p.setPen(QColor("#76b900"))
        p.setFont(QFont("DejaVu Sans", 36, QFont.Weight.Black))
        p.drawText(px.rect(), Qt.AlignmentFlag.AlignCenter, "Z")
        p.end()
        return QIcon(px)

    def _build_menu_bar(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&File")
        fans = QAction("Fans + Power…", self)
        fans.setShortcut("Ctrl+F")
        fans.triggered.connect(self._toggle_fans_window)
        file_menu.addAction(fans)
        upd = QAction("Updates…", self)
        upd.triggered.connect(self._open_updates)
        file_menu.addAction(upd)
        file_menu.addSeparator()
        hide = QAction("Hide to Tray", self)
        hide.setShortcut("Ctrl+H")
        hide.triggered.connect(self.hide)
        file_menu.addAction(hide)
        quit_action = QAction("&Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self._user_quit)
        file_menu.addAction(quit_action)
        help_menu = mb.addMenu("&Help")
        err_log = QAction("Error log…", self)
        err_log.setShortcut("Ctrl+L")
        err_log.triggered.connect(lambda: self._open_error_log())
        help_menu.addAction(err_log)
        help_menu.addSeparator()
        about = QAction("About Z13-Helper", self)
        about.triggered.connect(self._show_about)
        help_menu.addAction(about)

    def _show_about(self):
        QMessageBox.about(
            self, "About Z13-Helper",
            f"<h3>Z13-Helper {APP_VERSION}</h3>"
            "<p>Hardware control for the ASUS ROG Flow Z13 (2025) on SteamOS.</p>"
            "<p>Layout after G-Helper (seerge/g-helper). Backend: <code>z13ctl</code>.</p>"
            "<p>Profiles bundle platform profile, power limits, fan curve, lighting and "
            "battery limit; the Fans + Power window edits them.</p>")

    def _setup_tray(self):
        self._tray = None
        self._tray_profile_actions = {}
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        self._tray = QSystemTrayIcon(self._app_icon, self)
        self._tray.setToolTip("Z13-Helper")
        self._tray_menu = QMenu()
        self._rebuild_tray_menu()
        self._tray.setContextMenu(self._tray_menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _rebuild_tray_menu(self):
        m = self._tray_menu
        m.clear()
        self._tray_profile_actions = {}
        show = m.addAction("Show / Hide")
        show.triggered.connect(self._toggle_window)
        m.addSeparator()
        hdr = m.addAction("Performance Mode")
        hdr.setEnabled(False)
        active = self.control.active_name()
        for name in self.control.names():
            a = m.addAction(name)
            a.setCheckable(True)
            a.setChecked(name == active)
            a.triggered.connect(lambda checked, n=name: self.control.switch(n))
            self._tray_profile_actions[name] = a
        m.addSeparator()
        fans = m.addAction("Fans + Power…")
        fans.triggered.connect(self._toggle_fans_window)
        m.addSeparator()
        q = m.addAction("Quit")
        q.triggered.connect(self._user_quit)

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_window()

    def _toggle_window(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.timer.setInterval(TICK_VISIBLE_MS)
        self._sync_screen_brightness()

    def hideEvent(self, ev):
        super().hideEvent(ev)
        self.timer.setInterval(TICK_HIDDEN_MS)
        if self._fans_window is not None:
            self._fans_window.hide()

    def closeEvent(self, event):
        if self._tray is not None:
            event.ignore()
            self.hide()
            self._tray.showMessage("Z13-Helper",
                                   "Running in the system tray. Press the side button or click the tray icon to reopen.",
                                   QSystemTrayIcon.MessageIcon.Information, 2000)
        else:
            event.accept()
            self._user_quit()

    def _user_quit(self):
        """A Quit the user chose: leave the marker that keeps the Decky plugin's
        desktop-app check from starting the app again (single_instance.py)."""
        import single_instance
        single_instance.mark_user_quit()
        QApplication.quit()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Ready")
        # Errors since the Error log was last opened; hidden at zero.
        self.error_badge = QPushButton("")
        self.error_badge.setObjectName("errorBadge")
        self.error_badge.setFlat(True)
        self.error_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.error_badge.setToolTip("Errors were logged. Click for the Error log (Help → Error log).")
        self.error_badge.clicked.connect(lambda: self._open_error_log())
        self.error_badge.hide()
        self.status_bar.addPermanentWidget(self.error_badge)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        col = QVBoxLayout(root)
        col.setContentsMargins(6, 4, 6, 4)
        col.setSpacing(0)

        # 1. Performance Mode
        self.perf = Panel("Performance Mode")
        self.maxfans_pill = pill("MAX FANS", orange=True)
        self.maxfans_pill.setToolTip("Both fans at 100%. Off restores the profile's curve.")
        self.maxfans_pill.clicked.connect(self._toggle_max_fans)
        self.maxfans_pill.setFixedHeight(28)
        tiles = QHBoxLayout()
        tiles.setSpacing(6)
        self._tiles = {}
        for name, accent in _TILES:
            t = ModeTile(name, accent, height=48)
            t.clicked.connect(lambda checked, n=name: self._tile_clicked(n))
            tiles.addWidget(t)
            self._tiles[name] = t
        self.fans_tile = ModeTile("Fans + Power", CUSTOM, secondary=True, height=48)
        self.fans_tile.clicked.connect(self._fans_tile_clicked)
        tiles.addWidget(self.fans_tile)
        self.perf.body.addLayout(tiles)
        prow = QHBoxLayout()
        prow.addWidget(QLabel("Profile"))
        self.profile_combo = QComboBox()
        self.profile_combo.currentTextChanged.connect(self._combo_changed)
        prow.addWidget(self.profile_combo, 1)
        prow.addWidget(self.maxfans_pill)
        self.perf.body.addLayout(prow)
        col.addWidget(self.perf)
        col.addWidget(self._sep())

        # 2. Keyboard & Rear Light
        self.light = Panel("Keyboard & Rear Light")
        self.light_target = QComboBox()
        for label, _devs in _LIGHT_TARGETS:
            self.light_target.addItem(label)
        self.light_target.setFixedWidth(110)
        self.light.add_head_widget(self.light_target)
        brow = QHBoxLayout()
        brow.setSpacing(6)
        self._bright_tiles = {}
        for label, level in _BRIGHTNESS:
            t = ModeTile(label, STANDARD, height=36)
            t.clicked.connect(lambda checked, lv=level, lb=label: self._change_brightness(lv, lb))
            brow.addWidget(t)
            self._bright_tiles[level] = t
        self.light.body.addLayout(brow)
        col.addWidget(self.light)
        col.addWidget(self._sep())

        # 3. Screen Brightness (the display backlight; Keyboard & Rear Light is above)
        self.screen_panel = Panel("Screen Brightness: --%")
        self.screen_slider = QSlider(Qt.Orientation.Horizontal)
        self.screen_slider.setRange(MIN_PERCENT, 100)
        self.screen_slider.setSingleStep(1)
        self.screen_slider.setPageStep(10)
        self.screen_slider.valueChanged.connect(self._screen_slider_moved)
        self._screen_write_timer = QTimer(self)
        self._screen_write_timer.setSingleShot(True)
        self._screen_write_timer.setInterval(SCREEN_WRITE_MS)
        self._screen_write_timer.timeout.connect(self._apply_screen_brightness)
        self.screen_panel.body.addWidget(self.screen_slider)
        if not self.screen.available():
            self.screen_slider.setEnabled(False)
            self.screen_panel.right.setText("no backlight control found")
        col.addWidget(self.screen_panel)
        col.addWidget(self._sep())

        # 4. Battery Charge Limit
        self.batt = Panel("Battery Charge Limit: --%")
        srow = QHBoxLayout()
        self.battery_slider = QSlider(Qt.Orientation.Horizontal)
        self.battery_slider.setRange(40, 100)
        self.battery_slider.setSingleStep(5)
        self.battery_slider.valueChanged.connect(lambda v: self.batt.title.setText(f"Battery Charge Limit: {v}%"))
        self.battery_slider.sliderReleased.connect(self._apply_battery_limit)
        srow.addWidget(self.battery_slider, 1)
        self.batt_100 = pill("100%")
        self.batt_100.clicked.connect(self._battery_100)
        srow.addWidget(self.batt_100)
        self.batt.body.addLayout(srow)
        col.addWidget(self.batt)
        col.addWidget(self._sep())

        # 5. Run on Startup · power source
        row4 = QHBoxLayout()
        row4.setContentsMargins(10, 6, 10, 6)
        self.startup = QCheckBox("Run on Startup")
        self.startup.setToolTip("Enable the resident z13-helper user service (tray + side button).")
        self.startup.toggled.connect(self._toggle_startup)
        row4.addWidget(self.startup)
        row4.addStretch(1)
        self.power_label = QLabel("")
        self.power_label.setObjectName("dim")
        row4.addWidget(self.power_label)
        col.addLayout(row4)

        # 6. Version · side button status
        row5 = QHBoxLayout()
        row5.setContentsMargins(10, 2, 10, 6)
        self.version_label = QLabel(f"Version: {APP_VERSION}")
        self.version_label.setObjectName("dim")
        self.version_label.setCursor(Qt.CursorShape.PointingHandCursor)
        self.version_label.mousePressEvent = lambda _e: self._open_updates()
        row5.addWidget(self.version_label)
        row5.addStretch(1)
        self.button_label = QLabel("")
        self.button_label.setObjectName("dim")
        row5.addWidget(self.button_label)
        col.addLayout(row5)

        # 7. Footer
        foot = QHBoxLayout()
        foot.setContentsMargins(10, 4, 10, 4)
        foot.setSpacing(6)
        self.btn_updates = QPushButton("Updates")
        self.btn_updates.clicked.connect(self._open_updates)
        self.btn_quit = QPushButton("Quit")
        self.btn_quit.clicked.connect(self._user_quit)
        foot.addWidget(self.btn_updates)
        foot.addWidget(self.btn_quit)
        col.addLayout(foot)
        col.addStretch(1)

    @staticmethod
    def _sep():
        f = QFrame()
        f.setFrameShape(QFrame.Shape.HLine)
        f.setStyleSheet(f"color: {BORDER};")
        return f

    def _load_initial_state(self):
        try:
            limit = self.controller.get_battery_limit()
            if limit is not None:
                self.battery_slider.setValue(limit)
                self.config.set("battery_limit", limit)
        except Exception:
            pass
        self.batt_100.setChecked(self.battery_slider.value() == 100)
        self._highlight_brightness(self.config.get("brightness", "medium"))
        self._sync_screen_brightness(force=True)
        rc, out, _ = host.run(["systemctl", "--user", "is-enabled", _SERVICE], timeout=10)
        self.startup.blockSignals(True)
        self.startup.setChecked(rc == 0 and out.strip() == "enabled")
        self.startup.blockSignals(False)

    # ------------------------------------------------------------------
    # Profiles
    # ------------------------------------------------------------------

    def _refresh_profile_ui(self):
        active = self.control.shown()       # the clicked profile lights up at once
        name = active["name"]
        # Title: "Performance Mode: Balanced+ 20W" — '+' = custom curve, W = custom SPL
        suffix = ("+" if active.get("fan_curve") else "") + (f" {active['tdp']['pl1']}W" if active.get("tdp") else "")
        self.perf.title.setText(f"Performance Mode: {name}{suffix}")
        builtin = name in self._tiles
        for n, t in self._tiles.items():
            t.setChecked(n == name)
        self.fans_tile.setChecked(not builtin)
        self.fans_tile.setAccent(ACCENT_FOR_BASE.get(active.get("base"), STANDARD) if not builtin else CUSTOM)
        names = self.control.names()
        self.profile_combo.blockSignals(True)
        if [self.profile_combo.itemText(i) for i in range(self.profile_combo.count())] != names:
            self.profile_combo.clear()
            self.profile_combo.addItems(names)
        self.profile_combo.setCurrentText(name)
        self.profile_combo.blockSignals(False)
        if self._tray is not None:
            if set(self._tray_profile_actions) != set(names):
                self._rebuild_tray_menu()
            for n, a in self._tray_profile_actions.items():
                a.setChecked(n == name)
        self.maxfans_pill.setChecked(self.control.shown_max_fans())
        if not self.control.pending_name:
            self.config.set("profile", active.get("base", "balanced"))

    def _tile_clicked(self, name):
        self._refresh_profile_ui()      # undo the click's own toggle until the apply lands
        self.control.switch(name)

    def _combo_changed(self, name):
        if name:
            self.control.switch(name)

    def _fans_tile_clicked(self):
        self._refresh_profile_ui()
        self._toggle_fans_window()

    def _toggle_fans_window(self):
        if self._fans_window is None:
            self._fans_window = FansWindow(self.control, self.controller, self.sensors, self)
        w = self._fans_window
        if w.isVisible():
            w.hide()
            return
        w.select(self.control.active_name())
        w.resize(675, max(self.height(), 540))
        w.move(max(0, self.x() - w.width() - 5), self.y())
        w.show()
        w.raise_()

    # ------------------------------------------------------------------
    # Max Fans
    # ------------------------------------------------------------------

    def _toggle_max_fans(self, checked):
        c = self.controller

        def on():
            try:
                info = c.get_fancurve()
                pts = info.get("points") or []
                if len(pts) == 8 and not all(pwm >= 255 for _, pwm, _ in pts):
                    max_fans_state.save(saved_curve=[[int(t), int(pwm)] for t, pwm, _ in pts])
            except Exception:
                pass
            c.set_fans_max()
            max_fans_state.save(enabled=True)
            return "Max Fans ON — both fans at 100%"

        def off():
            saved = max_fans_state.saved_curve()
            curve = self.control.active().get("fan_curve")
            if saved:
                c.set_fancurve_pwm(saved)
            elif curve:
                c.set_fancurve(curve)
            else:
                c.reset_fancurve()
            max_fans_state.save(enabled=False)
            return "Max Fans OFF — profile curve restored"

        self.control._submit("Max Fans", on if checked else off)

    # ------------------------------------------------------------------
    # Lighting
    # ------------------------------------------------------------------

    def _change_brightness(self, level, label):
        devices = _LIGHT_TARGETS[self.light_target.currentIndex()][1]
        self._highlight_brightness(level)

        def go():
            failed = self.controller.set_brightness(level, devices=devices)
            self.config.set("brightness", level)
            return f"Brightness {label}" + (f" ({', '.join(failed)} not responding)" if failed else "")
        self.control._submit("Brightness", go)

    def _highlight_brightness(self, level):
        for lv, t in self._bright_tiles.items():
            t.setChecked(lv == level)

    # ------------------------------------------------------------------
    # Screen brightness
    # ------------------------------------------------------------------

    def _screen_slider_moved(self, value):
        self.screen_panel.title.setText(f"Screen Brightness: {value}%")
        if not self._screen_write_timer.isActive():
            self._screen_write_timer.start()

    def _apply_screen_brightness(self):
        value = self.screen_slider.value()
        self._screen_last_write = time.monotonic()
        try:
            self.screen.set(value)
        except Exception as e:
            logging.getLogger("z13.screen").error("Screen brightness %d%% failed: %s", value, e)
            self.status_bar.showMessage(f"Screen brightness failed: {e}", 8000)

    def _sync_screen_brightness(self, force=False):
        """Follow brightness changed elsewhere (Fn keys, Plasma's slider, dim-on-idle)."""
        if not self.screen.available() or self.screen_slider.isSliderDown():
            return
        if not force and (self._screen_write_timer.isActive()
                          or time.monotonic() - self._screen_last_write < SCREEN_FOLLOW_GRACE_S):
            return
        value = self.screen.get()
        if value is None:
            return
        value = max(MIN_PERCENT, value)
        self.screen_slider.blockSignals(True)
        self.screen_slider.setValue(value)
        self.screen_slider.blockSignals(False)
        self.screen_panel.title.setText(f"Screen Brightness: {value}%")

    # ------------------------------------------------------------------
    # Battery
    # ------------------------------------------------------------------

    def _apply_battery_limit(self):
        value = self.battery_slider.value()
        self.batt_100.setChecked(value == 100)
        self.control._submit(f"Battery limit {value}%", lambda: (self.controller.set_battery_limit(value),
                                                                 self.config.set("battery_limit", value)))

    def _battery_100(self):
        self.battery_slider.setValue(100)
        self._apply_battery_limit()

    # ------------------------------------------------------------------
    # Startup service
    # ------------------------------------------------------------------

    def _toggle_startup(self, on):
        verb = "enable" if on else "disable"
        self.control._submit(f"Run on Startup {'on' if on else 'off'}",
                             lambda: host.run(["systemctl", "--user", verb, _SERVICE], timeout=15))

    # ------------------------------------------------------------------
    # Updates
    # ------------------------------------------------------------------

    def _check_updates(self, force=False):
        if self._update_thread and self._update_thread.isRunning():
            return
        self._update_thread = UpdateCheckThread(force, self)
        self._update_thread.finished_rows.connect(self._updates_done)
        self._update_thread.start()

    def _updates_done(self, rows):
        # Only installable rows count as "updates"; tracking-only rows (kernel,
        # asusctl, community repos) are roadmap input and live in the dialog.
        new = [r for r in rows if r["new"] and r.get("installer")]
        self._new_updates = new
        n = len(new)
        if n:
            self.version_label.setText(f"Version: {APP_VERSION} — {n} upstream update{'s' if n > 1 else ''}")
            self.version_label.setStyleSheet(f"color: {TURBO}; text-decoration: underline;")
            self.btn_updates.setText(f"Updates ({n})")
        else:
            self.version_label.setText(f"Version: {APP_VERSION}")
            self.version_label.setStyleSheet("")
            self.btn_updates.setText("Updates")
        keys = sorted(f"{r['id']}:{r['latest']}" for r in new)
        if keys and keys != self.config.get("updates_notified", []) and self._tray is not None:
            names = ", ".join(f"{r['label'].split(' (')[0]} {r['latest']}" for r in new[:4])
            self._tray.showMessage("Z13-Helper — upstream updates", names,
                                   QSystemTrayIcon.MessageIcon.Information, 6000)
            self.config.set("updates_notified", keys)

    # ------------------------------------------------------------------
    # Error log
    # ------------------------------------------------------------------

    def _open_error_log(self, show_crashes=False):
        from log_dialog import LogDialog
        if self._log_dialog is None:
            self._log_dialog = LogDialog(self, on_logging=self.set_logging)
        self._log_dialog.refresh(show_crashes=show_crashes)
        self._log_dialog.show()
        self._log_dialog.raise_()
        self._log_dialog.activateWindow()
        self._errors_unseen = 0
        self.error_badge.hide()

    def _make_fan_trace(self):
        """The desktop watchdog's trace file, or None when logging is off."""
        if not app_log.to_disk():
            return None
        try:
            return FanTrace.to_file(app_log.fan_trace_path("desktop"), self._sensors, "desktop")
        except OSError as e:
            logging.getLogger("z13.fans").warning("No fan trace: %s", e)
            return None

    def set_logging(self, enabled):
        """The Error log's checkbox: record the choice in install.json (the Decky
        plugin reads it on its next start) and apply it to this run. Returns an
        error message, or None."""
        try:
            install_record.set_logging(enabled)
        except OSError as e:
            logging.getLogger("z13").warning("Could not save the logging choice: %s", e)
            return f"Could not save the choice: {e}"
        app_log.set_to_disk(enabled)
        old, self._fan_watchdog.trace = self._fan_watchdog.trace, None
        if old is not None:     # close it first: a new trace reuses the same logger
            for h in list(old.log.handlers):
                old.log.removeHandler(h)
                h.close()
        self._fan_watchdog.trace = self._make_fan_trace()
        return None

    def _bump_error_badge(self, n=1):
        if self._log_dialog is not None and self._log_dialog.isVisible():
            return
        self._errors_unseen += n
        self.error_badge.setText(f"⚠ {self._errors_unseen} error{'s' if self._errors_unseen != 1 else ''}")
        self.error_badge.show()

    def _on_error_logged(self):
        self._bump_error_badge()

    def _on_crash_reported(self, path, summary):
        self.status_bar.showMessage(f"Error caught and saved: {summary[:90]}", 15000)
        self._notify_crash(f"An error was caught and saved ({summary[:80]}). See Help → Error log.")

    def _notify_crash(self, text):
        # One tray notice a minute at most: a failing timer slot would otherwise
        # notify every tick.
        if self._tray is None or time.monotonic() - self._last_crash_notice < 60:
            return
        self._last_crash_notice = time.monotonic()
        self._tray.showMessage("Z13-Helper", text, QSystemTrayIcon.MessageIcon.Warning, 6000)

    def _open_updates(self):
        if self._updates_dialog is None:
            self._updates_dialog = UpdatesDialog(self)
            self._updates_dialog.finished.connect(lambda _r: self._updates_done(update_watch.summary()))
        self._updates_dialog.populate(update_watch.summary())
        self._updates_dialog.show()
        self._updates_dialog.raise_()

    # ------------------------------------------------------------------
    # Telemetry tick
    # ------------------------------------------------------------------

    def _tick(self):
        try:
            apu = self.sensors.get_apu_temp()
            gpu = self.sensors.get_gpu_temp()
            fan1, fan2 = self.sensors.get_fan_speeds()
            parts = []
            if apu is not None:
                parts.append(f"CPU {apu:.0f}°C")
            if gpu is not None:
                parts.append(f"GPU {gpu:.0f}°C")
            if fan1 is not None and fan2 is not None:
                parts.append(f"Fans {fan1}/{fan2}RPM")   # fan1 = CPU side, fan2 = GPU side
            elif fan1 is not None:
                parts.append(f"Fan {fan1}RPM")
            text = "  ".join(parts)
            self.perf.right.setText(text)
            if self._tray is not None:
                self._tray.setToolTip("Z13-Helper\n" + text.replace("  ", "\n"))
        except Exception:
            pass
        try:
            b = self.sensors.get_battery()
            cap, st, pw = b.get("capacity"), b.get("status") or "", b.get("power_w")
            self.batt.right.setText(f"Charge: {cap}%" if cap is not None else "")
            if st == "Discharging":
                self.power_label.setText(f"On battery · {pw:.1f} W" if pw else "On battery")
            elif st:
                self.power_label.setText("On AC" + (f" · {st.lower()}" if st not in ("Charging",) else " · charging"))
        except Exception:
            pass
        # Decides and enqueues only; the recovery runs on the worker.
        self._show_fan_message(self._fan_runner.tick())
        if self.isVisible():
            self._sync_screen_brightness()
        # The Decky plugin (or z13-profile on the host) may have switched profiles.
        try:
            if self.control.shown()["name"] != self.profile_combo.currentText() or \
                    self.maxfans_pill.isChecked() != self.control.shown_max_fans():
                self._refresh_profile_ui()
        except Exception:
            pass

    def _show_fan_message(self, msg):
        if msg:
            logging.getLogger("z13.fans").warning("Fan watchdog: %s", msg)
            self.status_bar.showMessage(msg, 8000)

    @pyqtSlot(str, object)
    def _on_fan_recovery_done(self, label, result):
        self._show_fan_message(self._fan_runner.on_done(label, result))

    # ------------------------------------------------------------------
    # Side button listener
    # ------------------------------------------------------------------

    def _start_side_button_listener(self):
        """Bind the Armoury Crate side button to show/hide.

        Preferred path: read the 'Asus WMI hotkeys' evdev device (KEY_PROG3 =
        202). Inside the Flatpak the sandbox drops the 'users'/'input' groups,
        so the reader runs on the host via `flatpak-spawn --host` and streams
        PRESS lines back. Legacy InputPlumber D-Bus fallback kept for machines
        where InputPlumber still owns the button.
        """
        if host.in_flatpak():
            if self._start_host_side_button_reader():
                return
        else:
            device, code = find_side_button_device()
            if device:
                self._evdev_listener = EvdevListener(
                    device, lambda ev: self.sideButtonPressed.emit() if ev == code else None)
                self._evdev_listener.start()
                self.button_label.setText(f"Side button: evdev {os.path.basename(device)}")
                return
        self._start_inputplumber_dbus_listener()

    def _start_host_side_button_reader(self) -> bool:
        reader_src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evdev_listener.py")
        try:
            with open(reader_src) as f:
                source = f.read()
        except OSError as exc:
            self.button_label.setText(f"Side button: cannot read reader: {exc}")
            return False
        try:
            subprocess.run(["flatpak-spawn", "--host", "pkill", "-f", _SIDE_BUTTON_READER_MATCH],
                           capture_output=True, timeout=5)
        except Exception:
            pass
        try:
            proc = subprocess.Popen(
                ["flatpak-spawn", "--host", "--watch-bus", "python3", "-u", "-", "--emit", _SIDE_BUTTON_READER_TAG],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        except Exception as exc:
            self.button_label.setText(f"Side button: reader failed: {exc}")
            return False
        try:
            proc.stdin.write(source)
            proc.stdin.close()
        except Exception as exc:
            self.button_label.setText(f"Side button: stdin error: {exc}")
            proc.terminate()
            return False
        self._host_reader_proc = proc

        def pump():
            try:
                for line in proc.stdout:
                    if line.strip() == "PRESS":
                        self.sideButtonPressed.emit()
            except Exception:
                logging.getLogger("z13.button").exception("Side-button reader pipe failed")
            rc = proc.poll()
            if not getattr(self, "_quitting", False):
                # The button is dead until the app restarts (ROADMAP: self-heal is open).
                logging.getLogger("z13.button").error(
                    "Side-button host reader exited (rc %s); the button won't open the window "
                    "until the app restarts", rc)
        threading.Thread(target=pump, daemon=True).start()
        self.button_label.setText("Side button: active")
        return True

    def _stop_side_button_listener(self):
        self._quitting = True    # the reader's exit from here on is expected, not an error
        if self._host_reader_proc is not None:
            try:
                self._host_reader_proc.terminate()
            except Exception:
                pass
            try:
                subprocess.run(["flatpak-spawn", "--host", "pkill", "-f", _SIDE_BUTTON_READER_MATCH],
                               capture_output=True, timeout=5)
            except Exception:
                pass
            self._host_reader_proc = None
        if self._evdev_listener is not None:
            try:
                self._evdev_listener.stop()
            except Exception:
                pass
            self._evdev_listener = None

    def _start_inputplumber_dbus_listener(self):
        bus = QDBusConnection.systemBus()
        if not bus.isConnected():
            self.button_label.setText("Side button: unavailable")
            return
        try:
            r = subprocess.run(["dbus-send", "--system", "--print-reply", f"--dest={_INPUTPLUMBER_SERVICE}",
                                _COMPOSITE_DEV_PATH, f"{_COMPOSITE_DEV_IFACE}.SetTargetDevices",
                                "array:string:" + ",".join(_TARGET_DEVICES_WITH_DBUS)],
                               capture_output=True, text=True, timeout=3)
            if r.returncode != 0:
                self.button_label.setText("Side button: unavailable (InputPlumber)")
                return
            self._target_devices_set = True
        except Exception:
            self.button_label.setText("Side button: unavailable")
            return
        try:
            r = subprocess.run(["dbus-send", "--system", "--print-reply", f"--dest={_INPUTPLUMBER_SERVICE}",
                                _COMPOSITE_DEV_PATH, "org.freedesktop.DBus.Properties.Set",
                                f"string:{_COMPOSITE_DEV_IFACE}", "string:InterceptMode",
                                f"variant:uint32:{_INTERCEPT_MODE_ALL}"],
                               capture_output=True, text=True, timeout=3)
            self._intercept_mode_set = r.returncode == 0
        except Exception:
            pass
        connected = []
        for n in range(4):
            path = f"/org/shadowblip/InputPlumber/devices/target/dbus{n}"
            if bus.connect(_INPUTPLUMBER_SERVICE, path, _INPUTPLUMBER_IFACE, "InputEvent", "sd",
                           self._on_inputplumber_dbus):
                connected.append(f"dbus{n}")
        self.button_label.setText("Side button: InputPlumber" if connected else "Side button: unavailable")

    @pyqtSlot("QString", "double")
    def _on_inputplumber_dbus(self, event: str, value: float):
        if event == _QUICK_ACCESS_EVENT and value > 0.5:
            self._toggle_window()

    def _reset_intercept_mode(self):
        if not self._intercept_mode_set:
            return
        try:
            subprocess.run(["dbus-send", "--system", f"--dest={_INPUTPLUMBER_SERVICE}", _COMPOSITE_DEV_PATH,
                            "org.freedesktop.DBus.Properties.Set", f"string:{_COMPOSITE_DEV_IFACE}",
                            "string:InterceptMode", f"variant:uint32:{_INTERCEPT_MODE_NONE}"],
                           capture_output=True, timeout=3)
        except Exception:
            pass


if __name__ == "__main__":
    controller = HardwareController()
    sensors = SensorReader()
    config = ConfigManager(_CONFIG_PATH)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    window = DashboardUI(controller, sensors, config)
    window.show()
    sys.exit(app.exec())

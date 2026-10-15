#!/usr/bin/env python3
"""Z13-Helper — launch entrypoint."""

import logging
import os
import signal
import sys

# Ensure src/ is importable regardless of working directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import app_log
import install_record
import single_instance


def main():
    # Single-instance guard runs before PyQt6 is imported so a second launch
    # (or `--quit` from the unit's ExecStop) is cheap. See single_instance.py.
    if "--quit" in sys.argv:
        # Ask the resident instance to quit and wait for it to be gone.
        single_instance.send_command("quit", wait_exit=10.0, connect_timeout=0.5)
        sys.exit(0)

    lock_fd = single_instance.acquire_lock()
    if lock_fd is None and "--resident" in sys.argv:
        # The unit (ExecStart ... --resident) owns the resident instance. One started
        # some other way -- Plasma's session restore relaunches the app at login, at
        # the same moment the unit starts -- is replaced instead of kept: exiting here
        # would make systemd run ExecStop/ExecStopPost, which kill that other instance
        # too, leaving no app and no side-button reader (seen 2026-10-13/14).
        single_instance.send_command("quit", wait_exit=10.0, connect_timeout=2.0)
        lock_fd = single_instance.acquire_lock(wait=5.0)
        if lock_fd is None:
            print("Z13-Helper: couldn't take over the running instance.", file=sys.stderr)
            sys.exit(1)   # Restart=on-failure tries again
    if lock_fd is None:
        cmd = "show" if "--show" in sys.argv else "ping"
        delivered = single_instance.send_command(cmd)
        print(f"Z13-Helper already running; sent '{cmd}' "
              f"({'delivered' if delivered else 'no answer'}), exiting.",
              file=sys.stderr)
        sys.exit(0)

    # Only the resident instance logs: from here on, every log line, uncaught
    # exception (Qt slots and worker threads included) and hard crash is kept
    # under ~/.local/state/z13-helper/ (see app_log.py, Help -> Error log) --
    # if the user opted in (install.json "logging"; missing = on).
    app_log.setup(to_disk=install_record.logging_enabled())
    single_instance.clear_user_quit()

    from PyQt6.QtCore import QTimer
    from PyQt6.QtGui import QSessionManager
    from PyQt6.QtWidgets import QApplication
    from dashboard_ui import APP_VERSION, DashboardUI, _CONFIG_PATH
    from hardware_controller import HardwareController
    from sensor_reader import SensorReader
    from config_manager import ConfigManager

    app_log.set_version(APP_VERSION)
    app_log.install_qt_handler()

    controller = HardwareController()
    sensors = SensorReader()
    config = ConfigManager(_CONFIG_PATH)

    app = QApplication(sys.argv)
    # WM class ties the running window to the .desktop launcher icon.
    # Application/desktop name help with Wayland compositors and Plasma.
    app.setApplicationName("Z13-Helper")
    app.setApplicationDisplayName("Z13-Helper")
    app.setDesktopFileName("io.github.iannitram.Z13Helper")
    app.setQuitOnLastWindowClosed(False)  # keep running when window is hidden
    # Opt out of Plasma's session restore: the --user unit starts the app at login,
    # and a restored copy racing it is what left no app running (see --resident).
    def _never_restore(manager):
        manager.setRestartHint(QSessionManager.RestartHint.RestartNever)
    app.commitDataRequest.connect(_never_restore)
    app.saveStateRequest.connect(_never_restore)

    window = DashboardUI(controller, sensors, config)
    # Reset InputPlumber InterceptMode (legacy path) and stop the side-button
    # reader on clean exit.
    app.aboutToQuit.connect(window._reset_intercept_mode)
    app.aboutToQuit.connect(window._stop_side_button_listener)

    def on_command(cmd, _conn):
        if cmd == "show":
            window.show()
            window.raise_()
            window.activateWindow()
        elif cmd == "quit":
            app.quit()
            return True  # hold the connection: client sees EOF once we've exited
        return False

    server = single_instance.InstanceServer(on_command)
    app.aboutToQuit.connect(server.close)
    app.aboutToQuit.connect(lambda: logging.getLogger("z13").info("Quitting"))
    app.aboutToQuit.connect(app_log.shutdown)

    # SIGTERM/SIGINT -> clean quit, so aboutToQuit teardown (host side-button
    # reader) runs. Python only runs signal handlers between bytecodes, so a
    # no-op timer wakes the interpreter out of Qt's C++ event loop.
    signal.signal(signal.SIGTERM, lambda *_: app.quit())
    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal_pump = QTimer()
    signal_pump.timeout.connect(lambda: None)
    signal_pump.start(500)

    # Profile restore at login is the host's job (z13-restore-profile.service
    # -> `z13-profile restore`), not the app's.

    # Start hidden — press side button or click tray icon to open
    # Pass --show flag to open immediately (useful for debugging)
    if "--show" in sys.argv:
        window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()

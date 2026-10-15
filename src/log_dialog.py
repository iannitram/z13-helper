"""Help -> Error log: the app's live log, its crash reports (app_log.py) and the
fan watchdog's traces from both frontends (fan_watchdog.FanTrace)."""

import logging
import os
import time

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFont, QGuiApplication
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPlainTextEdit, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

import app_log

_LEVELS = [("Warnings and errors", logging.WARNING), ("Errors only", logging.ERROR),
           ("Everything", logging.INFO)]


def _mono(widget):
    f = QFont("monospace")
    f.setStyleHint(QFont.StyleHint.Monospace)
    f.setPointSizeF(8.5)
    widget.setFont(f)


class LogDialog(QDialog):
    # A record from any thread, handed to the GUI thread (queued connection).
    _record = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Error log — Z13-Helper")
        self.resize(820, 560)
        v = QVBoxLayout(self)

        self.info = QLabel("")
        self.info.setObjectName("dim")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        self.tabs = QTabWidget()
        v.addWidget(self.tabs, 1)

        # --- Log tab -----------------------------------------------------
        log_tab = QWidget()
        lv = QVBoxLayout(log_tab)
        lv.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Show"))
        self.level = QComboBox()
        for label, lvl in _LEVELS:
            self.level.addItem(label, lvl)
        self.level.currentIndexChanged.connect(self._fill_log)
        row.addWidget(self.level)
        row.addStretch(1)
        lv.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.log_view.setMaximumBlockCount(5000)
        _mono(self.log_view)
        lv.addWidget(self.log_view, 1)
        self.tabs.addTab(log_tab, "Log (this run)")

        # --- Crash reports tab -------------------------------------------
        crash_tab = QWidget()
        cv = QVBoxLayout(crash_tab)
        cv.setContentsMargins(0, 6, 0, 0)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.crash_list = QListWidget()
        self.crash_list.currentItemChanged.connect(self._show_crash)
        split.addWidget(self.crash_list)
        self.crash_view = QPlainTextEdit()
        self.crash_view.setReadOnly(True)
        self.crash_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        _mono(self.crash_view)
        split.addWidget(self.crash_view)
        split.setSizes([260, 560])
        cv.addWidget(split, 1)
        self.tabs.addTab(crash_tab, "Crash reports")

        # --- Fan trace tab -----------------------------------------------
        trace_tab = QWidget()
        tv = QVBoxLayout(trace_tab)
        tv.setContentsMargins(0, 6, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Watchdog"))
        self.trace_side = QComboBox()
        self.trace_side.addItem("Desktop app", "desktop")
        self.trace_side.addItem("Game Mode (Decky)", "gamemode")
        self.trace_side.currentIndexChanged.connect(self._fill_trace)
        row.addWidget(self.trace_side)
        reload_btn = QPushButton("Reload")
        reload_btn.clicked.connect(self._fill_trace)
        row.addWidget(reload_btn)
        row.addStretch(1)
        tv.addLayout(row)
        self.trace_view = QPlainTextEdit()
        self.trace_view.setReadOnly(True)
        self.trace_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        _mono(self.trace_view)
        tv.addWidget(self.trace_view, 1)
        self.tabs.addTab(trace_tab, "Fan trace")
        self.tabs.currentChanged.connect(lambda i: i == 2 and self._fill_trace())

        # --- Buttons -----------------------------------------------------
        h = QHBoxLayout()
        self.btn_copy = QPushButton("Copy")
        self.btn_copy.setToolTip("Copy what's shown (the log, or the selected crash report)")
        self.btn_copy.clicked.connect(self._copy)
        self.btn_folder = QPushButton("Open folder")
        self.btn_folder.setToolTip(app_log.LOG_DIR)
        self.btn_folder.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(app_log.LOG_DIR)))
        self.btn_clear = QPushButton("Delete crash reports")
        self.btn_clear.clicked.connect(self._clear_crashes)
        h.addWidget(self.btn_copy)
        h.addWidget(self.btn_folder)
        h.addWidget(self.btn_clear)
        h.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        h.addWidget(close)
        v.addLayout(h)

        self._record.connect(self._append)
        if app_log.ring() is not None:
            app_log.ring().add_listener(lambda item: self._record.emit(item[0], item[2]))
        self.refresh()

    # ------------------------------------------------------------------

    def refresh(self, show_crashes=False):
        self._fill_log()
        self._fill_crashes()
        if show_crashes and self.crash_list.count():
            self.tabs.setCurrentIndex(1)
            self.crash_list.setCurrentRow(0)

    def _min_level(self):
        return self.level.currentData() or logging.WARNING

    def _fill_log(self):
        r = app_log.ring()
        lines = r.lines(self._min_level()) if r else []
        self.log_view.setPlainText("\n".join(lines) if lines else "(nothing at this level yet)")
        self._scroll_to_end()
        counts = r.counts if r else {}
        errs = counts.get("ERROR", 0) + counts.get("CRITICAL", 0)
        self.info.setText(
            f"This run: {errs} error(s), {counts.get('WARNING', 0)} warning(s). "
            f"Full log on disk: {app_log.log_path()}")

    def _append(self, levelno, text):
        if levelno < self._min_level():
            return
        if self.log_view.toPlainText().startswith("(nothing"):
            self.log_view.clear()
        self.log_view.appendPlainText(text)
        self._scroll_to_end()

    def _scroll_to_end(self):
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _fill_crashes(self):
        self.crash_list.clear()
        reports = app_log.list_crash_reports()
        for path, mtime, summary in reports:
            kind = "Hard crash" if os.path.basename(path).startswith("fatal-") else "Exception"
            item = QListWidgetItem(f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(mtime))}  "
                                   f"{kind}\n{summary[:80]}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self.crash_list.addItem(item)
        self.tabs.setTabText(1, f"Crash reports ({len(reports)})")
        self.btn_clear.setEnabled(bool(reports))
        if not reports:
            self.crash_view.setPlainText("No crash reports. Uncaught errors and hard crashes "
                                         "are saved here automatically.")

    def _show_crash(self, item, _prev=None):
        if item is None:
            return
        self.crash_view.setPlainText(app_log.read_text(item.data(Qt.ItemDataRole.UserRole)))

    def _fill_trace(self):
        path = app_log.fan_trace_path(self.trace_side.currentData())
        if os.path.exists(path):
            text = app_log.read_text(path, max_bytes=256 * 1024)
        else:
            text = (f"No trace yet ({path}). It starts when that watchdog runs: the desktop app "
                    "while it's open, the Decky plugin once installed with this version.")
        self.trace_view.setPlainText(text)
        sb = self.trace_view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def _copy(self):
        view = (self.log_view, self.crash_view, self.trace_view)[self.tabs.currentIndex()]
        QGuiApplication.clipboard().setText(view.toPlainText())
        self.info.setText("Copied to the clipboard.")

    def _clear_crashes(self):
        if QMessageBox.question(self, "Delete crash reports",
                                "Delete all saved crash reports? The app log is kept.") \
                != QMessageBox.StandardButton.Yes:
            return
        n = app_log.clear_crash_reports()
        logging.getLogger("z13").info("Deleted %d crash report(s) from the Error log window", n)
        self._fill_crashes()

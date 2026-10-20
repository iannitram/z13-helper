"""Updates dialog — what moved upstream (G-Helper's "Updates" window idea)."""

import os
import subprocess
import time

from PyQt6.QtCore import Qt, QUrl, QThread, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QColor
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QAbstractItemView, QHeaderView, QMessageBox, QPlainTextEdit,
)

import host
import install_record
import update_watch
from theme import TURBO, TEXT_DIM

_INSTALLERS_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "installers.py")


def _installer_cmd(action, target):
    """Run installers.py on the host: inside the Flatpak the sandbox has no
    network and no sudo, so the file is fed to the host's python3 on stdin."""
    if host.in_flatpak():
        return ["flatpak-spawn", "--host", "python3", "-u", "-", action, target], True
    return ["python3", "-u", _INSTALLERS_SRC, action, target], False


def installer_plan(target):
    cmd, feed = _installer_cmd("plan", target)
    src = open(_INSTALLERS_SRC).read() if feed else None
    p = subprocess.run(cmd, input=src, capture_output=True, text=True, timeout=30)
    return (p.stdout or p.stderr).strip()


class InstallThread(QThread):
    line = pyqtSignal(str)
    finished_rc = pyqtSignal(int)

    def __init__(self, target, parent=None, argv=None):
        super().__init__(parent)
        self.target = target
        self.argv = argv     # a ready command (Repair install) instead of installers.py

    def run(self):
        if self.argv:
            cmd, feed = self.argv, False
        else:
            cmd, feed = _installer_cmd("install", self.target)
        try:
            p = subprocess.Popen(cmd, stdin=subprocess.PIPE if feed else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            if feed:
                with open(_INSTALLERS_SRC) as f:
                    p.stdin.write(f.read())
                p.stdin.close()
            for line in p.stdout:
                self.line.emit(install_record.clean(line.rstrip("\n")))
            self.finished_rc.emit(p.wait())
        except Exception as e:  # noqa: BLE001
            self.line.emit(f"FAILED to start installer: {e}")
            self.finished_rc.emit(1)


class InstallDialog(QDialog):
    def __init__(self, target, label, parent=None, argv=None, title=None):
        super().__init__(parent)
        self.setWindowTitle(title or f"Installing {label}")
        self.resize(720, 420)
        v = QVBoxLayout(self)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        h = QHBoxLayout()
        self.status = QLabel("Running…")
        h.addWidget(self.status, 1)
        self.btn_close = QPushButton("Close")
        self.btn_close.setEnabled(False)
        self.btn_close.clicked.connect(self.accept)
        h.addWidget(self.btn_close)
        v.addLayout(h)
        self.rc = None
        self._lines = []
        self._repair = argv is not None
        self._t = InstallThread(target, self, argv=argv)
        self._t.line.connect(self._append)
        self._t.finished_rc.connect(self._done)
        self._t.start()

    def _append(self, line):
        self._lines.append(line)
        self.log.appendPlainText(line)

    def _done(self, rc):
        self.rc = rc
        if self._repair:
            green, text = install_record.verdict(self._lines[-15:], rc)
            self.status.setText(("✓ " if green else "✗ ") + text)
            self.status.setStyleSheet(f"color: {'#06B48A' if green else TURBO}; font-weight: bold;")
        else:
            self.status.setText("Done ✓" if rc == 0 else install_record.failure_text(self._lines[-40:], rc))
        self.btn_close.setEnabled(True)

    def closeEvent(self, ev):
        if self.rc is None:
            ev.ignore()      # don't orphan a running installer
        else:
            ev.accept()


class UpdateCheckThread(QThread):
    finished_rows = pyqtSignal(list)

    def __init__(self, force=False, parent=None):
        super().__init__(parent)
        self.force = force

    def run(self):
        try:
            rows = update_watch.check(force=self.force)
        except Exception:  # noqa: BLE001
            rows = update_watch.summary()
        self.finished_rows.emit(rows)


class UpdatesDialog(QDialog):
    COLS = ["Component", "Installed", "Latest", "Date", "What / why it matters"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Updates — upstream watch")
        self.resize(780, 560)
        v = QVBoxLayout(self)
        self.info = QLabel("")
        self.info.setObjectName("dim")
        v.addWidget(self.info)
        self.last_run = QLabel("")
        self.last_run.setObjectName("dim")
        v.addWidget(self.last_run)

        t1 = QLabel("Installable from here")
        t1.setObjectName("title")
        v.addWidget(t1)
        self.table = self._make_table()
        v.addWidget(self.table, 2)

        t2 = QLabel("Tracking only — reference for the roadmap, nothing to run")
        t2.setObjectName("title")
        v.addWidget(t2)
        self.track = self._make_table()
        v.addWidget(self.track, 3)

        h = QHBoxLayout()
        self.btn_check = QPushButton("Check now")
        self.btn_check.clicked.connect(lambda: self.check(force=True))
        self.btn_seen = QPushButton("Mark all seen")
        self.btn_seen.clicked.connect(self._mark_seen)
        self.btn_install = QPushButton("Install / Update")
        self.btn_install.setEnabled(False)
        self.btn_install.clicked.connect(self._install)
        h.addWidget(self.btn_check)
        h.addWidget(self.btn_seen)
        h.addWidget(self.btn_install)
        self.btn_repair = QPushButton("Repair install")
        self.btn_repair.clicked.connect(self._repair)
        h.addWidget(self.btn_repair)
        h.addStretch(1)
        h.addWidget(QLabel("Double-click a row to open it. Installs ask first and log every step."))
        self.table.itemSelectionChanged.connect(self._selection_changed)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        h.addWidget(close)
        v.addLayout(h)
        self._rows = []        # installable rows (table)
        self._track_rows = []  # tracking rows (track)
        self._thread = None
        self.populate(update_watch.summary())

    def _make_table(self):
        t = QTableWidget(0, len(self.COLS))
        t.setHorizontalHeaderLabels(self.COLS)
        t.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.cellDoubleClicked.connect(lambda r, c, tbl=t: self._open(tbl, r))
        return t

    def _fill(self, table, rows):
        table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            latest = "check failed" if row.get("error") else (row["latest"] or "—")
            vals = [row["label"], row["installed"] or "—", latest, row["published"] or "",
                    (row["note"] or "") + (("\n" + (row["notes"] or "")) if row.get("notes") else "")]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val.splitlines()[0] if c == 4 else val)
                if c == 4:
                    it.setToolTip(val)
                if row["new"]:
                    it.setForeground(QColor(TURBO))
                if row.get("error"):
                    it.setToolTip(f"Check failed: {row['error']}")
                table.setItem(r, c, it)

    def populate(self, rows):
        self._rows = [r for r in rows if r.get("installer")]
        self._track_rows = [r for r in rows if not r.get("installer")]
        self._fill(self.table, self._rows)
        self._fill(self.track, self._track_rows)
        ts = update_watch.last_checked()
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else "never"
        n_inst = sum(1 for r in self._rows if r["new"])
        n_track = sum(1 for r in self._track_rows if r["new"])
        self.info.setText(f"Last checked: {when} · {n_inst} installable update{'s' if n_inst != 1 else ''} · "
                          f"{n_track} tracked change{'s' if n_track != 1 else ''}")
        self._refresh_repair()

    def _refresh_repair(self):
        rec = install_record.load()
        self.last_run.setText(install_record.last_run_line(rec))
        if rec and rec.get("verify") in ("green", "red"):
            self.last_run.setStyleSheet(f"color: {'#06B48A' if rec['verify'] == 'green' else TURBO};")
        repo = install_record.repo_path(rec)
        self.btn_repair.setEnabled(bool(repo))
        self.btn_repair.setToolTip(
            f"Run ./install.sh --repair from {repo}" if repo else
            "Disabled: ~/.config/z13-helper/install.json has no repo_path yet. "
            "Run ./install.sh (or --repair) once from the repo in a terminal.")

    def _repair(self):
        repo = install_record.repo_path(install_record.load())
        if not repo:
            self._refresh_repair()
            return
        in_fp = host.in_flatpak()
        rc, _, _ = host.run(["test", "-f", f"{repo}/install.sh"], timeout=10)
        if rc != 0:
            QMessageBox.warning(self, "Repair install",
                                f"{repo}/install.sh was not found on the host.\n"
                                "Run ./install.sh once from the current checkout.")
            return
        ans = QMessageBox.question(
            self, "Repair install?", install_record.plan_text(repo) + "\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        dlg = InstallDialog(None, "install", self, argv=install_record.repair_argv(repo, in_fp),
                            title=f"Repair install — {repo}")
        dlg.exec()
        self._refresh_repair()

    def check(self, force=False):
        if self._thread and self._thread.isRunning():
            return
        self.btn_check.setEnabled(False)
        self.info.setText("Checking…")
        self._thread = UpdateCheckThread(force, self)
        self._thread.finished_rows.connect(self._done)
        self._thread.start()

    def _done(self, rows):
        self.btn_check.setEnabled(True)
        self.populate(rows)

    def _mark_seen(self):
        update_watch.mark_seen()
        self.populate(update_watch.summary())

    def _selected_row(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        return rows[0].row() if rows else None

    def _selection_changed(self):
        r = self._selected_row()
        row = self._rows[r] if r is not None and r < len(self._rows) else None
        self.btn_install.setEnabled(bool(row))
        self.btn_install.setToolTip(f"Install / update {row['label']}" if row else "")

    def _install(self):
        r = self._selected_row()
        if r is None:
            return
        row = self._rows[r]
        target = row.get("installer")
        if not target:
            return
        try:
            plan = installer_plan(target)
        except Exception as e:  # noqa: BLE001
            plan = f"(could not read the plan: {e})"
        ans = QMessageBox.question(
            self, f"Install {row['label']}?",
            f"This will run on the host:\n\n{plan}\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if ans != QMessageBox.StandardButton.Yes:
            return
        dlg = InstallDialog(target, row["label"], self)
        dlg.exec()
        self.check(force=True)

    def _open(self, table, row):
        rows = self._rows if table is self.table else self._track_rows
        url = rows[row].get("url") if row < len(rows) else None
        if url:
            QDesktopServices.openUrl(QUrl(url))

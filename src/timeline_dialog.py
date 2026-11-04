"""Timeline dialog (Phase 16g, #135): entries newest first, compare two, roll one back."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

import timeline
import timeline_view

ROLLBACK_LABEL = "Timeline roll back"


class TimelineDialog(QDialog):
    def __init__(self, control, parent=None):
        super().__init__(parent)
        self.control = control            # ProfileControl: runs the roll back on its worker
        self._entries = []
        self.setWindowTitle("Timeline")
        self.resize(560, 520)
        col = QVBoxLayout(self)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.itemSelectionChanged.connect(self._selection_changed)
        col.addWidget(self.list, 3)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        col.addWidget(self.detail, 2)
        self.why = QLabel("")
        self.why.setWordWrap(True)
        self.why.setObjectName("dim")
        col.addWidget(self.why)
        row = QHBoxLayout()
        self.btn_compare = QPushButton("Compare two")
        self.btn_compare.clicked.connect(self._compare)
        self.btn_roll = QPushButton("Roll back")
        self.btn_roll.clicked.connect(self._roll_back)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for b in (self.btn_compare, self.btn_roll):
            row.addWidget(b)
        row.addStretch(1)
        row.addWidget(close)
        col.addLayout(row)
        self.control.worker.done.connect(self._on_done)
        self.populate()

    def populate(self):
        self._entries = timeline.read_entries(limit=200, newest_first=True)
        self.list.clear()
        for r in timeline_view.rows(self._entries):
            mark = "" if r["ok"] else "  [failed]"
            item = QListWidgetItem(f"{r['ts']}   {r['summary']}{mark}")
            item.setData(Qt.ItemDataRole.UserRole, r["id"])
            self.list.addItem(item)
        self._selection_changed()

    def _selected(self):
        ids = [i.data(Qt.ItemDataRole.UserRole) for i in self.list.selectedItems()]
        return [e for e in self._entries if e.get("id") in ids]

    def _selection_changed(self):
        sel = self._selected()
        self.btn_compare.setEnabled(len(sel) == 2)
        reason = timeline_view.why_not(sel[0], timeline_view._rolled_back_ids(self._entries)) \
            if len(sel) == 1 else None
        self.btn_roll.setEnabled(len(sel) == 1 and reason is None)
        self.why.setText(f"Can't be undone: {reason}" if reason else "")
        if len(sel) == 1:
            self.detail.setPlainText(timeline_view.describe(sel[0]))

    def _compare(self):
        a, b = self._selected()
        self.detail.setPlainText(timeline_view.format_compare(a, b))

    def _roll_back(self):
        sel = self._selected()
        if len(sel) != 1:
            return
        entry, entries = sel[0], list(self._entries)
        self.btn_roll.setEnabled(False)
        self.control._submit(ROLLBACK_LABEL, lambda: (
            timeline_view.roll_back(entry, entries), f"Rolled back: {entry.get('summary')}")[1])

    def _on_done(self, label, _result):
        if label == ROLLBACK_LABEL:
            self.populate()

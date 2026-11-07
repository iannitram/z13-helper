"""Read-only desktop view of the append-only machine timeline."""

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QMessageBox, QPushButton, QSplitter, QVBoxLayout,
)

import timeline


def _label(entry):
    return f"{entry.get('ts', '?')}  ·  {entry.get('kind', 'change')}  ·  {entry.get('summary', '')}"


class TimelineDialog(QDialog):
    """Browse newest-first entries and compare the before/after snapshots."""

    def __init__(self, parent=None, rollback=None):
        super().__init__(parent)
        self.setWindowTitle("Machine timeline")
        self.resize(820, 520)
        self.entries = timeline.read_entries(limit=100, newest_first=True)
        self.rollback = rollback
        layout = QVBoxLayout(self)
        split = QSplitter(Qt.Orientation.Horizontal)
        self.list = QListWidget()
        self.details = QListWidget()
        split.addWidget(self.list)
        split.addWidget(self.details)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        layout.addWidget(split, 1)
        compare = QHBoxLayout()
        compare.addWidget(QLabel("Compare"))
        self.left = QComboBox()
        self.right = QComboBox()
        for i, entry in enumerate(self.entries):
            text = _label(entry)
            self.left.addItem(text, i)
            self.right.addItem(text, i)
        compare.addWidget(self.left, 1)
        compare.addWidget(QLabel("with"))
        compare.addWidget(self.right, 1)
        self.compare_result = QListWidget()
        layout.addLayout(compare)
        layout.addWidget(self.compare_result, 1)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.rollback_button = QPushButton("Roll back…")
        self.rollback_button.hide()
        self.rollback_button.clicked.connect(self._rollback_selected)
        layout.addWidget(self.rollback_button, alignment=Qt.AlignmentFlag.AlignLeft)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close, alignment=Qt.AlignmentFlag.AlignRight)
        for entry in self.entries:
            item = QListWidgetItem(_label(entry))
            self.list.addItem(item)
        self.list.currentRowChanged.connect(self._show_entry)
        self.left.currentIndexChanged.connect(self._compare)
        self.right.currentIndexChanged.connect(self._compare)
        if self.entries:
            self.list.setCurrentRow(0)
            self._compare()
        else:
            self.details.addItem("No timeline entries yet.")
            self.left.setEnabled(False)
            self.right.setEnabled(False)

    def _show_entry(self, row):
        self.details.clear()
        if not 0 <= row < len(self.entries):
            return
        entry = self.entries[row]
        for value in entry.get("changed", []):
            self.details.addItem(str(value))
        rollback = entry.get("rollback")
        if timeline.rollback_spec(entry):
            self.note.setText("A supported rollback record is available. Applying it creates a new timeline entry.")
            self.rollback_button.setVisible(self.rollback is not None)
        else:
            reason = entry.get("no_rollback_reason") or ("This rollback record format is not supported by the app." if rollback else "No rollback record was saved.")
            self.note.setText("Cannot be undone: " + str(reason))
            self.rollback_button.setVisible(False)

    def _rollback_selected(self):
        row = self.list.currentRow()
        if not (0 <= row < len(self.entries)) or self.rollback is None:
            return
        entry = self.entries[row]
        if QMessageBox.question(self, "Roll back change?", f"Apply the saved prior state for:\n\n{_label(entry)}") != QMessageBox.StandardButton.Yes:
            return
        ok, message = self.rollback(entry)
        QMessageBox.information(self, "Timeline rollback", message)
        if ok:
            self.accept()

    def _compare(self, *_):
        self.compare_result.clear()
        if not self.entries:
            return
        a = self.entries[self.left.currentData()]
        b = self.entries[self.right.currentData()]
        left = a.get("after") if isinstance(a.get("after"), dict) else {}
        right = b.get("after") if isinstance(b.get("after"), dict) else {}
        for key in sorted(set(left) | set(right)):
            if left.get(key) != right.get(key):
                self.compare_result.addItem(f"{key}: {json.dumps(left.get(key), sort_keys=True)}  →  {json.dumps(right.get(key), sort_keys=True)}")
        if not self.compare_result.count():
            self.compare_result.addItem("The selected after-states match.")

"""#22: a non-Z13 machine issues no ASUS write; batteryless hides charging; reprobe works.

The window tests need PyQt6 (absent from the minimal CI image) and skip without it.
"""

import pytest

pytest.importorskip("PyQt6")
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import profile_control


class _Worker:
    def __init__(self):
        self.submitted = []
        self.done = type("S", (), {"connect": lambda self, f: None})()

    def submit(self, label, fn):
        self.submitted.append(label)


def _control(allowed):
    from PyQt6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    w = _Worker()
    c = profile_control.ProfileControl(object(), w)
    c.writes_allowed = lambda: allowed
    return c, w


def test_unsupported_machine_submits_nothing():
    c, w = _control(False)
    c._submit("Battery limit 80%", lambda: None)
    c.switch("Turbo")
    assert w.submitted == [] and c.pending_name is None and c._pending == 0


def test_supported_machine_still_submits():
    c, w = _control(True)
    c._submit("Battery limit 80%", lambda: None)
    assert w.submitted == ["Battery limit 80%"]

"""The profile controller sends a hardware timeout to the UI without wrapping it."""

import pytest

qtcore = pytest.importorskip("PyQt6.QtCore")
qttest = pytest.importorskip("PyQt6.QtTest")
from PyQt6.QtCore import QCoreApplication, QObject, pyqtSignal
from PyQt6.QtTest import QSignalSpy

from profile_control import ProfileControl


class Worker(QObject):
    done = pyqtSignal(str, object)

    def submit(self, _label, _fn):
        raise AssertionError("the signal test does not submit work")


def test_profile_control_emits_write_timeout_exactly_to_ui():
    app = QCoreApplication.instance() or QCoreApplication([])
    worker = Worker()
    control = ProfileControl(controller=None, worker=worker)
    spy = QSignalSpy(control.message)
    message = "z13ctl write timed out after 20 s (kernel asus-wmi hang?)"

    control._on_done("Profile → Turbo", [("tdp", False, message)])

    assert len(spy) == 1
    assert spy[0][0] == f"{message} (step: tdp)"
    assert app is not None


def test_profile_control_does_not_confirm_when_base_profile_fails():
    app = QCoreApplication.instance() or QCoreApplication([])
    worker = Worker()
    control = ProfileControl(controller=None, worker=worker)
    control.pending_name = "Turbo"
    spy = QSignalSpy(control.profile_applied)
    message = "z13ctl write timed out after 20 s (kernel asus-wmi hang?)"

    control._on_done("Profile → Turbo", [("profile", False, message)])

    assert len(spy) == 0
    assert app is not None


def test_profile_control_confirms_when_every_step_succeeds():
    app = QCoreApplication.instance() or QCoreApplication([])
    worker = Worker()
    control = ProfileControl(controller=None, worker=worker)
    control.pending_name = "Turbo"
    spy = QSignalSpy(control.profile_applied)

    control._on_done("Profile → Turbo", [("profile", True, "performance"),
                                         ("fans", True, "firmware auto")])

    assert len(spy) == 1
    assert spy[0][0] == "Turbo"
    assert app is not None

"""ProfileControl.next_name / cycle (issue #131): the next profile, wrapping, from what is shown."""

import pytest

pytest.importorskip("PyQt6.QtCore")
from PyQt6.QtCore import QObject, pyqtSignal

from profile_control import ProfileControl


class Worker(QObject):
    done = pyqtSignal(str, object)

    def submit(self, *_a):
        pass


def make(names, shown, monkeypatch):
    c = ProfileControl(controller=None, worker=Worker())
    monkeypatch.setattr(c, "names", lambda: names)
    monkeypatch.setattr(c, "shown", lambda: {"name": shown})
    return c


def test_next_wraps(monkeypatch):
    assert make(["A", "B", "C"], "A", monkeypatch).next_name() == "B"
    assert make(["A", "B", "C"], "C", monkeypatch).next_name() == "A"


def test_nothing_to_cycle_with_one_profile_or_unknown_shown(monkeypatch):
    assert make(["A"], "A", monkeypatch).next_name() is None
    assert make(["A", "B"], "Z", monkeypatch).next_name() is None


def test_cycle_switches(monkeypatch):
    c = make(["A", "B"], "A", monkeypatch)
    seen = []
    monkeypatch.setattr(c, "switch", seen.append)
    assert c.cycle() == "B" and seen == ["B"]

"""Exercise the desktop signal adapter with a tiny Qt stand-in in pytest-only CI."""
import importlib.util
from pathlib import Path
import sys
import types


def test_host_event_reaches_desktop_refresh_and_shutdown(monkeypatch):
    class Signal:
        def __init__(self):
            self.callbacks = []
        def connect(self, callback):
            self.callbacks.append(callback)
        def emit(self, *args):
            for fn in self.callbacks:
                fn(*args)
    class Declaration:
        def __set_name__(self, owner, name):
            self.name = name
        def __get__(self, instance, owner):
            if instance is None:
                return self
            return instance.__dict__.setdefault(self.name, Signal())
    class QObject:
        def __init__(self, parent=None):
            pass
    qtcore = types.ModuleType('PyQt6.QtCore')
    qtcore.QObject = QObject
    qtcore.pyqtSignal = lambda *_: Declaration()
    monkeypatch.setitem(sys.modules, 'PyQt6', types.ModuleType('PyQt6'))
    monkeypatch.setitem(sys.modules, 'PyQt6.QtCore', qtcore)
    monkeypatch.setitem(sys.modules, 'apply_worker', types.SimpleNamespace(ApplyWorker=object))
    path = Path(__file__).resolve().parents[1] / 'src/profile_control.py'
    spec = importlib.util.spec_from_file_location('profile_control_under_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    class Stream:
        def __init__(self, callback):
            self.callback = callback
            self.closed = False
        def start(self):
            return self
        def close(self):
            self.closed = True
    monkeypatch.setattr(module, 'StateStream', Stream)
    control = module.ProfileControl(None, types.SimpleNamespace(done=Signal()))
    refreshed, toggles = [], []
    control.changed.connect(lambda: refreshed.append(control.backend_state))
    control.toggle_window.connect(lambda: toggles.append(1))
    control.start_events()
    control._events.callback({'profile': 'Turbo'}, 'state-changed')
    control._events.callback({'on_ac': False}, 'power-source')
    control._events.callback(None, 'gui-toggle')  # evdev remains selected
    assert refreshed == [{'profile': 'Turbo'}, {'on_ac': False}]
    assert not toggles
    control.close_events()
    assert control._events.closed

    # A profile applied before mirroring was installed still keeps its backend
    # identity when the desktop editor renames it.
    import profiles
    from z13ctl_client import Z13CtlClient
    p = {"name": "Travel", "base": "balanced", "builtin": False}
    profiles.upsert_profile(p)
    original = Z13CtlClient.mapped_profile_name(p)
    assert control.rename("Travel", "Away")
    assert Z13CtlClient.mapped_profile_name(profiles.get_profile("Away")) == original

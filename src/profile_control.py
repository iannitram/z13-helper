"""Profile store + live hardware pushes, shared by the main window and the
Fans + Power window.

Both windows edit *the same store* (profiles.json) and both show the active
profile, so a single object owns the store access and emits `changed` when
anything moved. Hardware writes go through the ApplyWorker so the UI never
blocks; results come back on `message`.
"""

import logging

from PyQt6.QtCore import QObject, pyqtSignal

import profiles
import max_fans_state
import state_events
import state_authority as authority
from state_stream import StateStream
from hardware_controller import HardwareController
from apply_worker import ApplyWorker
from fan_watchdog import FAN_RECOVERY_LABEL


logger = logging.getLogger("z13.profiles")
_WRITE_TIMEOUT_PREFIX = "z13ctl write timed out after "


class ProfileControl(QObject):
    changed = pyqtSignal()        # store (active name or contents) changed
    message = pyqtSignal(str)     # human-readable result for the status bar
    busy = pyqtSignal(bool)
    state_event = pyqtSignal(object, str)
    toggle_window = pyqtSignal()

    def __init__(self, controller: HardwareController, worker: ApplyWorker, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.worker = worker
        self.worker.done.connect(self._on_done)
        self._pending = 0
        # Profile the user just picked, shown at once while the apply runs
        # (a wedged lightbar alone makes an apply take 5 s).
        self.pending_name = None
        # Set by the window: returns False on a machine the app does not support
        # (platform_support), where no hardware write may be issued.
        self.writes_allowed = lambda: True
        self.backend_state = {}
        self.state_event.connect(self.dispatch_event)
        self._events = None

    def start_events(self):
        self._events = StateStream(lambda state, event: self.state_event.emit(state, event)).start()

    def close_events(self):
        if self._events:
            self._events.close()

    def dispatch_event(self, state, name):
        # The evdev watcher remains the selected button path; gui-toggle must
        # not double-toggle the window. This slot always runs on Qt's thread.
        self.backend_state = state or self.backend_state
        state_events.dispatch_event(name, False, refresh=self.changed.emit,
                                    toggle_window=self.toggle_window.emit)

    # ---------------- store access ----------------

    def store(self, create=False):
        return profiles.load_store(create=create)

    def names(self):
        return [p["name"] for p in self.store()["profiles"]]

    def active_name(self):
        return self.store().get("active")

    def active(self):
        s = self.store()
        return profiles.get_profile(s.get("active"), s) or s["profiles"][0]

    def get(self, name):
        return profiles.get_profile(name)

    def shown(self):
        """What the UI should highlight: the profile being applied, else the active one."""
        if self.pending_name:
            p = self.get(self.pending_name)
            if p:
                return p
        return self.active()

    def shown_max_fans(self):
        """Max Fans as it will be once the pending switch lands (profiles.apply rules)."""
        on = max_fans_state.is_enabled()
        if self.pending_name and self.pending_name != self.active_name():
            target, current = self.get(self.pending_name) or {}, self.active()
            if target.get("max_fans"):
                return True
            if current.get("max_fans"):
                return False
        return on

    # ---------------- switching ----------------

    def switch(self, name):
        """Apply profile `name` (hooks included) in the background."""
        if name == self.active_name() or name == self.pending_name:
            return
        if not self.writes_allowed():
            self.message.emit("Unsupported machine: hardware controls are disabled")
            return
        self.pending_name = name
        self.message.emit(f"Applying {name}…")
        self.changed.emit()
        self._submit(f"Profile → {name}", lambda: authority.apply_profile(name))

    def reapply_active(self):
        name = self.active_name()
        self._submit(f"Re-applied {name}", lambda: authority.apply_profile(name, skip_hooks=True))

    # ---------------- editing ----------------

    def save(self, profile, field=None):
        """Persist `profile`; with `field`, store that field through the owner (pushed live if active)."""
        if field:
            self._submit(f"{profile['name']}: {field}", self._pusher(profile, field))
        else:
            profiles.upsert_profile(profile)
            self.changed.emit()
            self._submit("Save profile", lambda: authority.mirror_profile(profile))

    def add_custom(self, from_name):
        """G-Helper's ＋: copy the selected profile (minus undervolt) as 'Custom N'."""
        src = self.get(from_name) or self.active()
        existing = set(self.names())
        n = 1
        while f"Custom {n}" in existing:
            n += 1
        new = dict(src)
        new.pop("z13ctl_profile", None)
        new.update({"name": f"Custom {n}", "builtin": False, "undervolt": None,
                    "hooks": dict(src.get("hooks") or {"enter": "", "exit": ""})})
        profiles.upsert_profile(new)
        self.changed.emit()
        self._submit("Create profile", lambda: authority.mirror_profile(new))
        return new["name"]

    def rename(self, old, new_name):
        new_name = new_name.strip()
        if not new_name or new_name == old:
            return False
        if new_name in self.names():
            self.message.emit(f"A profile named {new_name!r} already exists")
            return False
        with profiles.store_lock():
            store = self.store()
            for p in store["profiles"]:
                if p["name"] == old:
                    if p.get("builtin"):
                        self.message.emit("Built-in profiles keep their names")
                        return False
                    from z13ctl_client import Z13CtlClient
                    p.setdefault("z13ctl_profile", Z13CtlClient.mapped_profile_name(p))
                    p["name"] = new_name
            if store.get("active") == old:
                store["active"] = new_name
            profiles.save_store(store)
        self.changed.emit()
        return True

    def remove(self, name):
        was_active = name == self.active_name()
        removed = self.get(name)
        try:
            profiles.delete_profile(name)
        except (KeyError, ValueError) as e:
            self.message.emit(str(e))
            return
        self.changed.emit()
        # Removing the active profile falls back to Balanced: apply it. Removing
        # any other one changes nothing on the hardware (a re-apply would cost a
        # 5-10 s fan dip for nothing).
        if was_active:
            self.reapply_active()
        if removed:
            self._submit("Remove profile", lambda: authority.delete_named_profile(removed))

    def factory_defaults(self, name):
        """Reset a built-in to its shipped values; a custom profile loses its overrides."""
        defaults = {p["name"]: p for p in profiles.default_profiles()}
        with profiles.store_lock():
            store = self.store()
            for i, p in enumerate(store["profiles"]):
                if p["name"] != name:
                    continue
                if name in defaults:
                    store["profiles"][i] = defaults[name]
                else:
                    p.update({"tdp": None, "fan_curve": None, "undervolt": None})
            profiles.save_store(store)
        self.changed.emit()
        if name == self.active_name():
            self.reapply_active()
        else:
            self._submit("Reset profile", lambda: authority.mirror_profile(self.get(name)))

    # ---------------- live push ----------------

    def _pusher(self, profile, field):
        # Evaluate the target on the worker, after any queued profile switch.
        return lambda: authority.edit(profile, field)

    def _submit(self, label, fn):
        if not self.writes_allowed():
            self.message.emit("Unsupported machine: hardware controls are disabled")
            return
        self._pending += 1
        self.busy.emit(True)
        self.worker.submit(label, fn)

    def _on_done(self, label, result):
        if label == FAN_RECOVERY_LABEL:   # the fan watchdog shares the worker; not ours
            return
        if self.pending_name and label == f"Profile → {self.pending_name}":
            self.pending_name = None
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy.emit(False)
        # The status bar shows each result for 8 s; the log keeps it.
        if isinstance(result, Exception):
            logger.error("%s failed: %s", label, result,
                         exc_info=(type(result), result, result.__traceback__))
            text = str(result)
            self.message.emit(text if text.startswith(_WRITE_TIMEOUT_PREFIX) else f"{label}: {text}")
        elif isinstance(result, list) and result and isinstance(result[0], tuple):
            failed = [f"{s} ({m})" for s, ok, m in result if not ok]
            timeout = next(((step, message) for step, ok, message in result
                            if not ok and message.startswith(_WRITE_TIMEOUT_PREFIX)), None)
            if failed:
                logger.warning("%s: %s", label, "; ".join(failed))
            else:
                logger.info("%s ✓ (%s)", label, ", ".join(f"{s}: {m}" for s, _ok, m in result))
            self.message.emit((f"{timeout[1]} (step: {timeout[0]})" if timeout else None) or
                              label + (": " + "; ".join(failed) if failed else " ✓"))
        else:
            logger.info("%s ✓ %s", label, result if isinstance(result, str) else "")
            text = result if isinstance(result, str) else ""
            self.message.emit(text if text.startswith(_WRITE_TIMEOUT_PREFIX) else f"{label} ✓")
        self.changed.emit()

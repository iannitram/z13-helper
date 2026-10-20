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
from hardware_controller import HardwareController
from apply_worker import ApplyWorker


logger = logging.getLogger("z13.profiles")


class ProfileControl(QObject):
    changed = pyqtSignal()        # store (active name or contents) changed
    message = pyqtSignal(str)     # human-readable result for the status bar
    busy = pyqtSignal(bool)

    def __init__(self, controller: HardwareController, worker: ApplyWorker, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.worker = worker
        self.worker.done.connect(self._on_done)
        self._pending = 0
        # Profile the user just picked, shown at once while the apply runs
        # (a wedged lightbar alone makes an apply take 5 s).
        self.pending_name = None

    # ---------------- store access ----------------

    def store(self):
        return profiles.load_store()

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
        self.pending_name = name
        self.message.emit(f"Applying {name}…")
        self.changed.emit()
        self._submit(f"Profile → {name}", lambda: profiles.apply_by_name(name))

    def reapply_active(self):
        name = self.active_name()
        self._submit(f"Re-applied {name}", lambda: profiles.apply_by_name(name, skip_hooks=True))

    # ---------------- editing ----------------

    def save(self, profile, field=None):
        """Persist `profile`; if it is the active one, push `field` live."""
        profiles.upsert_profile(profile)
        self.changed.emit()
        if field and profile["name"] == self.active_name():
            self._submit(f"{profile['name']}: {field}", self._pusher(profile, field))

    def add_custom(self, from_name):
        """G-Helper's ＋: copy the selected profile (minus undervolt) as 'Custom N'."""
        src = self.get(from_name) or self.active()
        existing = set(self.names())
        n = 1
        while f"Custom {n}" in existing:
            n += 1
        new = dict(src)
        new.update({"name": f"Custom {n}", "builtin": False, "undervolt": None,
                    "hooks": dict(src.get("hooks") or {"enter": "", "exit": ""})})
        profiles.upsert_profile(new)
        self.changed.emit()
        return new["name"]

    def rename(self, old, new_name):
        new_name = new_name.strip()
        if not new_name or new_name == old:
            return False
        if new_name in self.names():
            self.message.emit(f"A profile named {new_name!r} already exists")
            return False
        store = self.store()
        for p in store["profiles"]:
            if p["name"] == old:
                if p.get("builtin"):
                    self.message.emit("Built-in profiles keep their names")
                    return False
                p["name"] = new_name
        if store.get("active") == old:
            store["active"] = new_name
        profiles.save_store(store)
        self.changed.emit()
        return True

    def remove(self, name):
        was_active = name == self.active_name()
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

    def factory_defaults(self, name):
        """Reset a built-in to its shipped values; a custom profile loses its overrides."""
        store = self.store()
        defaults = {p["name"]: p for p in profiles.default_profiles()}
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

    # ---------------- live push ----------------

    def _pusher(self, profile, field):
        c = self.controller

        def full():
            return profiles.apply(profile, previous=profile, skip_hooks=True)

        def push_tdp():
            tdp = profile.get("tdp")
            if not tdp:
                return full()          # firmware limits come back with a profile write
            return c.set_tdp(pl1=tdp["pl1"], pl2=tdp["pl2"], pl3=tdp["pl3"], force=bool(tdp.get("force")))

        def push_curve():
            curve = profile.get("fan_curve")
            if max_fans_state.is_enabled():
                max_fans_state.record_profile_curve(curve)
                return "saved (Max Fans is ON; applies when it's switched off)"
            if curve:
                return c.set_fancurve(curve)
            c.reset_fancurve()
            tdp = profile.get("tdp")
            if tdp:   # releasing the fans lets the firmware re-apply its limits
                c.set_tdp(pl1=tdp["pl1"], pl2=tdp["pl2"], pl3=tdp["pl3"], force=bool(tdp.get("force")))
            return "firmware auto"

        def push_uv():
            uv = profile.get("undervolt")
            return c.set_undervolt(uv) if uv is not None else c.reset_undervolt()

        def push_brightness():
            br = profile.get("brightness")
            return c.set_brightness(br) if br else "unchanged"

        def push_battery():
            bl = profile.get("battery_limit")
            return c.set_battery_limit(bl) if bl is not None else "unchanged"

        return {"tdp": push_tdp, "fan_curve": push_curve, "undervolt": push_uv,
                "brightness": push_brightness, "battery_limit": push_battery}.get(field, full)

    def _submit(self, label, fn):
        self._pending += 1
        self.busy.emit(True)
        self.worker.submit(label, fn)

    def _on_done(self, label, result):
        if self.pending_name and label == f"Profile → {self.pending_name}":
            self.pending_name = None
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.busy.emit(False)
        # The status bar shows each result for 8 s; the log keeps it.
        if isinstance(result, Exception):
            logger.error("%s failed: %s", label, result,
                         exc_info=(type(result), result, result.__traceback__))
            self.message.emit(f"{label}: {result}")
        elif isinstance(result, list) and result and isinstance(result[0], tuple):
            failed = [f"{s} ({m})" for s, ok, m in result if not ok]
            if failed:
                logger.warning("%s: %s", label, "; ".join(failed))
            else:
                logger.info("%s ✓ (%s)", label, ", ".join(f"{s}: {m}" for s, _ok, m in result))
            self.message.emit(label + (": " + "; ".join(failed) if failed else " ✓"))
        else:
            logger.info("%s ✓ %s", label, result if isinstance(result, str) else "")
            self.message.emit(f"{label} ✓")
        self.changed.emit()

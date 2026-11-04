"""Lighting signal language (issue #129, Phase 16a): pure event -> lightbar pattern mapping.

No I/O and no clock: callers pass `now` (monotonic seconds). The runner (#130) plays the
result through hardware_controller.set_lighting. Patterns use only modes and colours that
profiles.LIGHTING_MODES / LIGHTING_COLORS accept, so profiles.lighting_argv builds them.
"""

import threading
import time
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

DEVICE = "lightbar"

# Event names.
PROFILE_CHANGED = "profile_changed"
THROTTLING = "throttling"
CHARGE_LIMIT = "charge_limit"
UPDATE_ATTENTION = "update_attention"
GOAL_NOT_MET = "goal_not_met"

# Strict rate limit: the lightbar wedges under write bursts (Phase 7). Minimum seconds
# between two lightbar writes of any kind, and the longest a one-shot pattern may show.
MIN_WRITE_INTERVAL_S = 2.0
ONE_SHOT_S = 3.0


@dataclass(frozen=True)
class Pattern:
    mode: str
    color: str
    brightness: str = "high"

    def setting(self) -> dict:
        """The {mode, color} dict profiles.lighting_argv expects."""
        return {"mode": self.mode, "color": self.color}


@dataclass(frozen=True)
class Signal:
    event: str
    priority: int          # higher wins
    pattern: Pattern
    duration_s: Optional[float]  # None = held while the condition lasts ("sticky")


SIGNALS: Mapping[str, Signal] = {
    s.event: s for s in (
        Signal(PROFILE_CHANGED, 10, Pattern("static", "green"), ONE_SHOT_S),
        Signal(CHARGE_LIMIT, 20, Pattern("static", "cyan", "low"), None),
        Signal(GOAL_NOT_MET, 25, Pattern("breathe", "yellow"), None),
        Signal(UPDATE_ATTENTION, 30, Pattern("static", "orange"), None),
        Signal(THROTTLING, 40, Pattern("breathe", "red"), None),
    )
}


def signal_for(event: str) -> Optional[Signal]:
    """The signal for an event name, or None for unknown events."""
    return SIGNALS.get(event)


def active_signal(active: Mapping[str, Optional[float]], now: float) -> Optional[Signal]:
    """Highest-priority signal showing now.

    `active` maps event -> start time (sticky events stay while present; one-shots
    expire `duration_s` after start). Unknown events are ignored.
    """
    best = None
    for event, started in active.items():
        sig = SIGNALS.get(event)
        if sig is None or started is None:
            continue
        if sig.duration_s is not None and now - started >= sig.duration_s:
            continue
        if best is None or sig.priority > best.priority:
            best = sig
    return best


def next_action(active: Mapping[str, Optional[float]], shown: Optional[Pattern], *,
                now: float, last_write: Optional[float], enabled: bool = True,
                restore: Optional[Pattern] = None) -> Tuple[str, Optional[Pattern]]:
    """Decide the next lightbar step: ("none"|"play"|"restore", pattern).

    - off switch (`enabled` False): never play; restore the owner's lighting once if a
      signal is still showing.
    - nothing active but a signal showing: restore the owner's lighting (`restore`);
      with no known lighting to restore, do nothing.
    - a different pattern wanted: play it, unless the last write was under
      MIN_WRITE_INTERVAL_S ago (deferred: "none"; the caller asks again later).
    """
    want = active_signal(active, now) if enabled else None
    if want is None:
        if shown is not None and restore is not None and _may_write(now, last_write):
            return "restore", restore
        return "none", None
    if want.pattern == shown or not _may_write(now, last_write):
        return "none", None
    return "play", want.pattern


def _may_write(now: float, last_write: Optional[float]) -> bool:
    return last_write is None or now - last_write >= MIN_WRITE_INTERVAL_S


SIGNAL_LABEL = "Lighting signal"

# Config key for the off switch (default on). Off: no lightbar write the user did not ask for.
CONFIG_KEY = "signals_enabled"


class SignalRunner:
    """Plays the signal language on the lightbar (issue #130, Phase 16b).

    Qt-free. `write(pattern)` does one lightbar write (raises on failure; it should go
    through hardware_controller.set_lighting, which carries the lightbar recovery).
    `restore()` returns the owner's lightbar Pattern (the active profile's) or None.
    `submit(label, fn)` runs `fn` off the caller's thread (ApplyWorker.submit); the
    default runs it inline. One write is in flight at a time and every write obeys
    MIN_WRITE_INTERVAL_S, so a burst of events cannot wedge the lightbar. A failed
    write drops the event (no retry loop against a wedged bar).
    """

    def __init__(self, write, restore, enabled=lambda: True, *, submit=None,
                 clock=None, log=None):
        self._write = write
        self._restore = restore
        self._enabled = enabled
        self._submit = submit or (lambda _label, fn: fn())
        self._clock = clock or time.monotonic
        self._log = log or (lambda _msg: None)
        self._lock = threading.Lock()
        self._active = {}
        self._shown = None
        self._last_write = None
        self._inflight = False

    def raise_event(self, event):
        """A condition began (one-shots restart their timer). Ignored while signals are off."""
        if event not in SIGNALS or not self._enabled():
            return
        with self._lock:
            self._active[event] = self._clock()

    def clear_event(self, event):
        with self._lock:
            self._active.pop(event, None)

    def note_external_write(self):
        """A lighting write the runner did not make (a profile apply): count it for the rate limit."""
        with self._lock:
            self._last_write = self._clock()

    def tick(self):
        """Take at most one step. Returns "none", "play" or "restore"."""
        enabled = bool(self._enabled())
        with self._lock:
            if self._inflight:
                return "none"
            now = self._clock()
            for event in [e for e, t in self._active.items()
                          if SIGNALS[e].duration_s is not None and now - t >= SIGNALS[e].duration_s]:
                del self._active[event]
            if not enabled:
                self._active.clear()
            restore = None
            if self._shown is not None and (not enabled or not self._active):
                restore = self._restore()
            action, pattern = next_action(dict(self._active), self._shown, now=now,
                                          last_write=self._last_write, enabled=enabled,
                                          restore=restore)
            if action == "none":
                return "none"
            self._inflight = True
        self._submit(SIGNAL_LABEL, lambda: self._do(action, pattern))
        return action

    def _do(self, action, pattern):
        try:
            self._write(pattern)
            ok = True
        except Exception as exc:  # noqa: BLE001 - a signal must never break the app
            ok = False
            self._log(f"lighting signal {action} failed: {exc}")
        with self._lock:
            self._last_write = self._clock()
            self._inflight = False
            if ok:
                self._shown = pattern if action == "play" else None
            elif action == "play":
                # Give up on this signal rather than retry against a wedged lightbar.
                self._active = {e: t for e, t in self._active.items()
                                if SIGNALS[e].pattern != pattern}
            else:
                self._shown = None
        return f"{action} {pattern.mode}/{pattern.color}" if ok else f"{action} failed"

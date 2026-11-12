"""Phase 15c goal runner: owns the fan curve and TDP while a goal is active.

One ``GoalRunner`` lives in each frontend (the desktop app in Desktop Mode,
the Decky backend in Game Mode). Each ``tick()``:

1. Takes the shared hardware transaction (``state_authority.transaction``), so
   a tick never interleaves with a profile apply, a slider push or a fan
   recovery.
2. Reads the active profile. No goal (or a goal-less profile switched to):
   the runner releases the goal lease and writes nothing. The profile apply
   that switched profiles writes the fixed values itself, in principle 4's
   order (base profile, EPP, TDP, fan curve), so the runner never writes after
   it.
3. Holds the goal lease in ``state_authority`` (the #23 state owner). Only the
   lease holder writes, so exactly one runner is active at a time. A runner
   whose ``may_run()`` says no (wrong session mode, writes not allowed)
   releases the lease; a holder that died is replaced once its heartbeat is
   older than ``state_authority.GOAL_LEASE_TTL_S``. A successor on the same
   profile and goal starts from the setpoint its predecessor published, so a
   Desktop/Game Mode switch does not reset the goal's progress.
4. At most one write per dwell period (``goal_control.MIN_DWELL_S``): between
   writes nothing is read or decided.
5. Reads sensors and the FPS source (both injected callables; any exception
   or a non-number is a missing reading), calls ``goal_control.step`` and
   writes only what changed, TDP before curve, through z13ctl. Readings come
   from hwmon only; the runner never reads Armoury ``current_value``
   (principle 5). Its "current" setpoints are the ones it last wrote, starting
   from the profile's stored TDP and curve.

A failed write keeps the previous setpoints and still counts as the dwell's
write (no hammering a failing z13ctl). A timed-out write leaves the hardware
state unknown (#79): the runner suspends itself and writes nothing more until
the active profile changes (a profile apply re-establishes known state).

No Qt here. ``GoalRunnerDriver`` is the desktop's timer glue: it hands each
tick to the serialized ApplyWorker queue.
"""

import logging
import math
import time

import goal_control

logger = logging.getLogger(__name__)

GOAL_RUNNER_LABEL = "Goal runner"
TIMEOUT_PREFIX = "z13ctl write timed out after "
# Goal kinds this runner drives; anything else is left to the fixed apply.
RUNNER_GOALS = ("quiet", "comfort", "fps", "max")


def _number(value):
    """A finite non-bool number, else None (a missing reading)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return value if math.isfinite(value) else None
    except OverflowError:
        return None


def _safe(read):
    try:
        return read()
    except Exception as exc:  # noqa: BLE001 - a broken source is a missing reading
        logger.warning("goal runner: reading failed: %s", exc)
        return None


def _is_timeout(text):
    return isinstance(text, str) and text.startswith(TIMEOUT_PREFIX)


def _tdp_of(tdp):
    if not isinstance(tdp, dict):
        return None
    vals = {r: tdp.get(r) for r in goal_control.RAILS}
    if any(isinstance(v, bool) or not isinstance(v, int) for v in vals.values()):
        return None
    return vals


def _curve_of(curve):
    if not isinstance(curve, (list, tuple)) or len(curve) != 8:
        return None
    try:
        return [[int(t), int(p)] for t, p in curve]
    except (TypeError, ValueError):
        return None


def sensor_readings(sensors, comfort=lambda: None):
    """The readings goal_control.step uses, from a SensorReader (hwmon only).

    ``apu_temp_c`` is the chip (k10temp Tctl) temperature; ``fan_rpm`` the
    faster fan. ``comfort`` is the Comfort proxy source; no proxy has been
    chosen yet (#127), so the default reports it missing and Comfort holds.
    """
    cpu = _safe(sensors.get_cpu_temp)
    fans = _safe(sensors.get_fan_speeds) or (None, None)
    rpms = [r for r in (_number(f) for f in fans) if r is not None]
    return {"apu_temp_c": cpu[0] if cpu else None,
            "fan_rpm": max(rpms) if rpms else None,
            "comfort_temp_c": _safe(comfort)}


class ControllerWriter:
    """Writes through a HardwareController (z13ctl via state_authority.run).

    Each method returns None on success or the problem text; a z13ctl timeout
    keeps its "z13ctl write timed out after" text so the runner can tell.
    """

    def __init__(self, controller):
        self._c = controller

    def tdp(self, tdp):
        try:
            self._c.set_tdp(tdp["pl1"], tdp["pl2"], tdp["pl3"], force=bool(tdp.get("force")))
        except Exception as exc:  # noqa: BLE001
            return str(exc) or exc.__class__.__name__
        return None

    def fan_curve(self, points):
        try:
            self._c.set_fancurve(points)
        except Exception as exc:  # noqa: BLE001
            return str(exc) or exc.__class__.__name__
        return None


def _default_transaction():
    import state_authority
    return state_authority.transaction()


def _default_active_profile():
    import profiles
    store = profiles.load_store(create=False)
    return profiles.get_profile(store.get("active"), store)


class _AuthorityLease:
    """The goal lease kept by state_authority (the #23 state owner)."""

    def acquire(self, owner, now):
        import state_authority
        return state_authority.goal_lease_acquire(owner, now)

    def publish(self, owner, now, setpoint):
        import state_authority
        return state_authority.goal_lease_publish(owner, now, setpoint)

    def release(self, owner):
        import state_authority
        state_authority.goal_lease_release(owner)


class GoalRunner:
    """One goal loop; the caller owns the cadence (call tick() every few s).

    owner           "desktop" or "decky": the lease identity.
    active_profile  () -> the active profile dict, or None.
    readings        () -> dict of goal_control readings (sensor_readings()).
    fps             () -> current game FPS or None (no dependable source yet:
                    #126); any exception or non-number is a missing reading.
    writer          .tdp(tdp) / .fan_curve(points): None on success, else
                    the problem text (ControllerWriter).
    ranges          () -> power_range.detect_ranges() shape.
    may_run         () -> bool: this frontend's session is the one in charge.
    clock           monotonic seconds, shared across processes (CLOCK_MONOTONIC).
    """

    def __init__(self, owner, *, readings, writer, ranges, fps=lambda: None,
                 active_profile=_default_active_profile, may_run=lambda: True,
                 clock=time.monotonic, lease=None, transaction=_default_transaction,
                 dwell_s=goal_control.MIN_DWELL_S):
        self.owner = owner
        self._readings = readings
        self._fps = fps
        self._writer = writer
        self._ranges = ranges
        self._active_profile = active_profile
        self._may_run = may_run
        self._clock = clock
        self._lease = lease or _AuthorityLease()
        self._transaction = transaction
        self._dwell_s = dwell_s
        self._held = False
        self._key = None          # (profile name, goal) the setpoint belongs to
        self._setpoint = None     # {"tdp": {...}, "fan_curve": [[t, pct]...]}
        self._state = {}          # goal_control.step state
        self._last_write_at = None
        self._force = False       # the profile's TDP asks for --force
        self.suspended = None     # timeout text while suspended, else None
        self.last_reason = None

    # -- public -----------------------------------------------------------

    @property
    def active(self):
        """True while this runner holds the lease and drives a goal."""
        return self._held and self._setpoint is not None

    @property
    def setpoint(self):
        return None if self._setpoint is None else {
            "tdp": dict(self._setpoint["tdp"]),
            "fan_curve": [list(p) for p in self._setpoint["fan_curve"]]}

    def stop(self):
        """Release the lease and forget the goal (app quit / plugin unload)."""
        if self._held:
            try:
                self._lease.release(self.owner)
            except Exception as exc:  # noqa: BLE001
                logger.warning("goal runner: lease release failed: %s", exc)
        self._held = False
        self._key = None
        self._setpoint = None
        self._state = {}
        self._last_write_at = None

    def tick(self):
        """One decision. Returns a message worth showing, else None."""
        with self._transaction():
            return self._tick(self._clock())

    # -- internals --------------------------------------------------------

    def _tick(self, now):
        if not _safe(self._may_run):
            if self._held:
                self.stop()
                return "Goal runner handed over"
            return None
        profile = _safe(self._active_profile)
        goal = profile.get("goal") if isinstance(profile, dict) else None
        kind = goal.get("kind") if isinstance(goal, dict) else None
        if kind not in RUNNER_GOALS:
            was = self._held
            self.stop()
            self.suspended = None
            return "Goal runner stopped: the active profile has no goal" if was else None
        key = (profile.get("name"), repr(sorted(goal.items())))
        if key != self._key:
            # A new profile or goal: a profile apply has just written its
            # fixed values, so a timeout suspension no longer applies.
            self.suspended = None
        if self.suspended:
            return None

        if _tdp_of(profile.get("tdp")) is None or (
                kind != "max" and _curve_of(profile.get("fan_curve")) is None):
            # Nothing known to start from. Hold no lease, so slider edits and
            # fan recovery keep their normal behaviour.
            self.stop()
            return self._hold("Goal runner waiting: the profile needs a fixed TDP and fan curve")
        if not self._held:
            got = self._lease.acquire(self.owner, now)
            if got is None:
                return None   # another runner holds the lease
            self._held = True
            self._start(key, profile, got)
        elif key != self._key:
            self._start(key, profile, None)

        if self._last_write_at is not None and now - self._last_write_at < self._dwell_s:
            return self._renew(now)

        ranges = _safe(self._ranges)
        readings = dict(_safe(self._readings) or {})
        readings["fps"] = _number(_safe(self._fps))
        try:
            result = goal_control.step(goal, readings, self._setpoint, ranges,
                                       now_s=now, state=self._state)
        except ValueError as exc:
            self._renew(now)
            return self._hold(f"Goal runner holding: {exc}")
        self.last_reason = result["reason"]
        new_tdp, new_curve = result["tdp"], result["fan_curve"]
        changed_tdp = new_tdp != self._setpoint["tdp"]
        changed_curve = new_curve != self._setpoint["fan_curve"]
        if not (changed_tdp or changed_curve):
            self._state = result["state"]
            return self._renew(now)

        # One write per dwell, TDP before curve (principle 4's order). Re-check
        # the lease first: it may have expired while this process was stalled.
        if not self._renew(now, check=True):
            self._held = False
            self._setpoint = None
            self._key = None
            return None
        self._last_write_at = now
        written = dict(self._setpoint)
        if changed_tdp:
            tdp = dict(new_tdp)
            if self._force:
                tdp["force"] = True
            problem = self._writer.tdp(tdp)
            if problem:
                return self._failed("TDP", problem, now)
            written["tdp"] = dict(new_tdp)
        if changed_curve:
            problem = self._writer.fan_curve(new_curve)
            if problem:
                self._setpoint = written
                self._lease.publish(self.owner, now, self._published())
                return self._failed("fan curve", problem, now)
            written["fan_curve"] = [list(p) for p in new_curve]
        self._setpoint = written
        self._state = result["state"]
        self._lease.publish(self.owner, now, self._published())
        return f"Goal: {result['reason']}"

    def _start(self, key, profile, previous):
        self._key = key
        self._state = {}
        self._last_write_at = None
        self._force = bool((profile.get("tdp") or {}).get("force")) \
            if isinstance(profile.get("tdp"), dict) else False
        handed = previous.get("setpoint") if isinstance(previous, dict) else None
        tdp = curve = None
        if (isinstance(handed, dict) and handed.get("profile") == key[0]
                and handed.get("goal") == key[1]):
            # Handover on the same profile and goal: continue from where the
            # previous runner left the hardware.
            tdp, curve = _tdp_of(handed.get("tdp")), _curve_of(handed.get("fan_curve"))
            written_at = _number(handed.get("written_at"))
            if tdp is not None and curve is not None and written_at is not None:
                # The dwell spans the handover: one write per dwell in total.
                self._last_write_at = written_at
                self._state = {"last_change_at": written_at}
        if tdp is None or curve is None:
            tdp, curve = _tdp_of(profile.get("tdp")), _curve_of(profile.get("fan_curve"))
            if profile["goal"]["kind"] == "max":
                # The apply pinned Max Fans' full-speed curve already.
                curve = [[t, 100] for t in goal_control.MAX_FAN_TEMPS]
        self._setpoint = {"tdp": tdp, "fan_curve": curve}
        if self._setpoint is not None:
            self._lease.publish(self.owner, self._clock(), self._published())

    def _published(self):
        sp = self.setpoint
        sp["profile"], sp["goal"] = self._key
        sp["written_at"] = self._last_write_at
        return sp

    def _renew(self, now, check=False):
        ok = self._lease.publish(self.owner, now, self._published())
        if check:
            return ok
        if not ok:
            self._held = False
            self._setpoint = None
            self._key = None
        return None

    def _hold(self, reason):
        if reason == self.last_reason:
            return None
        self.last_reason = reason
        return reason

    def _failed(self, what, problem, now):
        if _is_timeout(problem):
            self.suspended = problem
            # The hardware state is unknown: stop driving and let a fresh
            # profile apply establish it. Release so recovery is not blocked
            # on a setpoint nobody is sure about.
            self._lease.release(self.owner)
            self._held = False
            self._setpoint = None
            return f"Goal runner suspended: {what} {problem}"
        return f"Goal runner: {what} write failed ({problem}); holding the last setpoint"


class GoalRunnerDriver:
    """The desktop's timer glue, free of Qt so it can be tested.

    tick() queues one runner tick on the serialized worker (submit(label, fn),
    e.g. ApplyWorker.submit) unless one is still queued; on_done(label, result)
    returns the message to show (None when the label is not ours).
    """

    def __init__(self, runner, submit):
        self.runner = runner
        self._submit = submit
        self._pending = False

    @property
    def pending(self):
        return self._pending

    def tick(self):
        if self._pending:
            return
        self._pending = True
        self._submit(GOAL_RUNNER_LABEL, self.runner.tick)

    def on_done(self, label, result):
        if label != GOAL_RUNNER_LABEL:
            return None
        self._pending = False
        if isinstance(result, Exception):
            return f"Goal runner error: {result}"
        return result

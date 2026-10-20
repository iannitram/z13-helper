"""Fan watchdog: keeps the fan curve the user asked for actually running.

Two failure modes, both seen live on this machine:

1. **Curve dropped.** asus-wmi disables custom fan curves on every
   platform-profile write (this app, the Decky plugin, z13-* scripts,
   Boondock, Steam). pwmN_enable on the asus_custom_fan_curve node flips to
   2 (auto), which is easy to see and re-apply.

2. **Curve stalled.** After a Desktop <-> Game Mode session switch
   (2026-10-09) the sysfs curve still read "custom, 100% everywhere" and
   pwmN_enable was 1, yet both fans sat at 0 RPM at 39 °C. Re-writing the
   curve or resetting it did nothing; re-writing the *platform profile*
   (same value) and then the curve brought the fans straight back to
   8 800 RPM. So the kernel's flags are not ground truth; RPM is. If the
   curve says the fans should be spinning and both read 0 RPM for a few
   ticks, the watchdog rewrites the profile and then the curve.

This is the only copy. The desktop app ticks it from its telemetry timer;
the Decky plugin ticks it in Game Mode from a copy installed in its
py_modules/ (docs/adr/0001-decky-imports-root-owned-copies.md). Each
frontend hands it its own fan commands, Max Fans reader and "may I act now?"
check, so nothing here knows which process it is running in.
"""

import collections
import errno
import logging
import logging.handlers
import os
import stat
import sys
import time

# How many consecutive telemetry ticks (1.5 s each) of "should spin but 0 RPM"
# before we call it a stall. One tick can be a transient after a profile write.
STALL_TICKS = 2
# Minimum gap between re-apply attempts so a failing z13ctl isn't hammered.
RETRY_S = 10
# Only treat 0 RPM as a stall when the curve asks for at least this much
# (50%): fans at half speed or more never read 0, while a low first point
# may legitimately idle them, and a false stall would rewrite the profile
# every RETRY_S.
STALL_MIN_PWM = 128
# Only call it a stall this many degrees above the first curve point.
STALL_HYSTERESIS_C = 3
# 3. Frozen tach. When the firmware fails to re-ramp a fan after a curve
#    change, the EC's RPM report freezes at its last value (seen 2026-10-09:
#    only the left fan stopped while fan1_input read 8700 on every sample and
#    the other fan's reading kept jittering). A healthy fan's reading moves by
#    +-100 almost every tick, so ONE reading bit-identical for this long means
#    that tach is stale, not the fan steady. Per fan, since one can stall alone.
FROZEN_S = 90
#    A constant reading alone is only a suspicion (a real fan at full speed can
#    hold the exact same RPM for over a minute). It becomes a frozen tach only
#    when corroborated: the other fan's tach is still moving (the reader works)
#    and the APU has warmed by STALL_HYSTERESIS_C or more since the reading
#    stopped changing (the cooling it should be giving is missing). That
#    margin is the one the stall rule already uses for "meaningfully warmer".
#
# 4. One fan at 0. The stall rule needs both fans at 0; a single fan stuck at 0
#    while its partner runs is the same fault. It counts only after a grace of
#    RETRY_S following our own writes and any resume (fans legitimately restart
#    then), and must persist two more RETRY_S windows: ONE_FAN_ZERO_S.
ONE_FAN_ZERO_S = 2 * RETRY_S
# Recovery attempts per fan before the fault is declared and left to the user.
# Each attempt briefly stops the fans and cannot repair a physical failure, so
# three tries separate a transient firmware glitch from a real fault.
MAX_RECOVERY_ATTEMPTS = 3
# Worker-queue label of a recovery run off the UI thread (see FanWatchdogRunner).
FAN_RECOVERY_LABEL = "Fan recovery"


class FanWatchdog:
    """One check per tick(); the caller owns the cadence.

    sensors      a SensorReader (or anything with its fan/curve/temp reads).
    fans         the fan commands: rewrite_profile(), set_fans_max(),
                 set_fancurve_pwm(points). Each raises on failure.
    max_fans_on  () -> bool: is Max Fans on right now (re-read every tick).
    may_act      () -> bool: asked only just before a fan recovery. When it
                 says no, nothing is written and the retry gap doesn't start,
                 so the recovery happens on the first tick it says yes.
    clock        monotonic seconds; injectable for tests.
    trace        optional FanTrace: gets every tick's readings and decisions.
    """

    def __init__(self, sensors, fans, *, max_fans_on, may_act=lambda: True,
                 clock=time.monotonic, trace=None):
        self.sensors = sensors
        self.fans = fans
        self._max_fans_on = max_fans_on
        self._may_act = may_act
        self._clock = clock
        self.trace = trace
        self._stall_ticks = 0
        self._last_reapply = float("-inf")
        self._last_rpm = [None, None]
        now = clock()
        self._rpm_changed_at = [now, now]
        self._temp_at_change = [None, None]   # APU temp when each reading last changed
        self._zero_since = [None, None]       # when each fan started reading 0 under demand
        self._alive_since = [None, None]      # start of an unbroken run of moving positive readings
        self._attempts = [0, 0]               # detection recoveries per fan, until it shows life
        self._grace_until = float("-inf")     # after our writes / a resume: don't judge yet
        self._last_tick_at = None
        self._suspect_logged = [False, False]
        self._declared = None                 # message for a fault declared this tick
        self.fault = None                     # unresolved fault text for the UI/Decky, else None
        self._fault_fans = set()
        self._holding = False   # may_act refused the last due recovery
        self._tick = None       # this tick's readings + events, for the trace
        self._defer = None      # tick(defer=...): hand recoveries to a worker
        self._inflight = False  # a deferred recovery has not finished yet

    def _throttled(self) -> bool:
        return self._clock() - self._last_reapply < RETRY_S

    def _ready(self, why: str) -> bool:
        """Past the retry gap and allowed to write. Checked in that order so the
        (possibly costly) may_act check runs only when a recovery is due."""
        if self._throttled() or self._inflight:
            return False
        if not self._may_act():
            if not self._holding:
                self._event(f"HOLD {why} (not allowed to act now)")
            self._holding = True
            return False
        self._holding = False
        return True

    def _gate(self, now, fans, why) -> bool:
        """May a detection recovery for these fans (0-based) be written now?
        Spent attempts declare the fault instead; a recovery that goes ahead
        starts the retry gap and a grace for the fans' own restart."""
        if any(self._attempts[i] >= MAX_RECOVERY_ATTEMPTS for i in fans):
            new = [i for i in fans if i not in self._fault_fans]
            if new:
                self._fault_fans.update(fans)
                names = " and ".join(f"fan {i + 1}" for i in sorted(self._fault_fans))
                self.fault = (f"Fault: {names} not recovering after {MAX_RECOVERY_ATTEMPTS} "
                              f"recovery attempts ({why}); the fans may be failing")
                self._declared = self.fault
                self._event(f"FAULT {self.fault}")
            return False
        if not self._ready(why):
            return False
        self._last_reapply = now
        self._grace_until = now + RETRY_S
        for i in fans:
            self._attempts[i] += 1
            self._alive_since[i] = None
        return True

    def _event(self, text):
        self._tick["events"].append(text)

    def tick(self, defer=None) -> str | None:
        """Run one check. Returns a status message when it had to act.

        defer, when given, is called with a no-argument function that performs
        the recovery (it blocks on hardware commands) instead of running it
        here; tick() then returns None for that tick and the caller shows the
        function's result when the worker finishes. No second recovery is
        decided until finished() is called."""
        now = self._clock()
        self._defer = defer
        self._tick = {"events": []}
        if self._last_tick_at is not None and now - self._last_tick_at > RETRY_S:
            # Longer than any tick cadence (2 s shown, 6 s hidden) plus a retry
            # gap: the machine was suspended. Fans restart and readings repeat
            # across it, so judge nothing until they have had RETRY_S.
            self._grace_until = now + RETRY_S
            self._stall_ticks = 0
            self._zero_since = [None, None]
            self._rpm_changed_at = [now, now]
            self._temp_at_change = [None, None]
            self._event("RESUME gap in ticks; grace started")
        self._last_tick_at = now
        msg = self._check(now)
        self._tick["fault"] = self.fault
        if msg:
            self._event(f"RESULT {msg}")
        self._defer = None
        if self.trace is not None:
            try:
                self.trace.record(now, self._tick, self._ages(now))
            except Exception:  # noqa: BLE001  (tracing must never stop the watchdog)
                pass
        return msg

    def _ages(self, now):
        """Seconds each fan's reading has been unchanged; None when not tracked
        (the frozen-tach rule only tracks readings under a custom curve)."""
        if self._tick.get("enabled") is not True:
            return [None, None]
        return [None if r is None else now - t
                for r, t in zip(self._last_rpm, self._rpm_changed_at)]

    def _check(self, now) -> str | None:
        t = self._tick
        max_on = t["max_on"] = self._max_fans_on()
        enabled = t["enabled"] = self.sensors.custom_fan_curve_enabled()
        if self.trace is not None:
            # Read for the trace even on the paths below that don't need them.
            t["temp"] = self.sensors.get_apu_temp()
            t["rpm"] = self.sensors.get_fan_speeds()

        # 1. Dropped curve while Max Fans is ON -> re-pin 100%. Done as a
        #    profile rewrite + curve, not a bare curve write: after an external
        #    platform-profile write the firmware was seen to ignore bare curve
        #    writes (fans at 0 RPM) until the profile was written again.
        if max_on and enabled is False:
            self._stall_ticks = 0
            if not self._ready("dropped curve"):
                return None
            self._last_reapply = now
            self._grace_until = now + RETRY_S
            self._event("ACT dropped curve with Max Fans on")
            return self._dispatch(True, lambda msg: msg.replace(
                "Fans were stalled at 0 RPM;", "Max Fans re-applied after a profile change;"))

        # 2. Stalled curve: custom mode claims active, the curve says >0 at the
        #    current temperature, but both fans read 0 RPM.
        if enabled is not True:
            self._stall_ticks = 0
            self._zero_since = [None, None]
            self._holding = False
            return None
        temp = t["temp"] = self.sensors.get_apu_temp()
        fan1, fan2 = t["rpm"] = self.sensors.get_fan_speeds()
        if temp is None or fan1 is None or fan2 is None:
            # A sensor dropout breaks the "consecutive ticks" chain: two 0-RPM
            # readings minutes apart must not add up to a stall.
            self._stall_ticks = 0
            self._zero_since = [None, None]
            return None
        # Hysteresis margin: the firmware keeps the fans off until a couple of
        # degrees above the first point once they have stopped.
        expected = t["want"] = self.sensors.expected_fan_pwm(temp - STALL_HYSTERESIS_C)
        demand = expected is not None and expected >= STALL_MIN_PWM
        # 3. Frozen tach (see FROZEN_S): a constant reading is a suspicion until
        #    the other fan is still moving and the APU has warmed.
        frozen = None
        for i, rpm in enumerate((fan1, fan2)):
            if rpm != self._last_rpm[i] or self._temp_at_change[i] is None:
                if rpm > 0 and rpm != self._last_rpm[i]:
                    if self._alive_since[i] is None:
                        self._alive_since[i] = now
                self._last_rpm[i] = rpm
                self._rpm_changed_at[i] = now
                self._temp_at_change[i] = temp
                self._suspect_logged[i] = False
            elif rpm > 0 and now - self._rpm_changed_at[i] >= FROZEN_S:
                partner_moving = now - self._rpm_changed_at[1 - i] < FROZEN_S
                warmed = temp - self._temp_at_change[i] >= STALL_HYSTERESIS_C
                if partner_moving and warmed:
                    frozen = (i + 1, rpm)
                elif demand and not self._suspect_logged[i]:
                    self._suspect_logged[i] = True
                    self._event(f"SUSPECT fan {i + 1} reading constant at {rpm} RPM for "
                                f"{FROZEN_S}s; not corroborated (partner moving: {partner_moving}, "
                                f"warmed: {warmed})")
        # Forgive attempts and clear a fault only after sustained health: an
        # unbroken run of moving readings as long as the zero rule needs to
        # condemn a fan (ONE_FAN_ZERO_S). A fan that spins briefly after each
        # recovery and dies again never gets there, so the bound holds.
        for i, rpm in enumerate((fan1, fan2)):
            if rpm == 0 or (rpm == self._last_rpm[i] and now - self._rpm_changed_at[i] >= RETRY_S):
                self._alive_since[i] = None
            elif self._alive_since[i] is not None and now - self._alive_since[i] >= ONE_FAN_ZERO_S:
                self._attempts[i] = 0
                self._clear_fault(i)
        # 4. Per-fan zero under demand, after the grace.
        for i, rpm in enumerate((fan1, fan2)):
            if demand and rpm == 0 and now >= self._grace_until:
                if self._zero_since[i] is None:
                    self._zero_since[i] = now
            else:
                self._zero_since[i] = None
        if frozen and demand and self._gate(now, [frozen[0] - 1], "frozen tach"):
            self._rpm_changed_at = [now, now]
            self._temp_at_change = [None, None]
            self._event(f"ACT frozen tach: fan {frozen[0]} at {frozen[1]} RPM for {FROZEN_S}s")
            return self._dispatch(max_on, lambda msg: msg.replace(
                "Fans were stalled at 0 RPM;",
                f"Fan {frozen[0]} reading frozen at {frozen[1]} RPM for {FROZEN_S}s (stale tach, fan likely stopped);"))
        if self._declared:
            return self._pop_declared()
        if demand and fan1 == 0 and fan2 == 0:
            self._stall_ticks += 1
        else:
            self._stall_ticks = 0
        t["stall"] = self._stall_ticks
        if self._stall_ticks >= STALL_TICKS and self._gate(now, [0, 1], "stall"):
            self._stall_ticks = 0
            self._event("ACT stall: both fans 0 RPM")
            return self._dispatch(max_on)
        if self._declared:
            return self._pop_declared()
        zero = [i for i in (0, 1) if self._zero_since[i] is not None
                and now - self._zero_since[i] >= ONE_FAN_ZERO_S]
        if zero and self._gate(now, zero, "one fan at 0 RPM"):
            self._zero_since = [None, None]
            who = " and ".join(str(i + 1) for i in zero)
            self._event(f"ACT one fan stalled: fan {who} at 0 RPM for {ONE_FAN_ZERO_S:.0f}s")
            return self._dispatch(max_on, lambda msg: msg.replace(
                "Fans were stalled at 0 RPM;",
                f"Fan {who} stuck at 0 RPM for {ONE_FAN_ZERO_S:.0f}s while the curve asks for cooling;"))
        if self._declared:
            return self._pop_declared()
        if not frozen and not self._stall_ticks and not zero:
            self._holding = False
        return None

    def _pop_declared(self):
        msg, self._declared = self._declared, None
        return msg

    def _clear_fault(self, i):
        if i in self._fault_fans:
            self._fault_fans.discard(i)
            if not self._fault_fans:
                self.fault = None
                self._event("FAULT cleared: fan reading alive again")

    def _dispatch(self, max_on: bool, fix=lambda msg: msg) -> str | None:
        """Act on a decided recovery: inline, or handed to tick()'s defer."""
        if self._defer is None:
            return fix(self._recover(max_on))
        self._inflight = True
        self._event("QUEUED recovery on the worker")
        # Re-read Max Fans on the worker: a profile apply queued after this
        # decision may have changed it, and max_on would be stale.
        self._defer(lambda: fix(self._recover(self._max_fans_on())))
        return None

    def finished(self, msg: str | None = None) -> None:
        """A deferred recovery completed (or failed): decisions may resume.
        msg goes to the trace as the RESULT line an inline recovery writes."""
        self._inflight = False
        if msg and self.trace is not None and self._tick is not None:
            now = self._clock()
            try:
                self.trace.record(now, dict(self._tick, events=[f"RESULT {msg}"]),
                                  self._ages(now))
            except Exception:  # noqa: BLE001
                pass

    def _recover(self, max_on: bool) -> str:
        """Rewrite the platform profile (same value), then restore what that
        write resets, in profiles.apply's order: the profile's TDP, the curve,
        the undervolt (z13ctl: a firmware profile resets undervolt and hands the
        power limits back to firmware). A failed restore is reported, and does
        not stop the curve."""
        problems = []

        def restore(name):
            fn = getattr(self.fans, name, None)   # optional on a fans object
            if fn is None:
                return
            try:
                problem = fn()
            except Exception as e:  # noqa: BLE001
                problem = f"{name}: {e}"
            if problem:
                problems.append(problem)

        def result(text):
            return f"{text}; {'; '.join(problems)}" if problems else text

        try:
            curve = None if max_on else self.sensors.custom_fan_curve_points()
            self.fans.rewrite_profile()
            restore("restore_tdp")
            if max_on:
                self.fans.set_fans_max()
            elif curve:
                self.fans.set_fancurve_pwm(curve)
            restore("restore_undervolt")
            if not max_on and not curve:
                return result("Fans stalled; profile rewritten (no curve to restore)")
            return result("Fans were stalled at 0 RPM; profile and curve re-applied")
        except Exception as e:  # noqa: BLE001
            return f"Fan recovery failed: {e}"


class FanWatchdogRunner:
    """The desktop's timer callback logic, free of Qt so it can be tested.

    tick() only decides and enqueues; the recovery's hardware commands run on
    the serialized worker (submit(label, fn), e.g. ApplyWorker.submit). The
    worker's done(label, result) goes to on_done(), which returns the message
    to show. At most one recovery is pending at a time."""

    def __init__(self, watchdog, submit):
        self.watchdog = watchdog
        self._submit = submit

    @property
    def pending(self) -> bool:
        return self.watchdog._inflight

    def tick(self) -> str | None:
        return self.watchdog.tick(defer=lambda fn: self._submit(FAN_RECOVERY_LABEL, fn))

    def on_done(self, label, result) -> str | None:
        """None when `label` is not ours (other writes share the worker's signal)."""
        if label != FAN_RECOVERY_LABEL:
            return None
        msg = result if isinstance(result, str) else f"Fan recovery failed: {result}"
        self.watchdog.finished(msg)
        return msg


# ----------------------------------------------------------------------
# Fan trace: what the watchdog saw, for troubleshooting (both frontends)
# ----------------------------------------------------------------------

class FanTrace:
    """Writes the watchdog's readings to a log, at a rate that stays small.

    - one sample line every SAMPLE_S;
    - every tick while a spinning fan's reading has been unchanged for
      SUSPECT_S or more (the run-up to a frozen-tach trigger);
    - on any event (recovery, refused recovery, result): the event, the ticks
      of the last HISTORY_S not yet written (marked "<"), then every tick
      for AFTER_S, to see what the fans did next.

    A line: wall time, APU temp, fan1/fan2 RPM, the ACPI fan's own RPM, curve
    mode, what the curve asks (pwm 0-255), Max Fans, platform profile, stall
    count, and how long each fan reading has been unchanged.
    """

    SAMPLE_S = 30
    SUSPECT_S = 30
    HISTORY_S = 120
    AFTER_S = 60

    def __init__(self, logger, sensors=None, wall=time.time):
        self.log = logger
        self.sensors = sensors      # for the extra reads (ACPI fan, profile)
        self._wall = wall
        self._history = collections.deque()   # [mono, line, written], last HISTORY_S
        self._last_sample = float("-inf")
        self._after_until = float("-inf")

    @classmethod
    def to_file(cls, path, sensors=None, name="desktop", owner=None):
        """A trace writing to a rotating file. owner=(uid, gid) chowns the file
        (Decky runs as root but the log lives in deck's state folder)."""
        os.makedirs(os.path.dirname(path), exist_ok=True)
        logger = logging.getLogger(f"z13.fans.trace.{name}")
        logger.setLevel(logging.INFO)
        logger.propagate = False   # its own file; never floods app.log
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
        try:
            handler = _OwnedRotatingHandler(path, owner, maxBytes=TRACE_MAX_BYTES,
                                            backupCount=TRACE_BACKUPS, encoding="utf-8")
        except OSError as e:   # e.g. a symlink planted at the path: refuse, don't follow
            print(f"z13 fan trace: refusing to open {path}: {e}; file logging disabled",
                  file=sys.stderr)
            handler = logging.NullHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        trace = cls(logger, sensors)
        logger.info("%s --- fan trace started (%s, pid %d)", trace._stamp(), name, os.getpid())
        return trace

    def _stamp(self):
        w = self._wall()
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(w)) + f".{int(w * 10) % 10}"

    def _extra(self, method):
        fn = getattr(self.sensors, method, None)
        try:
            return fn() if fn else None
        except Exception:  # noqa: BLE001
            return None

    def line(self, tick, ages):
        rpm = tick.get("rpm") or (None, None)
        enabled = tick.get("enabled")
        temp = tick.get("temp")
        same = "/".join("-" if a is None else f"{a:.0f}" for a in ages)
        return (f"{self._stamp()} temp={'-' if temp is None else f'{temp:.1f}'}"
                f" rpm={_v(rpm[0])}/{_v(rpm[1])} acpi={_v(self._extra('get_acpi_fan_rpm'))}"
                f" curve={ {True: 'custom', False: 'auto'}.get(enabled, '?') }"
                f" want={_v(tick.get('want'))} max={'on' if tick.get('max_on') else 'off'}"
                f" prof={_v(self._extra('get_platform_profile'))}"
                f" stall={tick.get('stall', 0)} same={same}s"
                + (f" FAULT={tick['fault']!r}" if tick.get("fault") else ""))

    def record(self, now, tick, ages):
        text = self.line(tick, ages)
        entry = [now, text, False]
        self._history.append(entry)
        while self._history and now - self._history[0][0] > self.HISTORY_S:
            self._history.popleft()
        events = tick.get("events") or []
        rpm = tick.get("rpm") or (None, None)
        suspect = any(a is not None and a >= self.SUSPECT_S and r
                      for a, r in zip(ages, rpm))
        if events:
            for e in events:
                self.log.info("%s *** %s", self._stamp(), e)
            for old in list(self._history)[:-1]:
                if not old[2]:
                    self.log.info("< %s", old[1])
                    old[2] = True
            self._after_until = now + self.AFTER_S
        if events or suspect or now <= self._after_until or now - self._last_sample >= self.SAMPLE_S:
            self.log.info("%s", text)
            self._last_sample = now
            entry[2] = True


def _v(x):
    return "-" if x is None else x


TRACE_MAX_BYTES = 2 << 20
TRACE_BACKUPS = 3


class _OwnedRotatingHandler(logging.handlers.RotatingFileHandler):
    """RotatingFileHandler that hands each file it creates to `owner`."""

    def __init__(self, path, owner, **kw):
        self._owner = owner          # set before super() opens the first file
        super().__init__(path, **kw)

    def _open(self):
        """Open the log through one fd, never through the path a second time.

        The watchdog runs as root and the log lives in a deck-writable folder, so a
        planted symlink must not be followed: O_NOFOLLOW makes that an ELOOP, which
        is raised (fail closed, no fallback to a path open). Mode, owner and the
        regular-file check all act on the fd. Created 0600 from the first byte."""
        flags = (os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
                 | os.O_CLOEXEC | os.O_NONBLOCK)   # NONBLOCK: a FIFO can't hang the open
        fd = os.open(self.baseFilename, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise OSError(errno.EINVAL, "not a regular file", self.baseFilename)
            try:
                os.fchmod(fd, 0o600)
            except OSError:
                pass
            if self._owner is not None:
                try:
                    os.fchown(fd, *self._owner)
                except OSError:
                    pass
            return os.fdopen(fd, self.mode, encoding=self.encoding, errors=self.errors)
        except BaseException:
            os.close(fd)
            raise

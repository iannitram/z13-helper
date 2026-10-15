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
import logging
import logging.handlers
import os
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
        self._holding = False   # may_act refused the last due recovery
        self._tick = None       # this tick's readings + events, for the trace

    def _throttled(self) -> bool:
        return self._clock() - self._last_reapply < RETRY_S

    def _ready(self, why: str) -> bool:
        """Past the retry gap and allowed to write. Checked in that order so the
        (possibly costly) may_act check runs only when a recovery is due."""
        if self._throttled():
            return False
        if not self._may_act():
            if not self._holding:
                self._event(f"HOLD {why} (not allowed to act now)")
            self._holding = True
            return False
        self._holding = False
        return True

    def _event(self, text):
        self._tick["events"].append(text)

    def tick(self) -> str | None:
        """Run one check. Returns a status message when it had to act."""
        now = self._clock()
        self._tick = {"events": []}
        msg = self._check(now)
        if msg:
            self._event(f"RESULT {msg}")
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
            self._event("ACT dropped curve with Max Fans on")
            msg = self._recover(True)
            return msg.replace("Fans were stalled at 0 RPM;", "Max Fans re-applied after a profile change;")

        # 2. Stalled curve: custom mode claims active, the curve says >0 at the
        #    current temperature, but both fans read 0 RPM.
        if enabled is not True:
            self._stall_ticks = 0
            self._holding = False
            return None
        temp = t["temp"] = self.sensors.get_apu_temp()
        fan1, fan2 = t["rpm"] = self.sensors.get_fan_speeds()
        if temp is None or fan1 is None or fan2 is None:
            # A sensor dropout breaks the "consecutive ticks" chain: two 0-RPM
            # readings minutes apart must not add up to a stall.
            self._stall_ticks = 0
            return None
        # Hysteresis margin: the firmware keeps the fans off until a couple of
        # degrees above the first point once they have stopped.
        expected = t["want"] = self.sensors.expected_fan_pwm(temp - STALL_HYSTERESIS_C)
        # 3. Frozen tach (see FROZEN_S).
        frozen = None
        for i, rpm in enumerate((fan1, fan2)):
            if rpm != self._last_rpm[i]:
                self._last_rpm[i] = rpm
                self._rpm_changed_at[i] = now
            elif rpm > 0 and now - self._rpm_changed_at[i] >= FROZEN_S:
                frozen = (i + 1, rpm)
        if (frozen and expected is not None and expected >= STALL_MIN_PWM
                and self._ready("frozen tach")):
            self._last_reapply = now
            self._rpm_changed_at = [now, now]
            self._event(f"ACT frozen tach: fan {frozen[0]} at {frozen[1]} RPM for {FROZEN_S}s")
            return self._recover(max_on).replace(
                "Fans were stalled at 0 RPM;",
                f"Fan {frozen[0]} reading frozen at {frozen[1]} RPM for {FROZEN_S}s (stale tach, fan likely stopped);")
        if expected is not None and expected >= STALL_MIN_PWM and fan1 == 0 and fan2 == 0:
            self._stall_ticks += 1
        else:
            self._stall_ticks = 0
            if not frozen:
                self._holding = False
            return None
        t["stall"] = self._stall_ticks
        if self._stall_ticks < STALL_TICKS or not self._ready("stall"):
            return None
        self._last_reapply = now
        self._stall_ticks = 0
        self._event("ACT stall: both fans 0 RPM")
        return self._recover(max_on)

    def _recover(self, max_on: bool) -> str:
        """Rewrite the platform profile (same value) and then the curve."""
        try:
            curve = None if max_on else self.sensors.custom_fan_curve_points()
            self.fans.rewrite_profile()
            if max_on:
                self.fans.set_fans_max()
            elif curve:
                self.fans.set_fancurve_pwm(curve)
            else:
                return "Fans stalled; profile rewritten (no curve to restore)"
            return "Fans were stalled at 0 RPM; profile and curve re-applied"
        except Exception as e:  # noqa: BLE001
            return f"Fan recovery failed: {e}"


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
        handler = _OwnedRotatingHandler(path, owner, maxBytes=TRACE_MAX_BYTES,
                                        backupCount=TRACE_BACKUPS, encoding="utf-8")
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
                f" stall={tick.get('stall', 0)} same={same}s")

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
        stream = super()._open()
        if self._owner is not None:
            try:
                os.chown(self.baseFilename, *self._owner)
            except OSError:
                pass
        return stream

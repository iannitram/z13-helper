import os
import re
import logging

import host
import profiles

logger = logging.getLogger(__name__)

# Regexes for parsing z13ctl --get outputs
_RE_TDP_LINE   = re.compile(r"PL([123])\s+\([^)]+\):\s+(\d+)")
_RE_FAN_LINE   = re.compile(r"(\d+)°C:\s+(\d+)/255\s*\(\s*(\d+)%\)")
_RE_FAN_HEADER = re.compile(r"Fans:\s+(\d+)\s*RPM,\s*mode:\s*(\w+)")


_DEFAULT_Z13CTL = os.path.expanduser("~/.local/bin/z13ctl")

# z13ctl --device targets for the RGB/brightness commands.
_LIGHTING_DEVICES = ("keyboard", "lightbar")


class HardwareController:
    """Wraps z13ctl CLI to control ASUS ROG Flow Z13 hardware features."""

    def __init__(self, z13ctl_path=None):
        if z13ctl_path is None:
            # Inside Flatpak, host paths are not visible to os.path.exists.
            # Trust the default path and let flatpak-spawn resolve it on the host.
            if host.in_flatpak():
                z13ctl_path = _DEFAULT_Z13CTL
            else:
                z13ctl_path = _DEFAULT_Z13CTL if os.path.exists(_DEFAULT_Z13CTL) else "z13ctl"
        self.z13ctl_path = z13ctl_path

    def _run_command(self, args, quiet: bool = False, timeout: float = 120):
        """z13ctl's output; RuntimeError when it fails (ProfileControl relies on that)."""
        cmd = [self.z13ctl_path] + args
        r = host.run(cmd, timeout=timeout)
        if r.timed_out:
            if not quiet:
                logger.error(f"Command '{' '.join(cmd)}' timed out after {timeout} s")
            raise RuntimeError(f"z13ctl command timed out after {timeout} s")
        if r.rc == host.RC_NOT_FOUND:
            logger.error(f"z13ctl not found at: {self.z13ctl_path}")
            raise RuntimeError("z13ctl executable not found.")
        if r.rc != 0:
            if not quiet:
                logger.error(f"Command '{' '.join(cmd)}' failed: {r.out}")
            raise RuntimeError(f"z13ctl command failed: {r.out}")
        return r.out

    # --- Write commands ---

    def set_profile(self, profile_name: str):
        """Sets the hardware power profile. Valid values: balanced, performance, quiet, custom."""
        return self._run_command(["profile", "--set", profile_name])

    def rewrite_profile(self):
        """Write the current platform profile back to itself.

        A same-value write still goes through asus-wmi's throttle-policy path,
        which makes the firmware re-initialise fan control. That is the only
        thing found to revive fans stuck at 0 RPM under a stalled custom curve
        (fan_watchdog.py). It drops the custom curve, so re-apply it after.
        """
        return self._run_command(["profile", "--set", self.get_platform_profile()])

    def get_platform_profile(self) -> str:
        """The firmware profile actually in force (quiet/balanced/performance).

        Read from sysfs rather than `z13ctl profile --get`: since z13ctl 1.3 the
        latter answers "custom" whenever one of z13ctl's own profiles is active
        (which is always, once a fan curve has been set), and the platform
        profile underneath is what the profile write needs.
        """
        try:
            with open("/sys/firmware/acpi/platform_profile") as f:
                v = f.read().strip().lower()
            if v in ("quiet", "balanced", "performance"):
                return v
        except OSError:
            pass
        v = self.get_profile().strip().lower()
        return v if v in ("quiet", "balanced", "performance") else "balanced"

    def set_battery_limit(self, limit_percent: int):
        """Sets the battery charge limit (40–100)."""
        return self._run_command(["batterylimit", "--set", str(limit_percent)])

    def set_brightness(self, level: str, devices=None) -> list:
        """Sets keyboard/lightbar brightness. Valid values: low, medium, high, off.

        Each device gets its own z13ctl call: with the default --device all,
        z13ctl sends each packet to every device in turn and aborts on the
        first write error, so a stalled lightbar endpoint (ETIMEDOUT on
        /dev/hidraw5) or a detached keyboard stops the other device from ever
        receiving the brightness packet.

        A lightbar write that fails with a wedged USB endpoint (timeout) triggers
        one rate-limited USB reset (profiles.recover_lightbar) and one retry;
        one that fails with a stale daemon handle (ENODEV -- the daemon cached a
        hidraw path that no longer exists) triggers one daemon restart
        (profiles.recover_lightbar_stale) and one retry. The keyboard is always
        written first.

        Returns the devices that failed. Raises only if every device failed.
        """
        devices = tuple(d for d in _LIGHTING_DEVICES if d in (devices or _LIGHTING_DEVICES))
        failed = []
        errors = []
        for device in devices:
            argv = ["--device", device, "brightness", level]
            try:
                self._run_command(argv, timeout=30)
            except RuntimeError as e:
                if device == "lightbar":
                    kind = profiles.lightbar_failure_kind(str(e))
                    recover = {"wedged": profiles.recover_lightbar,
                               "stale": profiles.recover_lightbar_stale}.get(kind)
                    if recover:
                        ok, msg = recover()
                        if ok:
                            try:
                                self._run_command(argv, timeout=30)
                                continue
                            except RuntimeError as e2:
                                e = RuntimeError(f"{e2} (still failing after a {msg})")
                        else:
                            e = RuntimeError(f"{e}; {msg}")
                failed.append(device)
                errors.append(f"{device}: {e}")
        if len(failed) == len(devices):
            raise RuntimeError("; ".join(errors))
        return failed

    # --- Read commands ---

    def get_profile(self) -> str:
        """Returns the active hardware profile name."""
        return self._run_command(["profile", "--get"])

    def get_battery_limit(self) -> int:
        """Returns the current battery charge limit as an integer."""
        raw = self._run_command(["batterylimit", "--get"])
        try:
            return int(raw)
        except ValueError:
            logger.warning(f"Unexpected batterylimit output: {raw!r}")
            return None

    def get_status(self) -> str:
        """Returns full z13ctl status output."""
        return self._run_command(["status"])

    # --- TDP (PL1/PL2/PL3 power limits) ---

    def get_tdp(self) -> dict:
        """Returns dict {pl1, pl2, pl3} of current TDP limits in watts.

        Falls back to {} if parsing fails.
        """
        try:
            out = self._run_command(["tdp", "--get"])
        except RuntimeError:
            return {}
        result = {}
        for line in out.splitlines():
            m = _RE_TDP_LINE.search(line)
            if m:
                result[f"pl{m.group(1)}"] = int(m.group(2))
        return result

    def set_tdp(self, pl1=None, pl2=None, pl3=None, force: bool = False):
        """Apply per-rail TDP overrides. None means leave that rail alone.

        If only pl1 is given, all three are pinned to that value (z13ctl behaviour).
        Pass force=True to allow PL1 above 75W.
        """
        # z13ctl requires --set as the anchor; --plN flags override individual rails.
        # If only some rails are provided, use the smallest as the --set value and
        # specify the others explicitly so values stay distinct.
        provided = [v for v in (pl1, pl2, pl3) if v is not None]
        if not provided:
            raise ValueError("set_tdp requires at least one of pl1/pl2/pl3")

        anchor = pl1 if pl1 is not None else min(provided)
        args = ["tdp", "--set", str(anchor)]
        if pl1 is not None and pl1 != anchor:
            args += ["--pl1", str(pl1)]
        if pl2 is not None:
            args += ["--pl2", str(pl2)]
        if pl3 is not None:
            args += ["--pl3", str(pl3)]
        if force:
            args.append("--force")
        return self._run_command(args)

    def reset_tdp(self):
        """Reset TDP to firmware-managed (returns to balanced profile)."""
        return self._run_command(["tdp", "--reset"])

    # --- Fan curves ---

    def get_fancurve(self) -> dict:
        """Returns dict with keys:
            mode:   current fan mode ("auto" or "manual")
            rpm:    current fan RPM
            points: list of (temp_c, pwm_0_255, pct) tuples — 8 entries
        """
        try:
            out = self._run_command(["fancurve", "--get"])
        except RuntimeError:
            return {"mode": "unknown", "rpm": 0, "points": []}
        rpm, mode, points = 0, "unknown", []
        for line in out.splitlines():
            m = _RE_FAN_HEADER.search(line)
            if m:
                rpm  = int(m.group(1))
                mode = m.group(2)
                continue
            m = _RE_FAN_LINE.search(line)
            if m:
                points.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        return {"mode": mode, "rpm": rpm, "points": points}

    def set_fancurve(self, points):
        """Apply a custom 8-point fan curve.

        `points` is a list of (temp_c, pct) tuples. Temps must be monotonically
        increasing; percentages must be non-decreasing. z13ctl validates this.
        """
        if len(points) != 8:
            raise ValueError("Fan curve must have exactly 8 points")
        spec = ",".join(f"{int(t)}:{int(p)}%" for t, p in points)
        return self._run_command(["fancurve", "--set", spec])

    # Temp anchors for the flat max-fans curve. Any 8 monotonically increasing
    # temps work; z13ctl only requires monotonic temps + non-decreasing speeds.
    # Starts at 20 C: the firmware idles the fans below the first point (with
    # hysteresis), so a curve starting at 40 C is not "always 100%".
    _MAX_FAN_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)

    def set_fans_max(self):
        """Pin both fans to 100% across the whole curve (max cooling)."""
        spec = ",".join(f"{t}:100%" for t in self._MAX_FAN_TEMPS)
        return self._run_command(["fancurve", "--set", spec])

    def set_fancurve_pwm(self, points):
        """Apply an 8-point curve given as (temp_c, pwm_0_255) tuples.

        Like set_fancurve() but takes raw PWM instead of percent, so a curve
        snapshotted via get_fancurve() restores bit-for-bit (no pct rounding).
        """
        if len(points) != 8:
            raise ValueError("Fan curve must have exactly 8 points")
        spec = ",".join(f"{int(t)}:{int(pwm)}" for t, pwm in points)
        return self._run_command(["fancurve", "--set", spec])

    def reset_fancurve(self):
        """Restore the firmware's automatic fan mode."""
        return self._run_command(["fancurve", "--reset"])

    # --- Undervolt (CPU Curve Optimizer) ---

    def is_undervolt_available(self) -> bool:
        """Probes whether ryzen_smu is loaded by attempting a --get."""
        try:
            self._run_command(["undervolt", "--get"], quiet=True)
            return True
        except RuntimeError:
            return False

    def get_undervolt(self) -> int:
        """Returns the current CO offset, or None if unavailable."""
        try:
            out = self._run_command(["undervolt", "--get"], quiet=True)
        except RuntimeError:
            return None
        # Output contains a number; extract first signed int found.
        m = re.search(r"-?\d+", out)
        return int(m.group(0)) if m else None

    def set_undervolt(self, value: int):
        """Apply CPU CO offset (0 to -40). Requires ryzen_smu kernel module."""
        if value > 0 or value < -40:
            raise ValueError("Undervolt offset must be in range -40..0")
        return self._run_command(["undervolt", "--set", str(value)])

    def reset_undervolt(self):
        """Reset CO offset to 0 (stock voltage)."""
        return self._run_command(["undervolt", "--reset"])


if __name__ == "__main__":
    controller = HardwareController()
    try:
        print(f"Profile:       {controller.get_profile()}")
        print(f"Battery limit: {controller.get_battery_limit()}%")
        print(f"TDP:           {controller.get_tdp()}")
        print(f"Fan curve:     {controller.get_fancurve()}")
        print(f"Undervolt:     {controller.get_undervolt()} "
              f"(available={controller.is_undervolt_available()})")
        print()
        print(controller.get_status())
    except Exception as e:
        print(f"Error: {e}")

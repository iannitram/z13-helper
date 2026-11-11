"""Pure setpoint decisions for Phase 15 goals.

``step`` consumes a profile goal, one sensor snapshot, current setpoints, and
the kernel's per-rail bounds. It returns a new TDP/curve pair plus a reason;
it performs no reads, writes, imports from Qt, or clock access.

Readings used here are ``fan_rpm`` (Quiet), ``comfort_temp_c`` (Comfort),
``fps`` (Hold FPS) and ``apu_temp_c`` (every goal). The Comfort reading is a
proxy supplied by its source probe, not a claim that the Z13 exposes a skin
temperature sensor.

Reading safety (#123 owner decisions):

1. A reading is valid only if it is an int or float (not bool), finite and
   >= 0. Anything else (NaN, inf, negative, bool, string) is treated exactly
   like a missing reading. A bad reading never raises and never moves power.
2. The APU over-temperature check runs first for every goal: a valid
   ``apu_temp_c`` at or above the limit sheds one power step and steps the fans
   up, regardless of the minimum dwell and of a missing goal reading.
3. Max needs a valid ``apu_temp_c``; without one it holds power (the fans
   still go to full speed).
4. Other goals hold everything when their own reading is missing or invalid.

The temperature limit is ``APU_TEMP_LIMIT_C`` unless the caller passes
``apu_temp_limit_c``. ``ranges`` has the shape ``power_range.detect_ranges()``
returns: ``{"pl1": (min_w, max_w), "pl2": ..., "pl3": ...}`` with integer watts.
"""

import math
from copy import deepcopy

RAILS = ("pl1", "pl2", "pl3")
MAX_FAN_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)
MAX_STEP_W = 5
MAX_STEP_PCT = 5
MIN_DWELL_S = 10
HYSTERESIS_RPM = 150
HYSTERESIS_C = 2
HYSTERESIS_FPS = 3
# Above this APU temperature every goal sheds power. Same default as the
# archived build/123 Limits; a caller with a better figure passes its own.
APU_TEMP_LIMIT_C = 95.0
OVER_LIMIT_REASON = "over APU temperature limit"
GOAL_SENSORS = {"quiet": ("fan_rpm", "max_rpm"),
                "comfort": ("comfort_temp_c", "target_c"),
                "fps": ("fps", "target_fps")}


def _reading(value):
    """Return a valid reading as a float, else None (decision 1)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return float(value)


def _finite_number(value):
    return (not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value))


def _unchanged(current, reason, state=None):
    return {"tdp": deepcopy(current.get("tdp")),
            "fan_curve": deepcopy(current.get("fan_curve")),
            "reason": reason,
            "state": deepcopy(state or {})}


def _limits(ranges):
    result = {}
    for rail in RAILS:
        try:
            lo, hi = ranges[rail]
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"missing power bounds for {rail}") from None
        if isinstance(lo, bool) or isinstance(hi, bool) or not isinstance(lo, int) or not isinstance(hi, int) or lo <= 0 or hi < lo:
            raise ValueError(f"invalid power bounds for {rail}")
        result[rail] = (lo, hi)
    # Check that the ordered intervals have at least one feasible combination.
    if (result["pl1"][0] > result["pl2"][1] or
            max(result["pl1"][0], result["pl2"][0]) > result["pl3"][1]):
        raise ValueError("power bounds cannot satisfy sPPT >= SPL and fPPT >= sPPT")
    return result


def _ordered_tdp(tdp, bounds):
    """Clamp three values while preserving PL2 >= PL1 and PL3 >= PL2."""
    vals = {}
    for rail in RAILS:
        value = tdp.get(rail)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"current TDP needs integer {rail}")
        lo, hi = bounds[rail]
        vals[rail] = max(lo, min(hi, value))
    # Lower an earlier rail only when the next rail's maximum requires it.
    lo2, hi2 = bounds["pl2"]
    if vals["pl1"] > hi2:
        vals["pl1"] = hi2
    vals["pl2"] = max(lo2, vals["pl1"], vals["pl2"])
    lo3, hi3 = bounds["pl3"]
    if vals["pl2"] > hi3:
        vals["pl2"] = hi3
        vals["pl1"] = min(vals["pl1"], vals["pl2"])
    vals["pl3"] = max(lo3, vals["pl2"], vals["pl3"])
    return vals


def _shift_tdp(tdp, bounds, direction):
    """Move all rails together by at most one bounded step."""
    shift = MAX_STEP_W * direction
    candidate = {r: max(bounds[r][0], min(bounds[r][1], tdp[r] + shift)) for r in RAILS}
    return _ordered_tdp(candidate, bounds)


def _curve(curve):
    if not isinstance(curve, (list, tuple)) or len(curve) != 8:
        raise ValueError("current fan_curve needs eight [temp, pct] points")
    points = []
    previous_temp, previous_pct = -1, -1
    for point in curve:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise ValueError("fan_curve points must be [temp, pct]")
        temp, pct = point
        if (isinstance(temp, bool) or isinstance(pct, bool) or
                not isinstance(temp, int) or not isinstance(pct, int) or
                not 20 <= temp <= 110 or not 0 <= pct <= 100 or
                temp < previous_temp or pct < previous_pct):
            raise ValueError("invalid current fan_curve")
        points.append([temp, pct])
        previous_temp, previous_pct = temp, pct
    return points


def _move_curve(points, direction):
    moved = []
    for temp, pct in points:
        floor = max(0, int((temp - 70) * 1.2))
        moved.append([temp, max(floor, min(100, pct + MAX_STEP_PCT * direction))])
    # Preserve a non-decreasing curve after applying the BIOS safety floor.
    for i in range(1, len(moved)):
        moved[i][1] = max(moved[i][1], moved[i - 1][1])
    return moved


def step(goal, readings, current, ranges, *, now_s, state=None,
         apu_temp_limit_c=APU_TEMP_LIMIT_C):
    """Return the next bounded setpoints for one goal-control tick.

    ``goal`` is the profile representation, e.g. ``{"kind":"fps",
    "target_fps":60}``; ``current`` has ``tdp`` and ``fan_curve``; ``ranges``
    maps ``pl1``, ``pl2``, and ``pl3`` to integer ``(min, max)`` watt bounds
    (``power_range.detect_ranges()``). ``state`` carries ``last_change_at``
    between calls, and ``now_s`` is a monotonic timestamp supplied by the
    caller. ``apu_temp_limit_c`` is the over-temperature limit in degrees C.
    Output state is safe to feed back unchanged. Invalid goal/input shapes
    raise ``ValueError``; invalid reading values never do.

    Every step changes TDP or curve by at most its per-tick limit. A deadband
    provides hysteresis; the caller-supplied timestamp enforces dwell, except
    for an over-temperature shed, which happens on every tick it applies.
    Missing or invalid required readings yield unchanged power and a reason.
    """
    if not isinstance(goal, dict) or not isinstance(readings, dict) or not isinstance(current, dict):
        raise ValueError("goal, readings, and current must be mappings")
    kind = goal.get("kind")
    if kind not in ("quiet", "comfort", "fps", "max"):
        raise ValueError("goal kind must be quiet, comfort, fps, or max")
    if not _finite_number(now_s):
        raise ValueError("now_s must be a finite monotonic timestamp")
    if not _finite_number(apu_temp_limit_c) or apu_temp_limit_c <= 0:
        raise ValueError("apu_temp_limit_c must be a finite positive number")
    target = None
    if kind != "max":
        sensor, target_key = GOAL_SENSORS[kind]
        target = goal.get(target_key)
        if not _finite_number(target) or target < 0:
            raise ValueError(f"{target_key} must be a finite number >= 0")
    state = dict(state or {})
    bounds = _limits(ranges)
    tdp = _ordered_tdp(current.get("tdp") or {}, bounds)
    curve = _curve(current.get("fan_curve"))
    full = [[temp, 100] for temp in MAX_FAN_TEMPS]
    apu = _reading(readings.get("apu_temp_c"))

    # Decision 2: over-temperature first, for every goal, ignoring dwell and
    # the goal's own reading.
    if apu is not None and apu >= apu_temp_limit_c:
        next_tdp = _shift_tdp(tdp, bounds, -1)
        next_curve = full if kind == "max" else _move_curve(curve, 1)
        new_state = dict(state)
        if next_tdp != tdp or next_curve != curve:
            new_state["last_change_at"] = now_s
        return {"tdp": next_tdp, "fan_curve": next_curve,
                "reason": OVER_LIMIT_REASON, "state": new_state}

    if kind == "max":
        # Max is the explicit safety override: request full speed immediately.
        new_state = dict(state)
        if apu is None:
            # Decision 3: no trustworthy chip temperature, so hold power.
            if curve != full:
                new_state["last_change_at"] = now_s
            return {"tdp": tdp, "fan_curve": full,
                    "reason": "Waiting for apu_temp_c reading; Max holds power with full fan speed",
                    "state": new_state}
        top = _ordered_tdp({r: bounds[r][1] for r in RAILS}, bounds)
        last = state.get("last_change_at")
        dwell_ok = not isinstance(last, (int, float)) or now_s - last >= MIN_DWELL_S
        next_tdp = tdp
        if dwell_ok:
            next_tdp = {r: min(top[r], tdp[r] + MAX_STEP_W) for r in RAILS}
            next_tdp = _ordered_tdp(next_tdp, bounds)
        if next_tdp != tdp or curve != full:
            new_state["last_change_at"] = now_s
        reason = "Max requests full fan speed and bounded maximum power"
        if not dwell_ok and next_tdp != top:
            reason += "; power increase is waiting for minimum dwell"
        return {"tdp": next_tdp, "fan_curve": full, "reason": reason, "state": new_state}

    # Decisions 1 and 4: an invalid goal reading is the same as a missing one.
    value = _reading(readings.get(sensor))
    if value is None:
        return _unchanged(current, f"Waiting for {sensor} reading", state)
    if kind == "quiet":
        error, deadband = value - target, HYSTERESIS_RPM
    elif kind == "comfort":
        error, deadband = value - target, HYSTERESIS_C
    else:
        # Above target -> negative direction (less power), below -> more power, like Quiet/Comfort.
        error, deadband = value - target, HYSTERESIS_FPS
    if abs(error) <= deadband:
        return _unchanged(current, f"{kind.title()} is within its hysteresis band", state)
    if kind == "quiet" and error < -deadband:
        return _unchanged(current, "Quiet fan speed is below its ceiling", state)
    direction = -1 if error > 0 else 1
    last = state.get("last_change_at")
    if isinstance(last, (int, float)) and now_s - last < MIN_DWELL_S:
        return _unchanged(current, "Minimum dwell time has not elapsed", state)
    next_tdp = _shift_tdp(tdp, bounds, direction)
    curve_direction = direction if kind != "comfort" else -direction
    next_curve = _move_curve(curve, curve_direction)
    new_state = dict(state)
    new_state["last_change_at"] = now_s
    return {"tdp": next_tdp, "fan_curve": next_curve,
            "reason": f"{kind.title()} error {error:g} exceeds hysteresis; moved one bounded step",
            "state": new_state}

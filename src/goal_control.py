"""Pure setpoint decisions for Phase 15 goals.

``step`` consumes a profile goal, one sensor snapshot, current setpoints, and
the kernel's per-rail bounds. It returns a new TDP/curve pair plus a reason;
it performs no reads, writes, imports from Qt, or clock access.

Readings used here are ``fan_rpm`` (Quiet), ``comfort_temp_c`` (Comfort), and
``fps`` (Hold FPS). The Comfort reading is a proxy supplied by its source
probe, not a claim that the Z13 exposes a skin temperature sensor.
"""

from copy import deepcopy

RAILS = ("pl1", "pl2", "pl3")
MAX_FAN_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)
MAX_STEP_W = 5
MAX_STEP_PCT = 5
MIN_DWELL_S = 10
HYSTERESIS_RPM = 150
HYSTERESIS_C = 2
HYSTERESIS_FPS = 3


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


def step(goal, readings, current, ranges, *, now_s, state=None):
    """Return the next bounded setpoints for one goal-control tick.

    ``goal`` is the profile representation, e.g. ``{"kind":"fps",
    "target_fps":60}``; ``current`` has ``tdp`` and ``fan_curve``; ``ranges``
    maps ``pl1``, ``pl2``, and ``pl3`` to integer ``(min, max)`` watt bounds.
    ``state`` carries ``last_change_at`` between calls, and ``now_s`` is a
    monotonic timestamp supplied by the caller. Output state is safe to feed
    back unchanged. Invalid goal/input shapes raise ``ValueError``.

    Every non-Max step changes TDP or curve by at most its per-tick limit. A
    deadband provides hysteresis; the caller-supplied timestamp enforces dwell.
    Missing required readings yield unchanged setpoints and a reason.
    """
    if not isinstance(goal, dict) or not isinstance(readings, dict) or not isinstance(current, dict):
        raise ValueError("goal, readings, and current must be mappings")
    kind = goal.get("kind")
    if kind not in ("quiet", "comfort", "fps", "max"):
        raise ValueError("goal kind must be quiet, comfort, fps, or max")
    if isinstance(now_s, bool) or not isinstance(now_s, (int, float)):
        raise ValueError("now_s must be a monotonic timestamp")
    state = dict(state or {})
    bounds = _limits(ranges)
    tdp = _ordered_tdp(current.get("tdp") or {}, bounds)
    curve = _curve(current.get("fan_curve"))

    if kind == "max":
        # Max is the explicit safety override: request full speed immediately.
        full = [[temp, 100] for temp in MAX_FAN_TEMPS]
        target = {r: bounds[r][1] for r in RAILS}
        target = _ordered_tdp(target, bounds)
        new_state = dict(state)
        last = state.get("last_change_at")
        dwell_ok = not isinstance(last, (int, float)) or now_s - last >= MIN_DWELL_S
        next_tdp = tdp
        if dwell_ok:
            next_tdp = {r: min(target[r], tdp[r] + MAX_STEP_W) for r in RAILS}
            next_tdp = _ordered_tdp(next_tdp, bounds)
        if next_tdp != tdp or curve != full:
            new_state["last_change_at"] = now_s
        reason = "Max requests full fan speed and bounded maximum power"
        if not dwell_ok and next_tdp != target:
            reason += "; power increase is waiting for minimum dwell"
        return {"tdp": next_tdp, "fan_curve": full, "reason": reason, "state": new_state}

    sensor, target_key = {"quiet": ("fan_rpm", "max_rpm"), "comfort": ("comfort_temp_c", "target_c"), "fps": ("fps", "target_fps")}[kind]
    value, target = readings.get(sensor), goal.get(target_key)
    if value is None:
        return _unchanged(current, f"Waiting for {sensor} reading", state)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or isinstance(target, bool) or not isinstance(target, (int, float)):
        raise ValueError(f"{sensor} and {target_key} must be numeric")
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

"""Live battery readout: charging/draining rate and time to full or empty (#228).

Pure logic plus one sysfs reader. ``read_sample`` takes an injectable BAT root so
tests use a fake tree; nothing here writes hardware.

Rate sign comes from ``status``: sysfs ``power_now`` is always a magnitude.
The rate is smoothed with an EMA (``RateSmoother``) so the ETA does not jump
with load spikes. The ETA to "full" targets the charge limit, not 100%.
"""

import math
import os
from datetime import datetime, timedelta

BAT_ROOT = "/sys/class/power_supply/BAT0"
EMA_TAU_S = 45.0          # smoothing time constant for the rate (30-60 s band)
NEAR_ZERO_W = 1.0         # below this magnitude the rate is "near zero": no ETA
MAX_ETA_S = 24 * 3600     # longer estimates are not shown


def _read_text(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _read_num(root, name):
    text = _read_text(os.path.join(root, name))
    if text is None:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def read_sample(root=BAT_ROOT):
    """One raw reading of a battery, or None when there is no battery."""
    if _read_text(os.path.join(root, "status")) is None and \
            _read_num(root, "capacity") is None:
        return None
    status = _read_text(os.path.join(root, "status")) or None
    capacity = _read_num(root, "capacity")
    voltage = _read_num(root, "voltage_now")  # µV

    power = _read_num(root, "power_now")       # µW
    power_w = abs(power) / 1e6 if power else None
    if not power_w:
        current = _read_num(root, "current_now")  # µA
        if current and voltage:
            power_w = abs(current * voltage) / 1e12  # µA * µV = pW

    def energy(now_name, full_name, charge_now, charge_full):
        out = []
        for name in (now_name, full_name):
            value = _read_num(root, name)
            out.append(value / 1e6 if value is not None else None)  # µWh -> Wh
        if out[0] is None or out[1] is None:
            if voltage is not None:
                # µAh * µV = pWh, so /1e12 gives Wh
                out = []
                for name in (charge_now, charge_full):
                    value = _read_num(root, name)
                    out.append(value * voltage / 1e12 if value is not None else None)
        return out

    now_wh, full_wh = energy("energy_now", "energy_full", "charge_now", "charge_full")
    return {
        "status": status,
        "capacity": int(capacity) if capacity is not None else None,
        "power_w": power_w,
        "energy_now_wh": now_wh,
        "energy_full_wh": full_wh,
    }


def signed_rate_w(sample):
    """Net watts into (+) or out of (-) the battery. Zero when not moving."""
    if not sample or sample["power_w"] is None:
        return 0.0
    status = sample["status"]
    if status == "Discharging":
        return -sample["power_w"]
    if status == "Charging":
        return sample["power_w"]
    return 0.0


class RateSmoother:
    """EMA over the signed rate. Restarts when the battery status changes."""

    def __init__(self, tau_s=EMA_TAU_S):
        self.tau_s = tau_s
        self._value = None
        self._t = None
        self._status = None

    def update(self, rate_w, now_s, status=None):
        if self._value is None or status != self._status:
            self._value = float(rate_w)
        else:
            dt = max(0.0, now_s - self._t)
            alpha = 1.0 - math.exp(-dt / self.tau_s)
            self._value += alpha * (rate_w - self._value)
        self._t = now_s
        self._status = status
        return self._value


def format_clock(dt):
    """12-hour clock without a leading zero: 7:00am, 4:45pm."""
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d}{'am' if dt.hour < 12 else 'pm'}"


def format_duration(seconds):
    seconds = int(round(seconds))
    if seconds < 60:
        return "<1 m"
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} h {minutes} m" if hours else f"{minutes} m"


def readout(sample, rate_w, charge_limit, now):
    """State, rate and ETA text for one sample and its smoothed signed rate."""
    if sample is None:
        return {"state": "Unavailable", "rate_w": None, "limit": charge_limit,
                "eta_text": None, "text": "Battery readout unavailable"}

    target = int(charge_limit) if charge_limit is not None else 100
    now_wh = sample["energy_now_wh"]
    full_wh = sample["energy_full_wh"]
    status = sample["status"]
    rate = f"{rate_w:+.1f} W"

    at_limit = False
    if status in ("Full", "Not charging"):
        at_limit = True
    elif full_wh and now_wh is not None:
        at_limit = now_wh >= full_wh * target / 100.0
    elif sample["capacity"] is not None:
        at_limit = sample["capacity"] >= target

    if at_limit and rate_w >= 0:
        text = f"Holding at limit ({target}%) · {rate}" if status != "Full" \
            else f"Holding (full, {target}% limit) · {rate}"
        return {"state": "Holding", "rate_w": rate_w, "limit": charge_limit,
                "eta_text": None, "text": text}

    if abs(rate_w) < NEAR_ZERO_W:
        return {"state": "Holding", "rate_w": rate_w, "limit": charge_limit,
                "eta_text": None,
                "text": f"Holding {rate} (near zero, no estimate)"}

    eta_text = None
    note = ""
    if rate_w > 0:
        state = "Charging"
        if full_wh and now_wh is not None:
            need_wh = full_wh * target / 100.0 - now_wh
            seconds = need_wh / rate_w * 3600.0
            eta_text = _eta(f"Full ({target}%)", seconds, now)
            if seconds > MAX_ETA_S:
                eta_text = None
                note = " · no estimate (over 24 h)"
    else:
        state = "Draining"
        if now_wh is not None:
            seconds = now_wh / -rate_w * 3600.0
            eta_text = _eta("Empty", seconds, now)
            if seconds > MAX_ETA_S:
                eta_text = None
                note = " · no estimate (over 24 h)"

    text = f"{state} {rate}"
    if eta_text:
        text += f" · {eta_text}"
    else:
        text += note
    return {"state": state, "rate_w": rate_w, "limit": charge_limit,
            "eta_text": eta_text, "text": text}


def _eta(label, seconds, now):
    if seconds <= 0:
        return None
    when = now + timedelta(seconds=seconds)
    return f"{label} at {format_clock(when)}, in {format_duration(seconds)}"


def current(root=BAT_ROOT, charge_limit=None, now=None, smoother=None):
    """Read the battery once and return the readout dict (JSON-safe)."""
    now = now or datetime.now()
    sample = read_sample(root)
    if sample is None:
        return readout(None, 0.0, charge_limit, now)
    raw = signed_rate_w(sample)
    if smoother is not None:
        raw = smoother.update(raw, now.timestamp(), sample["status"])
    return readout(sample, raw, charge_limit, now)

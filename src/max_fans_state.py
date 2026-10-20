"""Max Fans state shared by the desktop app and the Decky plugin.

Both frontends toggle Max Fans, so the ON/OFF flag and the curve snapshot to
restore on OFF live in one small file that each side re-reads instead of
caching:

    {"enabled": bool, "saved_curve": [[temp, pwm], ... 8 points]}

decky/main.py carries a copy of this logic (it can't import from the app).
The legacy config.json keys `fans_max` / `fan_curve_saved` are read only to
seed this file the first time.
"""

import json
import os

STATE_PATH = os.path.expanduser("~/.config/z13-helper/max-fans.json")


def load() -> dict | None:
    """Current state, or None if the file is missing or unreadable."""
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def is_enabled() -> bool:
    return bool((load() or {}).get("enabled", False))


def saved_curve():
    return (load() or {}).get("saved_curve")


def restore_plan():
    """What Max Fans OFF should restore, as (kind, curve).

    ("curve", pts): put the saved [temp, pwm] snapshot back.
    ("auto", None): the snapshot is explicitly null, i.e. the profile has no
        custom curve, so release the fans to firmware auto.
    ("unknown", None): no snapshot key, or the file is missing/unreadable;
        the caller falls back to the active profile's curve, else auto.
    """
    state = load()
    if state is None or "saved_curve" not in state:
        return ("unknown", None)
    curve = state["saved_curve"]
    if curve is None:
        return ("auto", None)
    return ("curve", curve) if curve else ("unknown", None)


def record_profile_curve(curve):
    """Max Fans is ON and the profile's curve ([temp, pct]) changed: keep the
    restore snapshot in step. None (custom curve disabled) clears it to auto."""
    if curve:
        save(saved_curve=[[int(t), round(int(p) * 255 / 100)] for t, p in curve])
    else:
        save(saved_curve=None)


def save(**changes):
    """Merge `changes` into the state file atomically (tmp + rename)."""
    state = load() or {}
    state.update(changes)
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = f"{STATE_PATH}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE_PATH)


def migrate_legacy(config):
    """Seed the shared file from the app's old config.json keys, once."""
    if load() is not None:
        return
    seed = {"enabled": bool(config.get("fans_max", False))}
    if config.get("fan_curve_saved") is not None:
        seed["saved_curve"] = config["fan_curve_saved"]   # absent = unknown, null = auto
    save(**seed)

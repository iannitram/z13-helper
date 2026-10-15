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
    save(enabled=bool(config.get("fans_max", False)),
         saved_curve=config.get("fan_curve_saved"))

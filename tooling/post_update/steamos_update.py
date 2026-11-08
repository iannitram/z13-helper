"""SteamOS-updated detection for the After SteamOS update tab (issue #70).

A BUILD_ID change, or a kernel major change (6.x to 7.x), marks the state "SteamOS updated,
re-verify hardware" until the user acknowledges it. The last values seen live in a small
JSON file under the user's data dir, written atomically. Nothing here runs a command.
"""

import json
import os
import re
from collections.abc import Callable, Sequence
from typing import Optional

from post_update_state import read_build_id

STATE_REL = os.path.join(".local", "share", "z13-helper", "post-update-state.json")
ARMOURY_DIR = "/sys/class/firmware-attributes/asus-armoury/attributes"
# The ppt_* attributes z13ctl writes (src/power_range.py) and, by name, any fan attribute.
PPT_ATTRS = ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt")
REVERIFY_TEXT = "SteamOS updated, re-verify hardware"


def kernel_major(release: Optional[str]) -> Optional[int]:
    """7 for '7.2.7-valve2-2-neptune'; None when the text has no leading number."""
    m = re.match(r"\s*(\d+)\.", release or "")
    return int(m.group(1)) if m else None


def state_path(home: str) -> str:
    return os.path.join(home, STATE_REL)


def load_state(path: str) -> dict:
    """The saved state, or {} when missing, unreadable or not an object."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(path: str, state: dict) -> None:
    """Write atomically: a temp file in the same folder, then rename over the old one."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def detect(os_release_text: str, kernel_release: str, recorded: dict) -> dict:
    """The new state from what is installed now and what was recorded.

    First run (nothing recorded) only records; it never claims an update. A pending
    "reverify" stays set until `acknowledge`, even if the next look sees no change.
    """
    build = read_build_id(os_release_text)
    major = kernel_major(kernel_release)
    old_build = recorded.get("build_id")
    old_major = recorded.get("kernel_major")
    reasons = []
    if build and old_build and build != old_build:
        reasons.append(f"BUILD_ID {old_build} to {build}")
    if major is not None and isinstance(old_major, int) and major != old_major:
        reasons.append(f"kernel {old_major}.x to {major}.x")
    state = {
        "build_id": build or old_build,
        "kernel_major": major if major is not None else old_major,
        "reverify": bool(recorded.get("reverify")),
        "reason": recorded.get("reason", ""),
    }
    if reasons:
        state["reverify"] = True
        state["reason"] = ", ".join(reasons)
    return state


def acknowledge(state: dict) -> dict:
    """The state after the user re-verified: the flag clears, the last values stay."""
    return {**state, "reverify": False, "reason": ""}


def armoury_check(listdir: Callable[[str], Sequence[str]]) -> dict:
    """Read-only: do the ppt_* and fan attributes z13ctl writes still exist?

    {"state": "na"|"ok"|"needs", "missing": [...]}; "na" when the attributes folder is absent.
    """
    names = list(listdir(ARMOURY_DIR))
    if not names:
        return {"state": "na", "missing": []}
    missing = [a for a in PPT_ATTRS if a not in names]
    if not any("fan" in n for n in names):
        missing.append("fan attributes")
    return {"state": "needs" if missing else "ok", "missing": missing}

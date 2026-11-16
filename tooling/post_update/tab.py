"""Rows and status for the After SteamOS update tab (issues #70, #72).

Pure assembly: the Qt dialog and the Decky panel only draw what `status` returns, as JSON
from `python3 -m post_update --tab-json`. Checks are read-only, with one deliberate
exception: `status()` saves the last-seen state file (atomic) on the first run and when it
changes. `--ack` clears the re-verify flag.
"""

import json
import os
from collections.abc import Sequence

from post_update_state import row_state

from . import steamos_update as su
from .builtin import HostIO, OS_RELEASE

KERNEL_RELEASE = "/proc/sys/kernel/osrelease"
BOOT_ID = "/proc/sys/kernel/random/boot_id"
REBOOT_REL = os.path.join(".local", "state", "z13-helper", "reboot-pending.json")
_FLAGS = ("sudo", "network", "pacman", "reboot")


def mark_reboot(home: str, ids: Sequence) -> None:
    """After a run: remember which done tasks need a reboot, tied to this boot's id."""
    if not ids:
        return
    try:
        with open(BOOT_ID, encoding="utf-8") as f:
            boot = f.read().strip()
    except OSError:
        return
    path = os.path.join(home, REBOOT_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"boot_id": boot, "ids": sorted(ids)}, f)


def reboot_ids(home: str, io: HostIO) -> set:
    """Tasks done since this boot that still need a reboot (empty after the reboot)."""
    try:
        data = json.loads(io.read(os.path.join(home, REBOOT_REL)) or "")
        boot = (io.read(BOOT_ID) or "").strip()
    except ValueError:
        return set()
    if not isinstance(data, dict) or not boot or data.get("boot_id") != boot:
        return set()
    return {i for i in data.get("ids", []) if isinstance(i, str)}


def build_rows(recipes: Sequence, errors: Sequence = (), reboot: Sequence = ()) -> list:
    """One dict per recipe (state: fine|needs|info|na|unknown|reboot), then an error row per bad file.

    `unknown` is a check that could not run (it needs root): the reason is in `detail`.
    """
    rows = []
    for r in recipes:
        detail = ""
        try:
            state = row_state(r.check(), r.id in reboot, True)
        except Exception as e:  # noqa: BLE001 - a check that cannot run must not hide the others
            state, detail = "unknown", f"could not check: {e}"
        rows.append({"id": r.id, "title": r.title, "fixes": r.fixes, "state": state,
                     "default_on": r.default_on, "detail": detail,
                     "flags": [f for f in _FLAGS if getattr(r, f)]})
    for err in errors:
        name, _, msg = err.partition(": ")
        rows.append({"id": "", "title": name, "fixes": "", "state": "error", "default_on": False,
                     "detail": msg or err, "flags": []})
    return rows


def banner_text(rows: Sequence, reason: str = "") -> str:
    """"SteamOS updated: N tasks" (N = tasks needing attention or that could not be checked)."""
    n = sum(1 for r in rows if r["state"] in ("needs", "unknown", "error"))
    text = f"SteamOS updated: {n} task" + ("" if n == 1 else "s")
    return text + (f" ({reason})" if reason else "")


def status(home: str, io: HostIO, recipes: Sequence, errors: Sequence = (),
           ack: bool = False) -> dict:
    """{"updated", "banner", "reason", "armoury", "rows"}; saves the last-seen state."""
    path = su.state_path(home)
    recorded = su.load_state(path)
    state = su.detect(io.read(OS_RELEASE) or "", io.read(KERNEL_RELEASE) or "", recorded)
    if ack:
        state = su.acknowledge(state)
    if state != recorded:
        su.save_state(path, state)
    updated = bool(state["reverify"])
    reason = state["reason"]
    rows = build_rows(recipes, errors, reboot_ids(home, io))
    return {
        "updated": updated,
        "banner": banner_text(rows, reason) if updated else None,
        "reason": reason,
        # Listed only after an update: do the ppt_* attributes and the fan-curve hwmon node z13ctl uses still exist?
        "armoury": su.armoury_check(io.listdir, io.read) if updated else None,
        "rows": rows,
    }

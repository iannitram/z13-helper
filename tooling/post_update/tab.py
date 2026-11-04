"""Rows and status for the After SteamOS update tab (issues #70, #72).

Pure assembly: the Qt dialog and the Decky panel only draw what `status` returns, as JSON
from `python3 -m post_update --tab-json`. Checks are read-only; the one write is the saved
last-seen state file (atomic), and `--ack` clears the re-verify flag.
"""

from collections.abc import Sequence

from post_update_state import row_state

from . import steamos_update as su
from .builtin import HostIO, OS_RELEASE

KERNEL_RELEASE = "/proc/sys/kernel/osrelease"
_FLAGS = ("sudo", "network", "pacman", "reboot")


def build_rows(recipes: Sequence, errors: Sequence = ()) -> list:
    """One dict per recipe (state: fine|needs|info|na|reboot), then one error row per bad file."""
    rows = []
    for r in recipes:
        detail = ""
        try:
            state = row_state(r.check(), False, True)
        except Exception as e:  # noqa: BLE001 - a check that cannot run must not hide the others
            state, detail = "na", f"could not check: {e}"
        rows.append({"id": r.id, "title": r.title, "fixes": r.fixes, "state": state,
                     "default_on": r.default_on, "detail": detail,
                     "flags": [f for f in _FLAGS if getattr(r, f)]})
    for err in errors:
        name, _, msg = err.partition(": ")
        rows.append({"id": "", "title": name, "fixes": "", "state": "error", "default_on": False,
                     "detail": msg or err, "flags": []})
    return rows


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
    return {
        "updated": updated,
        "banner": (su.REVERIFY_TEXT + (f" ({reason})" if reason else "")) if updated else None,
        "reason": reason,
        # Listed only after an update: do the ppt_* and fan attributes z13ctl writes still exist?
        "armoury": su.armoury_check(io.listdir) if updated else None,
        "rows": build_rows(recipes, errors),
    }

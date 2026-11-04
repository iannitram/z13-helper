"""Thin, Qt-free helpers for the After SteamOS update tab (issue #70, #72).

The logic lives in tooling/post_update/ (`python3 -m post_update --tab-json`); the dialog
only builds that command, parses its JSON and turns rows into table cells.
"""

import json
import os
from typing import Optional

STATE_TEXT = {"needs": "Needs attention", "fine": "Fine", "info": "Info", "na": "Not applicable",
              "reboot": "Reboot pending", "error": "Error"}
EMPTY = {"updated": False, "banner": None, "reason": "", "armoury": None, "rows": []}


def tab_argv(repo: str, ack: bool = False) -> list:
    """The host command (argv, no shell) that prints the tab as JSON; `ack` clears the flag."""
    if not os.path.isabs(repo):
        raise ValueError("repo path must be absolute")
    return ["env", f"PYTHONPATH={repo}/tooling:{repo}/src", "python3", "-m", "post_update",
            "--tab-json"] + (["--ack"] if ack else [])


def parse_status(out: str) -> Optional[dict]:
    """The status dict from the command's output, or None when it is not the expected JSON."""
    start = out.find("{")
    if start < 0:
        return None
    try:
        data = json.loads(out[start:])
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("rows"), list):
        return None
    return {**EMPTY, **data}


def armoury_line(armoury: Optional[dict]) -> str:
    """One line for the attribute check (empty when it is not listed)."""
    if not armoury:
        return ""
    if armoury["state"] == "ok":
        return "asus-armoury: the ppt_* and fan attributes z13ctl writes are present"
    if armoury["state"] == "na":
        return "asus-armoury: attributes folder not found"
    return "asus-armoury: missing " + ", ".join(armoury["missing"])


def row_cells(row: dict) -> list:
    """[Task, State, What it fixes] for one table row (error rows show the message)."""
    state = STATE_TEXT.get(row["state"], row["state"])
    what = row["detail"] if row["state"] == "error" else row["fixes"]
    flags = f" ({', '.join(row['flags'])})" if row.get("flags") else ""
    return [row["title"] + ("" if row["default_on"] or row["state"] == "error" else " [off by default]"),
            state, what + flags]

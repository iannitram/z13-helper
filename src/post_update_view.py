"""Thin, Qt-free helpers for the After SteamOS update tab (issue #70, #72).

The logic lives in tooling/post_update/ (`python3 -m post_update --tab-json`); the dialog
only builds that command, parses its JSON and turns rows into table cells.
"""

import json
import os
from typing import Optional

STATE_TEXT = {"needs": "Needs attention", "fine": "Fine", "info": "Info", "na": "Not applicable",
              "reboot": "Reboot pending", "error": "Error", "unknown": "Could not check"}
EMPTY = {"updated": False, "banner": None, "reason": "", "armoury": None, "rows": []}


def tab_argv(repo: str, ack: bool = False) -> list:
    """The host command (argv, no shell) that prints the tab as JSON; `ack` clears the flag."""
    if not os.path.isabs(repo):
        raise ValueError("repo path must be absolute")
    return ["env", f"PYTHONPATH={repo}/tooling:{repo}/src", "python3", "-m", "post_update",
            "--tab-json"] + (["--ack"] if ack else [])


def repair_argv(repo: str, ids=None) -> list:
    """The host command that prints the repair plan as JSON (no ids: changes nothing), or runs
    exactly the confirmed step ids and prints the result as JSON."""
    if not os.path.isabs(repo):
        raise ValueError("repo path must be absolute")
    argv = ["env", f"PYTHONPATH={repo}/tooling:{repo}/src", "python3", "-m", "post_update",
            "repair", "--json"]
    if ids is not None:
        ids = list(ids)
        if not ids or not all(isinstance(i, str) and i and "," not in i and " " not in i for i in ids):
            raise ValueError("ids must be non-empty step ids")
        argv += ["--run", ",".join(ids)]
    return argv


def parse_json(out: str, key: str) -> Optional[dict]:
    """The JSON object in the command output when it has `key`, else None."""
    start = out.find("{")
    try:
        data = json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and key in data else None


def plan_prompt(plan: dict) -> str:
    """The text the confirm box shows: every step with its exact commands and how to undo it."""
    lines = []
    for s in plan.get("steps", []):
        kind = ("guided, " if s.get("guided") else "") + ("needs root (sudo -n)" if s.get("needs_root") else "runs as you")
        lines.append(f"{s['id']}: {s['description']} [{kind}]")
        lines += [f"    $ {c}" for c in s.get("commands", [])]
        lines.append(f"    undo: {s.get('undo') or 'nothing changes on disk'}")
    for check_id in plan.get("unplanned", []):
        lines.append(f"{check_id}: no repair known; reported only")
    return "\n".join(lines)


def run_summary(result: dict) -> str:
    """One line per step: its final status."""
    return "\n".join(f"{i}: {s}" for i, s in result.get("results", []))


def tray_text(status: Optional[dict]) -> Optional[str]:
    """The tray/tooltip line (the status banner) or None when no update was seen."""
    return (status or {}).get("banner") or None


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
        return "asus-armoury: the ppt_* attributes and the fan-curve node z13ctl uses are present"
    if armoury["state"] == "na":
        return "asus-armoury: attributes folder not found"
    return "asus-armoury: missing " + ", ".join(armoury["missing"])


def row_cells(row: dict) -> list:
    """[Task, State, What it fixes] for one table row (error rows show the message)."""
    state = STATE_TEXT.get(row["state"], row["state"])
    what = row["detail"] if row["state"] in ("error", "unknown") and row["detail"] else row["fixes"]
    flags = f" ({', '.join(row['flags'])})" if row.get("flags") else ""
    return [row["title"] + ("" if row["default_on"] or row["state"] == "error" else " [off by default]"),
            state, what + flags]

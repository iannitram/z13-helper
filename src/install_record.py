"""What install.sh recorded, and how the app's "Repair install" button runs it.

install.sh writes ~/.config/z13-helper/install.json at the end of every
real (non-dry) run: date, mode (install/repair), verify green/red, versions,
the checkout it ran from (`repo_path`) and its log. The Updates dialog shows
the last run and offers "Repair install" = `<repo_path>/install.sh --repair`
on the host. Stdlib only, no Qt, so it is testable anywhere.
"""

import json
import os
import re
import time

INSTALL_JSON = os.path.expanduser("~/.config/z13-helper/install.json")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def load(path=None):
    """The record as a dict, or None if missing / unreadable / not a dict."""
    try:
        with open(path or INSTALL_JSON) as f:
            rec = json.load(f)
        return rec if isinstance(rec, dict) else None
    except (OSError, ValueError):
        return None


def repo_path(rec):
    """The recorded checkout, if it is a usable absolute path (else None)."""
    p = (rec or {}).get("repo_path")
    if isinstance(p, str) and os.path.isabs(p) and "\n" not in p:
        return p
    return None


def last_run_line(rec):
    """'Last install/repair: 2026-10-11 21:40 (repair, green)' or a 'never' line."""
    if not rec or not rec.get("date"):
        return "Last install/repair: none recorded (run ./install.sh once from the repo)"
    date = str(rec["date"])
    try:
        date = time.strftime("%Y-%m-%d %H:%M", time.strptime(date[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        pass
    return f"Last install/repair: {date} ({rec.get('mode', '?')}, {rec.get('verify', '?')})"


def repair_argv(repo, in_flatpak):
    """argv for the repair. Inside the Flatpak it must run on the host (sudo,
    network, systemctl --user). The repo path is one argv element: no shell."""
    argv = ["bash", os.path.join(repo, "install.sh"), "--repair"]
    return (["flatpak-spawn", "--host"] + argv) if in_flatpak else argv


def plan_text(repo):
    return (f"This runs ./install.sh --repair from {repo} on the host:\n\n"
            "• puts back host scripts, user units and the /etc files listed in "
            "tooling/install_check.py (backups first, journaled)\n"
            "• re-runs `sudo z13ctl setup --perms-only`, restores the z13ctl daemon units, "
            "keeps the installed z13ctl version\n"
            "• reinstalls Decky Loader / the z13-helper plugin only if missing or different\n"
            "• rebuilds the Flatpak only if it differs from the repo, then verifies "
            "everything (green/red report)\n\n"
            "Needs passwordless sudo (there is no terminal to ask for a password); "
            "otherwise run ./install.sh --repair in Konsole.\n\n"
            "If this app was rebuilt or its unit changed, the repair restarts the app "
            "near the end: this window then closes, the repair keeps running, and the "
            "full report stays in the log path it prints "
            "(~/.local/state/z13-helper/install-<date>.log).")


def clean(line):
    """A log line without terminal colour codes."""
    return _ANSI.sub("", line)


def verdict(lines, rc):
    """(green: bool, text) from install.sh's last lines and exit status."""
    for line in reversed(lines):
        t = clean(line).strip()
        if t == "All green.":
            return True, "All green."
        if t.startswith("Not all green"):
            return False, t
    return (rc == 0), ("Finished (exit 0)" if rc == 0 else f"Failed (exit {rc})")

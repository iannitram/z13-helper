"""Suspend/resume diagnostic (#27): read-only, reports health, never repairs.

Reads go through the same injectable `read` seam as `system_inventory` (a str target is a
file, a tuple is an argv). It returns `CheckResult` rows; `repair_plan.plan_for` gives them
no step. The journal is the kernel log of the current boot.
"""

import re

from post_update_checks import CheckResult

MEM_SLEEP = "/sys/power/mem_sleep"
JOURNAL = ("journalctl", "-b", "-k", "--no-pager")   # the whole boot: no window to cut a cycle
_ENTRY = re.compile(r"PM: suspend entry")
_EXIT = re.compile(r"PM: suspend exit")
_PROBLEM = re.compile(
    r"Freezing of tasks failed|failed to suspend|PM: Some devices failed to suspend"
    r"|PM: resume of devices complete after \d{4,}\.|amdgpu.*(?:timeout|failed to resume|ring .* timeout)"
    r"|ACPI: PM: .*failed", re.I)


def _read(read, target):
    try:
        value = read(target)
    except Exception:
        return None
    return value if isinstance(value, str) else None


def diagnose(read) -> list:
    """Return three rows: sleep mode, entry/exit balance this boot, suspend error lines."""
    mode_text = _read(read, MEM_SLEEP)
    selected = re.search(r"\[([^\]]+)\]", mode_text or "")
    rows = [CheckResult("suspend_mode", bool(selected),
                        f"sleep mode: {selected.group(1)}" if selected
                        else "sleep mode could not be read")]
    log = _read(read, JOURNAL)
    if log is None:
        rows.append(CheckResult("suspend_cycles", False, "kernel log could not be read"))
        rows.append(CheckResult("suspend_errors", False, "kernel log could not be read"))
        return rows
    lines = log.splitlines()
    entries = exits = 0
    open_cycle, balanced = False, True
    for l in lines:
        if _ENTRY.search(l):
            entries += 1
            balanced = balanced and not open_cycle   # an entry while one is open: its exit is missing
            open_cycle = True
        elif _EXIT.search(l):
            exits += 1
            if open_cycle:
                open_cycle = False
            elif entries:
                balanced = False                      # an exit with no entry after a cycle began
            # else: the log starts mid-cycle (entry rotated away); not a missing resume
    balanced = balanced and not open_cycle
    rows.append(CheckResult("suspend_cycles", balanced,
                            f"{entries} suspend(s), {exits} resume(s) this boot"
                            + ("" if balanced else ": a resume is missing")))
    problems = [l for l in lines if _PROBLEM.search(l)]
    rows.append(CheckResult("suspend_errors", not problems,
                            "no suspend/resume errors" if not problems
                            else f"{len(problems)} suspend/resume error line(s)"))
    return rows

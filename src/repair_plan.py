"""Reversible repair plan for post-update failures (#27).

`plan_for(failures, io)` builds one RepairStep per failing check_id that has a known repair.
Building a plan touches nothing. Each step has `apply`, `verify` and `rollback`; `apply`
backs a file up (through `io`) before replacing it and writes what it changed, and how to
undo it, into `plan.record[check_id]`. Failures with no repair here (version reads, firmware,
Mesa, the suspend/resume diagnostic) are listed in `plan.record["unplanned"]` and never
get a step: those are reported, not repaired.

Failures are `post_update_checks.CheckResult` rows or dicts with "check_id" and "detail";
passing rows are ignored.
"""

import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from post_update.builtin import AMDGPU_ARGS, GRUB_FILE
from post_update.cmdline import add_grub_args, missing_args

_GRUB_VALUE = re.compile(r'^[ \t]*GRUB_CMDLINE_LINUX_DEFAULT="((?:[^"\\]|\\.)*)"', re.MULTILINE)
_USER_UNIT = re.compile(r"unit:user:([A-Za-z0-9@:._-]+)\Z")


@dataclass(frozen=True)
class RepairIO:
    """What repairs need from the machine. Tests pass fakes; nothing here shells out itself."""
    read: Callable[[str], "str | None"]           # file text, None when unreadable
    write: Callable[[str, str], None]
    run: Callable[[Sequence[str]], tuple]         # argv -> (rc, output); no shell
    backup_dir: str                               # backups are written under here


@dataclass(frozen=True)
class RepairStep:
    check_id: str
    description: str
    apply: Callable[[], None]
    verify: Callable[[], bool]
    rollback: Callable[[], None]


@dataclass
class RepairPlan:
    steps: list
    record: dict = field(default_factory=dict)

    def run(self) -> list:
        """Apply each step, verify it, roll back a step that fails. Returns [(check_id, status)].

        A step that raises or fails verification is rolled back; later steps still run.
        """
        results = []
        for step in self.steps:
            entry = self.record[step.check_id]
            try:
                step.apply()
                ok = step.verify()
            except Exception as exc:  # noqa: BLE001 - recorded, then undone
                entry["error"] = str(exc)
                ok = False
            if ok:
                entry["status"] = "applied"
            else:
                try:
                    step.rollback()
                    entry["status"] = "rolled back"
                except Exception as exc:  # noqa: BLE001
                    entry["status"] = "rollback failed"
                    entry["rollback_error"] = str(exc)
            results.append((step.check_id, entry["status"]))
        return results


def _backup_path(io: RepairIO, path: str) -> str:
    return os.path.join(io.backup_dir, path.lstrip("/"))


def _kernel_args_step(io: RepairIO, entry: dict) -> RepairStep:
    backup = _backup_path(io, GRUB_FILE)
    entry.update(changed=[GRUB_FILE], backup={GRUB_FILE: backup},
                 undo=f"copy {backup} back to {GRUB_FILE}, then run update-grub")

    def apply():
        text = io.read(GRUB_FILE)
        if text is None:
            raise RuntimeError(f"cannot read {GRUB_FILE}")
        lines = text.split("\n")
        idx = next((i for i, l in enumerate(lines) if _GRUB_VALUE.match(l)), None)
        if idx is None:
            raise RuntimeError(f"no GRUB_CMDLINE_LINUX_DEFAULT line in {GRUB_FILE}")
        io.write(backup, text)               # backup before replace
        entry["status"] = "backed up"
        lines[idx] = add_grub_args(lines[idx], AMDGPU_ARGS)
        io.write(GRUB_FILE, "\n".join(lines))
        entry["status"] = "changed"
        rc, out = io.run(["update-grub"])
        if rc != 0:
            raise RuntimeError(f"update-grub failed: {out}")

    def verify():
        text = io.read(GRUB_FILE) or ""
        m = _GRUB_VALUE.search(text)
        return bool(m) and not missing_args(m.group(1), AMDGPU_ARGS)

    def rollback():
        old = io.read(backup)
        if old is None:
            raise RuntimeError(f"backup {backup} is missing; restore by hand")
        io.write(GRUB_FILE, old)
        rc, out = io.run(["update-grub"])
        if rc != 0:
            raise RuntimeError(f"update-grub failed: {out}")

    return RepairStep("kernel_args", "add the amdgpu kernel arguments to " + GRUB_FILE,
                      apply, verify, rollback)


def _user_unit_step(io: RepairIO, check_id: str, unit: str, entry: dict) -> RepairStep:
    entry.update(changed=[f"user unit {unit}"], backup={},
                 undo=f"systemctl --user disable --now {unit} (only if it was not enabled before)")
    prior = {}

    def apply():
        rc, out = io.run(["systemctl", "--user", "is-enabled", unit])
        prior["enabled"] = out.strip() == "enabled"
        entry["was_enabled"] = prior["enabled"]
        rc, out = io.run(["systemctl", "--user", "enable", "--now", unit])
        if rc != 0:
            raise RuntimeError(f"enable {unit} failed: {out}")
        entry["status"] = "changed"

    def verify():
        return io.run(["systemctl", "--user", "is-enabled", unit])[1].strip() == "enabled"

    def rollback():
        if prior.get("enabled", True):
            return  # it was enabled before: nothing of ours to undo
        rc, out = io.run(["systemctl", "--user", "disable", "--now", unit])
        if rc != 0:
            raise RuntimeError(f"disable {unit} failed: {out}")

    return RepairStep(check_id, f"enable {unit}", apply, verify, rollback)


def plan_for(failures, io: "RepairIO | None" = None) -> RepairPlan:
    """Build a plan for the failing checks. Without `io` no step can be built, only listed."""
    steps, record, unplanned = [], {}, []
    for item in failures or ():
        check_id = item["check_id"] if isinstance(item, dict) else item.check_id
        passed = False if isinstance(item, dict) else item.passed
        if passed or check_id in record:
            continue
        entry = {"status": "planned", "changed": [], "backup": {}, "undo": ""}
        step = None
        if io is not None:
            if check_id == "kernel_args":
                step = _kernel_args_step(io, entry)
            else:
                m = _USER_UNIT.fullmatch(check_id)
                if m:
                    step = _user_unit_step(io, check_id, m.group(1), entry)
        if step is None:
            if check_id not in unplanned:
                unplanned.append(check_id)
            continue
        record[check_id] = entry
        steps.append(step)
    record["unplanned"] = unplanned
    return RepairPlan(steps=steps, record=record)

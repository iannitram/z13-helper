"""Reversible repair plan for post-update failures (#27).

`plan_for(failures, io)` builds one RepairStep for every failing check_id the inventory
checks can report: version reads, firmware, Mesa/Vulkan, the amdgpu kernel arguments, user
and system units, and the ppt/udev permissions. Only an id the checks never produce is listed
in `plan.record["unplanned"]`. Building a plan touches nothing.

Every step has `apply`, `verify` and `rollback`, the exact `commands` it will run, and a
rollback note. `apply` backs a file up (through `io`) before replacing it and writes what it
changed, and how to undo it, into `plan.record[check_id]`.

Steps are of two kinds:
  - plain steps (user units, user-level) run as the user;
  - root steps (`needs_root`) and guided steps (`guided`) are shown to the user and run only
    when `RepairPlan.run(confirm)` gets a yes for that step. Root work goes through
    `io.run_root` / `io.write_root` (the existing `sudo -n` path); nothing escalates silently.
A guided step with `changes=False` (version reads that no command can repair) only runs its
read-only diagnostic and says what to do by hand; it never writes.

Failures are `post_update_checks.CheckResult` rows or dicts with "check_id" and "detail";
passing rows are ignored. The suspend/resume diagnostic is separate and read-only.
"""

import os
import re
import shlex
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from post_update.builtin import AMDGPU_ARGS, GRUB_FILE
from post_update.cmdline import add_grub_args, missing_args
from post_update_checks import expected_for, permission_problem
from post_update_state import read_build_id
from system_inventory import BIOS_VERSION, KERNEL_RELEASE, OS_RELEASE, PERMISSION_PATHS

_GRUB_VALUE = re.compile(r'^[ \t]*GRUB_CMDLINE_LINUX_DEFAULT="((?:[^"\\]|\\.)*)"', re.MULTILINE)
_UNIT = re.compile(r"unit:(user|system):([A-Za-z0-9@:._-]+)\Z")
_PERMISSION = "permission:"
# Package names behind the inventory checks that read a package version.
_PACKAGES = {"firmware": ("linux-firmware",), "mesa_vulkan": ("mesa", "vulkan-radeon")}


@dataclass(frozen=True)
class RepairIO:
    """What repairs need from the machine. Tests pass fakes; nothing here shells out itself."""
    read: Callable[[str], "str | None"]           # file text, None when unreadable
    write: Callable[[str, str], None]             # user-level write (backups, records)
    run: Callable[[Sequence[str]], tuple]         # argv -> (rc, output); no shell, as the user
    backup_dir: str                               # backups are written under here
    run_root: "Callable[[Sequence[str]], tuple] | None" = None    # argv as root (sudo -n)
    write_root: "Callable[[str, str], None] | None" = None        # write a root-owned file
    readonly_tool: str = "steamos-readonly"       # or holo-readonly; toggled around root writes


@dataclass(frozen=True)
class RepairStep:
    check_id: str
    description: str
    apply: Callable[[], None]
    verify: Callable[[], bool]
    rollback: Callable[[], None]
    needs_root: bool = False          # the user must confirm before it runs
    guided: bool = False              # shown with its exact commands; confirmed before it runs
    changes: bool = True              # False: read-only diagnostic, nothing to undo
    commands: tuple = ()              # exact command text, shown before anything runs
    rollback_note: str = ""           # how to undo it by hand


@dataclass
class RepairPlan:
    steps: list
    record: dict = field(default_factory=dict)

    def describe(self) -> list:
        """Lines the UI shows before anything runs: what, which commands, root, how to undo."""
        lines = []
        for step in self.steps:
            kind = ("guided, " if step.guided else "") + ("needs root (sudo -n)" if step.needs_root
                                                          else "runs as you")
            lines.append(f"{step.check_id}: {step.description} [{kind}]")
            lines += [f"    $ {c}" for c in step.commands]
            lines.append(f"    undo: {step.rollback_note or 'nothing changes on disk'}")
        for check_id in self.record.get("unplanned", []):
            lines.append(f"{check_id}: no repair known; reported only")
        return lines

    def needs_confirmation(self, step: RepairStep) -> bool:
        return step.needs_root or step.guided

    def run(self, confirm: "Callable[[RepairStep], bool] | None" = None) -> list:
        """Apply each step, verify it, roll back a step that fails. Returns [(check_id, status)].

        A root or guided step runs only when `confirm(step)` is true; without `confirm` it
        is skipped as "not confirmed" and nothing runs. A step that fails before it changed
        anything is "failed (nothing changed)" and is not rolled back. A step that raises or
        fails verification after it changed something is rolled back; later steps still run.
        """
        results = []
        for step in self.steps:
            entry = self.record[step.check_id]
            if self.needs_confirmation(step) and not (confirm and confirm(step)):
                entry["status"] = "not confirmed"
                results.append((step.check_id, entry["status"]))
                continue
            try:
                step.apply()
                ok = step.verify()
            except Exception as exc:  # noqa: BLE001 - recorded, then undone
                entry["error"] = str(exc)
                ok = False
            if ok:
                entry["status"] = "applied"
            elif not step.changes:
                entry["status"] = "not fixed"
            elif entry["status"] == "planned":
                entry["status"] = "failed (nothing changed)"
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


def _text(argv, root=False) -> str:
    return shlex.join((["sudo", "-n"] if root else []) + list(argv))


def _root(io: RepairIO, argv) -> tuple:
    return (io.run_root or io.run)(list(argv))


def _write_root(io: RepairIO, path: str, text: str) -> None:
    (io.write_root or io.write)(path, text)


def _writable(io: RepairIO, body):
    """Run `body` with read-only off; read-only is switched back on whatever happens."""
    rc, out = _root(io, [io.readonly_tool, "disable"])
    if rc != 0:
        raise RuntimeError(f"{io.readonly_tool} disable failed: {out}")
    try:
        return body()
    finally:
        rc, out = _root(io, [io.readonly_tool, "enable"])
        if rc != 0:
            raise RuntimeError(f"{io.readonly_tool} enable failed: {out}")


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
        lines[idx] = add_grub_args(lines[idx], AMDGPU_ARGS)

        def change():
            _write_root(io, GRUB_FILE, "\n".join(lines))
            entry["status"] = "changed"   # only now is there something to roll back
            rc, out = _root(io, ["update-grub"])
            if rc != 0:
                raise RuntimeError(f"update-grub failed: {out}")
        _writable(io, change)

    def verify():
        text = io.read(GRUB_FILE) or ""
        m = _GRUB_VALUE.search(text)
        return bool(m) and not missing_args(m.group(1), AMDGPU_ARGS)

    def rollback():
        old = io.read(backup)
        if old is None:
            raise RuntimeError(f"backup {backup} is missing; restore by hand")

        def undo():
            _write_root(io, GRUB_FILE, old)
            rc, out = _root(io, ["update-grub"])
            if rc != 0:
                raise RuntimeError(f"update-grub failed: {out}")
        _writable(io, undo)

    return RepairStep(
        "kernel_args", "add the amdgpu kernel arguments to " + GRUB_FILE, apply, verify, rollback,
        needs_root=True,
        commands=(f"write {GRUB_FILE} (backup first: {backup})", _text(["update-grub"], True)),
        rollback_note=entry["undo"])


def _unit_step(io: RepairIO, check_id: str, scope: str, unit: str, entry: dict) -> RepairStep:
    base = ["systemctl"] + (["--user"] if scope == "user" else [])
    run = io.run if scope == "user" else (lambda argv: _root(io, argv))
    entry.update(changed=[f"{scope} unit {unit}"], backup={},
                 undo=f"{_text(base + ['disable', '--now', unit], scope == 'system')} "
                      "(only if it was not enabled before)")
    prior = {}

    def apply():
        rc, out = io.run(base + ["is-enabled", unit])
        prior["enabled"] = out.strip() == "enabled"
        entry["was_enabled"] = prior["enabled"]
        rc, out = run(base + ["enable", "--now", unit])
        if rc != 0:
            raise RuntimeError(f"enable {unit} failed: {out}")
        entry["status"] = "changed"

    def verify():
        return io.run(base + ["is-enabled", unit])[1].strip() == "enabled"

    def rollback():
        if prior.get("enabled", True):
            return  # it was enabled before: nothing of ours to undo
        rc, out = run(base + ["disable", "--now", unit])
        if rc != 0:
            raise RuntimeError(f"disable {unit} failed: {out}")

    return RepairStep(check_id, f"enable {scope} unit {unit}", apply, verify, rollback,
                      needs_root=scope == "system", guided=scope == "system",
                      commands=(_text(base + ["enable", "--now", unit], scope == "system"),),
                      rollback_note=entry["undo"])


def _package_step(io: RepairIO, check_id: str, packages: tuple, entry: dict) -> RepairStep:
    """Guided: install the package if pacman cannot find it; remove it again on rollback.

    `--needed` never upgrades one that is installed (no partial upgrade). SteamOS replaces
    these files on the next image update, so this is a stop-gap, said so in the note.
    """
    install = ["pacman", "-S", "--needed", "--noconfirm", *packages]
    remove = ["pacman", "-R", "--noconfirm"]
    entry.update(changed=[f"packages {' '.join(packages)}"], backup={},
                 undo=f"{_text(remove + list(packages), True)} for packages that were not installed "
                      "before; the next SteamOS update replaces them anyway")
    before = {}

    def apply():
        for pkg in packages:
            before[pkg] = io.run(["pacman", "-Q", pkg])[0] == 0
        if all(before.values()):
            raise RuntimeError(f"{' '.join(packages)} already installed; reinstalling is not safe "
                               "here (no partial upgrades): run steamos-update or ./install.sh --repair")

        def change():
            rc, out = _root(io, install)
            if rc != 0:
                raise RuntimeError(f"pacman -S failed: {out}")
            entry["status"] = "changed"
        _writable(io, change)

    def verify():
        return all(io.run(["pacman", "-Q", pkg])[0] == 0 for pkg in packages)

    def rollback():
        added = [p for p in packages if not before.get(p, True)]
        if not added:
            return

        def undo():
            rc, out = _root(io, remove + added)
            if rc != 0:
                raise RuntimeError(f"pacman -R failed: {out}")
        _writable(io, undo)

    return RepairStep(
        check_id, f"install {' '.join(packages)} (read-only is switched off, then on again)",
        apply, verify, rollback, needs_root=True, guided=True,
        commands=(_text([io.readonly_tool, "disable"], True), _text(install, True),
                  _text([io.readonly_tool, "enable"], True)),
        rollback_note=entry["undo"])


def _permission_step(io: RepairIO, check_id: str, path: str, entry: dict) -> RepairStep:
    """chmod/chown to the mode and owner the udev rule expects, after recording the current ones."""
    stat = ["stat", "-c", "%a %U:%G", path]
    mode, owner = expected_for(path)
    fix = [["chmod", mode, path], ["chown", owner, path]]
    entry.update(changed=[path], backup={},
                 undo=f"chmod/chown {path} back to the mode and owner recorded as 'before'")
    before = {}
    # A file under /etc is on the root filesystem, read-only on SteamOS: same window as the
    # grub and package steps. A sysfs node (the ppt file) is not, so it needs no window.
    window = (lambda body: _writable(io, body)) if path.startswith("/etc/") else (lambda body: body())

    def apply():
        rc, out = io.run(stat)
        if rc != 0 or len(out.split()) != 2:
            raise RuntimeError(f"cannot stat {path}; install it with ./install.sh --repair")
        before["value"] = out.strip()
        entry["before"] = before["value"]
        entry["undo"] = (f"chmod {before['value'].split()[0]} {path} and "
                         f"chown {before['value'].split()[1]} {path}")

        def change():
            for argv in fix:
                rc, out = _root(io, argv)
                if rc != 0:
                    raise RuntimeError(f"{' '.join(argv)} failed: {out}")
                entry["status"] = "changed"   # from the first success there is something to undo
        window(change)

    def verify():
        rc, out = io.run(stat)
        return rc == 0 and permission_problem(path, out.strip()) is None

    def rollback():
        mode, owner = before["value"].split()

        def undo():
            for argv in (["chmod", mode, path], ["chown", owner, path]):
                rc, out = _root(io, argv)
                if rc != 0:
                    raise RuntimeError(f"{' '.join(argv)} failed: {out}")
        window(undo)

    return RepairStep(check_id, f"set {path} to mode {mode}, owner {owner}", apply, verify, rollback,
                      needs_root=True, commands=tuple(_text(a, True) for a in fix),
                      rollback_note="chmod and chown it back to the 'before' value in the record")


def _diagnostic_step(io: RepairIO, check_id: str, command: Sequence[str], probe: Callable,
                     hint: str, entry: dict) -> RepairStep:
    """Guided, read-only: no command repairs this, so run the exact read and say what to do."""
    entry.update(changed=[], backup={}, undo="nothing was changed", hint=hint)

    def apply():
        rc, out = io.run(command)
        entry["output"] = out.strip()[:500]

    return RepairStep(check_id, f"look at it: {hint}", apply, probe, lambda: None, guided=True,
                      changes=False, commands=(_text(command),),
                      rollback_note="nothing to undo: this only reads")


def _diagnostic(io: RepairIO, check_id: str, entry: dict):
    if check_id == "steamos_build":
        return _diagnostic_step(io, check_id, ["cat", OS_RELEASE],
                                lambda: bool(read_build_id(io.read(OS_RELEASE) or "")),
                                "SteamOS build unreadable: check /etc/os-release is intact; "
                                "if it is not, reinstall the image", entry)
    if check_id == "kernel":
        return _diagnostic_step(io, check_id, ["uname", "-r"],
                                lambda: bool((io.read(KERNEL_RELEASE) or "").strip()),
                                "kernel release unreadable: /proc is not available here", entry)
    if check_id == "bios_ec":
        return _diagnostic_step(io, check_id, ["cat", BIOS_VERSION],
                                lambda: bool((io.read(BIOS_VERSION) or "").strip()),
                                "BIOS/EC unreadable: update the BIOS from ASUS, the app cannot", entry)
    if check_id == "backend_version":
        return _diagnostic_step(io, check_id, ["z13ctl", "--version"],
                                lambda: io.run(["z13ctl", "--version"])[0] == 0,
                                "z13ctl does not answer: run ./install.sh --repair", entry)
    return None


def _step_for(io: RepairIO, check_id: str, entry: dict) -> "RepairStep | None":
    if check_id == "kernel_args":
        return _kernel_args_step(io, entry)
    if check_id in _PACKAGES:
        return _package_step(io, check_id, _PACKAGES[check_id], entry)
    m = _UNIT.fullmatch(check_id)
    if m:
        return _unit_step(io, check_id, m.group(1), m.group(2), entry)
    if check_id.startswith(_PERMISSION) and check_id[len(_PERMISSION):] in PERMISSION_PATHS:
        return _permission_step(io, check_id, check_id[len(_PERMISSION):], entry)
    return _diagnostic(io, check_id, entry)


def plan_for(failures, io: "RepairIO | None" = None) -> RepairPlan:
    """Build a plan for the failing checks. Without `io` no step can be built, only listed."""
    steps, record, unplanned = [], {}, []
    for item in failures or ():
        check_id = item["check_id"] if isinstance(item, dict) else item.check_id
        passed = False if isinstance(item, dict) else item.passed
        if passed or check_id in record:
            continue
        entry = {"status": "planned", "changed": [], "backup": {}, "undo": ""}
        step = _step_for(io, check_id, entry) if io is not None else None
        if step is None:
            if check_id not in unplanned:
                unplanned.append(check_id)
            continue
        record[check_id] = entry
        steps.append(step)
    record["unplanned"] = unplanned
    return RepairPlan(steps=steps, record=record)

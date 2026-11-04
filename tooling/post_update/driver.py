"""Real host wiring and entry point for the post-update runner (#67, #68).

`python3 -m post_update ARGS` (with tooling/ and src/ on the path, as install.sh sets it)
lists the tasks, runs the picked ones through `runner.run_plan`, or prints the pending
ones; `repair`, `checks`, `bundle` and `suspend` are the system-repair subcommands
(repair.py). Every command is an argv list, privileged ones through `sudo -n`. Checks never
write. Every applied task leaves an entry in `rollback-record.json` next to its backups.
"""

import json
import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Sequence

from .builtin import HostIO, builtin_recipes
from .builtin_4_6 import Sys, builtin_recipes_4_6
from .cli import parse_args
from .local_recipes import load_local_recipes
from .packs import pack_recipes
from . import repair
from .recipe import order_tasks, plan_text, with_keyring
from .runner import Env, run_plan
from .tab import mark_reboot, status as tab_status

_log = logging.getLogger(__name__)

BACKUP_ROOT = os.path.join(".local", "state", "z13-helper", "post-update-backups")
RECORD_NAME = "rollback-record.json"
# Commands that must run as root; anything else runs as the current user.
_ROOT_CMDS = {"update-grub", "pacman-key", "pacman", "ldconfig", "mkdir", "bsdtar"}


def _host():
    import host  # src/host.py: the one place host commands go through
    return host


def _needs_root(argv: Sequence[str]) -> bool:
    name = os.path.basename(argv[0])
    if name == "pacman" and len(argv) > 1 and argv[1].startswith("-Q"):
        return False  # querying the package database is read-only
    return name in _ROOT_CMDS


def _run(argv: Sequence[str], timeout: int = 600, escalate: bool = True) -> tuple:
    argv = list(argv)
    if _needs_root(argv) and os.geteuid() != 0:
        if not escalate:
            raise PermissionError(f"{argv[0]} needs root; not run when only listing")
        argv = ["sudo", "-n", *argv]
    res = _host().run(argv, timeout=timeout)
    return res.rc, res.out


def _run_plain(argv: Sequence[str]) -> tuple:
    """Run as the current user with no sudo at all (local recipe checks)."""
    res = _host().run(list(argv), timeout=60)
    return res.rc, res.out


def _read(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def _write(path: str, text: str) -> None:
    argv = ["tee", path] if os.geteuid() == 0 else ["sudo", "-n", "tee", path]
    res = _host().run(argv, input=text, timeout=60)
    if res.rc != 0:
        raise RuntimeError(f"could not write {path}: {res.out}")


def _listdir(path: str) -> list:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def make_hostio(escalate: bool = True) -> HostIO:
    """escalate=False: a command that needs root raises instead of calling sudo (listing only)."""
    return HostIO(read=_read, write=_write, exists=os.path.exists, listdir=_listdir,
                  run=lambda argv: _run(argv, escalate=escalate))


def make_sys(home: str, escalate: bool = True) -> Sys:
    def copy_tree(src: str, dest: str) -> None:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copytree(src, dest)

    def write(path: str, text: str) -> None:
        _write(path, text)

    return Sys(home=home, listdir=lambda p: _listdir(p) or None, exists=os.path.exists,
               read=_read, run=lambda argv: _run(argv, escalate=escalate)[0], write=write, copy_tree=copy_tree)


def readonly_cmd(tool_name: Callable[[str], str] | None = None) -> str:
    """The read-only toggle to use: holo-readonly when installed, else steamos-readonly."""
    return (tool_name or _host().holo_or_steamos)("readonly")


def _priv(argv: Sequence[str]) -> list:
    """argv with an explicit sudo -n unless already root (the one sudo path; never silent)."""
    return list(argv) if os.geteuid() == 0 else ["sudo", "-n", *argv]


def _safe_target(path: str) -> bool:
    """Only an absolute path at least two levels deep may be removed or replaced by a rollback."""
    return os.path.isabs(path) and ".." not in path.split("/") and len(path.strip("/").split("/")) >= 2


def make_env(home: str, run: Callable = _run, tool_name: Callable[[str], str] | None = None,
             stamp: str | None = None) -> Env:
    """Env for run_plan: backups under ~/.local/state/z13-helper/post-update-backups/<stamp>/.

    `rollback-record.json` in the same folder gets one entry per task: what was backed up
    (and where), what was created, how to undo it and how the run went.
    """
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    dest_root = os.path.join(home, BACKUP_ROOT, stamp)
    tool = readonly_cmd(tool_name)

    def backup(path: str) -> "str | None":
        if not os.path.lexists(path):
            return None  # nothing to lose; the record says "absent before"
        dest = os.path.join(dest_root, path.lstrip("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        try:
            if os.path.isdir(path):
                raise PermissionError("folder: copied as root to keep owners and modes")
            shutil.copy2(path, dest)
        except PermissionError:
            rc, out = run(_priv(["cp", "-a", "--", path, dest]))
            if rc != 0:  # the run then stops before replacing it
                raise RuntimeError(f"could not back up {path}: {out}") from None
        _log.info("backed up %s to %s", path, dest)
        return dest

    def restore(path: str, backup_path: "str | None") -> None:
        if not _safe_target(path):
            raise RuntimeError(f"refusing to touch {path}")
        if backup_path is None:
            steps = [["rm", "-rf", "--", path]]
        elif os.path.isdir(backup_path):
            steps = [["rm", "-rf", "--", path], ["cp", "-a", "--", backup_path, path]]
        else:
            steps = [["cp", "-a", "--", backup_path, path]]
        for argv in steps:
            rc, out = run(_priv(argv))
            if rc != 0:
                raise RuntimeError(f"{' '.join(argv)} failed: {out}")
        _log.info("restored %s", path)

    def record(entry: dict) -> None:
        path = os.path.join(dest_root, RECORD_NAME)
        try:
            with open(path, encoding="utf-8") as f:
                entries = json.load(f)
        except (OSError, ValueError):
            entries = []
        entries.append(entry)
        os.makedirs(dest_root, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp, path)

    def toggle(verb: str) -> None:
        _log.info("read-only: %s through %s", verb, tool)
        rc, out = run(["sudo", "-n", tool, verb])
        if rc != 0:
            raise RuntimeError(f"{tool} {verb} failed: {out}")

    return Env(backup=backup, readonly_disable=lambda: toggle("disable"),
               readonly_enable=lambda: toggle("enable"), restore=restore, record=record,
               readonly_tool=tool)


def make_repair_io(home: str, stamp: str | None = None,
                   tool_name: Callable[[str], str] | None = None):
    """RepairIO for the real machine: root work only through the explicit `sudo -n` path."""
    from repair_plan import RepairIO  # src/ is on the path, as install.sh sets it
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(home, BACKUP_ROOT, stamp, "repair")

    def run(argv):
        res = _host().run(list(argv), timeout=300)
        return res.rc, res.out

    def write(path: str, text: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    return RepairIO(read=_read, write=write, run=run, backup_dir=dest,
                    run_root=lambda argv: run(_priv(argv)), write_root=_write,
                    readonly_tool=readonly_cmd(tool_name))


def save_repair_record(io, record: dict) -> None:
    """The rollback record of a repair run, next to its backups."""
    io.write(os.path.join(os.path.dirname(io.backup_dir), "repair-record.json"),
             json.dumps(record, indent=2))


def subcommand(argv: Sequence[str], input_fn=input, out=print, io=None, read=None) -> "int | None":
    """repair | checks | bundle | suspend; None when argv is not one of them."""
    if not argv or argv[0] not in ("repair", "checks", "bundle", "suspend"):
        return None
    import system_inventory
    read = read or system_inventory.host_read
    name, rest = argv[0], list(argv[1:])
    if name == "checks":
        return repair.checks_main(read, out)
    if name == "suspend":
        return repair.suspend_main(read, out)
    if name == "bundle":
        def write_file(path: str, text: str) -> None:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
        return repair.bundle_main(rest, read, write_file, input_fn, out)
    io = io or make_repair_io(os.path.expanduser("~"))
    return repair.repair_main(rest, read, io, lambda rec: save_repair_record(io, rec), input_fn, out)


def collect(home: str, escalate: bool = True) -> tuple:
    """(recipes, errors): built-ins 1-6, the optional packs, then the local recipes."""
    io = make_hostio(escalate)
    recipes = builtin_recipes(io) + builtin_recipes_4_6(make_sys(home, escalate)) + pack_recipes(io)
    local, errors = load_local_recipes(home, lambda argv: _run(argv, escalate=escalate),
                                       is_root=os.geteuid() == 0, taken=[r.id for r in recipes],
                                       run_check=_run_plain)
    return recipes + local, errors


def all_recipes(home: str, escalate: bool = True) -> list:
    return collect(home, escalate)[0]


def classify(recipes: Sequence) -> tuple:
    """(needs, unchecked): recipes whose read-only check says "needs", and those whose check
    could not run (it needs root while only listing). An unchecked task is never dropped."""
    needs, unchecked = [], []
    for r in recipes:
        try:
            if r.check() == "needs":
                needs.append(r)
        except Exception as exc:  # noqa: BLE001 - a check that needs root cannot say; do not guess
            _log.info("could not check %s without root: %s", r.id, exc)
            unchecked.append(r)
    return needs, unchecked


def pending(recipes: Sequence) -> list:
    """Tasks to offer: those that need work, then those that could not be checked."""
    needs, unchecked = classify(recipes)
    return needs + unchecked


def _pick(candidates: Sequence, input_fn=input) -> list:
    for i, r in enumerate(candidates, 1):
        print(f"{i}. {r.id}: {r.title}")
    answer = input_fn("Run which? (numbers or ids, comma separated; empty = none): ")
    wanted = [a.strip() for a in answer.split(",") if a.strip()]
    chosen = []
    for w in wanted:
        match = candidates[int(w) - 1] if w.isdigit() and 0 < int(w) <= len(candidates) else \
            next((r for r in candidates if r.id == w), None)
        if match is not None and match not in chosen:
            chosen.append(match)
    return chosen


def main(argv: Sequence[str], recipes: Sequence | None = None, env: Env | None = None,
         input_fn=input, dry_run: bool = False) -> int:
    """0 = all picked tasks done (or nothing to do), 1 = a task failed, 2 = bad arguments.

    `--list-pending` prints the ids that need work and changes nothing. `--tab-json` prints the
    After SteamOS update tab as JSON (saving the last-seen BUILD_ID); with `--ack` it also clears
    the re-verify flag.
    """
    argv = list(argv)
    sub = subcommand(argv, input_fn)
    if sub is not None:
        return sub
    list_pending = "--list-pending" in argv
    try:
        args = parse_args(argv)
    except ValueError as e:
        print(f"post-update: {e}", file=sys.stderr)
        return 2
    home = os.path.expanduser("~")
    tab_json = "--tab-json" in argv
    if recipes is not None:
        recipes, errors = list(recipes), []
    else:
        recipes, errors = collect(home, escalate=not (list_pending or tab_json))
    if tab_json:
        print(json.dumps(tab_status(home, make_hostio(False), recipes, errors, ack="--ack" in argv)))
        return 0
    for err in errors:
        print(f"post-update: local recipe skipped: {err}", file=sys.stderr)
    if list_pending:
        needs, unchecked = classify(recipes)
        print("Post-update tasks pending: " + (", ".join(r.id for r in needs) if needs else "none"))
        if unchecked:
            print("Could not check without root (run ./install.sh --post-update to check them): "
                  + ", ".join(r.id for r in unchecked))
        return 0
    if args["only"]:
        known = {r.id for r in recipes}
        unknown = [i for i in args["only"] if i not in known]
        if unknown:
            print(f"post-update: unknown task: {', '.join(unknown)}", file=sys.stderr)
            return 2
        selected = [r for r in recipes if r.id in args["only"]]
    elif args["post_update"]:
        selected = _pick(pending(recipes) or [], input_fn)
    else:
        return 0
    ordered = order_tasks(with_keyring(selected, recipes))
    print(plan_text(ordered))
    if not ordered or dry_run or "--dry-run" in argv:
        return 0
    results = run_plan(ordered, env or make_env(home))
    for rid, status in results:
        print(f"{rid}: {status}")
    mark_reboot(home, [r.id for r in ordered if r.reboot and dict(results).get(r.id) == "done"])
    return 1 if any(s == "failed" for _, s in results) else 0

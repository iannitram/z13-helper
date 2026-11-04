"""Real host wiring and entry point for the post-update runner (#67, #68).

`python3 -m post_update ARGS` (with tooling/ and src/ on the path, as install.sh sets it)
lists the tasks, runs the picked ones through `runner.run_plan`, or prints the pending
ones. Every command is an argv list, privileged ones through `sudo -n`. Checks never write.
"""

import logging
import os
import shutil
import sys
import time
from collections.abc import Callable, Sequence

from .builtin import HostIO, builtin_recipes
from .builtin_4_6 import Sys, builtin_recipes_4_6
from .cli import parse_args
from .recipe import order_tasks, plan_text, with_keyring
from .runner import Env, run_plan

_log = logging.getLogger(__name__)

BACKUP_ROOT = os.path.join(".local", "state", "z13-helper", "post-update-backups")
# Commands that must run as root; anything else runs as the current user.
_ROOT_CMDS = {"update-grub", "pacman-key", "pacman", "ldconfig"}


def _host():
    import host  # src/host.py: the one place host commands go through
    return host


def _run(argv: Sequence[str], timeout: int = 600, escalate: bool = True) -> tuple:
    argv = list(argv)
    if os.path.basename(argv[0]) in _ROOT_CMDS and os.geteuid() != 0:
        if not escalate:
            raise PermissionError(f"{argv[0]} needs root; not run when only listing")
        argv = ["sudo", "-n", *argv]
    res = _host().run(argv, timeout=timeout)
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


def make_env(home: str, run: Callable = _run, tool_name: Callable[[str], str] | None = None,
             stamp: str | None = None) -> Env:
    """Env for run_plan: backups under ~/.local/state/z13-helper/post-update-backups/<stamp>/."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    dest_root = os.path.join(home, BACKUP_ROOT, stamp)
    tool = readonly_cmd(tool_name)

    def backup(path: str) -> None:
        if not os.path.exists(path):
            return  # nothing to lose
        dest = os.path.join(dest_root, path.lstrip("/"))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copy2(path, dest)  # raises when unreadable: the run then stops before replacing it
        _log.info("backed up %s to %s", path, dest)

    def toggle(verb: str) -> None:
        _log.info("read-only: %s through %s", verb, tool)
        rc, out = run(["sudo", "-n", tool, verb])
        if rc != 0:
            raise RuntimeError(f"{tool} {verb} failed: {out}")

    return Env(backup=backup, readonly_disable=lambda: toggle("disable"),
               readonly_enable=lambda: toggle("enable"))


def all_recipes(home: str, escalate: bool = True) -> list:
    return builtin_recipes(make_hostio(escalate)) + builtin_recipes_4_6(make_sys(home, escalate))


def pending(recipes: Sequence) -> list:
    """Recipes whose check says they need work (read-only). A check that cannot run is left out."""
    needs = []
    for r in recipes:
        try:
            if r.check() == "needs":
                needs.append(r)
        except Exception:  # noqa: BLE001 - a check that needs root cannot say; do not guess
            _log.info("could not check %s without root", r.id)
    return needs


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

    `--list-pending` prints the ids that need work and changes nothing.
    """
    argv = list(argv)
    list_pending = "--list-pending" in argv
    try:
        args = parse_args(argv)
    except ValueError as e:
        print(f"post-update: {e}", file=sys.stderr)
        return 2
    home = os.path.expanduser("~")
    recipes = list(recipes) if recipes is not None else all_recipes(home, escalate=not list_pending)
    if list_pending:
        needs = pending(recipes)
        print("Post-update tasks pending: " + (", ".join(r.id for r in needs) if needs else "none"))
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
    return 1 if any(s == "failed" for _, s in results) else 0

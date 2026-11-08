"""Post-update runner: read-only checks and an ordered, backed-up apply (#110)."""

import logging
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "src")
if os.path.isdir(_SRC) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Env:
    backup: Callable[[str], "str | None"]   # copy path aside; its copy's path, None if it did not exist
    readonly_disable: Callable[[], None]
    readonly_enable: Callable[[], None]
    restore: "Callable[[str, str | None], None] | None" = None  # put a backup back; None = remove path
    record: "Callable[[dict], None] | None" = None              # persist one rollback record entry
    readonly_tool: str = "steamos-readonly"                     # named in log messages
    # One timeline.jsonl `post_update` entry for the whole run, called once at the end of
    # run_plan as timeline(ordered, results, backups, before_checks); best-effort.
    timeline: "Callable[[Sequence, list, dict, dict], object] | None" = None


def make_timeline_recorder(*, backup_dir: str | None = None,
                           path: str | None = None,
                           journal_fn: Callable[[str, str], None] | None = None):
    """Build an `Env.timeline` callback that writes one `post_update` timeline entry
    with its Phase 12c file-backup rollback record and Principle 7 journal line.

    `backups` is built by `run_plan` from the per-recipe rollback entries (`{path: backup
    copy}`, only for files that existed). No production code builds an `Env` yet (that is
    #109/#110's host wiring), so nothing passes this to `Env(timeline=...)` outside tests.
    The Env builder must use `make_timeline_recorder()` when it lands."""
    import timeline

    def _record(ordered: Sequence, results: list, backups: dict, before_checks: dict):
        return timeline.record_post_update(
            recipes=ordered,
            results=results,
            backups=backups,
            before_checks=before_checks,
            backup_dir=backup_dir,
            path=path,
            journal_fn=journal_fn,
        )

    return _record


def check_all(recipes: Sequence) -> dict:
    """Return {id: check()} for every recipe, in order. Calls nothing else."""
    return {recipe.id: recipe.check() for recipe in recipes}


def _undo_text(entry: dict, recipe) -> str:
    parts = [f"copy {b} back to {p}" for p, b in entry["backups"].items() if b]
    parts += [f"remove {p}" for p in entry["created"]]
    if recipe.undo:
        parts.append(recipe.undo)
    return "; ".join(parts) or "this task declares no changed files and no automatic undo"


def _apply_one(recipe, env: Env, state: dict, entry: dict) -> None:
    if recipe.pacman and not state["disabled"]:
        state["disabled"] = True  # set first: re-enable even if disable raises
        env.readonly_disable()
    entry["created"] = list(recipe.creates()) if recipe.creates else []
    for path in recipe.replaces:
        entry["backups"][path] = env.backup(path)   # recorded as soon as it is made
        if entry["backups"][path] is None and path not in entry["created"]:
            entry["created"].append(path)           # absent before: undo is removing it
    entry["undo"] = _undo_text(entry, recipe)
    recipe.apply()


def _roll_back(env: Env, entry: dict) -> None:
    """Undo what this recipe changed; a step that changed nothing has nothing to roll back."""
    if not entry["backups"] and not entry["created"]:
        return
    if env.restore is None:
        entry["rollback"] = "not attempted"
        return
    try:
        for path, backup in entry["backups"].items():
            if backup is not None:
                env.restore(path, backup)
        for path in entry["created"]:
            env.restore(path, None)
        entry["rollback"] = "rolled back"
    except Exception as exc:  # noqa: BLE001 - recorded; the user restores by hand
        entry["rollback"] = "rollback failed"
        entry["rollback_error"] = str(exc)


def _save(env: Env, entry: dict) -> None:
    if env.record is not None:
        try:
            env.record(entry)
        except Exception:  # noqa: BLE001 - run_plan never raises
            _log.exception("could not write the rollback record for %s", entry["id"])


def run_plan(ordered: Sequence, env: Env) -> list:
    """Apply recipes in order; return [(id, "done"|"failed"|"skipped")]. Never raises."""
    results = []
    backups: dict = {}        # for the timeline entry: path -> backup copy
    before_checks: dict = {}  # for the timeline entry: recipe id -> its check before apply
    state = {"disabled": False}
    failed = False
    try:
        for recipe in ordered:
            if failed:
                results.append((recipe.id, "skipped"))
                continue
            entry = {"id": recipe.id, "status": "planned", "backups": {}, "created": [],
                     "undo": "", "rollback": ""}
            try:
                chk = recipe.check()
                before_checks[recipe.id] = chk
                if chk == "na":
                    results.append((recipe.id, "skipped"))
                    continue
                _apply_one(recipe, env, state, entry)
                entry["status"] = "done"
                results.append((recipe.id, "done"))
            except Exception as exc:  # noqa: BLE001 - any recipe or Env error fails this step
                failed = True
                entry["status"] = "failed"
                entry["error"] = str(exc)
                _roll_back(env, entry)
                results.append((recipe.id, "failed"))
            for path, bak in entry["backups"].items():
                if isinstance(bak, str) and bak:
                    backups[path] = bak
            _save(env, entry)
    finally:
        if state["disabled"]:
            try:
                env.readonly_enable()
            except Exception:  # noqa: BLE001 - run_plan never raises
                _log.exception("could not re-enable %s", env.readonly_tool)
        if env.timeline is not None:
            try:
                env.timeline(ordered, results, backups, before_checks)
            except Exception:  # noqa: BLE001 - run_plan never raises
                _log.exception("could not record post-update timeline entry")
    return results

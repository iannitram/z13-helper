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
    backup: Callable[[str], str | None]
    readonly_disable: Callable[[], None]
    readonly_enable: Callable[[], None]
    record: Callable[[Sequence, list, dict, dict], object] | None = None


def make_timeline_recorder(*, backup_dir: str | None = None,
                           path: str | None = None,
                           journal_fn: Callable[[str, str], None] | None = None):
    """Build an `Env.record` callback that writes one `post_update` timeline entry
    with its Phase 12c file-backup rollback record and Principle 7 journal line.

    No production code builds an `Env` yet (that is #109/#110's host wiring), so
    nothing passes this to `Env(record=...)` outside tests. The Env builder must
    use `make_timeline_recorder()` when it lands."""
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


def _apply_one(recipe, env: Env, state: dict, backups: dict) -> None:
    if recipe.pacman and not state["disabled"]:
        state["disabled"] = True  # set first: re-enable even if disable raises
        env.readonly_disable()
    for path in recipe.replaces:
        bak = env.backup(path)
        backups[path] = bak if isinstance(bak, str) and bak else path
    recipe.apply()


def run_plan(ordered: Sequence, env: Env) -> list:
    """Apply recipes in order; return [(id, "done"|"failed"|"skipped")]. Never raises."""
    results = []
    backups = {}
    before_checks = {}
    state = {"disabled": False}
    failed = False
    try:
        for recipe in ordered:
            if failed:
                results.append((recipe.id, "skipped"))
                continue
            try:
                chk = recipe.check()
                before_checks[recipe.id] = chk
                if chk == "na":
                    results.append((recipe.id, "skipped"))
                    continue
                _apply_one(recipe, env, state, backups)
                results.append((recipe.id, "done"))
            except Exception:  # noqa: BLE001 - any recipe or Env error fails this step
                failed = True
                results.append((recipe.id, "failed"))
    finally:
        if state["disabled"]:
            try:
                env.readonly_enable()
            except Exception:  # noqa: BLE001 - run_plan never raises
                _log.exception("could not re-enable steamos-readonly")
        if env.record is not None:
            try:
                env.record(ordered, results, backups, before_checks)
            except Exception:  # noqa: BLE001 - run_plan never raises
                _log.exception("could not record post-update timeline entry")
    return results

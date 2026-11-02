"""Post-update runner: read-only checks and an ordered, backed-up apply (#110)."""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Env:
    backup: Callable[[str], None]
    readonly_disable: Callable[[], None]
    readonly_enable: Callable[[], None]


def check_all(recipes: Sequence) -> dict:
    """Return {id: check()} for every recipe, in order. Calls nothing else."""
    return {recipe.id: recipe.check() for recipe in recipes}


def _apply_one(recipe, env: Env, state: dict) -> None:
    if recipe.pacman and not state["disabled"]:
        state["disabled"] = True  # set first: re-enable even if disable raises
        env.readonly_disable()
    for path in recipe.replaces:
        env.backup(path)
    recipe.apply()


def run_plan(ordered: Sequence, env: Env) -> list:
    """Apply recipes in order; return [(id, "done"|"failed"|"skipped")]. Never raises."""
    results = []
    state = {"disabled": False}
    failed = False
    try:
        for recipe in ordered:
            if failed:
                results.append((recipe.id, "skipped"))
                continue
            try:
                if recipe.check() == "na":
                    results.append((recipe.id, "skipped"))
                    continue
                _apply_one(recipe, env, state)
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
    return results

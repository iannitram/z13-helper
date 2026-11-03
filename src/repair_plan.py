"""Reversible repair plan skeleton for post-update failures (#27)."""

from collections.abc import Callable
from dataclasses import dataclass


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
    record: dict


def plan_for(failures) -> RepairPlan:
    """Return an empty plan. Every future step names the check_id it repairs."""
    return RepairPlan(steps=[], record={})

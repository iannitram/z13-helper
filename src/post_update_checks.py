"""Read-only post-update check skeleton (#27)."""

from dataclasses import dataclass


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    detail: str


def run_checks(inventory) -> list:
    """Return no results. Checks arrive with the post-update recipes (#67-#72)."""
    return []

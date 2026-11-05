"""Ordered registry of built-in post-update recipes (issue #71)."""

from .bluetooth import bluetooth_recipe
from .builtin import builtin_recipes
from .builtin_4_6 import etc_snapshot_recipe, rocm_recipe


def ordered_builtins(io, sys_, src_root=None) -> list:
    """Return all built-in recipes in execution order (1-6):
    amdgpu, keyring, carry-forward, etc-snapshot, mt7925-bt, rocm.
    """
    return (
        builtin_recipes(io)
        + [etc_snapshot_recipe(sys_)]
        + [bluetooth_recipe(io, src_root)]
        + [rocm_recipe(sys_)]
    )

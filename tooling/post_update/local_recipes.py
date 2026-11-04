"""Turn loaded local recipe dicts (local.py) into runnable recipes (issue #72).

`check` and `apply` are command lines; they are split with shlex into argv lists and run
without a shell. A check passes when it exits 0 and says "needs" otherwise; it never gets
sudo. An apply gets `sudo -n` when the recipe says sudo and we are not root.
"""

import os
import shlex
from collections.abc import Callable, Sequence

from .local import load_dir
from .recipe import Recipe

LOCAL_REL = os.path.join(".config", "z13-helper", "post-update.d")


def local_dir(home: str) -> str:
    return os.path.join(home, LOCAL_REL)


def _argv(line: str, field: str) -> list:
    try:
        argv = shlex.split(line)
    except ValueError as e:
        raise ValueError(f"{field} is not a valid command line: {e}") from e
    if not argv:
        raise ValueError(f"{field} is empty")
    return argv


def local_recipe(d: dict, run: Callable[[Sequence[str]], tuple], is_root: bool = False) -> Recipe:
    """A Recipe from one parsed dict; ValueError when check or apply cannot be split."""
    check_argv = _argv(d["check"], "check")
    apply_argv = _argv(d["apply"], "apply")
    if d["sudo"] and not is_root:
        apply_argv = ["sudo", "-n", *apply_argv]

    def check() -> str:
        rc, _ = run(check_argv)
        return "ok" if rc == 0 else "needs"

    def apply() -> None:
        rc, out = run(apply_argv)
        if rc != 0:
            raise RuntimeError(f"{d['id']} failed (rc={rc}): {out}")

    return Recipe(id=d["id"], title=d["title"], fixes=d["fixes"], check=check, apply=apply,
                  sudo=d["sudo"], network=d["network"], reboot=d["reboot"], pacman=d["pacman"],
                  default_on=d["default_on"])


def load_local_recipes(home: str, run: Callable[[Sequence[str]], tuple], is_root: bool = False,
                       taken: Sequence[str] = ()) -> tuple:
    """(recipes, errors) from ~/.config/z13-helper/post-update.d/*.toml.

    A malformed file, or an id a built-in already uses, is an error string; the rest load.
    """
    dicts, errors = load_dir(local_dir(home))
    recipes = []
    for d in dicts:
        if d["id"] in taken:
            errors.append(f"{d['id']}: id is already used by a built-in task")
            continue
        try:
            recipes.append(local_recipe(d, run, is_root))
        except ValueError as e:
            errors.append(f"{d['id']}: {e}")
    return recipes, errors

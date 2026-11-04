"""Built-in post-update recipes 4 (SteamOS /etc snapshot) and 6 (ROCm pack) (issue #71).

Every host touch goes through the Sys object, so the recipes run on a fake tree in
tests. check() only reads; apply() is idempotent and raises on failure (the runner
then marks the task failed and re-enables steamos-readonly).
"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Optional

from post_update.recipe import Recipe

SNAPSHOT_SRC = "/var/lib/steamos-atomupd/etc_backup"
SNAPSHOT_DEST_REL = os.path.join(".local", "share", "z13-helper", "etc-backups")

ROCM_DIR = "/opt/rocm"
ROCM_ROCMINFO = "/opt/rocm/bin/rocminfo"
ROCM_LD_CONF = "/etc/ld.so.conf.d/00-rocm.conf"
ROCM_PROFILE = "/etc/profile.d/rocm.sh"
ROCM_LD_TEXT = "/opt/rocm/lib\n/opt/rocm/lib64\n"
ROCM_PROFILE_TEXT = 'export ROCM_PATH=/opt/rocm\nexport PATH="$ROCM_PATH/bin:$PATH"\n'
ROCM_PACKAGES = ["fmt", "google-glog"]


@dataclass(frozen=True)
class Sys:
    """The host operations the recipes use. Real wiring lives in the runner's caller."""

    home: str
    listdir: Callable[[str], Optional[list]]     # None when missing or unreadable
    exists: Callable[[str], bool]
    read: Callable[[str], Optional[str]]         # None when missing or unreadable
    run: Callable[[Sequence[str]], int]          # exit code; 127 when the command is missing
    write: Callable[[str, str], None]            # write text, creating parent folders
    copy_tree: Callable[[str, str], None]        # copy a folder, creating parent folders


def etc_snapshot_recipe(sys_: Sys) -> Recipe:
    """Task 4: copy SteamOS's own per-update /etc snapshot into /home (never deletes)."""
    dest = os.path.join(sys_.home, SNAPSHOT_DEST_REL)

    def _missing_copies() -> list:
        names = sys_.listdir(SNAPSHOT_SRC) or []
        copied = set(sys_.listdir(dest) or [])
        return [name for name in sorted(names) if name not in copied]

    def check() -> str:
        if not sys_.listdir(SNAPSHOT_SRC):
            return "na"
        return "needs" if _missing_copies() else "ok"

    def apply() -> None:
        for name in _missing_copies():
            sys_.copy_tree(os.path.join(SNAPSHOT_SRC, name), os.path.join(dest, name))

    return Recipe(
        id="etc-snapshot",
        title="Save SteamOS's /etc snapshot",
        fixes="keep SteamOS's per-update /etc backup in /home, where the next slot switch can't drop it",
        check=check,
        apply=apply,
        sudo=True,
        default_on=True,
    )


def rocm_recipe(sys_: Sys) -> Recipe:
    """Task 6: ROCm pack. Shown only when /opt/rocm exists (otherwise check says 'na')."""

    def check() -> str:
        if not sys_.exists(ROCM_DIR):
            return "na"
        if sys_.run([ROCM_ROCMINFO]) != 0:
            return "needs"
        if not (sys_.exists(ROCM_LD_CONF) and sys_.exists(ROCM_PROFILE)):
            return "needs"
        return "ok"

    def apply() -> None:
        if sys_.run(["pacman", "-S", "--needed", "--noconfirm", *ROCM_PACKAGES]) != 0:
            raise RuntimeError("pacman could not install fmt and google-glog")
        sys_.write(ROCM_LD_CONF, ROCM_LD_TEXT)
        if sys_.run(["ldconfig"]) != 0:
            raise RuntimeError("ldconfig failed")
        sys_.write(ROCM_PROFILE, ROCM_PROFILE_TEXT)

    return Recipe(
        id="rocm",
        title="ROCm pack (local LLMs)",
        fixes="the update strips fmt and google-glog and the ld.so and profile entries, so rocminfo won't run",
        check=check,
        apply=apply,
        sudo=True,
        network=True,
        pacman=True,
        replaces=(ROCM_LD_CONF, ROCM_PROFILE),
    )


def builtin_recipes_4_6(sys_: Sys) -> list:
    """Recipes 4 and 6 in roadmap order."""
    return [etc_snapshot_recipe(sys_), rocm_recipe(sys_)]

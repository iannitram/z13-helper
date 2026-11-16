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
ROCM_PACKAGES = ["google-glog"]
ROCM_LIB_DIR = "/opt/rocm/lib"
# The ROCm in /opt/rocm (6.4.1, not a pacman package) links libfmt.so.11, but the repos now ship
# fmt 12 (libfmt.so.12) and a pacman -U of fmt 11 would replace fmt 12 under everything else.
# So only the soname is extracted from the pinned Arch Linux Archive package into /opt/rocm/lib,
# which is on the ld.so path, on its own partition, and untouched by pacman and SteamOS updates.
# This is the one soname list: add a row here for the next library the post-check names.
FMT11_PKG = "fmt-11.2.0-1-x86_64.pkg.tar.zst"
ROCM_COMPAT_LIBS = (
    {
        "soname": "libfmt.so.11",
        "real": "libfmt.so.11.2.0",
        "url": "https://archive.archlinux.org/packages/f/fmt/" + FMT11_PKG,
        "sha256": "509b15e66cb605572bea50ce9b25475e29e5e720d09f683887f9cdf264b4ed60",
    },
)
ROCM_COMPAT_CACHE_REL = os.path.join(".cache", "z13-helper", "rocm-compat")


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
    run_out: Callable[[Sequence[str]], tuple] | None = None  # (exit code, output); None when not wired


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


def _missing_libs(sys_: Sys) -> list:
    """Shared libraries the ROCm tools cannot load: the names ldd reports as 'not found'."""
    if sys_.run_out is None:
        return []
    _rc, out = sys_.run_out(["ldd", ROCM_ROCMINFO])
    names = []
    for line in (out or "").splitlines():
        if "not found" in line:
            name = line.split("=>")[0].strip()
            if name and name not in names:
                names.append(name)
    return names


def rocm_recipe(sys_: Sys) -> Recipe:
    """Task 6: ROCm pack. Shown only when /opt/rocm exists (otherwise check says 'na')."""

    def _lib_path(lib: dict, key: str = "soname") -> str:
        return os.path.join(ROCM_LIB_DIR, lib[key])

    def _absent_compat() -> list:
        return [lib for lib in ROCM_COMPAT_LIBS if not sys_.exists(_lib_path(lib))]

    def check() -> str:
        if not sys_.exists(ROCM_DIR):
            return "na"
        if sys_.run([ROCM_ROCMINFO]) != 0:
            return "needs"
        if not (sys_.exists(ROCM_LD_CONF) and sys_.exists(ROCM_PROFILE)):
            return "needs"
        return "ok"

    def creates() -> list:
        out = []
        for lib in _absent_compat():
            out += [_lib_path(lib), _lib_path(lib, "real")]
        return out

    def _install_compat(lib: dict) -> None:
        pkg = os.path.join(sys_.home, ROCM_COMPAT_CACHE_REL, lib["url"].rsplit("/", 1)[1])
        if sys_.run(["curl", "-fL", "--create-dirs", "-o", pkg, lib["url"]]) != 0:
            raise RuntimeError(f"could not download {lib['soname']} from the Arch Linux Archive")
        if sys_.run(["sh", "-c", f'echo "{lib["sha256"]}  {pkg}" | sha256sum -c -']) != 0:
            raise RuntimeError(f"the downloaded package for {lib['soname']} failed its checksum")
        member = "usr/lib/" + lib["soname"] + "*"
        if sys_.run(["bsdtar", "-xf", pkg, "-C", ROCM_LIB_DIR, "--strip-components=2", member]) != 0:
            raise RuntimeError(f"could not extract {lib['soname']} into {ROCM_LIB_DIR}")

    def apply() -> None:
        if sys_.run(["pacman", "-S", "--needed", "--noconfirm", *ROCM_PACKAGES]) != 0:
            raise RuntimeError("pacman could not install " + " and ".join(ROCM_PACKAGES))
        for lib in _absent_compat():
            _install_compat(lib)
        sys_.write(ROCM_LD_CONF, ROCM_LD_TEXT)
        if sys_.run(["ldconfig"]) != 0:
            raise RuntimeError("ldconfig failed")
        sys_.write(ROCM_PROFILE, ROCM_PROFILE_TEXT)
        _post_check()

    def _post_check() -> None:
        """Fail, naming the library, when rocminfo still cannot load (never report a false done)."""
        missing = _missing_libs(sys_)
        if missing:
            msg = "rocminfo still cannot load: " + ", ".join(missing)
            print("rocm: FAILED - " + msg, flush=True)
            raise RuntimeError(msg)
        rc = sys_.run([ROCM_ROCMINFO])
        if rc != 0:
            msg = f"rocminfo exited with code {rc} after the fix"
            print("rocm: FAILED - " + msg, flush=True)
            raise RuntimeError(msg)

    return Recipe(
        id="rocm",
        title="ROCm pack (local LLMs)",
        fixes="the update strips google-glog, the ld.so and profile entries and the libfmt.so.11 ROCm needs, so rocminfo won't run",
        check=check,
        apply=apply,
        sudo=True,
        network=True,
        pacman=True,
        replaces=(ROCM_LD_CONF, ROCM_PROFILE),
        creates=creates,
        undo=(f"installed packages stay (remove google-glog with pacman -R if unwanted); the libfmt.so.11 files "
              f"this task added to {ROCM_LIB_DIR} are listed above and are removed on rollback"),
    )


def builtin_recipes_4_6(sys_: Sys) -> list:
    """Recipes 4 and 6 in roadmap order."""
    return [etc_snapshot_recipe(sys_), rocm_recipe(sys_)]

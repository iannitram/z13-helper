"""Built-in post-update recipes 1-3 (issue #69): amdgpu args, pacman keyring, carry-forward health.

Every recipe reads and runs things only through the HostIO it is given, so the tests use
fake /proc, grub, pacman and /etc data. The caller supplies the real HostIO.

Safety: a check returns "na" anywhere but SteamOS on a GZ302 (the runner skips "na"), and
every apply refuses on anything else. Checks never write or run a command that changes state.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .cmdline import add_grub_args, missing_args
from .health import carry_forward_missing, keyring_status
from .recipe import KEYRING_ID, Recipe

OS_RELEASE = "/etc/os-release"
DMI_PRODUCT = "/sys/class/dmi/id/product_name"
CMDLINE = "/proc/cmdline"
GRUB_FILE = "/etc/default/grub-steamos"
KEYRING_DIR = "/etc/pacman.d/gnupg"   # --init/--populate rewrite it: backed up first
KEEP_DIR = "/etc/atomic-update.conf.d"

AMDGPU_ARGS = ("dcdebugmask=0x20e10", "gpu_recovery=1", "cwsr_enable=0", "sg_display=0", "abmlevel=0")

_GRUB_ASSIGN = re.compile(r'\s*GRUB_CMDLINE_LINUX_DEFAULT="')
# Anchored to a line start (leading blanks only) so a commented "# GRUB_CMDLINE_..." line never matches.
_GRUB_VALUE = re.compile(r'^[ \t]*GRUB_CMDLINE_LINUX_DEFAULT="((?:[^"\\]|\\.)*)"', re.MULTILINE)


@dataclass(frozen=True)
class HostIO:
    """What the built-in recipes need from the machine. Never raises from run()."""
    read: Callable[[str], str | None]           # file text, None when unreadable
    write: Callable[[str, str], None]
    exists: Callable[[str], bool]
    listdir: Callable[[str], Sequence[str]]     # names; empty when the folder is missing
    run: Callable[[Sequence[str]], tuple]       # (rc, output)


def _os_release(text: str) -> dict:
    values = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key and not key.startswith("#"):
            values[key] = value.strip().strip('"').strip("'")
    return values


def supported_device(io: HostIO) -> bool:
    """True only on SteamOS (os-release ID=steamos) on a GZ302 (DMI product name)."""
    release = _os_release(io.read(OS_RELEASE) or "")
    product = (io.read(DMI_PRODUCT) or "").upper()
    return release.get("ID") == "steamos" and "GZ302" in product


def _refuse_unless_supported(io: HostIO) -> None:
    if not supported_device(io):
        raise RuntimeError("not SteamOS on a GZ302: refusing to apply")


def _grub_index(lines: Sequence[str]):
    for i, line in enumerate(lines):
        if _GRUB_ASSIGN.match(line):
            return i
    return None


# 1. amdgpu display + MES stability: the kernel args in Valve's grub-steamos, then update-grub.

def _amdgpu_check(io: HostIO) -> str:
    if not supported_device(io):
        return "na"
    cmdline = io.read(CMDLINE) or ""
    grub = io.read(GRUB_FILE) or ""
    match = _GRUB_VALUE.search(grub)
    if match is None:
        return "needs"
    if missing_args(cmdline, AMDGPU_ARGS) or missing_args(match.group(1), AMDGPU_ARGS):
        return "needs"
    return "ok"


def _amdgpu_apply(io: HostIO) -> None:
    _refuse_unless_supported(io)
    text = io.read(GRUB_FILE)
    if text is None:
        raise RuntimeError(f"cannot read {GRUB_FILE}")
    lines = text.split("\n")
    index = _grub_index(lines)
    if index is None:
        raise RuntimeError(f"no GRUB_CMDLINE_LINUX_DEFAULT line in {GRUB_FILE}")
    lines[index] = add_grub_args(lines[index], AMDGPU_ARGS)
    new_text = "\n".join(lines)
    if new_text != text:
        io.write(GRUB_FILE, new_text)
    rc, out = io.run(["update-grub"])
    if rc != 0:
        raise RuntimeError(f"update-grub failed: {out}")


# 2. pacman keyring: rebuild the keyring. The runner disables steamos-readonly around it (pacman flag).

def _keyring_check(io: HostIO) -> str:
    if not supported_device(io):
        return "na"
    rc, out = io.run(["pacman-key", "--list-keys"])
    if rc != 0:
        return "needs"
    return keyring_status(out)


def _keyring_apply(io: HostIO) -> None:
    _refuse_unless_supported(io)
    for argv in (["pacman-key", "--init"], ["pacman-key", "--populate"]):
        rc, out = io.run(argv)
        if rc != 0:
            raise RuntimeError(f"{' '.join(argv)} failed: {out}")


# 3. Carry-forward list health: info only, so a missing path is "info" and never "needs"
# (apply changes nothing, so "needs" could never clear).

def _carry_forward_check(io: HostIO) -> str:
    if not supported_device(io):
        return "na"
    texts = []
    for name in sorted(io.listdir(KEEP_DIR)):
        if name.endswith(".conf"):
            texts.append(io.read(f"{KEEP_DIR}/{name}") or "")
    return "info" if carry_forward_missing(texts, io.exists) else "ok"


def _carry_forward_apply(io: HostIO) -> None:
    """Nothing to apply: this task only shows what is missing, so the runner records it as done."""
    return None


def builtin_recipes(io: HostIO) -> list:
    """The three built-in recipes, bound to the given HostIO."""
    return [
        Recipe(
            id="amdgpu",
            title="amdgpu display + MES stability",
            fixes="Game Mode display freezes, idle reboots and MES GPU-scheduler wedges on Strix Halo",
            check=lambda: _amdgpu_check(io),
            apply=lambda: _amdgpu_apply(io),
            sudo=True,
            reboot=True,
            default_on=True,
            replaces=(GRUB_FILE,),
        ),
        Recipe(
            id=KEYRING_ID,
            title="pacman keyring",
            fixes='"required key missing from keyring" on pacman steps after an update',
            check=lambda: _keyring_check(io),
            apply=lambda: _keyring_apply(io),
            replaces=(KEYRING_DIR,),
            sudo=True,
            pacman=True,
        ),
        Recipe(
            id="carry-forward",
            title="carry-forward list health",
            fixes="registered /etc files that went missing after a slot switch (info only)",
            check=lambda: _carry_forward_check(io),
            apply=lambda: _carry_forward_apply(io),
        ),
    ]

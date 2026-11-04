"""Read-only system inventory for post-update repair (#27).

`read` is the injectable seam for every read. `read(target)` returns text or None when
the target cannot be read:
  - a `str` target is a file path (sysfs, /proc, /etc),
  - a `tuple` of str is an argv, run without a shell, returning its output.
Tests pass a fake; `host_read` is the real one (files directly, commands through
`host.run`). Nothing here writes. With `read=None` nothing is read and every value is None.
"""

import re

from post_update_state import read_build_id

INVENTORY_KEYS = (
    "steamos_build",
    "kernel",
    "bios",
    "ec",
    "firmware",
    "mesa",
    "vulkan_radeon",
    "backend_version",
    "units",
    "permissions",
    "kernel_args",
)

OS_RELEASE = "/etc/os-release"
KERNEL_RELEASE = "/proc/sys/kernel/osrelease"
BIOS_VERSION = "/sys/class/dmi/id/bios_version"
EC_VERSION = "/sys/class/dmi/id/ec_firmware_release"
CMDLINE = "/proc/cmdline"
ARMOURY_DIR = "/sys/class/firmware-attributes/asus-armoury/attributes"
# Units our installer enables (tooling/install_check.py) and the system-level ones.
USER_UNITS = ("z13-button-watcher.service", "z13-helper.service",
              "z13-restore-profile.service", "z13ctl.socket")
SYSTEM_UNITS = ("z13-fan-resume.service", "z13-lightbar-reset.service")
# Files the helper depends on: the ppt attribute z13ctl writes and its udev rule.
PERMISSION_PATHS = (
    ARMOURY_DIR + "/ppt_pl1_spl/current_value",
    "/etc/udev/rules.d/99-z13ctl.rules",
)

_VERSION_TOKEN = re.compile(r"(?<![0-9A-Za-z])\d+(?:\.[0-9A-Za-z]+)+(?:[_+~-][0-9A-Za-z.]+)*")


def host_read(target):
    """The real seam: a file's text, or a command's output; None when it fails."""
    if isinstance(target, str):
        try:
            with open(target, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return None
    import host  # local import: tests that use a fake seam never need it
    result = host.run(list(target), timeout=10)
    return result.out if result.rc == 0 else None


def _text(read, target):
    try:
        value = read(target)
    except Exception:  # a failing seam must not take the inventory down
        return None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _first_version(text):
    m = _VERSION_TOKEN.search(text or "")
    return m.group(0) if m else None


def _pacman_version(read, package):
    out = _text(read, ("pacman", "-Q", package))
    parts = out.split() if out else []
    return parts[1] if len(parts) == 2 and parts[0] == package else None


def collect_inventory(read=None) -> dict:
    """Return one entry per inventory key. Every read goes through `read`; None = unknown."""
    if read is None:
        return {key: None for key in INVENTORY_KEYS}
    os_release = _text(read, OS_RELEASE)
    units = {}
    for unit in USER_UNITS:
        units["user:" + unit] = _text(read, ("systemctl", "--user", "is-enabled", unit))
    for unit in SYSTEM_UNITS:
        units["system:" + unit] = _text(read, ("systemctl", "is-enabled", unit))
    permissions = {}
    for path in PERMISSION_PATHS:
        permissions[path] = _text(read, ("stat", "-c", "%a %U:%G", path))
    return {
        "steamos_build": read_build_id(os_release) if os_release else None,
        "kernel": _text(read, KERNEL_RELEASE),
        "bios": _text(read, BIOS_VERSION),
        "ec": _text(read, EC_VERSION),
        "firmware": _pacman_version(read, "linux-firmware"),
        "mesa": _pacman_version(read, "mesa"),
        "vulkan_radeon": _pacman_version(read, "vulkan-radeon"),
        "backend_version": _first_version(_text(read, ("z13ctl", "--version"))),
        "units": units,
        "permissions": permissions,
        "kernel_args": _text(read, CMDLINE),
    }

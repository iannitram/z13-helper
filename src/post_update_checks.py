"""Read-only post-update checks over the inventory (#27).

`run_checks` looks only at the dict `system_inventory.collect_inventory` returns; it reads
and runs nothing. A value that could not be read (None) is a failing check, never a pass.
"""

from dataclasses import dataclass

from post_update.builtin import AMDGPU_ARGS
from post_update.cmdline import missing_args
from system_inventory import PERMISSION_PATHS

_PRESENT = (
    ("steamos_build", "SteamOS build"),
    ("kernel", "kernel"),
    ("bios_ec", "BIOS/EC"),
    ("firmware", "firmware package"),
    ("mesa_vulkan", "Mesa/Vulkan"),
    ("backend_version", "z13ctl backend"),
)


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    passed: bool
    detail: str


def run_checks(inventory) -> list:
    """One CheckResult per inventory item and unit; check_id names what a repair would fix."""
    inv = inventory or {}
    results = []
    for key, label in _PRESENT:
        value = inv.get(key)
        results.append(CheckResult(key, bool(value), f"{label}: {value}" if value
                                   else f"{label} could not be read"))
    cmdline = inv.get("kernel_args")
    if not cmdline:
        results.append(CheckResult("kernel_args", False, "kernel arguments could not be read"))
    else:
        missing = missing_args(cmdline, AMDGPU_ARGS)
        results.append(CheckResult("kernel_args", not missing,
                                   "amdgpu arguments present" if not missing
                                   else "missing: " + " ".join(missing)))
    for name, state in sorted((inv.get("units") or {}).items()):
        results.append(CheckResult("unit:" + name, state == "enabled",
                                   f"{name} is {state or 'unknown'}"))
    perms = inv.get("permissions") or {}
    for path in PERMISSION_PATHS:
        results.append(CheckResult("permission:" + path, bool(perms.get(path)),
                                   f"{path}: {perms.get(path) or 'missing or unreadable'}"))
    return results

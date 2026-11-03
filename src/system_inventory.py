"""Read-only system inventory skeleton for post-update repair (#27).

`read` is the injectable seam for sysfs and command reads: the real collector
will call it, tests pass a fake. This skeleton performs no reads.
"""

INVENTORY_KEYS = (
    "steamos_build",
    "kernel",
    "bios_ec",
    "firmware",
    "mesa_vulkan",
    "backend_version",
    "units",
    "permissions",
    "kernel_args",
)


def collect_inventory(read=None) -> dict:
    """Return one None-valued entry per inventory key. No I/O is performed yet."""
    return {key: None for key in INVENTORY_KEYS}

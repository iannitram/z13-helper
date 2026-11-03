"""System inventory skeleton shape (issue #27)."""

from system_inventory import INVENTORY_KEYS, collect_inventory


def test_collect_inventory_has_every_key_with_no_reads():
    inventory = collect_inventory(read=None)
    assert tuple(inventory) == INVENTORY_KEYS
    assert set(inventory) == {
        "steamos_build",
        "kernel",
        "bios_ec",
        "firmware",
        "mesa_vulkan",
        "backend_version",
        "units",
        "permissions",
        "kernel_args",
    }
    assert all(value is None for value in inventory.values())

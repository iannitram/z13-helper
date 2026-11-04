"""System inventory: real collection through the read seam (issue #27)."""

from system_inventory import (INVENTORY_KEYS, PERMISSION_PATHS, SYSTEM_UNITS, USER_UNITS,
                              collect_inventory)

OS_RELEASE = 'NAME="SteamOS"\nID=steamos\nBUILD_ID=20261101.1\n'
GOOD = {
    "/etc/os-release": OS_RELEASE,
    "/proc/sys/kernel/osrelease": "7.2.7-valve2-2-neptune\n",
    "/sys/class/dmi/id/bios_version": "GZ302EA.313\n",
    "/sys/class/dmi/id/ec_firmware_release": "0.1\n",
    "/proc/cmdline": "quiet gpu_recovery=1\n",
    ("pacman", "-Q", "linux-firmware"): "linux-firmware 20261020-1\n",
    ("pacman", "-Q", "mesa"): "mesa 26.0.1-1\n",
    ("pacman", "-Q", "vulkan-radeon"): "vulkan-radeon 26.0.1-1\n",
    ("z13ctl", "--version"): "z13ctl 1.4.2\n",
    ("systemctl", "--user", "is-enabled", "z13-helper.service"): "enabled\n",
    ("stat", "-c", "%a %U:%G", PERMISSION_PATHS[0]): "664 root:users\n",
}


def fake(table):
    reads = []

    def read(target):
        reads.append(target)
        return table.get(target)
    read.reads = reads
    return read


def test_no_seam_reads_nothing():
    inventory = collect_inventory(read=None)
    assert tuple(inventory) == INVENTORY_KEYS
    assert all(value is None for value in inventory.values())


def test_every_key_is_collected_from_the_seam():
    inv = collect_inventory(fake(GOOD))
    assert tuple(inv) == INVENTORY_KEYS
    assert inv["steamos_build"] == "20261101.1"
    assert inv["kernel"] == "7.2.7-valve2-2-neptune"
    assert inv["bios"] == "GZ302EA.313" and inv["ec"] == "0.1"
    assert inv["firmware"] == "20261020-1"
    assert inv["mesa"] == "26.0.1-1" and inv["vulkan_radeon"] == "26.0.1-1"
    assert inv["backend_version"] == "1.4.2"
    assert inv["kernel_args"] == "quiet gpu_recovery=1"
    assert inv["units"]["user:z13-helper.service"] == "enabled"
    assert inv["units"]["user:z13ctl.socket"] is None
    assert set(inv["units"]) == {"user:" + u for u in USER_UNITS} | {"system:" + u for u in SYSTEM_UNITS}
    assert inv["permissions"][PERMISSION_PATHS[0]] == "664 root:users"
    assert inv["permissions"][PERMISSION_PATHS[1]] is None


def test_unreadable_things_are_none_and_a_raising_seam_is_survived():
    def boom(target):
        raise OSError("nope")
    inv = collect_inventory(boom)
    assert tuple(inv) == INVENTORY_KEYS
    assert inv["kernel"] is None and inv["bios"] is None
    assert all(v is None for v in inv["units"].values())


def test_reads_are_only_files_and_non_mutating_commands():
    read = fake(GOOD)
    collect_inventory(read)
    for target in read.reads:
        if isinstance(target, tuple):
            assert target[0] in {"pacman", "z13ctl", "systemctl", "stat"}
            assert target[:2] != ("pacman", "-S") and "enable" not in target

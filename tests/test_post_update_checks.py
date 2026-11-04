"""Post-update checks over the inventory (issue #27)."""

from post_update.builtin import AMDGPU_ARGS
from post_update_checks import CheckResult, run_checks
from system_inventory import PERMISSION_PATHS, collect_inventory
from test_system_inventory import GOOD, fake


def by_id(rows):
    return {r.check_id: r for r in rows}


def test_empty_inventory_fails_every_check_not_an_empty_list():
    rows = run_checks({})
    assert rows and all(isinstance(r, CheckResult) for r in rows)
    assert not any(r.passed for r in rows)


def test_real_inventory_rows():
    table = dict(GOOD)
    table["/proc/cmdline"] = "quiet " + " ".join(AMDGPU_ARGS)
    rows = by_id(run_checks(collect_inventory(fake(table))))
    assert rows["kernel"].passed and rows["steamos_build"].passed and rows["backend_version"].passed
    assert rows["kernel_args"].passed
    assert rows["unit:user:z13-helper.service"].passed
    assert not rows["unit:user:z13ctl.socket"].passed
    assert rows["permission:" + PERMISSION_PATHS[0]].passed
    assert not rows["permission:" + PERMISSION_PATHS[1]].passed


def test_missing_kernel_args_are_named():
    rows = by_id(run_checks(collect_inventory(fake(GOOD))))
    assert not rows["kernel_args"].passed
    assert "dcdebugmask=0x20e10" in rows["kernel_args"].detail
    assert "gpu_recovery=1" not in rows["kernel_args"].detail


def test_ppt_value_with_group_write_for_users_is_healthy():
    from post_update_checks import permission_problem
    ppt = PERMISSION_PATHS[0]
    assert permission_problem(ppt, "664 root:users") is None
    assert "owner" in permission_problem(ppt, "664 root:root")
    assert "writable" in permission_problem(ppt, "666 root:users")
    table = dict(GOOD)
    table[("stat", "-c", "%a %U:%G", ppt)] = "664 root:users\n"
    assert by_id(run_checks(collect_inventory(fake(table))))["permission:" + ppt].passed


def test_run_checks_does_not_mutate_inventory():
    inv = collect_inventory(fake(GOOD))
    before = repr(inv)
    run_checks(inv)
    assert repr(inv) == before


def test_permission_mode_and_owner_are_compared_not_just_present():
    from post_update_checks import permission_problem
    p = PERMISSION_PATHS[1]
    assert permission_problem(p, "644 root:root") is None
    assert "writable" in permission_problem(p, "666 root:root")
    assert "writable" in permission_problem(p, "664 root:root")
    assert permission_problem(p, "664 root:users") is not None
    assert "owner" in permission_problem(p, "644 deck:deck")
    assert permission_problem(p, None) == "missing or unreadable"
    assert permission_problem(p, "zzz root:root").startswith("unreadable mode")
    table = dict(GOOD)
    table[("stat", "-c", "%a %U:%G", PERMISSION_PATHS[0])] = "666 root:root\n"
    rows = by_id(run_checks(collect_inventory(fake(table))))
    assert not rows["permission:" + PERMISSION_PATHS[0]].passed
    assert "666" in rows["permission:" + PERMISSION_PATHS[0]].detail


def checks_for(**overrides):
    table = dict(GOOD)
    table.update(overrides)
    return by_id(run_checks(collect_inventory(fake(table))))


def test_missing_vulkan_radeon_fails_even_with_mesa_present():
    table = {k: v for k, v in GOOD.items() if k != ("pacman", "-Q", "vulkan-radeon")}
    rows = by_id(run_checks(collect_inventory(fake(table))))
    assert rows["mesa"].passed and not rows["vulkan_radeon"].passed


def test_missing_mesa_fails_even_with_vulkan_radeon_present():
    table = {k: v for k, v in GOOD.items() if k != ("pacman", "-Q", "mesa")}
    rows = by_id(run_checks(collect_inventory(fake(table))))
    assert rows["vulkan_radeon"].passed and not rows["mesa"].passed


def test_both_graphics_packages_present_pass():
    rows = checks_for()
    assert rows["mesa"].passed and rows["vulkan_radeon"].passed


def test_bios_and_ec_are_each_checked():
    no_ec = {k: v for k, v in GOOD.items() if k != "/sys/class/dmi/id/ec_firmware_release"}
    rows = by_id(run_checks(collect_inventory(fake(no_ec))))
    assert rows["bios"].passed and not rows["ec"].passed
    no_bios = {k: v for k, v in GOOD.items() if k != "/sys/class/dmi/id/bios_version"}
    rows = by_id(run_checks(collect_inventory(fake(no_bios))))
    assert rows["ec"].passed and not rows["bios"].passed
    rows = checks_for()
    assert rows["bios"].passed and rows["ec"].passed

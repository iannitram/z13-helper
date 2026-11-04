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


def test_run_checks_does_not_mutate_inventory():
    inv = collect_inventory(fake(GOOD))
    before = repr(inv)
    run_checks(inv)
    assert repr(inv) == before

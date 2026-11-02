"""amdgpu recipe helpers (issue #69, part a): /proc/cmdline check and the grub-steamos line edit."""

import pytest

from post_update.cmdline import add_grub_args, missing_args

WANTED = ("dcdebugmask=0x20e10", "gpu_recovery=1", "cwsr_enable=0", "sg_display=0", "abmlevel=0")
HEALTHY = "BOOT_IMAGE=/vmlinuz root=UUID=1 quiet " + " ".join(WANTED) + " splash"
STOCK = "BOOT_IMAGE=/vmlinuz root=UUID=1 quiet splash"
LINE = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amd_iommu=off"'


def test_missing_args_none_when_all_present():
    assert missing_args(HEALTHY, WANTED) == []


def test_missing_args_lists_all_on_a_stock_cmdline_in_wanted_order():
    assert missing_args(STOCK, WANTED) == list(WANTED)


def test_missing_args_exact_match_only():
    assert missing_args("quiet dcdebugmask=0x20e1 gpu_recovery=10", WANTED[:2]) == list(WANTED[:2])
    assert missing_args("quiet", ()) == []


def test_add_grub_args_appends_missing_and_keeps_the_rest():
    out = add_grub_args(LINE, ("gpu_recovery=1", "abmlevel=0"))
    assert out == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash amd_iommu=off gpu_recovery=1 abmlevel=0"'


def test_add_grub_args_is_idempotent():
    once = add_grub_args(LINE, WANTED)
    assert add_grub_args(once, WANTED) == once
    assert once.count("gpu_recovery=1") == 1


def test_add_grub_args_replaces_same_key_with_a_different_value_in_place():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet dcdebugmask=0x10 splash"'
    assert add_grub_args(line, ("dcdebugmask=0x20e10",)) == \
        'GRUB_CMDLINE_LINUX_DEFAULT="quiet dcdebugmask=0x20e10 splash"'


def test_add_grub_args_keeps_text_after_the_closing_quote():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"  # mine'
    assert add_grub_args(line, ("a=1",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet a=1"  # mine'


def test_add_grub_args_empty_value():
    assert add_grub_args('GRUB_CMDLINE_LINUX_DEFAULT=""', ("a=1",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="a=1"'


@pytest.mark.parametrize("line", ['GRUB_TIMEOUT=5', '# GRUB_CMDLINE_LINUX_DEFAULT="x"', ''])
def test_add_grub_args_rejects_other_lines(line):
    with pytest.raises(ValueError):
        add_grub_args(line, ("a=1",))

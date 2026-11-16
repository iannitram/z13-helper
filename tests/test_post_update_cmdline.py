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


# --- whole-file helpers: GRUB_CMDLINE_LINUX and backslash continuations (#244) ---------------

from post_update.cmdline import add_grub_args_text, grub_value

BLOCK = 'A=1\nGRUB_CMDLINE_LINUX="${GRUB_CMDLINE_LINUX} \\\nlog_buf_len=4M \\\nfoo=1 bar=2 \\\n"\nB=2\n'


def test_grub_value_reads_continuations_as_whitespace():
    v = grub_value(BLOCK)
    assert v.split() == ["${GRUB_CMDLINE_LINUX}", "log_buf_len=4M", "foo=1", "bar=2"]


def test_grub_value_none_without_an_active_line():
    assert grub_value('# GRUB_CMDLINE_LINUX="x"\nGRUB_TIMEOUT=0\n') is None


def test_add_text_appends_a_continuation_line_and_keeps_the_rest():
    out = add_grub_args_text(BLOCK, ("baz=3",))
    assert out == BLOCK.replace('bar=2 \\\n"', 'bar=2 \\\nbaz=3 \\\n"')


def test_add_text_replaces_a_changed_value_in_place():
    assert add_grub_args_text(BLOCK, ("foo=9",)) == BLOCK.replace("foo=1", "foo=9")


def test_add_text_no_change_when_present():
    assert add_grub_args_text(BLOCK, ("foo=1", "bar=2")) == BLOCK


def test_add_text_does_not_match_a_longer_key():
    out = add_grub_args_text(BLOCK.replace("foo=1", "xfoo=1"), ("foo=9",))
    assert "xfoo=1" in out and out.count("foo=9") == 1


def test_add_text_raises_without_an_assignment():
    with pytest.raises(ValueError):
        add_grub_args_text("GRUB_TIMEOUT=0\n", ("a=1",))

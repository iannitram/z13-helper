"""R28 review tests for issue #112: Expectation sentences the locked test does not cover.

Pure string checks only; no file, shell, or boot config is touched.
"""

import pytest

from post_update.cmdline import add_grub_args, missing_args


def test_add_grub_args_keeps_leading_whitespace():
    line = '   GRUB_CMDLINE_LINUX_DEFAULT="quiet"'
    assert add_grub_args(line, ("a=1",)) == '   GRUB_CMDLINE_LINUX_DEFAULT="quiet a=1"'


def test_add_grub_args_keeps_leading_whitespace_and_trailing_text_together():
    line = '\tGRUB_CMDLINE_LINUX_DEFAULT="quiet"  # mine'
    assert add_grub_args(line, ("a=1",)) == '\tGRUB_CMDLINE_LINUX_DEFAULT="quiet a=1"  # mine'


@pytest.mark.parametrize("line", ['# GRUB_CMDLINE_LINUX_DEFAULT="x"', '  # GRUB_CMDLINE_LINUX_DEFAULT="x"'])
def test_add_grub_args_rejects_commented_line_with_leading_whitespace(line):
    with pytest.raises(ValueError):
        add_grub_args(line, ("a=1",))


def test_add_grub_args_rejects_unquoted_assignment():
    with pytest.raises(ValueError):
        add_grub_args("GRUB_CMDLINE_LINUX_DEFAULT=quiet", ("a=1",))


def test_missing_args_duplicate_wanted_entries_are_reported_per_entry():
    assert missing_args("quiet", ("a=1", "a=1")) == ["a=1", "a=1"]
    assert missing_args("quiet a=1", ("a=1", "a=1")) == []


def test_add_grub_args_escaped_quote_stays_inside_the_value():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet \\"x y\\" splash" # c'
    assert add_grub_args(line, ("a=1",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet \\"x y\\" splash a=1" # c'


def test_add_grub_args_single_quoted_value_is_rejected_not_mangled():
    with pytest.raises(ValueError):
        add_grub_args("GRUB_CMDLINE_LINUX_DEFAULT='quiet'", ("a=1",))


def test_add_grub_args_bare_token_with_the_same_key_is_replaced_not_dropped():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet splash"'
    assert add_grub_args(line, ("quiet=1",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet=1 splash"'


def test_add_grub_args_key_that_is_a_prefix_of_another_key_is_not_matched():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.xy=1"'
    assert add_grub_args(line, ("amdgpu.x=2",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.xy=1 amdgpu.x=2"'
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.x=1"'
    assert add_grub_args(line, ("amdgpu.xy=2",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="amdgpu.x=1 amdgpu.xy=2"'


def test_add_grub_args_two_args_with_one_key_keep_the_last_and_stay_idempotent():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet"'
    once = add_grub_args(line, ("a=1", "a=2"))
    assert once == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet a=2"'
    assert add_grub_args(once, ("a=1", "a=2")) == once


def test_add_grub_args_hash_tab_and_line_ending():
    line = 'GRUB_CMDLINE_LINUX_DEFAULT="quiet\t# x"\r\n'
    assert add_grub_args(line, ("a=1",)) == 'GRUB_CMDLINE_LINUX_DEFAULT="quiet # x a=1"\r\n'


def test_add_grub_args_trailing_backslash_has_no_closing_quote_and_is_rejected():
    with pytest.raises(ValueError):
        add_grub_args('GRUB_CMDLINE_LINUX_DEFAULT="quiet ' + chr(92) + '"', ("a=1",))


@pytest.mark.parametrize("arg", ["", "a b=1", 'a"b=1', "a=" + chr(92), "a=$x", "a=`x`", "a=1\n"])
def test_add_grub_args_rejects_an_arg_that_would_break_the_quoted_value(arg):
    with pytest.raises(ValueError):
        add_grub_args('GRUB_CMDLINE_LINUX_DEFAULT="quiet"', (arg,))

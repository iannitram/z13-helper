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

"""install.sh --post-update / --only flag parsing (issue #68, part a)."""

import pytest

from post_update.cli import parse_args


def test_no_post_update_flags():
    assert parse_args(()) == {"post_update": False, "only": []}
    assert parse_args(("--repair",)) == {"post_update": False, "only": []}


def test_post_update_alone_means_pick_from_a_list():
    assert parse_args(("--post-update",)) == {"post_update": True, "only": []}


def test_only_splits_on_commas_in_order():
    assert parse_args(("--post-update", "--only", "amdgpu,rocm")) == {
        "post_update": True, "only": ["amdgpu", "rocm"]}


def test_only_trims_drops_empty_and_dedupes():
    assert parse_args(("--post-update", "--only", "a,, b ,a")) == {
        "post_update": True, "only": ["a", "b"]}


def test_flag_order_does_not_matter_and_other_flags_pass_through():
    assert parse_args(("--only", "x", "--dry-run", "--post-update")) == {
        "post_update": True, "only": ["x"]}


def test_only_without_post_update_is_an_error():
    with pytest.raises(ValueError, match="--only needs --post-update"):
        parse_args(("--only", "x"))


@pytest.mark.parametrize("argv", [("--post-update", "--only"),
                                  ("--post-update", "--only", "--dry-run")])
def test_only_without_a_value_is_an_error(argv):
    with pytest.raises(ValueError, match="--only needs a value"):
        parse_args(argv)

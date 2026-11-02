"""Review additions for post_update_state (R28): os-release quoting and comments."""

import pytest

import post_update_state as pus


@pytest.mark.parametrize("text, expected", [
    ("BUILD_ID='20261023.100'\n", "20261023.100"),
    ('BUILD_ID="20261023.100"\n', "20261023.100"),
    ("BUILD_ID=20261023.100\n", "20261023.100"),
    ("BUILD_ID=20261023.100\r\n", "20261023.100"),
])
def test_read_build_id_quoting_forms(text, expected):
    assert pus.read_build_id(text) == expected


def test_read_build_id_ignores_comment_line():
    assert pus.read_build_id("# BUILD_ID=5\nNAME=SteamOS\n") is None


def test_read_build_id_empty_single_quotes_is_none():
    assert pus.read_build_id("BUILD_ID=''\n") is None

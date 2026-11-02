"""SteamOS update detection and tab rows (issue #70, part a)."""

import pytest

import post_update_state as pus

OS_RELEASE = 'NAME="SteamOS"\nVERSION_ID=3.9.5\nBUILD_ID=20261023.100\nVARIANT_ID=steamdeck\n'


def test_read_build_id_unquoted_and_quoted():
    assert pus.read_build_id(OS_RELEASE) == "20261023.100"
    assert pus.read_build_id('BUILD_ID="20260922.1"\n') == "20260922.1"


@pytest.mark.parametrize("text", ["", "NAME=SteamOS\n", 'BUILD_ID=""\n', "XBUILD_ID=5\n"])
def test_read_build_id_missing(text):
    assert pus.read_build_id(text) is None


@pytest.mark.parametrize("recorded, current, expected", [
    ("20260922.1", "20261023.100", True),
    ("20261023.100", "20261023.100", False),
    (None, "20261023.100", False),
    ("20261023.100", None, False),
    ("", "20261023.100", False),
])
def test_needs_attention(recorded, current, expected):
    assert pus.needs_attention(recorded, current) is expected


def test_banner_plural_and_singular():
    assert pus.banner("a", "b", 3) == "SteamOS updated: 3 post-update tasks need attention"
    assert pus.banner("a", "b", 1) == "SteamOS updated: 1 post-update task needs attention"


def test_banner_none_when_unchanged_or_nothing_pending():
    assert pus.banner("a", "a", 3) is None
    assert pus.banner("a", "b", 0) is None
    assert pus.banner(None, "b", 2) is None


@pytest.mark.parametrize("check, reboot, applicable, expected", [
    ("ok", False, True, "fine"),
    ("needs", False, True, "needs"),
    ("needs", True, True, "reboot"),
    ("ok", True, True, "reboot"),
    ("info", False, True, "info"),
    ("info", True, True, "info"),
    ("na", False, True, "na"),
    ("needs", True, False, "na"),
])
def test_row_state(check, reboot, applicable, expected):
    assert pus.row_state(check, reboot, applicable) == expected

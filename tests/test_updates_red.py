"""Tests for issue #236: Updates panel shows newer installable upstream in red (SPEC M4).

No hardware needed: exercises the table cell styling in UpdatesDialog in both
dark and light themes offscreen.
"""

import os
import pytest

pytest.importorskip("PyQt6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

import design_tokens as tokens
import install_record
import theme
import updates_dialog


@pytest.fixture(autouse=True)
def _no_post_update_thread(monkeypatch):
    """Ensure PostUpdateTab doesn't launch a background host thread during dialog init."""
    monkeypatch.setattr(install_record, "repo_path", lambda rec=None: None)


@pytest.fixture
def qapp():
    return QApplication.instance() or QApplication([])


def _sample_rows():
    return [
        {
            "id": "z13ctl",
            "label": "z13ctl (hardware backend)",
            "installer": "z13ctl",
            "installed": "1.4.2",
            "latest": "1.4.3",
            "error": None,
            "new": True,
            "published": "2026-11-15",
            "note": "Hardware daemon",
            "url": "https://github.com/dahui/z13ctl/releases/tag/v1.4.3",
        },
        {
            "id": "decky",
            "label": "Decky Loader",
            "installer": "decky",
            "installed": "3.3.0",
            "latest": "3.3.0",
            "error": None,
            "new": False,
            "published": "2026-10-20",
            "note": "Plugin loader",
            "url": "https://github.com/SteamDeckHomebrew/decky-loader",
        },
        {
            "id": "steamos",
            "label": "SteamOS",
            "installer": "steamos",
            "installed": "3.6.20",
            "latest": None,
            "error": "Connection timed out",
            "new": False,
            "published": "",
            "note": "SteamOS update",
            "url": None,
        },
        {
            "id": "kernel",
            "label": "Linux kernel",
            "installer": None,
            "installed": "6.12.0",
            "latest": "6.13.0",
            "error": None,
            "new": True,
            "published": "2026-11-10",
            "note": "Reference only",
            "url": "https://kernel.org",
        },
    ]


def test_newer_installable_row_is_red_in_dark_and_light_themes(qapp, monkeypatch):
    theme.apply(qapp, "dark")
    dlg = updates_dialog.UpdatesDialog()
    rows = _sample_rows()
    dlg.populate(rows)

    dark_danger = tokens.colours("dark")["danger"].upper()
    light_danger = tokens.colours("light")["danger"].upper()

    # z13ctl is row 0 in "Installable from here" (self.table)
    # Check Latest cell (col 2) and row cells
    latest_cell = dlg.table.item(0, 2)
    assert latest_cell.text() == "1.4.3"
    assert latest_cell.data(theme._TONE_ROLE) == "danger"
    assert latest_cell.foreground().color().name().upper() == dark_danger

    for c in range(dlg.table.columnCount()):
        cell = dlg.table.item(0, c)
        assert cell.data(theme._TONE_ROLE) == "danger"
        assert cell.foreground().color().name().upper() == dark_danger

    # Switch to light theme and verify the colour changes to light danger red
    theme.apply(qapp, "light")
    assert latest_cell.data(theme._TONE_ROLE) == "danger"
    assert latest_cell.foreground().color().name().upper() == light_danger
    for c in range(dlg.table.columnCount()):
        cell = dlg.table.item(0, c)
        assert cell.foreground().color().name().upper() == light_danger

    # Keeps its link: double-clicking opens the url
    opened_urls = []
    monkeypatch.setattr(updates_dialog.QDesktopServices, "openUrl", lambda url: opened_urls.append(url.toString()))
    dlg._open(dlg.table, 0)
    assert opened_urls == ["https://github.com/dahui/z13ctl/releases/tag/v1.4.3"]


def test_up_to_date_and_check_failed_rows_are_not_red(qapp):
    theme.apply(qapp, "dark")
    dlg = updates_dialog.UpdatesDialog()
    rows = _sample_rows()
    dlg.populate(rows)

    dark_danger = tokens.colours("dark")["danger"].upper()

    # Decky (row 1 in self.table) is up to date (3.3.0 -> 3.3.0)
    decky_cell = dlg.table.item(1, 2)
    assert decky_cell.text() == "3.3.0"
    assert decky_cell.data(theme._TONE_ROLE) != "danger"
    assert decky_cell.foreground().color().name().upper() != dark_danger

    # SteamOS (row 2 in self.table) check failed
    steamos_cell = dlg.table.item(2, 2)
    assert steamos_cell.text() == "check failed"
    assert steamos_cell.data(theme._TONE_ROLE) != "danger"
    assert steamos_cell.foreground().color().name().upper() != dark_danger


def test_tracking_rows_are_not_red(qapp):
    theme.apply(qapp, "dark")
    dlg = updates_dialog.UpdatesDialog()
    rows = _sample_rows()
    dlg.populate(rows)

    dark_danger = tokens.colours("dark")["danger"].upper()

    # Kernel is in tracking table (self.track), not "Installable from here"
    kernel_cell = dlg.track.item(0, 2)
    assert kernel_cell.text() == "6.13.0"
    assert kernel_cell.data(theme._TONE_ROLE) != "danger"
    assert kernel_cell.foreground().color().name().upper() != dark_danger
    # New tracking row gets accent (blue), not danger
    assert kernel_cell.data(theme._TONE_ROLE) == "accent"


def test_newer_installable_remains_red_when_marked_seen(qapp):
    theme.apply(qapp, "dark")
    dlg = updates_dialog.UpdatesDialog()
    rows = _sample_rows()
    # Simulate marked seen: new is False, but latest > installed
    rows[0]["new"] = False
    dlg.populate(rows)

    dark_danger = tokens.colours("dark")["danger"].upper()
    latest_cell = dlg.table.item(0, 2)
    assert latest_cell.text() == "1.4.3"
    assert latest_cell.data(theme._TONE_ROLE) == "danger"
    assert latest_cell.foreground().color().name().upper() == dark_danger

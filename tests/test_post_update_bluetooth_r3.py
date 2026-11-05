"""Recipe 5, round 3: installed-host wiring for the MT7925 Bluetooth recipe (issue #71).

Locked test: the fix must not edit this file. It reads install_check.MANIFEST as data, calls
driver._needs_root and the recipe-list functions directly, and needs no PyQt6, hardware or
network. Run with HOME pointed at a temp dir.
"""

import os
from pathlib import Path

import pytest

import install_check as ic
from post_update import bluetooth, driver
from post_update.bluetooth import BT_FILES
from post_update.builtin import builtin_recipes

REPO = Path(__file__).resolve().parent.parent
BT_MODULE = "tooling/post_update/bluetooth.py"


def manifest_rows(rel):
    return [row for row in ic.MANIFEST if row[0] == rel]


# --- A2: the MANIFEST deploys the module and its four source files ---------------------

def test_bluetooth_module_is_in_the_manifest():
    rows = manifest_rows(BT_MODULE)
    assert rows, "tooling/post_update/bluetooth.py is missing from MANIFEST"
    _rel, dest, mode, root = rows[0]
    assert dest == f"{ic.LIB}/post_update/bluetooth.py"
    assert mode == "644"
    assert root is False


@pytest.mark.parametrize("src,_dest", BT_FILES)
def test_each_workaround_source_is_deployed_under_the_lib_folder(src, _dest):
    rows = manifest_rows(src)
    assert rows, f"{src} is missing from MANIFEST"
    _rel, dest, _mode, root = rows[0]
    assert dest == f"{ic.LIB}/{src}", "sources must mirror the repo layout under LIB"
    assert root is False


def test_installed_source_root_is_the_lib_folder(tmp_path, monkeypatch):
    lib = tmp_path / ".local" / "lib" / "z13-helper"
    for src, _dest in BT_FILES:
        (lib / src).parent.mkdir(parents=True, exist_ok=True)
        (lib / src).write_text("x\n", encoding="utf-8")
    (lib / "post_update").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(bluetooth, "__file__", str(lib / "post_update" / "bluetooth.py"))
    root = bluetooth.default_source_root()
    assert os.path.realpath(root) == os.path.realpath(str(lib))
    for src, _dest in BT_FILES:
        assert os.path.isfile(os.path.join(root, src)), src


def test_repo_source_root_is_the_checkout():
    root = bluetooth.default_source_root()
    assert os.path.realpath(root) == os.path.realpath(str(REPO))
    for src, _dest in BT_FILES:
        assert os.path.isfile(os.path.join(root, src)), src


# --- A4: user systemctl never escalates; system ones still do --------------------------

@pytest.mark.parametrize("argv", [
    ["systemctl", "--user", "daemon-reload"],
    ["systemctl", "--user", "enable", "z13-helper.service"],
    ["/usr/bin/systemctl", "--user", "restart", "z13-helper.service"],
])
def test_user_systemctl_is_not_escalated(argv):
    assert driver._needs_root(argv) is False


@pytest.mark.parametrize("argv", [
    ["systemctl", "daemon-reload"],
    ["systemctl", "enable", "bt-mt7925-cycle.service"],
    ["chmod", "755", "/etc/systemd/system/mt7925-bt-resume.sh"],
])
def test_system_systemctl_and_chmod_still_escalate(argv):
    assert driver._needs_root(argv) is True


# --- A1/A3/A5: recipe 5 is in the CLI list and in driver order ------------------------

def _cli_ids():
    import z13_helper_cli
    recipes, _errors = z13_helper_cli._all_recipes()
    return [r.id for r in recipes]


def test_cli_post_update_list_includes_recipe_5(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    ids = _cli_ids()
    assert "mt7925-bt" in ids
    assert ids.index("etc-snapshot") < ids.index("mt7925-bt") < ids.index("rocm")


def test_driver_order_puts_recipe_5_between_4_and_6(tmp_path):
    recipes, _errors = driver.collect(str(tmp_path), escalate=False)
    ids = [r.id for r in recipes]
    assert "mt7925-bt" in ids
    assert ids.index("etc-snapshot") < ids.index("mt7925-bt") < ids.index("rocm")


def test_cli_and_driver_list_the_built_ins_in_the_same_order(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    builtin_ids = {r.id for r in builtin_recipes(driver.make_hostio(False))}
    builtin_ids |= {"etc-snapshot", "mt7925-bt", "rocm"}
    driver_ids = [r.id for r in driver.collect(str(tmp_path), escalate=False)[0]]
    cli_ids = _cli_ids()
    assert [i for i in cli_ids if i in builtin_ids] == [i for i in driver_ids if i in builtin_ids]

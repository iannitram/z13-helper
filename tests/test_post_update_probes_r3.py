"""Recipe 7, round 3: installed-host wiring for the upstream probes (issue #71).

Locked test: the fix must not edit this file. It reads install_check.MANIFEST as data and
calls the recipe-list functions directly. No PyQt6, hardware or network. Run with HOME
pointed at a temp dir. Recipe 7 is info-only, so listing it never runs a probe.
"""

from pathlib import Path

import install_check as ic
from post_update import driver
from post_update.builtin import builtin_recipes

REPO = Path(__file__).resolve().parent.parent
PROBES_MODULE = "tooling/post_update/probes.py"


def test_probes_module_is_in_the_manifest():
    rows = [row for row in ic.MANIFEST if row[0] == PROBES_MODULE]
    assert rows, "tooling/post_update/probes.py is missing from MANIFEST"
    _rel, dest, mode, root = rows[0]
    assert dest == f"{ic.LIB}/post_update/probes.py"
    assert mode == "644"
    assert root is False


def _cli_ids():
    import z13_helper_cli
    recipes, _errors = z13_helper_cli._all_recipes()
    return [r.id for r in recipes]


def test_cli_post_update_list_includes_recipe_7(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    ids = _cli_ids()
    assert "upstream-probes" in ids
    assert ids.index("rocm") < ids.index("upstream-probes")


def test_driver_lists_recipe_7_after_recipe_6(tmp_path):
    recipes, _errors = driver.collect(str(tmp_path), escalate=False)
    ids = [r.id for r in recipes]
    assert "upstream-probes" in ids
    assert ids.index("rocm") < ids.index("upstream-probes")


def test_cli_and_driver_list_the_built_ins_in_the_same_order(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    builtin_ids = {r.id for r in builtin_recipes(driver.make_hostio(False))}
    builtin_ids |= {"etc-snapshot", "rocm", "upstream-probes"}
    driver_ids = [r.id for r in driver.collect(str(tmp_path), escalate=False)[0]]
    cli_ids = _cli_ids()
    assert [i for i in cli_ids if i in builtin_ids] == [i for i in driver_ids if i in builtin_ids]

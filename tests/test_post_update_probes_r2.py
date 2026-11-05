"""Recipe 7 (upstream probes), round 2: non-str inputs, collect() order, MAP row (issue #71)."""

from pathlib import Path

import pytest

from post_update import driver
from post_update.probes import (
    probe_kernel_version,
    probe_mes_firmware,
    probe_mt7925_bt,
    probe_ryzen_smu,
    probe_wmi_write_hang,
)
from post_update.recipe import Recipe

REPO = Path(__file__).resolve().parent.parent

TEXT_PROBES = [
    ("mes_fw", probe_mes_firmware),
    ("mt7925_bt", probe_mt7925_bt),
    ("kernel_version", probe_kernel_version),
    ("ryzen_smu", probe_ryzen_smu),
]

NON_TEXT_VALUES = [
    25,
    0,
    True,
    False,
    6.18,
    ["7.3.0-valve1-1-neptune"],
    b"MES fw version: 0x0000007f\n",
]


# 1. Every probe returns "unreadable" for a non-str input instead of raising.

@pytest.mark.parametrize("probe_id, probe", TEXT_PROBES)
@pytest.mark.parametrize("value", NON_TEXT_VALUES)
def test_text_probe_non_str_input_is_unreadable(probe_id, probe, value):
    result = probe(value)
    assert result.probe_id == probe_id
    assert result.fixed is False
    assert result.finding == "unreadable"


@pytest.mark.parametrize("value", NON_TEXT_VALUES)
def test_wmi_probe_non_str_readback_is_unreadable(value):
    result = probe_wmi_write_hang(40, 0, value, 25)
    assert result.probe_id == "wmi_hang"
    assert result.fixed is False
    assert result.finding == "unreadable"


def test_wmi_probe_bool_readback_does_not_raise():
    # True.strip() would raise AttributeError; a bool is not a read-back text.
    assert probe_wmi_write_hang(40, 0, True, 25).finding == "unreadable"


# 2. collect() runs built-in recipe 7 before the optional packs.

def _recipe(rid):
    return Recipe(id=rid, title=rid, fixes=rid, check=lambda: "ok", apply=lambda: None)


def test_upstream_probes_come_before_every_pack_in_collect(monkeypatch, tmp_path):
    monkeypatch.setattr(driver, "make_hostio", lambda escalate=True: None)
    monkeypatch.setattr(driver, "make_sys", lambda home, escalate=True: None)
    monkeypatch.setattr(driver, "builtin_recipes", lambda io: [_recipe("builtin-1")])
    monkeypatch.setattr(driver, "builtin_recipes_4_6", lambda sys_: [_recipe("builtin-4")])
    monkeypatch.setattr(driver, "pack_recipes", lambda io: [_recipe("pack-8"), _recipe("pack-9")])
    monkeypatch.setattr(driver, "load_local_recipes", lambda *a, **k: ([], []))

    recipes, errors = driver.collect(str(tmp_path), escalate=False)

    ids = [r.id for r in recipes]
    assert errors == []
    assert "upstream-probes" in ids
    probes_at = ids.index("upstream-probes")
    for pack_id in ("pack-8", "pack-9"):
        assert probes_at < ids.index(pack_id), f"upstream-probes must come before {pack_id}: {ids}"
    assert ids.index("builtin-4") < probes_at


# 3. MAP/README.md has a row for the probes module.

def test_map_readme_has_a_row_for_probes_module():
    text = (REPO / "MAP" / "README.md").read_text(encoding="utf-8")
    rows = [line for line in text.splitlines() if line.startswith("| `tooling/post_update/probes.py`")]
    assert len(rows) == 1

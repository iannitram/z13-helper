"""Qt-free helpers of the After SteamOS update tab (issue #70)."""

import json

import pytest

import post_update_view as v


def test_tab_argv_is_an_argv_list_with_the_repo_on_the_path():
    assert v.tab_argv("/home/x/repo") == ["env", "PYTHONPATH=/home/x/repo/tooling:/home/x/repo/src",
                                          "python3", "-m", "post_update", "--tab-json"]
    assert v.tab_argv("/r", ack=True)[-1] == "--ack"
    with pytest.raises(ValueError):
        v.tab_argv("relative")


def test_parse_status_skips_noise_and_rejects_junk():
    data = {"updated": True, "banner": "b", "rows": []}
    assert v.parse_status("warning\n" + json.dumps(data))["banner"] == "b"
    assert v.parse_status("") is None and v.parse_status("{bad") is None
    assert v.parse_status('{"rows": 3}') is None
    assert v.parse_status('{"rows": []}')["updated"] is False


def test_armoury_line():
    assert v.armoury_line(None) == ""
    assert "missing ppt_pl1_spl, fan attributes" in v.armoury_line(
        {"state": "needs", "missing": ["ppt_pl1_spl", "fan attributes"]})
    assert "present" in v.armoury_line({"state": "ok", "missing": []})


def test_row_cells_for_task_off_by_default_and_error_row():
    row = {"id": "x", "title": "X", "fixes": "f", "state": "needs", "default_on": False,
           "detail": "", "flags": ["sudo"]}
    assert v.row_cells(row) == ["X [off by default]", "Needs attention", "f (sudo)"]
    err = {"id": "", "title": "bad.toml", "fixes": "", "state": "error", "default_on": False,
           "detail": "invalid TOML", "flags": []}
    assert v.row_cells(err) == ["bad.toml", "Error", "invalid TOML"]

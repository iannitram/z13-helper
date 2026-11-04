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


def test_repair_argv_plan_and_run_and_bad_ids():
    from post_update_view import repair_argv
    base = ["env", "PYTHONPATH=/r/tooling:/r/src", "python3", "-m", "post_update", "repair", "--json"]
    assert repair_argv("/r") == base
    assert repair_argv("/r", ["kernel_args", "unit:user:a.service"]) == base + [
        "--run", "kernel_args,unit:user:a.service"]
    for bad in ([], ["a b"], ["a,b"], [""]):
        with pytest.raises(ValueError):
            repair_argv("/r", bad)
    with pytest.raises(ValueError):
        repair_argv("rel")


def test_plan_prompt_run_summary_tray_text_and_unknown_row():
    from post_update_view import parse_json, plan_prompt, row_cells, run_summary, tray_text
    plan = {"steps": [{"id": "kernel_args", "description": "add args", "needs_root": True, "guided": False,
                       "commands": ["sudo -n update-grub"], "undo": "copy back"}],
            "unplanned": ["made_up"]}
    text = plan_prompt(plan)
    assert "needs root (sudo -n)" in text and "$ sudo -n update-grub" in text and "undo: copy back" in text
    assert "made_up: no repair known" in text
    assert run_summary({"results": [["kernel_args", "applied"]]}) == "kernel_args: applied"
    assert tray_text({"banner": "SteamOS updated: 2 tasks"}) == "SteamOS updated: 2 tasks"
    assert tray_text({"banner": None}) is None and tray_text(None) is None
    assert parse_json('x {"steps": []}', "steps") == {"steps": []} and parse_json("{}", "steps") is None
    row = {"title": "keyring", "state": "unknown", "fixes": "fixes", "detail": "could not check: needs root",
           "default_on": True, "flags": []}
    assert row_cells(row) == ["keyring", "Could not check", "could not check: needs root"]

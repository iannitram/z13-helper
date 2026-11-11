"""Qt-free helpers of the After SteamOS update tab (issue #70)."""

import json

import pytest

import post_update_view as v


def _home(tmp_path, monkeypatch, complete):
    """Point DECK_HOME at a temp dir; LIB holds a package with (complete) or without __main__.py."""
    import host
    monkeypatch.setattr(host, "DECK_HOME", str(tmp_path))
    pkg = tmp_path / ".local" / "lib" / "z13-helper" / "post_update"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    if complete:
        (pkg / "__main__.py").write_text("")
    return v.host_lib()


def _run_launcher(argv, monkeypatch):
    """Execute the launcher code in-process with runpy stubbed; return (sys.argv, sys.path head)."""
    import os
    import runpy
    import sys
    monkeypatch.setattr(sys, "argv", ["-c"] + argv[3:])
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(os, "environ", dict(os.environ))
    seen = {}
    monkeypatch.setattr(runpy, "run_module", lambda name, run_name: seen.update(
        mod=name, argv=list(sys.argv), path=sys.path[:3]))
    exec(argv[2], {})
    assert seen["mod"] == "post_update"
    return seen["argv"], seen["path"]


def test_tab_argv_is_an_argv_list_with_the_repo_on_the_path(tmp_path, monkeypatch):
    lib = _home(tmp_path, monkeypatch, True)
    argv = v.tab_argv("/home/x/repo")
    assert argv[:2] == ["python3", "-c"] and argv[3:] == [lib, "/home/x/repo", "--tab-json"]
    assert v.tab_argv("/r", ack=True)[-1] == "--ack"
    with pytest.raises(ValueError):
        v.tab_argv("relative")


def test_launcher_puts_a_complete_library_first(tmp_path, monkeypatch):
    lib = _home(tmp_path, monkeypatch, True)
    repo = tmp_path / "repo"
    argv = v.tab_argv(str(repo))
    args, path = _run_launcher(argv, monkeypatch)
    assert args == ["post_update", "--tab-json"]
    assert path == [lib, f"{repo}/tooling", f"{repo}/src"]


def test_partial_installed_library_is_left_out_of_the_path(tmp_path, monkeypatch):
    """An install from before this change has post_update/__init__.py but no __main__.py: it would
    shadow the checkout's package, so only the checkout goes on the path."""
    lib = _home(tmp_path, monkeypatch, False)
    repo = tmp_path / "repo"
    args, path = _run_launcher(v.repair_argv(str(repo)), monkeypatch)
    assert args == ["post_update", "repair", "--json"]
    assert path[:2] == [f"{repo}/tooling", f"{repo}/src"] and lib not in path


def test_flatpak_mode_decides_on_the_host_not_in_the_sandbox(tmp_path, monkeypatch):
    """In the Flatpak the sandbox has no ~/.local/lib: the argv must not depend on a local probe,
    so it is identical whether or not the library exists here, and host.run sends it to the host."""
    import host
    monkeypatch.setattr(host, "MODE", "flatpak")
    monkeypatch.setattr(host, "DECK_HOME", str(tmp_path / "absent"))
    argv = v.tab_argv("/r")
    assert argv[3:] == [v.host_lib(), "/r", "--tab-json"]
    assert host.command(argv, mode="flatpak")[0][:2] == ["flatpak-spawn", "--host"]
    assert "-c" in host.command(argv, mode="flatpak")[0]


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


def test_repair_argv_plan_and_run_and_bad_ids(tmp_path, monkeypatch):
    from post_update_view import repair_argv
    lib = _home(tmp_path, monkeypatch, True)
    base = ["python3", "-c", v._LAUNCHER, lib, "/r", "repair", "--json"]
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

"""src/install_record.py: what the Updates dialog reads from install.json."""

import json

import install_record as ir


def test_load_missing_and_bad(tmp_path):
    assert ir.load(str(tmp_path / "nope.json")) is None
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    assert ir.load(str(bad)) is None
    bad.write_text("[1, 2]")
    assert ir.load(str(bad)) is None


def test_repo_path_validation():
    assert ir.repo_path({"repo_path": "/home/deck/dev/z13-helper"}) == "/home/deck/dev/z13-helper"
    for bad in (None, {}, {"repo_path": "relative/dir"}, {"repo_path": 3}, {"repo_path": "/a\nb"}):
        assert ir.repo_path(bad) is None


def test_last_run_line():
    rec = {"date": "2026-10-11T21:40:05+0200", "mode": "repair", "verify": "green"}
    assert ir.last_run_line(rec) == "Last install/repair: 2026-10-11 21:40 (repair, green)"
    assert "none recorded" in ir.last_run_line(None)
    assert "(install, red)" in ir.last_run_line({"date": "yesterday", "mode": "install", "verify": "red"})


def test_repair_argv_is_not_a_shell_string():
    repo = "/home/deck/dev/z13 controller; rm -rf ~"
    argv = ir.repair_argv(repo, in_flatpak=True)
    assert argv == ["flatpak-spawn", "--host", "bash", repo + "/install.sh", "--repair"]
    assert ir.repair_argv("/r", in_flatpak=False) == ["bash", "/r/install.sh", "--repair"]


def test_plan_mentions_repo_and_restart():
    t = ir.plan_text("/home/deck/dev/z13-helper")
    assert "./install.sh --repair from /home/deck/dev/z13-helper" in t
    assert "restarts the app" in t and "log" in t


def test_verdict_reads_install_sh_last_line():
    assert ir.verdict(["…", "\x1b[32mAll green.\x1b[0m"], 0) == (True, "All green.")
    green, text = ir.verdict(["\x1b[31mNot all green — see above.\x1b[0m"], 1)
    assert not green and text.startswith("Not all green")
    assert ir.verdict([], -9) == (False, "Failed (exit -9)")
    assert ir.verdict(["x"], 0) == (True, "Finished (exit 0)")


def test_clean_strips_colours():
    assert ir.clean("  \x1b[33mwarn:\x1b[0m x") == "  warn: x"


def test_record_keys_written_by_install_sh_are_read(tmp_path):
    """The keys install.sh's record() writes are the ones this module reads."""
    import re
    from pathlib import Path
    sh = (Path(__file__).resolve().parent.parent / "install.sh").read_text()
    for key in ("date", "mode", "verify", "repo_path"):
        assert re.search(rf'"{key}":', sh), key
    p = tmp_path / "install.json"
    p.write_text(json.dumps({"date": "2026-10-11T10:00:00+0000", "mode": "install",
                             "verify": "green", "repo_path": "/x", "custom": 1}))
    rec = ir.load(str(p))
    assert ir.repo_path(rec) == "/x" and rec["custom"] == 1


# ----------------------------------------------------------------------
# the logging choice (ROADMAP 0c)
# ----------------------------------------------------------------------

def test_logging_choice_on_off_missing(tmp_path):
    p = tmp_path / "install.json"
    assert ir.logging_enabled(str(p)) is True                     # no record: on
    p.write_text(json.dumps({"mode": "repair"}))
    assert ir.logging_enabled(str(p)) is True                     # record without the key: on
    p.write_text(json.dumps({"logging": True}))
    assert ir.logging_enabled(str(p)) is True
    p.write_text(json.dumps({"logging": False}))
    assert ir.logging_enabled(str(p)) is False
    p.write_text("{broken")
    assert ir.logging_enabled(str(p)) is True


def test_set_logging_keeps_other_keys(tmp_path):
    p = tmp_path / "cfg" / "install.json"
    ir.set_logging(False, str(p))                                 # creates the folder + file
    assert json.loads(p.read_text()) == {"logging": False}
    p.write_text(json.dumps({"mode": "install", "repo_path": "/x", "logging": False}))
    ir.set_logging(True, str(p))
    assert json.loads(p.read_text()) == {"mode": "install", "repo_path": "/x", "logging": True}
    assert [f.name for f in p.parent.iterdir()] == ["install.json"]   # no temp files left


def test_default_path_is_redirected_by_conftest(tmp_path):
    assert ir.INSTALL_JSON.startswith(str(tmp_path))
    ir.set_logging(False)
    assert ir.logging_enabled() is False


def test_install_sh_records_the_logging_key():
    from pathlib import Path
    sh = (Path(__file__).resolve().parent.parent / "install.sh").read_text()
    assert 'rec["logging"] = logging == "true"' in sh

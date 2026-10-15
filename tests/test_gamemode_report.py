"""tooling/z13_gamemode_report.py: the Game Mode session report.

Journals come from fake_host; Steam's logs, the fan trace and screenshots are
files under tmp_path. Nothing reads the real ~/.local or /run/user.
"""

import io
import json
import os
import time

import pytest

import z13_gamemode_report as gr


def at(stamp):
    """Local "YYYY-mm-dd HH:MM:SS" → epoch, the way the report reads Steam's logs."""
    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def jrow(stamp, msg):
    return f"{at(stamp):.6f} steamdeck {msg}"


START = "2026-10-15 11:00:00"
STOP = "2026-10-15 12:00:00"
SESSION = gr.Session(at(START), at(STOP))


@pytest.fixture
def env(tmp_path, monkeypatch):
    steam = tmp_path / "Steam"
    (steam / "logs").mkdir(parents=True)
    (steam / "steamapps").mkdir()
    (steam / "steamapps" / "appmanifest_2075800.acf").write_text(
        '"AppState"\n{\n\t"appid"\t\t"2075800"\n\t"name"\t\t"Zero Company"\n}\n')
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(gr, "STEAM", str(steam))
    monkeypatch.setattr(gr, "FAN_TRACE", str(state / "fan-trace-gamemode.log"))
    monkeypatch.setattr(gr, "SCREEN_DIR", str(state / "screenshots"))
    monkeypatch.setattr(gr, "INSTALL_JSON", str(tmp_path / "install.json"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir()
    monkeypatch.setattr(gr, "running_appids", lambda proc="/proc": {})
    monkeypatch.setattr(gr, "gamescope_running", lambda proc="/proc": False)
    return tmp_path


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------

def test_sessions_pair_start_and_stop():
    rows = [(10.0, "systemd[2234]: Started Gamescope Session."),
            (20.0, "systemd[2234]: Stopping Gamescope Session..."),
            (21.0, "systemd[2234]: Stopped Gamescope Session."),
            (30.0, "systemd[2300]: Started Gamescope Session.")]
    assert gr.parse_sessions(rows) == [gr.Session(10.0, 20.0), gr.Session(30.0, None)]


def test_session_without_stop_is_closed_by_the_next_start():
    rows = [(10.0, "systemd[1]: Started Gamescope Session."),
            (50.0, "systemd[2]: Started Gamescope Session.")]
    assert gr.parse_sessions(rows)[0] == gr.Session(10.0, 50.0)


def test_find_sessions_asks_the_user_journal(fake_host):
    fake_host.fail("--grep", rc=0, out=jrow(START, "systemd[9]: Started Gamescope Session."))
    assert gr.find_sessions(3) == [gr.Session(at(START), None)]
    argv = fake_host.argvs[0]
    assert argv[:2] == ["journalctl", "--no-pager"]
    assert "--user" in argv and "-3d" in argv


# ----------------------------------------------------------------------
# Games
# ----------------------------------------------------------------------

def test_game_run_spans_first_to_last_tracked_pid():
    lines = ["[2026-10-15 11:10:00] AppID 7 adding PID 1 as a tracked process",
             "[2026-10-15 11:10:05] AppID 7 adding PID 2 as a tracked process",
             "[2026-10-15 11:40:00] AppID 7 no longer tracking PID 1, exit code 0",
             "[2026-10-15 11:40:01] AppID 7 no longer tracking PID 2, exit code 0",
             "[2026-10-15 11:45:00] AppID 7 adding PID 3 as a tracked process"]
    assert gr.parse_game_runs(lines) == [
        gr.GameRun("7", at("2026-10-15 11:10:00"), at("2026-10-15 11:40:01")),
        gr.GameRun("7", at("2026-10-15 11:45:00"), None)]


def test_steam_restart_closes_runs_whose_exit_wasnt_logged():
    lines = ["[2026-10-01 10:00:00] AppID 7 adding PID 1 as a tracked process",
             "[2026-10-15 06:45:01] Client version: 1791918634"]
    assert gr.parse_game_runs(lines) == [
        gr.GameRun("7", at("2026-10-01 10:00:00"), at("2026-10-15 06:45:01"))]


def test_games_in_keeps_only_runs_overlapping_the_session(env):
    (env / "Steam" / "logs" / "gameprocess_log.txt").write_text("\n".join([
        "[2026-10-14 09:00:00] AppID 1 adding PID 1 as a tracked process",
        "[2026-10-14 09:30:00] AppID 1 no longer tracking PID 1, exit code 0",
        "[2026-10-15 11:05:00] AppID 2075800 adding PID 5 as a tracked process",
        "[2026-10-15 11:50:00] AppID 2075800 no longer tracking PID 5, exit code 0"]))
    runs = gr.games_in(SESSION, at(STOP))
    assert [r.appid for r in runs] == ["2075800"]


def test_app_name_from_manifest_or_shortcut(env):
    assert gr.app_name("2075800") == "Zero Company"
    assert gr.app_name("13985734359986995200") == "non-Steam shortcut"
    assert gr.app_name("42") == "?"


def test_running_appids_reads_proc_environ(tmp_path):
    for pid, comm, envs in [("10", "game.exe", b"A=1\0SteamAppId=2075800\0"),
                            ("11", "steam", b"SteamAppId=769\0"),
                            ("12", "bash", b"HOME=/x\0")]:
        d = tmp_path / pid
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "environ").write_bytes(envs)
    assert gr.running_appids(str(tmp_path)) == {"2075800": ["game.exe"]}


def test_gamescope_running(tmp_path):
    (tmp_path / "5").mkdir()
    (tmp_path / "5" / "comm").write_text("bash\n")
    assert not gr.gamescope_running(str(tmp_path))
    (tmp_path / "6").mkdir()
    (tmp_path / "6" / "comm").write_text("gamescope-wl\n")
    assert gr.gamescope_running(str(tmp_path))


# ----------------------------------------------------------------------
# Decky, errors, fan trace
# ----------------------------------------------------------------------

def test_decky_lines_follow_the_backend_process():
    rows = [(1, "PluginLoader[100]: [loader][INFO]: Loaded Z13-Helper (v0.4.0)"),
            (2, "PluginLoader[200]: z13-helper up. z13ctl=/x exists=True"),
            (3, "PluginLoader[200]: Fan watchdog: Max Fans re-applied"),
            (4, "PluginLoader[300]: Tailscale says hi"),
            (5, "PluginLoader[100]: [loader][INFO]: Loaded Tailscale Control")]
    assert [t for t, _ in gr.decky_lines(rows)] == [1, 2, 3]


def test_decky_lines_know_the_old_plugin_name():
    rows = [(1, "PluginLoader[2846]: z13-decky up. z13ctl=/x"),
            (2, "PluginLoader[2846]: Performance profile set to balanced")]
    assert len(gr.decky_lines(rows)) == 2


def test_errors_collapse_repeats_across_pids():
    rows = [(1, "gamescope-session[11]: drmModeRmFB failed"),
            (2, "gamescope-session[12]: drmModeRmFB failed"),
            (3, "gamescope-session[11]: other")]
    assert gr.summarize_errors(rows) == [[2, 1, "gamescope-session: drmModeRmFB failed"],
                                         [1, 3, "gamescope-session: other"]]


def test_trace_in_window_events_and_stats(tmp_path):
    p = tmp_path / "trace.log"
    p.write_text("\n".join([
        "2026-10-15 10:59:00.0 temp=99.0 rpm=1/1 prof=quiet",          # before: ignored
        "2026-10-15 11:00:01.0 --- fan trace started (gamemode, pid 7)",
        "2026-10-15 11:01:00.5 temp=50.0 rpm=3700/7600 acpi=1 curve=custom prof=performance",
        "< 2026-10-15 11:02:00.0 temp=60.0 rpm=0/- acpi=1 curve=custom prof=performance",
        "2026-10-15 11:02:01.0 *** ACT dropped curve with Max Fans on",
        "2026-10-15 12:30:00.0 temp=10.0 rpm=1/1 prof=quiet"]))        # after: ignored
    events, st = gr.trace_in(SESSION, at(STOP), str(p))
    assert [b for _, b in events] == ["--- fan trace started (gamemode, pid 7)",
                                      "*** ACT dropped curve with Max Fans on"]
    assert st == {"samples": 2, "temp": (50.0, 60.0), "rpm": (0, 7600),
                  "profiles": {"performance": 2}}


def test_logging_enabled_mirrors_install_record(tmp_path):
    p = tmp_path / "install.json"
    assert gr.logging_enabled(str(p))                      # missing = on
    p.write_text(json.dumps({"logging": False}))
    assert not gr.logging_enabled(str(p))
    p.write_text(json.dumps({"logging": True}))
    assert gr.logging_enabled(str(p))
    p.write_text("not json")
    assert gr.logging_enabled(str(p))


# ----------------------------------------------------------------------
# The whole report
# ----------------------------------------------------------------------

def _script_journals(fake_host):
    fake_host.fail("--grep", rc=0, out="\n".join([
        jrow(START, "systemd[9]: Started Gamescope Session."),
        jrow(STOP, "systemd[9]: Stopped Gamescope Session.")]))
    fake_host.fail("plugin_loader", rc=0, out="\n".join([
        jrow("2026-10-15 11:00:02", "PluginLoader[200]: z13-helper up. z13ctl=/x"),
        jrow("2026-10-15 11:00:08", "PluginLoader[200]: Fan watchdog: Max Fans re-applied")]))
    fake_host.fail("-p err", rc=0, out=jrow(
        "2026-10-15 11:30:00", "gamescope-session[11]: [gamescope] [Error] drm: boom"))


def test_report_covers_every_section(env, fake_host):
    _script_journals(fake_host)
    (env / "Steam" / "logs" / "gameprocess_log.txt").write_text("\n".join([
        "[2026-10-15 11:05:00] AppID 2075800 adding PID 5 as a tracked process",
        "[2026-10-15 11:50:00] AppID 2075800 no longer tracking PID 5, exit code 0"]))
    shots = env / "Steam" / "userdata" / "1" / "760" / "remote" / "2075800" / "screenshots"
    shots.mkdir(parents=True)
    (shots / "a.jpg").write_bytes(b"x")
    os.utime(shots / "a.jpg", (at("2026-10-15 11:20:00"),) * 2)
    open(gr.FAN_TRACE, "w").write(
        "2026-10-15 11:00:08.0 *** RESULT Max Fans re-applied\n"
        "2026-10-15 11:01:00.0 temp=55.0 rpm=8700/8700 prof=performance\n")

    out = io.StringIO()
    assert gr.main([], out=out) == 0
    text = out.getvalue()
    assert "ended 2026-10-15 12:00:00" in text and "(60m00s)" not in text and "1h00m" in text
    assert "Zero Company [2075800]" in text and "(45m00s)" in text
    assert "Fan watchdog: Max Fans re-applied" in text
    assert "*** RESULT Max Fans re-applied" in text and "watchdog acted 1 time(s)" in text
    assert "temp 55.0-55.0 C" in text
    assert "drm: boom" in text
    assert str(shots / "a.jpg") in text
    err_call = next(c for c in fake_host.argvs if "-p" in c)
    assert "gamescope-session.service" in err_call and "z13*" in err_call
    assert f"@{int(at(START))}" in err_call


def test_report_skips_the_fan_trace_when_logging_is_off(env, fake_host):
    _script_journals(fake_host)
    open(gr.INSTALL_JSON, "w").write(json.dumps({"logging": False}))
    open(gr.FAN_TRACE, "w").write("2026-10-15 11:00:08.0 *** RESULT secret\n")
    out = io.StringIO()
    gr.main([], out=out)
    assert "logging is off" in out.getvalue() and "secret" not in out.getvalue()


def test_no_sessions(env, fake_host):
    out = io.StringIO()
    assert gr.main(["--days", "2"], out=out) == 0
    assert "No Game Mode session in the last 2 days" in out.getvalue()


# ----------------------------------------------------------------------
# Screenshot
# ----------------------------------------------------------------------

def test_screenshot_needs_game_mode(env, fake_host):
    ok, msg = gr.screenshot(str(env / "s.png"))
    assert not ok and "Game Mode isn't running" in msg
    assert fake_host.calls == []


def test_screenshot_needs_a_path_when_logging_is_off(env, fake_host):
    ok, msg = gr.screenshot(None, logging_on=False)
    assert not ok and "logging is off" in msg


def test_screenshot_asks_gamescope_and_waits_for_the_file(env, fake_host):
    (env / "run" / "gamescope-0").write_text("")
    (env / "run" / "gamescope-0.lock").write_text("")
    target = env / "s.png"

    def gamescopectl(argv):
        if "gamescopectl" in argv:
            target.write_bytes(b"\x89PNG")
            return True
        return False
    fake_host.fail(gamescopectl, rc=0, out=f"Screenshot taken to: {target}")
    ok, path = gr.screenshot(str(target), wait=1)
    assert ok and path == str(target)
    assert fake_host.argvs == [["env", f"XDG_RUNTIME_DIR={env / 'run'}",
                                "GAMESCOPE_WAYLAND_DISPLAY=gamescope-0",
                                "gamescopectl", "screenshot", str(target)]]


def test_screenshot_default_path_is_under_state(env, fake_host):
    (env / "run" / "gamescope-0").write_text("")
    fake_host.fail("gamescopectl", rc=1, out="Failed to open GAMESCOPE_WAYLAND_DISPLAY.")
    ok, msg = gr.screenshot(None, wait=0)
    assert not ok and "rc 1" in msg
    assert fake_host.argvs[0][-1].startswith(gr.SCREEN_DIR + "/gamemode-")

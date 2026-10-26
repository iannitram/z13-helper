#!/usr/bin/env python3
"""z13-gamemode-report: what happened in Game Mode, read back from a shell.

Nobody can watch Game Mode from Desktop Mode or SSH, so this pieces a session
together from what it leaves on disk:

- when it ran:     `journalctl --user` (gamescope-session.service start/stop)
- which games:     Steam's logs/gameprocess_log.txt (tracked PIDs per AppID),
                   names from steamapps/appmanifest_*.acf
- Decky actions:   `journalctl -u plugin_loader`, lines from the Z13-Helper backend
- watchdog events: Z13-Helper's fan-trace-gamemode.log ("***" event lines)
- errors:          `journalctl --user -p err` in the session's window
- screenshots:     Steam screenshots and gamescope's Super+S captures taken then

Read-only and local: it reads files and journals and prints a report. The one
thing it can write is a screenshot, and only when asked (`--screenshot`): that
asks the running gamescope for a PNG of what's on screen. Z13-Helper's logging
opt-in (install.json "logging": false) is respected: the fan trace isn't read
and `--screenshot` needs an explicit path.

Installed as ~/.local/bin/z13-gamemode-report (tooling/install_check.py's MANIFEST).

    z13-gamemode-report                 # last (or current) Game Mode session
    z13-gamemode-report --last 3        # the last three
    z13-gamemode-report --screenshot    # live: PNG of the screen, prints its path
"""

import argparse
import collections
import glob
import json
import os
import re
import sys
import time

# Installed as ~/.local/bin/z13-gamemode-report, the host seam sits in
# ~/.local/lib/z13-helper/ (install.sh puts it there); from the repo it's in src/.
for _d in (os.path.expanduser("~/.local/lib/z13-helper"),
           os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src")):
    if os.path.isfile(os.path.join(_d, "host.py")) and _d not in sys.path:
        sys.path.append(_d)
import host  # noqa: E402

HOME = os.path.expanduser("~")
STATE_DIR = os.path.join(HOME, ".local", "state", "z13-helper")
FAN_TRACE = os.path.join(STATE_DIR, "fan-trace-gamemode.log")
SCREEN_DIR = os.path.join(STATE_DIR, "screenshots")
INSTALL_JSON = os.path.join(HOME, ".config", "z13-helper", "install.json")
STEAM = os.path.join(HOME, ".local", "share", "Steam")

SCAN_DAYS = 14          # how far back to look for sessions
MAX_LINES = 25          # per section, so the report stays readable
# The user units whose errors matter here: the session itself (gamescope and Steam
# log through it) and ours. Other user services (anything else the machine runs)
# stay out of the report.
ERROR_UNITS = ["-u", "gamescope-session.service", "-u", "z13*"]

Session = collections.namedtuple("Session", "start end")    # epoch floats; end None = open
GameRun = collections.namedtuple("GameRun", "appid start end")


# ----------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------

def logging_enabled(path=None):
    """Mirror of src/install_record.logging_enabled(): on unless "logging": false."""
    try:
        with open(path or INSTALL_JSON) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return True
    return not (isinstance(rec, dict) and rec.get("logging") is False)


def fmt(t):
    return "-" if t is None else time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def dur(a, b):
    s = int(max(0, b - a))
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def read_lines(path):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()
    except OSError:
        return []


def journal(args):
    """`journalctl ... -o short-unix` → [(epoch, "ident[pid]: message")]."""
    rc, out, _ = host.run(["journalctl", "--no-pager", "-o", "short-unix", *args], timeout=30)
    rows = []
    for line in out.splitlines():
        parts = line.split(" ", 2)      # epoch, hostname, rest
        if len(parts) < 3:
            continue
        try:
            rows.append((float(parts[0]), parts[2]))
        except ValueError:
            continue
    return rows


def window_args(s, now):
    end = s.end if s.end is not None else now
    return ["--since", f"@{int(s.start)}", "--until", f"@{int(end) + 1}"]


# ----------------------------------------------------------------------
# Sessions
# ----------------------------------------------------------------------

_START = re.compile(r"systemd\[\d+\]: Started Gamescope Session\.?$")
_STOP = re.compile(r"systemd\[\d+\]: (Stopped|Stopping) Gamescope Session|"
                   r"systemd\[\d+\]: gamescope-session\.service: (Failed|Main process exited)")


def parse_sessions(rows):
    """Journal rows → [Session], oldest first. A session with no stop line is
    closed by the next start (crash, power-off) or left open (still running)."""
    out = []
    for t, msg in rows:
        if _START.search(msg):
            if out and out[-1].end is None:
                out[-1] = out[-1]._replace(end=t)
            out.append(Session(t, None))
        elif _STOP.search(msg) and out and out[-1].end is None:
            out[-1] = out[-1]._replace(end=t)
    return out


def find_sessions(days=SCAN_DAYS):
    rows = journal(["--user", "--since", f"-{days}d", "-u", "gamescope-session.service",
                    "--grep", "Gamescope Session|gamescope-session.service"])
    return parse_sessions(rows)


def gamescope_running(proc="/proc"):
    for comm in glob.glob(os.path.join(proc, "[0-9]*", "comm")):
        try:
            with open(comm) as f:
                name = f.read().strip()
        except OSError:
            continue
        if name in ("gamescope", "gamescope-wl"):
            return True
    return False


# ----------------------------------------------------------------------
# Games
# ----------------------------------------------------------------------

_STEAM_TS = re.compile(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\] (.*)$")
_ADD = re.compile(r"AppID (\d+) adding PID (\d+)")
_DROP = re.compile(r"AppID (\d+) no longer tracking PID (\d+)")
_CLIENT = re.compile(r"^Client version: ")     # Steam (re)started: it tracks nothing yet


def steam_time(stamp):
    return time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S"))


def parse_game_runs(lines):
    """gameprocess_log lines → [GameRun]: a run starts at an AppID's first tracked
    PID and ends when its last one goes, or when Steam restarts (end = that time,
    the exit itself wasn't logged). end None = still running."""
    pids, open_runs, runs = collections.defaultdict(set), {}, []
    for line in lines:
        m = _STEAM_TS.match(line)
        if not m:
            continue
        t, msg = steam_time(m.group(1)), m.group(2)
        if _CLIENT.match(msg):
            for i in open_runs.values():
                runs[i] = runs[i]._replace(end=t)
            open_runs.clear()
            pids.clear()
        elif a := _ADD.search(msg):
            app = a.group(1)
            if not pids[app]:
                open_runs[app] = len(runs)
                runs.append(GameRun(app, t, None))
            pids[app].add(a.group(2))
        elif d := _DROP.search(msg):
            app = d.group(1)
            pids[app].discard(d.group(2))
            if not pids[app] and app in open_runs:
                i = open_runs.pop(app)
                runs[i] = runs[i]._replace(end=t)
    return runs


def library_dirs(steam=None):
    steam = steam or STEAM
    dirs = [steam]
    for line in read_lines(os.path.join(steam, "steamapps", "libraryfolders.vdf")):
        m = re.match(r'\s*"path"\s+"(.+)"', line)
        if m and m.group(1) not in dirs:
            dirs.append(m.group(1))
    return dirs


def app_name(appid, steam=None):
    for lib in library_dirs(steam):
        for line in read_lines(os.path.join(lib, "steamapps", f"appmanifest_{appid}.acf")):
            m = re.match(r'\s*"name"\s+"(.+)"', line)
            if m:
                return m.group(1)
    return "non-Steam shortcut" if int(appid) > 0xFFFFFFF else "?"


def games_in(s, now, steam=None):
    lines = read_lines(os.path.join(steam or STEAM, "logs", "gameprocess_log.txt"))
    end = s.end if s.end is not None else now
    return [r for r in parse_game_runs(lines)
            if r.start <= end and (r.end is None or r.end >= s.start)]


def running_appids(proc="/proc"):
    """{appid: [comm, ...]} from processes whose environment has SteamAppId."""
    found = collections.defaultdict(set)
    for env in glob.glob(os.path.join(proc, "[0-9]*", "environ")):
        try:
            with open(env, "rb") as f:
                data = f.read()
            with open(os.path.join(os.path.dirname(env), "comm")) as f:
                comm = f.read().strip()
        except OSError:
            continue
        for kv in data.split(b"\0"):
            if kv.startswith(b"SteamAppId="):
                app = kv.split(b"=", 1)[1].decode(errors="replace")
                if app not in ("", "0", "769"):     # 769: the Steam client itself
                    found[app].add(comm)
    return {k: sorted(v) for k, v in found.items()}


# ----------------------------------------------------------------------
# Decky, errors, fan trace, screenshots
# ----------------------------------------------------------------------

_PID = re.compile(r"^\S+\[(\d+)\]: ")
# Before the 2026-10-15 rename the plugin was z13-decky ("Z13 Controller").
_OURS = re.compile(r"z13-helper|Z13-Helper|z13-decky|Z13 Controller")


def decky_lines(rows):
    """plugin_loader rows → the Z13-Helper backend's lines: anything naming it, plus
    everything its own process printed (its logger lines don't name the plugin)."""
    pids = {m.group(1) for _, msg in rows
            if _OURS.search(msg) and (m := _PID.match(msg))
            and "[loader]" not in msg and "[plugin]" not in msg}
    return [(t, msg) for t, msg in rows
            if _OURS.search(msg) or ((m := _PID.match(msg)) and m.group(1) in pids)]


def summarize_errors(rows, limit=MAX_LINES):
    """Collapse repeats: [(count, first_time, message)] most frequent first."""
    seen = {}
    for t, msg in rows:
        key = re.sub(r"\[\d+\]", "", msg)
        if key in seen:
            seen[key][0] += 1
        else:
            seen[key] = [1, t, key]
    return sorted(seen.values(), key=lambda e: (-e[0], e[1]))[:limit]


_TRACE_TS = re.compile(r"^(?:< )?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d)? (.*)$")
_TEMP = re.compile(r"temp=([\d.]+)")
_RPM = re.compile(r"rpm=(\d+|-)/(\d+|-)")
_PROF = re.compile(r"prof=(\S+)")


def trace_in(s, now, path=None):
    """Fan-trace lines in the window → (events, stats). Events are the "***"
    (watchdog acted) and "---" (trace started) lines."""
    end = s.end if s.end is not None else now
    events, temps, rpms, profs, n = [], [], [], collections.Counter(), 0
    for line in read_lines(path or FAN_TRACE):
        m = _TRACE_TS.match(line)
        if not m:
            continue
        t = steam_time(m.group(1))
        if not s.start <= t <= end + 1:
            continue
        body = m.group(2)
        if body.startswith(("***", "---")):
            events.append((t, body))
            continue
        n += 1
        if tm := _TEMP.search(body):
            temps.append(float(tm.group(1)))
        if r := _RPM.search(body):
            rpms += [int(x) for x in r.groups() if x != "-"]
        if p := _PROF.search(body):
            profs[p.group(1)] += 1
    stats = {"samples": n,
             "temp": (min(temps), max(temps)) if temps else None,
             "rpm": (min(rpms), max(rpms)) if rpms else None,
             "profiles": dict(profs)}
    return events, stats


def screenshots_in(s, now, steam=None):
    steam = steam or STEAM
    end = s.end if s.end is not None else now
    pats = [os.path.join(steam, "userdata", "*", "760", "remote", "*", "screenshots", "*.jpg"),
            "/tmp/gamescope_*.avif", "/tmp/gamescope_*.png", os.path.join(SCREEN_DIR, "*.png")]
    hits = []
    for pat in pats:
        for p in glob.glob(pat):
            try:
                t = os.path.getmtime(p)
            except OSError:
                continue
            if s.start <= t <= end + 1:
                hits.append((t, p))
    return sorted(hits)


# ----------------------------------------------------------------------
# Screenshot (live)
# ----------------------------------------------------------------------

def runtime_dir():
    return os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"


def gamescope_display(rt=None):
    rt = rt or runtime_dir()
    socks = sorted(p for p in glob.glob(os.path.join(rt, "gamescope-*"))
                   if not p.endswith(".lock"))
    return os.path.basename(socks[0]) if socks else None


def screenshot(path=None, wait=5.0, logging_on=True):
    """Ask gamescope for a PNG of the screen. Returns (ok, path_or_error)."""
    if path is None:
        if not logging_on:
            return False, ("logging is off (install.json), so there's no default folder; "
                           "pass a path: --screenshot /path/to/shot.png")
        path = os.path.join(SCREEN_DIR, time.strftime("gamemode-%Y%m%d-%H%M%S.png"))
    path = os.path.abspath(os.path.expanduser(path))
    rt = runtime_dir()
    display = gamescope_display(rt)
    if not display:
        return False, f"no gamescope socket in {rt}: Game Mode isn't running"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    before = os.path.getmtime(path) if os.path.exists(path) else None
    rc, out, _ = host.run(["env", f"XDG_RUNTIME_DIR={rt}", f"GAMESCOPE_WAYLAND_DISPLAY={display}",
                           "gamescopectl", "screenshot", path], timeout=wait + 5)
    if rc != 0:
        return False, f"gamescopectl failed (rc {rc}): {out}"
    deadline = time.monotonic() + wait     # gamescope writes the file asynchronously
    while time.monotonic() < deadline:
        if os.path.exists(path) and os.path.getmtime(path) != before and os.path.getsize(path):
            return True, path
        time.sleep(0.2)
    return False, f"gamescope didn't write {path} within {wait:.0f}s ({out or 'no output'})"


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------

def report_session(s, live, now, logging_on, out):
    p = lambda *a: print(*a, file=out)  # noqa: E731
    end = s.end if s.end is not None else now
    state = "RUNNING NOW" if live else ("ended " + fmt(s.end) if s.end else "no stop logged")
    p(f"== Game Mode session {fmt(s.start)} -> {state}  ({dur(s.start, end)})")

    p("\n-- Games")
    runs = games_in(s, now)
    for r in runs:
        stop = fmt(r.end) if r.end else "still running" if live else "no exit logged"
        length = f" ({dur(r.start, r.end)})" if r.end else ""
        p(f"  {fmt(r.start)} -> {stop}{length}  {app_name(r.appid)} [{r.appid}]")
    if live:
        for app, comms in sorted(running_appids().items()):
            p(f"  live: {app_name(app)} [{app}] processes: {', '.join(comms)[:100]}")
    if not runs and not live:
        p("  none launched")

    p("\n-- Z13-Helper (Decky backend)")
    rows = decky_lines(journal(["-u", "plugin_loader", *window_args(s, now)]))
    for t, msg in rows[-MAX_LINES:]:
        p(f"  {fmt(t)} {msg}")
    if len(rows) > MAX_LINES:
        p(f"  ({len(rows) - MAX_LINES} earlier lines: journalctl -u plugin_loader)")
    if not rows:
        p("  nothing logged (is the plugin installed? journalctl -u plugin_loader)")

    p("\n-- Fan watchdog (fan-trace-gamemode.log)")
    if not logging_on:
        p("  logging is off (install.json), so there's no fan trace")
    elif not os.path.exists(FAN_TRACE):
        p(f"  no trace file ({FAN_TRACE})")
    else:
        events, st = trace_in(s, now)
        if st["samples"]:
            tl, th = st["temp"] or ("-", "-")
            rl, rh = st["rpm"] or ("-", "-")
            profs = ", ".join(f"{k} x{v}" for k, v in st["profiles"].items())
            p(f"  {st['samples']} samples  temp {tl}-{th} C  rpm {rl}-{rh}  profile {profs}")
        acted = [e for e in events if e[1].startswith("***")]
        for t, body in acted[-MAX_LINES:]:
            p(f"  {fmt(t)} {body}")
        p(f"  watchdog acted {len(acted)} time(s); trace (re)started {len(events) - len(acted)} time(s)")

    p("\n-- Errors (gamescope/Steam and z13 units, priority err, repeats collapsed)")
    errs = summarize_errors(journal(["--user", "-p", "err", *ERROR_UNITS, *window_args(s, now)]))
    for count, t, msg in errs:
        p(f"  {fmt(t)} {'x' + str(count) if count > 1 else '  '} {msg[:160]}")
    if not errs:
        p("  none")

    p("\n-- Screenshots taken")
    shots = screenshots_in(s, now)
    for t, path in shots[-MAX_LINES:]:
        p(f"  {fmt(t)} {path}")
    if not shots:
        p("  none")
    p("")


def main(argv=None, out=sys.stdout):
    ap = argparse.ArgumentParser(prog="z13-gamemode-report", description=__doc__.split("\n")[0])
    ap.add_argument("--last", type=int, default=1, metavar="N",
                    help="report the last N Game Mode sessions (default 1)")
    ap.add_argument("--days", type=int, default=SCAN_DAYS,
                    help=f"how far back to look for sessions (default {SCAN_DAYS})")
    ap.add_argument("--screenshot", nargs="?", const="", metavar="PATH",
                    help="Game Mode must be running: save a PNG of the screen "
                         f"(default {SCREEN_DIR.replace(HOME, '~')}/gamemode-<time>.png)")
    args = ap.parse_args(argv)
    logging_on = logging_enabled()

    if args.screenshot is not None:
        ok, msg = screenshot(args.screenshot or None, logging_on=logging_on)
        print(msg, file=out if ok else sys.stderr)
        return 0 if ok else 1

    now = time.time()
    sessions = find_sessions(args.days)
    live = gamescope_running()
    if not sessions:
        print(f"No Game Mode session in the last {args.days} days "
              "(journalctl --user -u gamescope-session).", file=out)
        return 0
    for i, s in enumerate(sessions[-args.last:]):
        is_last = s is sessions[-1]
        report_session(s, live and is_last and s.end is None, now, logging_on, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

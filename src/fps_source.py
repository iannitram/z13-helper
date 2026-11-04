"""Read-only frame-rate reading for the running game in Game Mode (Phase 15d).

One function, `read()`: the current fps as a float, or None when there is no
fresh reading. It never blocks longer than DEADLINE_S (50 ms), needs no root
and writes nothing.

Source implemented: the newest MangoHud CSV log in one of LOG_DIRS (MangoHud's
`output_folder`, with logging on). Only the tail of the file is read; a log
whose last write is older than STALE_S means the game stopped, so None.

Other candidates (gamescope's own stats, the Steam client through Decky) are
listed in docs/ROADMAP.md §10a with their status; none is wired in yet because
they need a measurement on the Z13 in Game Mode. No sensor or Armoury file is
touched (principle 5).
"""

from __future__ import annotations

import os
import time

LOG_DIRS = (
    os.path.expanduser("~/.local/state/z13-helper/mangohud"),
    os.path.expanduser("~/.local/share/MangoHud"),
)
DEADLINE_S = 0.05      # hard budget for one read()
STALE_S = 2.0          # a log not written for this long is a finished game
TAIL_BYTES = 4096
MAX_FPS = 1000.0       # anything above is a parse error, not a frame rate


def _newest_log(log_dirs, deadline):
    best, best_mtime = None, -1.0
    for d in log_dirs:
        try:
            entries = os.scandir(d)
        except OSError:
            continue
        with entries:
            for e in entries:
                if time.monotonic() > deadline:
                    return best
                if not e.name.endswith(".csv"):
                    continue
                try:
                    m = e.stat().st_mtime
                except OSError:
                    continue
                if m > best_mtime:
                    best, best_mtime = e.path, m
    return best


def _last_fps(path):
    """fps column of the last complete data row, or None."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_BYTES))
            tail = f.read().decode("utf-8", "replace")
            head = b""
            if size > TAIL_BYTES:
                f.seek(0)
                head = f.read(TAIL_BYTES)
    except OSError:
        return None
    # Column index: the header row is the one starting with "fps," near the top.
    col = None
    for line in (head.decode("utf-8", "replace") if head else tail).splitlines():
        cells = line.strip().split(",")
        if cells and cells[0] == "fps":
            col = cells.index("fps")
            break
    if col is None:
        return None
    lines = tail.splitlines()
    # The final line may be half-written; drop it unless the file ends in a newline.
    if lines and not tail.endswith("\n"):
        lines = lines[:-1]
    for line in reversed(lines):
        cells = line.strip().split(",")
        if len(cells) <= col or cells[0] == "fps":
            continue
        try:
            fps = float(cells[col])
        except ValueError:
            continue
        return fps if 0.0 <= fps <= MAX_FPS else None
    return None


def read(*, log_dirs=None, now=None):
    """Current fps of the running game, or None. Bounded by DEADLINE_S."""
    deadline = time.monotonic() + DEADLINE_S
    path = _newest_log(LOG_DIRS if log_dirs is None else log_dirs, deadline)
    if path is None or time.monotonic() > deadline:
        return None
    try:
        age = (time.time() if now is None else now) - os.stat(path).st_mtime
    except OSError:
        return None
    if age > STALE_S:
        return None
    fps = _last_fps(path)
    return fps if time.monotonic() <= deadline else None

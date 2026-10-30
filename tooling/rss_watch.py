#!/usr/bin/env python3
"""Bounded RSS sampler for one explicitly identified Linux process.

The PID and /proc start-time must both match throughout the run. The command
fragment is used only to verify identity and is never written to the log.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import os
from pathlib import Path
import sys
import time


MAX_HOURS = 24
DEFAULT_INTERVAL_S = 60
MAX_INTERVAL_S = 300


def process_start_ticks(stat_line: str) -> int:
    """Read Linux /proc stat field 22, handling ')' inside the comm field."""
    close = stat_line.rfind(")")
    if close < 0:
        raise ValueError("malformed proc stat")
    fields_from_state = stat_line[close + 1 :].split()
    if len(fields_from_state) <= 19:
        raise ValueError("truncated proc stat")
    return int(fields_from_state[19])


def _read_identity(proc_root: Path, pid: int) -> tuple[int, bytes]:
    base = proc_root / str(pid)
    start_ticks = process_start_ticks((base / "stat").read_text())
    cmdline = (base / "cmdline").read_bytes()
    return start_ticks, cmdline


def _read_kib_value(path: Path, key: str) -> int:
    for line in path.read_text().splitlines():
        name, sep, value = line.partition(":")
        if sep and name == key:
            fields = value.split()
            if not fields or (len(fields) > 1 and fields[1] != "kB"):
                break
            return int(fields[0])
    raise ValueError(f"missing or invalid {key}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log_line(out, line: str) -> None:
    print(line, file=out, flush=True)


def sample(pid: int, expected_start_ticks: int, cmd_fragment: str,
           output: Path, interval_s: int = DEFAULT_INTERVAL_S,
           duration_hours: int = MAX_HOURS, *, proc_root: Path = Path("/proc"),
           monotonic=time.monotonic, sleep=time.sleep) -> int:
    if pid <= 0 or expected_start_ticks <= 0:
        raise ValueError("pid and start ticks must be positive")
    if not cmd_fragment:
        raise ValueError("command identity fragment must not be empty")
    if not 1 <= duration_hours <= MAX_HOURS:
        raise ValueError(f"duration must be between 1 and {MAX_HOURS} hours")
    if not 1 <= interval_s <= MAX_INTERVAL_S:
        raise ValueError(f"interval must be between 1 and {MAX_INTERVAL_S} seconds")

    # Create one private, bounded result file. Never append to old data.
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o600)
    duration_s = duration_hours * 60 * 60
    sample_count = duration_s // interval_s + 1
    start = monotonic()
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        _log_line(out, f"# z13-helper rss-watch v1 target=decky-backend pid={pid} "
                      f"start_ticks={expected_start_ticks} interval_s={interval_s} "
                      f"max_duration_s={duration_s} max_samples={sample_count}")
        for index in range(sample_count):
            try:
                start_ticks, cmdline = _read_identity(proc_root, pid)
                if start_ticks != expected_start_ticks or cmd_fragment.encode() not in cmdline:
                    _log_line(out, f"{_utc_now()} STOP identity_changed samples={index}")
                    return 2
                rss_kib = _read_kib_value(proc_root / str(pid) / "status", "VmRSS")
                available_kib = _read_kib_value(proc_root / "meminfo", "MemAvailable")
            except (FileNotFoundError, ProcessLookupError, PermissionError, ValueError) as exc:
                reason = "process_unavailable" if isinstance(exc, (FileNotFoundError, ProcessLookupError)) else "sample_error"
                _log_line(out, f"{_utc_now()} STOP {reason} samples={index}")
                return 2

            row = (f"{_utc_now()} sample={index} pid={pid} start_ticks={start_ticks} "
                   f"rss_mib={rss_kib / 1024:.1f} mem_available_mib={available_kib / 1024:.1f}")
            _log_line(out, row)
            if index + 1 == sample_count:
                break
            deadline = start + (index + 1) * interval_s
            sleep(max(0.0, deadline - monotonic()))
        elapsed = monotonic() - start
        _log_line(out, f"{_utc_now()} COMPLETE samples={sample_count} "
                      f"elapsed_s={elapsed:.1f} max_duration_s={duration_s}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--start-ticks", type=int, required=True,
                        help="field 22 from /proc/PID/stat, captured before launch")
    parser.add_argument("--cmd-fragment", required=True,
                        help="required identity marker; never included in output")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_S)
    parser.add_argument("--hours", type=int, default=MAX_HOURS)
    args = parser.parse_args(argv)
    try:
        return sample(args.pid, args.start_ticks, args.cmd_fragment, args.output,
                      args.interval, args.hours)
    except (OSError, ValueError) as exc:
        print(f"rss-watch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

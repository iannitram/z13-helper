#!/usr/bin/env python3
"""Phase 15e probe: pair hwmon readings with an external surface temperature.

  z13_comfort_probe.py sample --phase idle|game|load --external-c 33.4 [--out FILE]
      append one CSV row: time, phase, external_c, every comfort candidate
  z13_comfort_probe.py fit FILE
      per-candidate linear fit external = offset + slope * reading, ranked by RMSE

Read-only: it reads hwmon through SensorReader and writes only the CSV. The
owner takes the external reading (IR thermometer on the back panel) by hand.
"""

import argparse
import csv
import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

CANDIDATES = ("tctl", "acpitz", "nvme", "battery", "gpu_edge")
FIELDS = ("time", "phase", "external_c", *CANDIDATES)
DEFAULT_OUT = os.path.expanduser("~/.local/state/z13-helper/comfort-probe.csv")


def append_row(path, phase, external_c, readings, now=None):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        w.writerow([int(time.time() if now is None else now), phase, external_c,
                    *("" if readings.get(c) is None else readings[c] for c in CANDIDATES)])


def read_rows(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def fit(rows):
    """[(candidate, offset, slope, rmse, n)] best (lowest RMSE) first.

    Rows missing the candidate or the external reading are skipped; a
    candidate with fewer than 3 usable rows or no spread is left out.
    """
    out = []
    for cand in CANDIDATES:
        pts = []
        for r in rows:
            try:
                x, y = float(r[cand]), float(r["external_c"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            pts.append((x, y))
        n = len(pts)
        if n < 3:
            continue
        mx = sum(x for x, _ in pts) / n
        my = sum(y for _, y in pts) / n
        sxx = sum((x - mx) ** 2 for x, _ in pts)
        if sxx == 0:
            continue
        slope = sum((x - mx) * (y - my) for x, y in pts) / sxx
        offset = my - slope * mx
        rmse = math.sqrt(sum((y - (offset + slope * x)) ** 2 for x, y in pts) / n)
        out.append((cand, offset, slope, rmse, n))
    return sorted(out, key=lambda t: t[3])


def _external_c(text):
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"--external-c must be a number from 0 to 80 (degrees C), got {text!r}")
    if not math.isfinite(value) or not 0.0 <= value <= 80.0:
        raise argparse.ArgumentTypeError(f"--external-c must be a number from 0 to 80 (degrees C), got {text!r}")
    return value


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--phase", required=True, choices=("idle", "game", "load"))
    s.add_argument("--external-c", required=True, type=_external_c)
    s.add_argument("--out", default=DEFAULT_OUT)
    s = sub.add_parser("fit")
    s.add_argument("file")
    args = ap.parse_args(argv)
    if args.cmd == "sample":
        from sensor_reader import SensorReader
        append_row(args.out, args.phase, args.external_c, SensorReader().get_comfort_candidates())
        return 0
    for cand, offset, slope, rmse, n in fit(read_rows(args.file)):
        print(f"{cand:9} external = {offset:+.2f} + {slope:.3f} * reading   rmse {rmse:.2f} C  (n={n})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

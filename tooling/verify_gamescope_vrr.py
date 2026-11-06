#!/usr/bin/env python3
"""Read-only #73 preflight, run from the checkout; prints JSON to stdout.

Does not install scripts, change modes or infer physical acceptance. Record the
output and the manual Game Mode observations in the issue #73 comment.
"""

import argparse
import json
import os
import re
import subprocess
from pathlib import Path


PROFILE = Path("gamescope/scripts/00-gamescope/displays/asus.rogflowz13.lcd.lua")
GAMESCOPE_VERSION = "3.16.30.1-2"
SOURCE = Path(__file__).resolve().parents[1] / "packaging/host/gamescope/displays/asus.rogflowz13.lcd.lua"


def read_text(path):
    try:
        return path.read_text().strip()
    except (OSError, UnicodeError):
        return None


def panel_identity(data):
    """Decode the EDID base block only; never include serial numbers."""
    if (len(data) < 128 or data[:8] != b"\x00\xff\xff\xff\xff\xff\xff\x00"
            or sum(data[:128]) % 256):
        return None
    vendor_id = int.from_bytes(data[8:10], "big")
    vendor = "".join(chr(64 + ((vendor_id >> shift) & 31)) for shift in (10, 5, 0))
    model = None
    for offset in range(54, 126, 18):
        descriptor = data[offset:offset + 18]
        if descriptor[:5] == b"\x00\x00\x00\xfc\x00":
            model = descriptor[5:].decode("ascii", errors="replace").strip()
    return {"vendor": vendor, "product": int.from_bytes(data[10:12], "little"),
            "model": model}


def version_tuple(value):
    """Leading numeric release (3.9.5-preview -> 3.9.5), padded to three parts."""
    match = re.match(r"\d+(?:\.\d+)*", value or "")
    if not match:
        return None
    parts = tuple(int(part) for part in match.group().split("."))
    return parts + (0,) * (3 - len(parts))


def installed_gamescope_version():
    """Version of the installed gamescope package (pacman), or None."""
    try:
        out = subprocess.run(["pacman", "-Q", "gamescope"], capture_output=True,
                             text=True, timeout=10, check=True).stdout.split()
    except (OSError, subprocess.SubprocessError):
        return None
    return out[1] if len(out) == 2 else None


def collect(config_home, drm=Path("/sys/class/drm"),
            os_release=Path("/etc/os-release"), proc=Path("/proc"), source=SOURCE,
            gamescope_version=None):
    installed = config_home / PROFILE
    try:
        expected = source.read_bytes()
    except OSError:
        expected = None
    try:
        actual = installed.read_bytes()
    except OSError:
        actual = None
    if expected is None:
        profile_state = "checkout source missing"
    elif actual is None:
        profile_state = "unreadable or missing"
    else:
        profile_state = "matches" if actual == expected else "differs"
    panels = []
    for connector in sorted(drm.glob("card*-eDP-*")):
        if read_text(connector / "status") != "connected":
            continue
        try:
            identity = panel_identity((connector / "edid").read_bytes())
        except OSError:
            identity = None
        panels.append({"connector": connector.name, "identity": identity,
                       "matches_profile": identity == {"vendor": "TMA", "product": 0x0803,
                                                       "model": "TL134ADXP03"}})
    os_info = {}
    for line in (read_text(os_release) or "").splitlines():
        key, sep, value = line.partition("=")
        if sep and key in ("ID", "VERSION_ID", "BUILD_ID"):
            os_info[key] = value.strip("\"'")
    running = any(read_text(path / "comm") in ("gamescope", "gamescope-wl")
                  for path in proc.glob("[0-9]*"))
    blockers = []
    if profile_state not in ("matches", "checkout source missing"):
        blockers.append("Installed display script does not match this checkout")
    if not any(panel["matches_profile"] for panel in panels):
        blockers.append("No connected eDP panel with the exact profile EDID identity")
    found = " ".join(filter(None, (os_info.get("ID"), os_info.get("VERSION_ID")))) or "unknown"
    notes = []
    if gamescope_version != GAMESCOPE_VERSION:
        blockers.append(f"expected Gamescope {GAMESCOPE_VERSION}, found {gamescope_version or 'unknown'}")
    if os_info.get("ID") != "steamos" or version_tuple(os_info.get("VERSION_ID")) is None:
        blockers.append(f"expected SteamOS 3.9.5, found {found}")
    elif version_tuple(os_info["VERSION_ID"]) < (3, 9, 5):
        blockers.append(f"expected SteamOS 3.9.5, found {found}")
    elif version_tuple(os_info["VERSION_ID"]) > (3, 9, 5):
        blockers.append(f"expected SteamOS 3.9.5, found newer {found}; "
                        "the 3.9.5 retest is not valid on this release")
    if not running:
        blockers.append("No Gamescope process observed; repeat during Game Mode")
    return {
        "os": os_info, "installed_profile": profile_state, "panels": panels,
        "gamescope_process_observed": running, "preflight_blockers": blockers,
        "gamescope_version": gamescope_version, "preflight_notes": notes,
        "device_acceptance": "pending owner-device verification",
        "manual_checks": [
            {"vrr": "on", "disable_frame_limiter": disabled, "refresh_hz": hz,
             "requested_limit_hz": None if disabled else hz,
             "slider_reach": None, "panel_stable": None,
             "measured_fps": None, "result": "pending"}
            for disabled in (False, True) for hz in (48, 60, 120, 180)
        ],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    # Same location install_check.py writes to; it ignores XDG_CONFIG_HOME.
    config = Path(os.path.expanduser("~")) / ".config"
    report = collect(config, gamescope_version=installed_gamescope_version())
    print(json.dumps(report, indent=2))
    # Zero means only that preflight found no blockers, never hardware sign-off.
    return 1 if report["preflight_blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

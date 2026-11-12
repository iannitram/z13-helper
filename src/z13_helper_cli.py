#!/usr/bin/env python3
"""The `z13-helper` command line: one entry point for every app feature.

Reads use the same shared modules as the desktop and Decky frontends. Every
hardware or profile write goes through `state_authority`, so the CLI never
fights a running app or bypasses the shared transaction/lock owner. Values are
clamped with the same safe ranges as the GUI.

The existing `z13-profile` entry point remains the profile engine's own CLI
(installed from `src/profiles.py`); `z13-helper profile ...` exposes the same
store through this command.

CLI:
    z13-helper status
    z13-helper profile list|get|set|apply|create|rename|delete
    z13-helper tdp [get|set|reset]
    z13-helper fans curve get|set
    z13-helper fans max-fans on|off|status
    z13-helper battery [get|set]
    z13-helper brightness [get|set|screen]
    z13-helper lighting [get|set|clear]
    z13-helper boondock [status|on|off]
    z13-helper button [status]
    z13-helper post-update list|run
    z13-helper crash queue|list|upload
    z13-helper mcp
    z13-helper version

Every command accepts `--json`.
"""

import argparse
import json
import math
import os
import sys
import time

_HOST_LIB = os.path.expanduser("~/.local/lib/z13-helper")
if __name__ == "__main__" and os.path.isdir(_HOST_LIB) and _HOST_LIB not in sys.path:
    sys.path.append(_HOST_LIB)
_REPO_TOOLING = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tooling"
)
if os.path.isdir(_REPO_TOOLING) and _REPO_TOOLING not in sys.path:
    sys.path.append(_REPO_TOOLING)

import host  # noqa: E402
import profiles  # noqa: E402
import state_authority as authority  # noqa: E402
import max_fans_state  # noqa: E402
import power_range  # noqa: E402
import sensor_reader  # noqa: E402
from versions import APP_VERSION  # noqa: E402

BATTERY_MIN, BATTERY_MAX, BATTERY_STEP = 40, 100, 5
PL_MIN, PL_MAX = 5, 93
PL1_SAFE_MAX = 75
DEVICE_CHOICES = ("keyboard", "lightbar", "both", "all")
BOONDOCK_BIN = os.path.expanduser("~/.local/bin/boondock")
CRASH_DIR = os.path.expanduser("~/.local/state/z13-helper/crash-upload")
POST_UPDATE_LOCAL_DIR = os.path.expanduser("~/.config/z13-helper/post-update.d")
POST_UPDATE_BACKUP_DIR = os.path.expanduser(
    "~/.local/state/z13-helper/post-update-backups"
)
CONFIG_PATH = os.path.expanduser("~/.config/z13-helper/config.json")


class CliError(RuntimeError):
    """A user-facing failure, printed without a traceback."""


class _InputError(ValueError):
    """A bad argument, reported like a usage error with exit 2."""


class _UsageError(Exception):
    """An argparse failure, raised so main() can honour --json."""

    def __init__(self, parser, message):
        super().__init__(message)
        self.parser = parser
        self.message = message


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise _UsageError(self, message)


# ----------------------------------------------------------------------
# Output and small shared helpers
# ----------------------------------------------------------------------


def _parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print machine-readable JSON",
    )

    ap = _ArgumentParser(prog="z13-helper", description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--json", action="store_true", default=False, help="print machine-readable JSON"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser(
        "status", parents=[common], help="profile, sensors and fans at a glance"
    )

    profile = sub.add_parser(
        "profile", parents=[common], help="profile store and apply"
    )
    psub = profile.add_subparsers(dest="profile_cmd", required=True)
    psub.add_parser("list", parents=[common], help="list profiles")
    s = psub.add_parser("get", parents=[common], help="show one profile")
    s.add_argument("name", nargs="?")
    s = psub.add_parser("set", parents=[common], help="save fields on one profile")
    s.add_argument("name")
    s.add_argument("--base", choices=profiles.BASES)
    s.add_argument("--tdp", help="PL1,PL2,PL3 in watts")
    for rail in ("pl1", "pl2", "pl3"):
        s.add_argument("--" + rail, type=int)
    s.add_argument("--force", action="store_true")
    s.add_argument("--curve", help="eight temp:pct points, comma separated")
    s.add_argument("--battery-limit", type=int)
    s.add_argument("--brightness", choices=profiles.BRIGHTNESS_LEVELS)
    s.add_argument("--undervolt", type=int)
    s.add_argument("--epp", choices=profiles.EPP_CHOICES)
    s.add_argument("--max-fans", choices=("on", "off"))
    s = psub.add_parser("apply", parents=[common], help="apply a stored profile")
    s.add_argument("name")
    s.add_argument("--no-hooks", action="store_true")
    s = psub.add_parser("create", parents=[common], help="add a new profile")
    s.add_argument("name")
    s.add_argument("--from", dest="from_name", metavar="PROFILE",
                   help="copy this profile instead of the Balanced base")
    s = psub.add_parser("rename", parents=[common], help="rename a custom profile")
    s.add_argument("old")
    s.add_argument("new")
    s = psub.add_parser("delete", parents=[common], help="delete a custom profile")
    s.add_argument("name")

    tdp = sub.add_parser("tdp", parents=[common], help="power limits")
    tdp.add_argument("action", nargs="?", default="get")
    tdp.add_argument("value", nargs="?")
    for rail in ("pl1", "pl2", "pl3"):
        tdp.add_argument("--" + rail, type=int)
    tdp.add_argument("--force", action="store_true")

    fans = sub.add_parser("fans", parents=[common], help="fan curve and Max Fans")
    fsub = fans.add_subparsers(dest="fans_cmd", required=True)
    curve = fsub.add_parser("curve", parents=[common], help="fan curve")
    csub = curve.add_subparsers(dest="curve_cmd", required=True)
    csub.add_parser("get", parents=[common], help="show live and stored curve")
    s = csub.add_parser("set", parents=[common], help="save and apply a curve")
    s.add_argument("value", help="eight temp:pct points, comma separated")
    mf = fsub.add_parser("max-fans", parents=[common], help="Max Fans toggle")
    mf.add_argument(
        "action", nargs="?", default="status", choices=("on", "off", "status")
    )

    battery = sub.add_parser("battery", parents=[common], help="battery charge limit")
    battery.add_argument("action", nargs="?", default="get")
    battery.add_argument("value", nargs="?")

    brightness = sub.add_parser(
        "brightness", parents=[common], help="keyboard/rear/screen brightness"
    )
    brightness.add_argument("action", nargs="?", default="get")
    brightness.add_argument("value", nargs="?")
    brightness.add_argument("--device", action="append", choices=DEVICE_CHOICES)

    lighting = sub.add_parser(
        "lighting", parents=[common], help="rear light and keyboard lighting"
    )
    lighting.add_argument("action", nargs="?", default="get")
    lighting.add_argument("device", nargs="?", choices=profiles.LIGHTING_DEVICES)
    lighting.add_argument("--mode", choices=profiles.LIGHTING_MODES)
    lighting.add_argument("--color", choices=profiles.LIGHTING_COLORS, default="red")
    lighting.add_argument("--brightness", choices=profiles.BRIGHTNESS_LEVELS)
    lighting.add_argument(
        "--device", dest="device_option", choices=profiles.LIGHTING_DEVICES
    )

    boondock = sub.add_parser(
        "boondock", parents=[common], help="Boondock low-power profile"
    )
    boondock.add_argument(
        "action", nargs="?", default="status", choices=("status", "on", "off")
    )

    button = sub.add_parser("button", parents=[common], help="side-button status")
    button.add_argument("action", nargs="?", default="status", choices=("status",))

    post = sub.add_parser(
        "post-update", parents=[common], help="post-SteamOS-update recipes"
    )
    postsub = post.add_subparsers(dest="post_cmd", required=True)
    postsub.add_parser("list", parents=[common], help="list recipes and their checks")
    s = postsub.add_parser("run", parents=[common], help="run selected recipes")
    s.add_argument("ids", nargs="*")
    s.add_argument("--dry-run", action="store_true")

    crash = sub.add_parser("crash", parents=[common], help="crash report queue")
    csub = crash.add_subparsers(dest="crash_cmd", required=True)
    s = csub.add_parser(
        "queue", parents=[common], help="queue a ZIP, or show the queue"
    )
    s.add_argument("file", nargs="?")
    csub.add_parser("list", parents=[common], help="list queued reports")
    s = csub.add_parser("upload", parents=[common], help="upload queued reports")
    s.add_argument("names", nargs="*")

    sub.add_parser(
        "mcp",
        parents=[common],
        help="local stdio MCP server (JSON-RPC over stdin/stdout)",
    )

    sub.add_parser("version", parents=[common], help="print the app version")
    return ap


def _jsonable(value):
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _emit(args, payload, lines):
    if getattr(args, "json", False):
        print(json.dumps(_jsonable(payload), sort_keys=True))
    else:
        for line in lines:
            print(line)


def _fail(args, message):
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "error": str(message)}, sort_keys=True))
    else:
        print(str(message), file=sys.stderr)
    return 1


def _fail_input(args, message):
    if getattr(args, "json", False):
        print(json.dumps({"ok": False, "error": str(message)}, sort_keys=True))
    else:
        print(str(message), file=sys.stderr)
    return 2


def _as_int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _clamp(value, lo, hi):
    return max(lo, min(hi, int(value)))


def _clamp_battery(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise _InputError(f"battery limit must be a number, got {value!r}") from None
    if not math.isfinite(number):
        raise _InputError(
            f"battery limit must be a finite number, got {value!r}"
        ) from None
    if not (BATTERY_MIN <= number <= BATTERY_MAX):
        raise _InputError(f"battery limit is out of range, got {value!r}")
    try:
        v = int(round(number / BATTERY_STEP) * BATTERY_STEP)
    except (ValueError, OverflowError):
        raise _InputError(f"battery limit is out of range, got {value!r}") from None
    if not (BATTERY_MIN <= v <= BATTERY_MAX):
        raise _InputError(f"battery limit is out of range, got {value!r}")
    return v


def _clamp_tdp(pl1, pl2=None, pl3=None, ranges=None, force=False):
    r = ranges or {}

    def one(value, rail):
        lo, hi = r.get(rail, (PL_MIN, PL_MAX))
        return _clamp(round(float(value)), lo, hi)

    a = one(pl1, "pl1")
    b = max(one(pl2 if pl2 is not None else a, "pl2"), a)
    c = max(one(pl3 if pl3 is not None else b, "pl3"), b)
    return {"pl1": a, "pl2": b, "pl3": c, "force": bool(force and a > PL1_SAFE_MAX)}


def _parse_tdp_values(values, allow_one=False):
    parts = [p for p in str(values).replace(":", ",").split(",") if p.strip()]
    if len(parts) == 1 and allow_one:
        value = _as_int(parts[0])
        if value is None:
            raise _InputError("tdp values must be whole watts")
        return [value]
    if len(parts) != 3:
        raise _InputError("tdp needs PL1,PL2,PL3")
    nums = [_as_int(p) for p in parts]
    if any(n is None for n in nums):
        raise _InputError("tdp values must be whole watts")
    return nums


def _parse_curve(value):
    if isinstance(value, (list, tuple)):
        points = value
    elif str(value).lstrip().startswith("["):
        try:
            points = json.loads(value)
        except ValueError as exc:
            raise _InputError(f"bad fan curve JSON: {exc}") from None
    else:
        points = []
        for part in str(value).split(","):
            part = part.strip()
            if not part:
                continue
            sep = ":" if ":" in part else "-"
            try:
                temp, pct = part.split(sep, 1)
            except ValueError:
                raise _InputError(f"bad fan curve point {part!r}") from None
            try:
                points.append([int(temp.strip()), int(pct.strip().rstrip("%"))])
            except ValueError:
                raise _InputError(f"bad fan curve point {part!r}") from None
    try:
        profiles.validate_curve(points)
    except ValueError as exc:
        raise _InputError(str(exc)) from None
    if all(int(pct) == 0 for _, pct in points):
        raise _InputError("all-zero fan curves are not allowed")
    for temp, pct in points:
        floor = profiles.fan_curve_floor_pct(int(temp))
        if int(pct) < floor:
            raise _InputError(f"fan curve needs at least {floor:g}% at {temp} °C")
    return points


def _active_profile():
    name = profiles.active_name()
    profile = profiles.get_profile(name)
    if profile is None:
        raise CliError("no active profile")
    return profile


def _config_brightness():
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return "medium"
    level = data.get("brightness") if isinstance(data, dict) else None
    return level if level in profiles.BRIGHTNESS_LEVELS else "medium"


def _lighting_devices(device_args):
    if not device_args:
        return tuple(profiles.LIGHTING_DEVICES)
    devices = []
    for item in device_args:
        if item in ("both", "all"):
            devices.extend(profiles.LIGHTING_DEVICES)
        elif item in profiles.LIGHTING_DEVICES:
            devices.append(item)
        else:
            raise _InputError(f"unknown device {item!r}")
    return tuple(dict.fromkeys(devices))


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _screen_percent():
    from screen_brightness import find_backlight, to_percent

    path = find_backlight()
    if path is None:
        return None
    raw = _read_int(os.path.join(path, "brightness"))
    maximum = _read_int(os.path.join(path, "max_brightness"))
    if raw is None or not maximum:
        return None
    return to_percent(raw, maximum)


def _set_screen_percent(percent):
    from screen_brightness import MIN_PERCENT, find_backlight, to_raw

    path = find_backlight()
    if path is None:
        raise CliError("no backlight control found")
    maximum = _read_int(os.path.join(path, "max_brightness"))
    if not maximum:
        raise CliError("backlight max_brightness is unreadable")
    value = _clamp(percent, MIN_PERCENT, 100)
    authority.set_screen_backlight(path, to_raw(value, maximum))
    return value


# ----------------------------------------------------------------------
# status / version
# ----------------------------------------------------------------------


def cmd_status(args):
    from hardware_controller import HardwareController

    reader = sensor_reader.SensorReader()
    fan1, fan2 = reader.get_fan_speeds()
    active = profiles.get_profile(profiles.active_name())
    try:
        charge_limit = HardwareController().get_battery_limit()
    except Exception:  # noqa: BLE001 - status is read-only and must not fail
        charge_limit = None
    payload = {
        "version": APP_VERSION,
        "profile": active,
        "active": profiles.active_name(),
        "platform_profile": reader.get_platform_profile(),
        "fans": {
            "rpm": [fan1, fan2],
            "custom_curve": reader.custom_fan_curve_enabled(),
        },
        "temperatures": {
            "apu": reader.get_apu_temp(),
            "gpu": reader.get_gpu_temp(),
        },
        "battery": dict(reader.get_battery(), charge_limit=charge_limit),
        "max_fans": max_fans_state.is_enabled(),
        "screen_brightness": _screen_percent(),
        "brightness": (active or {}).get("brightness") or _config_brightness(),
    }
    lines = [
        f"Version: {APP_VERSION}",
        f"Profile: {payload['active'] or '-'} (platform: {payload['platform_profile'] or '-'})",
        f"Fans: {fan1 if fan1 is not None else '?'} / {fan2 if fan2 is not None else '?'} RPM"
        f"  custom curve: {payload['fans']['custom_curve']}",
        f"APU/GPU: {payload['temperatures']['apu']} / {payload['temperatures']['gpu']} °C",
        f"Battery: {payload['battery']['capacity']}% ({payload['battery']['status'] or '-'})"
        f"  limit: {charge_limit if charge_limit is not None else '-'}%",
        f"Max Fans: {'ON' if payload['max_fans'] else 'OFF'}",
        f"Screen brightness: {payload['screen_brightness'] if payload['screen_brightness'] is not None else '-'}%",
        f"Keyboard/rear brightness: {payload['brightness']}",
    ]
    _emit(args, payload, lines)
    return 0


def cmd_version(args):
    payload = {"name": "z13-helper", "version": APP_VERSION}
    _emit(args, payload, [APP_VERSION])
    return 0


def cmd_mcp(args):
    import mcp_server

    return mcp_server.serve()


# ----------------------------------------------------------------------
# profile
# ----------------------------------------------------------------------


def cmd_profile_list(args):
    store = profiles.load_store()
    rows = [
        {"name": p["name"], "base": p.get("base"), "builtin": bool(p.get("builtin"))}
        for p in store["profiles"]
    ]
    payload = {"active": store.get("active"), "profiles": rows}
    lines = [
        f"{'*' if p['name'] == store.get('active') else ' '} {p['name']}  (base: {p['base']})"
        for p in rows
    ]
    _emit(args, payload, lines)
    return 0


def cmd_profile_get(args):
    name = args.name or profiles.active_name()
    profile = profiles.get_profile(name)
    if profile is None:
        raise CliError(f"no profile named {name!r}")
    _emit(args, profile, [json.dumps(profile, indent=2, sort_keys=True)])
    return 0


def cmd_profile_set(args):
    store = profiles.load_store()
    profile = profiles.get_profile(args.name, store)
    if profile is None:
        raise CliError(f"no profile named {args.name!r}")
    profile = dict(profile)

    updated = []

    def change(field, value):
        profile[field] = value
        updated.append(field)

    if args.base is not None:
        change("base", args.base)
    if args.tdp is not None or any(v is not None for v in (args.pl1, args.pl2, args.pl3)):
        if args.tdp is not None:
            if any(v is not None for v in (args.pl1, args.pl2, args.pl3)):
                raise _InputError("give tdp with --tdp or --pl1/--pl2/--pl3, not both")
            pl1, pl2, pl3 = _parse_tdp_values(args.tdp)
        else:
            pl1 = args.pl1
            pl2 = args.pl2
            pl3 = args.pl3
            if pl1 is None:
                raise _InputError("tdp needs --pl1 (or --tdp PL1,PL2,PL3)")
        tdp = _clamp_tdp(pl1, pl2, pl3, power_range.detect_ranges(), force=args.force)
        if tdp["pl1"] > PL1_SAFE_MAX and not args.force:
            raise _InputError(
                f"PL1 above {PL1_SAFE_MAX} W runs the fans at full speed; "
                "repeat the command with --force to confirm"
            )
        change("tdp", tdp)
    if args.curve is not None:
        change("fan_curve", _parse_curve(args.curve))
    if args.battery_limit is not None:
        change("battery_limit", _clamp_battery(args.battery_limit))
    if args.brightness is not None:
        change("brightness", args.brightness)
    if args.undervolt is not None:
        if not -40 <= args.undervolt <= 0:
            raise _InputError("undervolt must be 0..-40")
        change("undervolt", args.undervolt)
    if args.epp is not None:
        change("epp", args.epp)
    if args.max_fans is not None:
        change("max_fans", args.max_fans == "on")
    if not updated:
        raise _InputError("profile set needs at least one field")

    profiles.validate(profile)
    hardware_fields = {
        "tdp",
        "fan_curve",
        "battery_limit",
        "brightness",
        "undervolt",
        "epp",
    }
    # A profile switch must not interleave with the active-only Max Fans toggle.
    with authority.transaction():
        for field in updated:
            if field in hardware_fields:
                authority.edit(profile, field)
        full = [field for field in updated if field not in hardware_fields]
        for field in full:
            results = authority.edit(profile, field)
            if isinstance(results, list):
                failed = [f"{step}: {message}" for step, ok, message in results if not ok]
                if failed:
                    raise CliError("; ".join(failed))
        if "max_fans" in updated and profile["name"] == profiles.active_name():
            # profiles.apply only plans Max Fans on a real profile switch.
            authority.set_max_fans(profile["max_fans"])
    payload = {
        "ok": True,
        "name": profile["name"],
        "updated": updated,
        "profile": profile,
    }
    _emit(args, payload, [f"{profile['name']}: updated {', '.join(updated)}"])
    return 0


def _owner_call(call, *args, **kwargs):
    """Run a state-owner profile edit; store errors become CLI failures."""
    try:
        return call(*args, **kwargs)
    except KeyError as exc:
        raise CliError(f"no profile named {exc.args[0]!r}") from None
    except ValueError as exc:
        raise CliError(str(exc)) from None


def cmd_profile_create(args):
    made = _owner_call(authority.create_profile, args.name, from_name=args.from_name)
    payload = {"ok": True, "name": made["name"], "from": args.from_name, "profile": made}
    source = f" from {args.from_name}" if args.from_name else ""
    _emit(args, payload, [f"Created profile: {made['name']}{source}"])
    return 0


def cmd_profile_rename(args):
    renamed = _owner_call(authority.rename_profile, args.old, args.new)
    payload = {"ok": True, "old": args.old, "name": renamed["name"]}
    _emit(args, payload, [f"Renamed profile: {args.old} -> {renamed['name']}"])
    return 0


def cmd_profile_delete(args):
    result = _owner_call(authority.delete_profile, args.name)
    steps = result["steps"]
    ok = all(step_ok for _, step_ok, _ in steps)
    payload = {"ok": ok, "name": args.name, "active": result["active"],
               "steps": [[step, step_ok, message] for step, step_ok, message in steps]}
    lines = [f"Deleted profile: {args.name}"]
    if steps:
        lines.append(f"Active profile is now {result['active']}; applied it")
    lines.extend(f"  {'ok ' if step_ok else 'ERR'}  {step:<22}  {message}"
                 for step, step_ok, message in steps)
    _emit(args, payload, lines)
    return 0 if ok else 1


def cmd_profile_apply(args):
    results = authority.apply_profile(args.name, skip_hooks=args.no_hooks)
    ok = all(step_ok for _, step_ok, _ in results)
    payload = {
        "ok": ok,
        "name": args.name,
        "steps": [[step, step_ok, message] for step, step_ok, message in results],
    }
    lines = [f"Applying profile: {args.name}"]
    lines.extend(
        f"  {'ok ' if step_ok else 'ERR'}  {step:<22}  {message}"
        for step, step_ok, message in results
    )
    _emit(args, payload, lines)
    return 0 if ok else 1


# ----------------------------------------------------------------------
# tdp
# ----------------------------------------------------------------------


def cmd_tdp(args):
    action = args.action
    if _as_int(action) is not None and args.value is None:
        args.value, action = action, "set"
    if action == "get" and any(v is not None for v in (args.pl1, args.pl2, args.pl3)):
        action = "set"
    if action == "get":
        profile = _active_profile()
        stored = profile.get("tdp")
        ranges = power_range.detect_ranges()
        status = power_range.tdp_status(stored, ranges) if stored else None
        payload = {
            "profile": profile["name"],
            "tdp": stored,
            "ranges": ranges,
            "status": status,
        }
        lines = [
            f"Profile {profile['name']}: "
            + (
                f"{stored['pl1']}/{stored['pl2']}/{stored['pl3']} W"
                if stored
                else "no TDP override"
            )
        ]
        if status and status["needs_attention"]:
            lines.append(status["note"])
        _emit(args, payload, lines)
        return 0
    if action == "reset":
        if args.value is not None or any(
            v is not None for v in (args.pl1, args.pl2, args.pl3)
        ) or args.force:
            raise _InputError("tdp reset does not accept limits or --force")
        results = authority.set_field("tdp", None)
        if isinstance(results, list):
            failed = [f"{step}: {message}" for step, ok, message in results if not ok]
            if failed:
                raise CliError("; ".join(failed))
        _emit(args, {"ok": True, "tdp": None}, ["TDP reset to firmware control"])
        return 0
    if action != "set":
        raise _InputError(f"unknown tdp action {action!r}")
    if args.value is not None:
        values = _parse_tdp_values(args.value, allow_one=True)
        if args.pl1 is not None or (
            len(values) > 1 and (args.pl2 is not None or args.pl3 is not None)
        ):
            raise _InputError("give tdp as PL1,PL2,PL3 or with --pl1/--pl2/--pl3, not both")
        pl1 = values[0]
        pl2 = values[1] if len(values) > 1 else args.pl2
        pl3 = values[2] if len(values) > 2 else args.pl3
    else:
        if args.pl1 is None:
            raise _InputError("tdp set needs PL1,PL2,PL3 or --pl1")
        pl1, pl2, pl3 = args.pl1, args.pl2, args.pl3
    tdp = _clamp_tdp(pl1, pl2, pl3, power_range.detect_ranges(), force=args.force)
    if tdp["pl1"] > PL1_SAFE_MAX and not args.force:
        raise _InputError(
            f"PL1 above {PL1_SAFE_MAX} W runs the fans at full speed; "
            "repeat the command with --force to confirm"
        )
    authority.set_field("tdp", tdp)
    payload = {"ok": True, "tdp": tdp}
    _emit(args, payload, [f"TDP: {tdp['pl1']}/{tdp['pl2']}/{tdp['pl3']} W"])
    return 0


# ----------------------------------------------------------------------
# fans
# ----------------------------------------------------------------------


def cmd_fans_curve_get(args):
    from hardware_controller import HardwareController

    try:
        live = HardwareController().get_fancurve()
    except Exception as exc:  # noqa: BLE001 - a read failure is reported, not fatal
        live = {"mode": "unknown", "rpm": 0, "points": [], "error": str(exc)}
    profile = profiles.get_profile(profiles.active_name())
    stored = profile.get("fan_curve") if profile else None
    payload = {
        "live": live,
        "profile": profile["name"] if profile else None,
        "curve": stored,
    }
    lines = [
        f"Live: mode={live.get('mode')} rpm={live.get('rpm')}",
        "  "
        + (
            ", ".join(f"{t}°C:{p}%" for t, _pwm, p in live.get("points") or [])
            if live.get("points")
            else "no live curve reported"
        ),
    ]
    lines.append(
        f"{payload['profile'] or '-'}: "
        + (", ".join(f"{t}°C:{p}%" for t, p in stored) if stored else "no stored curve")
    )
    _emit(args, payload, lines)
    return 0


def cmd_fans_curve_set(args):
    curve = _parse_curve(args.value)
    result = authority.set_field("fan_curve", curve)
    payload = {"ok": True, "curve": curve, "message": result}
    _emit(
        args,
        payload,
        [f"Fan curve applied: {', '.join(f'{t}°C:{p}%' for t, p in curve)}"],
    )
    return 0


def cmd_fans_max_fans(args):
    if args.action == "status":
        enabled = max_fans_state.is_enabled()
        _emit(args, {"max_fans": enabled}, [f"Max Fans: {'ON' if enabled else 'OFF'}"])
        return 0
    enabled = args.action == "on"
    message = authority.set_max_fans(enabled)
    _emit(
        args,
        {"max_fans": enabled, "message": message},
        [message or f"Max Fans {'ON' if enabled else 'OFF'}"],
    )
    return 0


# ----------------------------------------------------------------------
# battery / brightness / lighting
# ----------------------------------------------------------------------


def cmd_battery(args):
    from hardware_controller import HardwareController

    action = args.action
    if action not in ("get", "set") and args.value is None:
        action, args.value = "set", action
    if action == "get":
        try:
            limit = HardwareController().get_battery_limit()
        except Exception:  # noqa: BLE001
            limit = None
        battery = sensor_reader.SensorReader().get_battery()
        payload = {"limit": limit, "battery": battery}
        _emit(
            args,
            payload,
            [
                f"Charge limit: {limit if limit is not None else '-'}%",
                f"Battery: {battery['capacity']}% ({battery['status'] or '-'})",
            ],
        )
        return 0
    if action != "set":
        raise _InputError(f"unknown battery action {action!r}")
    if args.value is None:
        raise _InputError("battery set needs a percentage")
    value = _clamp_battery(args.value)
    authority.set_field("battery_limit", value)
    _emit(args, {"ok": True, "limit": value}, [f"Charge limit: {value}%"])
    return 0


def cmd_brightness(args):
    action = args.action
    if (
        action not in ("get", "set", "screen")
        and action not in profiles.BRIGHTNESS_LEVELS
    ):
        if _as_int(action) is not None and args.value is None:
            args.value, action = action, "screen"
    if action == "get":
        profile = profiles.get_profile(profiles.active_name())
        level = (profile or {}).get("brightness") or _config_brightness()
        screen = _screen_percent()
        payload = {"level": level, "screen_percent": screen}
        _emit(
            args,
            payload,
            [
                f"Keyboard/rear: {level}",
                f"Screen: {screen if screen is not None else '-'}%",
            ],
        )
        return 0
    if action == "screen":
        if args.value is None:
            percent = _screen_percent()
            _emit(
                args,
                {"percent": percent},
                [f"Screen: {percent if percent is not None else '-'}%"],
            )
            return 0
        value = _as_int(args.value)
        if value is None:
            raise _InputError("screen brightness needs a percentage")
        percent = _set_screen_percent(value)
        _emit(args, {"ok": True, "percent": percent}, [f"Screen: {percent}%"])
        return 0
    if action == "set":
        if args.value in profiles.BRIGHTNESS_LEVELS:
            level = args.value
        elif _as_int(args.value) is not None:
            percent = _set_screen_percent(_as_int(args.value))
            _emit(args, {"ok": True, "percent": percent}, [f"Screen: {percent}%"])
            return 0
        else:
            raise _InputError("brightness set needs a level or a screen percentage")
    elif action in profiles.BRIGHTNESS_LEVELS:
        level = action
    else:
        raise _InputError(f"unknown brightness action {action!r}")
    devices = _lighting_devices(args.device)
    failed = authority.set_field("brightness", level, devices=devices)
    payload = {"ok": True, "level": level, "devices": devices, "failed": failed}
    _emit(
        args,
        payload,
        [
            f"Brightness: {level}"
            + (f" ({', '.join(failed)} not responding)" if failed else "")
        ],
    )
    return 0


def cmd_lighting(args):
    action = args.action
    device = args.device or args.device_option
    if action in profiles.LIGHTING_DEVICES:
        action, device = "set", action
    elif action == "get" and (args.mode or args.device_option):
        action = "set"
    profile = _active_profile()
    if action == "get":
        payload = {
            "profile": profile["name"],
            "lighting": profile.get("lighting") or {},
            "brightness": _config_brightness(),
        }
        _emit(
            args,
            payload,
            [
                f"{profile['name']} lighting: "
                + (
                    json.dumps(payload["lighting"], sort_keys=True)
                    if payload["lighting"]
                    else "none"
                )
            ],
        )
        return 0
    if action not in ("set", "clear"):
        raise _InputError(f"unknown lighting action {action!r}")
    if device not in profiles.LIGHTING_DEVICES:
        raise _InputError(
            "lighting needs a device: " + ", ".join(profiles.LIGHTING_DEVICES)
        )
    brightness = args.brightness or _config_brightness()
    if action == "clear":
        message = authority.set_lighting(profile["name"], device, None, brightness)
        _emit(
            args,
            {"ok": True, "device": device, "message": message},
            [message or f"{device} lighting override cleared"],
        )
        return 0
    if args.mode is None:
        raise _InputError("lighting set needs --mode")
    setting = {"mode": args.mode, "color": args.color}
    message = authority.set_lighting(profile["name"], device, setting, brightness)
    payload = {
        "ok": True,
        "device": device,
        **setting,
        "brightness": brightness,
        "message": message,
    }
    _emit(args, payload, [f"{device}: {args.mode}/{args.color} at {brightness}"])
    return 0


# ----------------------------------------------------------------------
# boondock / button
# ----------------------------------------------------------------------


def cmd_boondock(args):
    if args.action == "status":
        result = host.run([BOONDOCK_BIN, "status"], timeout=15)
        lines = [line for line in result.out.splitlines() if line.strip()]
        payload = {"ok": result.rc == 0, "state": result.out, "rc": result.rc}
        _emit(args, payload, lines or ["boondock: no status output"])
        return 0 if result.rc == 0 else 1
    name = "Boondock" if args.action == "on" else "Balanced"
    results = authority.apply_profile(name)
    ok = all(step_ok for _, step_ok, _ in results)
    payload = {
        "ok": ok,
        "profile": name,
        "steps": [[step, step_ok, message] for step, step_ok, message in results],
    }
    _emit(
        args,
        payload,
        [f"Boondock {'on' if args.action == 'on' else 'off'}: applied {name}"]
        + [
            f"  {'ok ' if step_ok else 'ERR'}  {step:<22}  {message}"
            for step, step_ok, message in results
        ],
    )
    return 0 if ok else 1


def cmd_button(args):
    import evdev_listener

    device, code = evdev_listener.find_side_button_device()
    app = host.run(
        ["systemctl", "--user", "is-active", "z13-helper.service"], timeout=5
    )
    watcher = host.run(
        ["systemctl", "--user", "is-active", "z13-button-watcher.service"], timeout=5
    )
    payload = {
        "device": device,
        "keycode": code,
        "app_active": app.out.strip() == "active",
        "watcher_active": watcher.out.strip() == "active",
    }
    _emit(
        args,
        payload,
        [
            f"Side-button device: {device or 'not found'}"
            + (f" (key {code})" if device else ""),
            f"Desktop app: {'running' if payload['app_active'] else 'not running'}",
            f"Game Mode watcher: {'running' if payload['watcher_active'] else 'not running'}",
        ],
    )
    return 0


# ----------------------------------------------------------------------
# post-update recipes
# ----------------------------------------------------------------------


def _priv(argv):
    return list(argv) if os.geteuid() == 0 else ["sudo", "--", *argv]


def _host_run(argv, privileged=False, input=None):
    res = host.run(_priv(argv) if privileged else list(argv), timeout=120, input=input)
    return res.rc, res.out


def _host_read(path, privileged=False):
    rc, out = _host_run(["cat", path], privileged)
    return out if rc == 0 else None


def _host_exists(path, privileged=False):
    return _host_run(["test", "-e", path], privileged)[0] == 0


def _host_listdir(path, privileged=False, missing_none=False):
    rc, out = _host_run(["ls", "-1", path], privileged)
    if rc != 0:
        return None if missing_none else []
    return [line for line in out.splitlines() if line]


def _host_copy_tree(src, dest, privileged=False):
    parent = os.path.dirname(dest)
    if parent:
        rc, out = _host_run(["mkdir", "-p", parent], privileged)
        if rc != 0:
            raise CliError(f"could not create {parent}: {out}")
    rc, out = _host_run(["cp", "-a", src, dest], privileged)
    if rc != 0:
        raise CliError(f"could not copy {src} to {dest}: {out}")


def _host_io(privileged=False):
    from post_update.builtin import HostIO

    def write(path, text):
        parent = os.path.dirname(path)
        if parent:
            _host_run(["mkdir", "-p", parent], privileged)
        rc, out = _host_run(["tee", path], privileged, input=text)
        if rc != 0:
            raise CliError(f"could not write {path}: {out}")

    return HostIO(
        read=lambda path: _host_read(path, privileged),
        write=write,
        exists=lambda path: _host_exists(path, privileged),
        listdir=lambda path: _host_listdir(path, privileged),
        run=lambda argv: _host_run(argv, privileged),
    )


def _host_sys(privileged=False):
    from post_update.builtin_4_6 import Sys

    def write(path, text):
        parent = os.path.dirname(path)
        if parent:
            _host_run(["mkdir", "-p", parent], privileged)
        rc, out = _host_run(["tee", path], privileged, input=text)
        if rc != 0:
            raise CliError(f"could not write {path}: {out}")

    return Sys(
        home=os.path.expanduser("~"),
        listdir=lambda path: _host_listdir(path, privileged, missing_none=True),
        exists=lambda path: _host_exists(path, privileged),
        read=lambda path: _host_read(path, privileged),
        run=lambda argv: _host_run(argv, privileged)[0],
        write=write,
        copy_tree=lambda src, dest: _host_copy_tree(src, dest, privileged),
    )


def _local_recipes(io):
    from post_update.local import load_dir
    from post_update.recipe import Recipe

    recipes, errors = load_dir(POST_UPDATE_LOCAL_DIR)

    def make(item):
        def check():
            rc, _out = io.run(["sh", "-c", item["check"]])
            return "ok" if rc == 0 else "needs"

        def apply():
            rc, out = io.run(["sh", "-c", item["apply"]])
            if rc != 0:
                raise CliError(out or f"{item['id']} failed")

        return Recipe(
            id=item["id"],
            title=item["title"],
            fixes=item["fixes"],
            check=check,
            apply=apply,
            sudo=item["sudo"],
            network=item["network"],
            reboot=item["reboot"],
            pacman=item["pacman"],
            default_on=item["default_on"],
        )

    return [make(item) for item in recipes], errors


def _all_recipes(privileged=False):
    from post_update.registry import ordered_builtins

    io = _host_io(privileged)
    recipes = ordered_builtins(io, _host_sys(privileged))
    local, errors = _local_recipes(io)
    return recipes + local, errors


def cmd_post_update_list(args):
    recipes, errors = _all_recipes()
    rows = [
        {
            "id": r.id,
            "title": r.title,
            "fixes": r.fixes,
            "check": r.check(),
            "sudo": r.sudo,
            "network": r.network,
            "pacman": r.pacman,
            "reboot": r.reboot,
            "default_on": r.default_on,
        }
        for r in recipes
    ]
    payload = {"recipes": rows, "errors": errors}
    lines = [f"{row['id']:<16} {row['check']:<6} {row['title']}" for row in rows]
    lines.extend(f"error: {err}" for err in errors)
    _emit(args, payload, lines)
    return 0


def _post_update_env():
    from post_update.runner import Env

    def backup(path):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(
            POST_UPDATE_BACKUP_DIR, stamp, os.path.basename(path.rstrip("/"))
        )
        _host_copy_tree(path, dest, True)

    def readonly(action):
        name = host.holo_or_steamos("readonly")
        rc, out = _host_run([name, action], True)
        if rc != 0:
            raise CliError(out or f"{name} {action} failed")

    return Env(
        backup=backup,
        readonly_disable=lambda: readonly("disable"),
        readonly_enable=lambda: readonly("enable"),
    )


def cmd_post_update_run(args):
    from post_update.recipe import order_tasks, plan_text, with_keyring
    from post_update.runner import run_plan

    recipes, errors = _all_recipes(privileged=not args.dry_run)
    by_id = {recipe.id: recipe for recipe in recipes}
    unknown = [name for name in args.ids if name not in by_id]
    if unknown:
        raise _InputError("unknown recipe(s): " + ", ".join(unknown))
    if args.ids:
        selected = [by_id[name] for name in args.ids]
    else:
        selected = [recipe for recipe in recipes if recipe.check() != "na"]
        selected = [
            recipe for recipe in selected if recipe.check() in ("needs", "info")
        ]
    ordered = order_tasks(with_keyring(selected, recipes))
    plan = plan_text(ordered)
    if args.dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "plan": plan,
            "ids": [recipe.id for recipe in ordered],
        }
        _emit(args, payload, plan.splitlines())
        return 0
    results = run_plan(ordered, _post_update_env())
    ok = all(status != "failed" for _id, status in results)
    payload = {"ok": ok, "plan": plan, "results": results, "errors": errors}
    _emit(
        args,
        payload,
        plan.splitlines() + [f"{recipe_id}: {status}" for recipe_id, status in results],
    )
    return 0 if ok else 1


# ----------------------------------------------------------------------
# crash queue
# ----------------------------------------------------------------------


def _crash_queue():
    from crash_queue import CrashQueue

    return CrashQueue(CRASH_DIR)


def _crash_status(queue):
    return {
        "directory": CRASH_DIR,
        "pending": queue.pending(),
        "can_send": queue.can_send(),
    }


def cmd_crash_queue(args):
    queue = _crash_queue()
    if args.file is None:
        payload = _crash_status(queue)
        _emit(
            args,
            payload,
            [
                f"Queue: {CRASH_DIR}",
                f"Pending: {len(payload['pending'])}",
                f"Upload allowed today: {'yes' if payload['can_send'] else 'no'}",
            ],
        )
        return 0
    path = os.path.abspath(os.path.expanduser(args.file))
    if not path.endswith(".zip"):
        raise _InputError("crash queue needs a .zip report")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        raise CliError(f"could not read {path}: {exc}") from None
    name = os.path.basename(path)
    queue.add(name, data)
    _emit(
        args, {"ok": True, "queued": name, **_crash_status(queue)}, [f"Queued {name}"]
    )
    return 0


def cmd_crash_list(args):
    payload = _crash_status(_crash_queue())
    _emit(args, payload, payload["pending"] or ["No queued reports"])
    return 0


def cmd_crash_upload(args):
    import crash_report

    queue = _crash_queue()
    pending = queue.pending()
    names = args.names or pending
    invalid = [
        name for name in names
        if name not in pending or os.path.islink(queue.directory / name)
    ]
    if invalid:
        raise _InputError("not queued crash report(s): " + ", ".join(invalid))
    if not crash_report.reporting_enabled(
        os.path.expanduser("~/.config/z13-helper/install.json")
    ):
        raise CliError("crash reporting is disabled in install.json")
    try:
        build = json.loads(crash_report.BUILD_CONFIG.read_text())
    except (OSError, ValueError):
        raise CliError("no build-time crash drop configuration") from None
    if not isinstance(build, dict) or not all(
        isinstance(build.get(k), str) and build[k]
        for k in ("base", "token", "version", "commit")
    ):
        raise CliError("incomplete build-time crash drop configuration")

    uploaded, failed = [], []
    for name in names:
        if not queue.can_send():
            failed.append(name)
            continue
        try:
            data = queue.read(name)
        except OSError:
            failed.append(name)
            continue
        queue.mark_sent()
        try:
            crash_report.upload_report(build, name, data)
        except Exception:  # noqa: BLE001 - keep the report queued
            failed.append(name)
            continue
        queue.remove(name)
        uploaded.append(name)
    payload = {
        "ok": not failed,
        "uploaded": uploaded,
        "failed": failed,
        "pending": queue.pending(),
    }
    _emit(
        args,
        payload,
        [f"Uploaded {name}" for name in uploaded]
        + [f"Still queued: {name}" for name in failed]
        + ([] if uploaded or failed else ["Nothing to upload"]),
    )
    return 0 if not failed else 1


# ----------------------------------------------------------------------
# dispatch
# ----------------------------------------------------------------------


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    argv = list(argv)
    want_json = "--json" in argv
    ap = _parser()
    try:
        args = ap.parse_args(argv)
    except _UsageError as exc:
        if want_json:
            print(json.dumps({"ok": False, "error": exc.message}, sort_keys=True))
        else:
            exc.parser.print_usage(sys.stderr)
            print(f"{exc.parser.prog}: error: {exc.message}", file=sys.stderr)
        return 2
    handlers = {
        ("status",): cmd_status,
        ("version",): cmd_version,
        ("mcp",): cmd_mcp,
        ("profile", "list"): cmd_profile_list,
        ("profile", "get"): cmd_profile_get,
        ("profile", "set"): cmd_profile_set,
        ("profile", "apply"): cmd_profile_apply,
        ("profile", "create"): cmd_profile_create,
        ("profile", "rename"): cmd_profile_rename,
        ("profile", "delete"): cmd_profile_delete,
        ("tdp",): cmd_tdp,
        ("fans", "curve", "get"): cmd_fans_curve_get,
        ("fans", "curve", "set"): cmd_fans_curve_set,
        ("fans", "max-fans"): cmd_fans_max_fans,
        ("battery",): cmd_battery,
        ("brightness",): cmd_brightness,
        ("lighting",): cmd_lighting,
        ("boondock",): cmd_boondock,
        ("button",): cmd_button,
        ("post-update", "list"): cmd_post_update_list,
        ("post-update", "run"): cmd_post_update_run,
        ("crash", "queue"): cmd_crash_queue,
        ("crash", "list"): cmd_crash_list,
        ("crash", "upload"): cmd_crash_upload,
    }
    key = tuple(
        getattr(args, name, None)
        for name in (
            "cmd",
            "profile_cmd",
            "fans_cmd",
            "curve_cmd",
            "post_cmd",
            "crash_cmd",
        )
    )
    if args.cmd == "profile":
        key = ("profile", args.profile_cmd)
    elif args.cmd == "fans":
        key = (
            ("fans", args.fans_cmd, getattr(args, "curve_cmd", None))
            if args.fans_cmd == "curve"
            else ("fans", args.fans_cmd)
        )
    elif args.cmd == "post-update":
        key = ("post-update", args.post_cmd)
    elif args.cmd == "crash":
        key = ("crash", args.crash_cmd)
    else:
        key = (args.cmd,)
    handler = handlers.get(key)
    if handler is None:
        return _fail(args, f"unknown command {' '.join(part for part in key if part)}")
    try:
        return handler(args)
    except (_InputError, OverflowError) as exc:
        return _fail_input(args, exc)
    except (CliError, KeyError, ValueError, OSError, RuntimeError) as exc:
        return _fail(args, exc)


if __name__ == "__main__":
    sys.exit(main())

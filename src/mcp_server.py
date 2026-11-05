#!/usr/bin/env python3
"""Local stdio Model Context Protocol server for `z13-helper mcp`.

The server speaks JSON-RPC 2.0 on stdin/stdout, so it has no listener and never
leaves this machine. It is a transport adapter over the existing CLI: every
tool call is rebuilt as the matching `z13-helper` argv and dispatched through
`z13_helper_cli.main(..., "--json")`. Reads therefore use the same shared
modules, and writes keep the CLI's safe-range clamps and `state_authority`
ownership.

Tools mirror the CLI leaves:

    status, version
    profile list|get|set|apply
    tdp get|set|reset
    fans curve get|set, max-fans status|on|off
    battery get|set
    brightness get|set, screen brightness get|set
    lighting get|set|clear
    boondock status|on|off
    button status
    post-update list|run
    crash queue|list|upload
"""

import contextlib
import io
import json
import os
import sys

import z13_helper_cli as cli
from versions import APP_VERSION

SERVER_NAME = "z13-helper"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_READ = {"readOnlyHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": False}
_DANGER = {"readOnlyHint": False, "destructiveHint": True}


class ToolError(ValueError):
    """A tool name, argument set or handler failure reported back to the client."""


def _schema(properties=None, required=()):
    schema = {"type": "object", "properties": properties or {}}
    if required:
        schema["required"] = list(required)
    return schema


def _tool(name, description, properties=None, required=(), annotations=None):
    return {
        "name": name,
        "description": description,
        "inputSchema": _schema(properties, required),
        "annotations": annotations or _READ,
    }


def _devices():
    return list(cli.DEVICE_CHOICES)


def _tool_specs():
    levels = list(cli.profiles.BRIGHTNESS_LEVELS)
    lighting_devices = list(cli.profiles.LIGHTING_DEVICES)
    specs = [
        _tool(
            "status",
            "Profile, fans, temperatures, battery, brightness and Max Fans at a glance.",
        ),
        _tool("version", "Print the Z13-Helper version."),
        _tool("profile_list", "List stored profiles and the active one."),
        _tool(
            "profile_get",
            "Show one stored profile; defaults to the active profile.",
            {"name": {"type": "string"}},
        ),
        _tool(
            "profile_set",
            "Save fields on one stored profile. Writes are clamped and owned by the state authority.",
            {
                "name": {"type": "string"},
                "base": {"type": "string", "enum": list(cli.profiles.BASES)},
                "tdp": {
                    "type": "string",
                    "description": "PL1,PL2,PL3 in watts, comma separated",
                },
                "pl1": {"type": "integer"},
                "pl2": {"type": "integer"},
                "pl3": {"type": "integer"},
                "curve": {
                    "type": "string",
                    "description": "eight temp:pct points, comma separated",
                },
                "battery_limit": {
                    "type": "integer",
                    "minimum": 40,
                    "maximum": 100,
                },
                "brightness": {"type": "string", "enum": levels},
                "undervolt": {"type": "integer", "minimum": -40, "maximum": 0},
                "epp": {"type": "string", "enum": list(cli.profiles.EPP_CHOICES)},
                "max_fans": {"type": "string", "enum": ["on", "off"]},
            },
            required=("name",),
            annotations=_WRITE,
        ),
        _tool(
            "profile_apply",
            "Apply a stored profile through the state authority, with the fixed write order.",
            {
                "name": {"type": "string"},
                "no_hooks": {"type": "boolean"},
            },
            required=("name",),
            annotations=_WRITE,
        ),
        _tool("tdp_get", "Show the active profile's TDP override and kernel ranges."),
        _tool(
            "tdp_set",
            "Set PL1/PL2/PL3 on the active profile, clamped to the detected safe ranges.",
            {
                "values": {
                    "type": "string",
                    "description": "PL1,PL2,PL3 (or one PL1 value) in watts",
                },
                "pl1": {
                    "type": "integer",
                    "minimum": cli.PL_MIN,
                    "maximum": cli.PL1_SAFE_MAX,
                },
                "pl2": {
                    "type": "integer",
                    "minimum": cli.PL_MIN,
                    "maximum": cli.PL_MAX,
                },
                "pl3": {
                    "type": "integer",
                    "minimum": cli.PL_MIN,
                    "maximum": cli.PL_MAX,
                },
            },
            annotations=_WRITE,
        ),
        _tool(
            "tdp_reset",
            "Clear the active profile's TDP override (firmware control).",
            annotations=_WRITE,
        ),
        _tool(
            "fans_curve_get", "Show the live fan curve and the stored profile curve."
        ),
        _tool(
            "fans_curve_set",
            "Save and apply a fan curve, clamped and owned by the state authority.",
            {
                "curve": {
                    "type": "array",
                    "description": "eight [temp_c, percent] points",
                    "items": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 2,
                        "maxItems": 2,
                    },
                }
            },
            required=("curve",),
            annotations=_WRITE,
        ),
        _tool("max_fans_status", "Show whether Max Fans is on."),
        _tool(
            "max_fans_set",
            "Turn Max Fans on or off through the state authority.",
            {"enabled": {"type": "boolean"}},
            required=("enabled",),
            annotations=_WRITE,
        ),
        _tool("battery_get", "Show the charge limit and battery state."),
        _tool(
            "battery_set",
            "Set the charge limit, rounded to 5% and clamped to 40-100%.",
            {"limit": {"type": "integer", "minimum": 40, "maximum": 100}},
            required=("limit",),
            annotations=_WRITE,
        ),
        _tool("brightness_get", "Show keyboard/rear lighting and screen brightness."),
        _tool(
            "brightness_set",
            "Set keyboard/rear brightness through the state authority.",
            {
                "level": {"type": "string", "enum": levels},
                "devices": {
                    "type": "array",
                    "items": {"type": "string", "enum": _devices()},
                },
            },
            required=("level",),
            annotations=_WRITE,
        ),
        _tool("screen_brightness_get", "Show the built-in display brightness."),
        _tool(
            "screen_brightness_set",
            "Set the built-in display brightness, clamped to the safe range.",
            {"percent": {"type": "integer", "minimum": 0, "maximum": 100}},
            required=("percent",),
            annotations=_WRITE,
        ),
        _tool("lighting_get", "Show the active profile's lighting overrides."),
        _tool(
            "lighting_set",
            "Set a lighting mode/color on one device through the state authority.",
            {
                "device": {"type": "string", "enum": lighting_devices},
                "mode": {"type": "string", "enum": list(cli.profiles.LIGHTING_MODES)},
                "color": {"type": "string", "enum": list(cli.profiles.LIGHTING_COLORS)},
                "brightness": {"type": "string", "enum": levels},
            },
            required=("device", "mode"),
            annotations=_WRITE,
        ),
        _tool(
            "lighting_clear",
            "Clear the active profile's lighting override for one device.",
            {"device": {"type": "string", "enum": lighting_devices}},
            required=("device",),
            annotations=_WRITE,
        ),
        _tool("boondock_status", "Show the Boondock low-power session state."),
        _tool(
            "boondock_set",
            "Turn the Boondock low-power profile on or off.",
            {"enabled": {"type": "boolean"}},
            required=("enabled",),
            annotations=_WRITE,
        ),
        _tool("button_status", "Show the side-button device and related services."),
        _tool("post_update_list", "List post-SteamOS-update recipes and their checks."),
        _tool(
            "post_update_run",
            "Run selected post-update recipes (or plan them with dry_run).",
            {
                "ids": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean"},
            },
            annotations=_DANGER,
        ),
        _tool(
            "crash_queue",
            "Show the crash-report queue, or queue one local .zip report.",
            {"file": {"type": "string"}},
            annotations=_WRITE,
        ),
        _tool("crash_list", "List queued crash reports."),
        _tool(
            "crash_upload",
            "Upload queued crash reports when reporting is enabled.",
            {"names": {"type": "array", "items": {"type": "string"}, "minItems": 1}},
            required=("names",),
            annotations=_DANGER,
        ),
    ]
    return specs


TOOLS = _tool_specs()
_BY_NAME = {tool["name"]: tool for tool in TOOLS}


# ----------------------------------------------------------------------
# Argv builders: one per tool, the only place tool arguments become CLI words
# ----------------------------------------------------------------------


def _present(args, key):
    return args.get(key) is not None


def _flag(args, key, flag):
    return [flag, str(args[key])] if _present(args, key) else []


def _many(args, key, flag):
    values = args.get(key) or []
    argv = []
    for value in values:
        argv += [flag, str(value)]
    return argv


def _profile_set(args):
    argv = ["profile", "set", str(args["name"])]
    for key, flag in (
        ("base", "--base"),
        ("tdp", "--tdp"),
        ("pl1", "--pl1"),
        ("pl2", "--pl2"),
        ("pl3", "--pl3"),
        ("curve", "--curve"),
        ("battery_limit", "--battery-limit"),
        ("brightness", "--brightness"),
        ("undervolt", "--undervolt"),
        ("epp", "--epp"),
        ("max_fans", "--max-fans"),
    ):
        argv += _flag(args, key, flag)
    return argv


def _profile_apply(args):
    argv = ["profile", "apply", str(args["name"])]
    return argv + (["--no-hooks"] if args.get("no_hooks") else [])


def _tdp_set(args):
    argv = ["tdp", "set"]
    if _present(args, "values"):
        argv.append(str(args["values"]))
    else:
        if not _present(args, "pl1"):
            raise ToolError("tdp_set needs values or pl1")
        for rail in ("pl1", "pl2", "pl3"):
            argv += _flag(args, rail, "--" + rail)
    return argv


def _brightness_set(args):
    return ["brightness", "set", str(args["level"])] + _many(
        args, "devices", "--device"
    )


def _lighting_set(args):
    argv = ["lighting", "set", str(args["device"]), "--mode", str(args["mode"])]
    argv += _flag(args, "color", "--color")
    argv += _flag(args, "brightness", "--brightness")
    return argv


def _post_update_run(args):
    return (
        ["post-update", "run"]
        + [str(item) for item in args.get("ids") or []]
        + (["--dry-run"] if args.get("dry_run") else [])
    )


def _crash_queue(args):
    if _present(args, "file"):
        return ["crash", "queue", str(args["file"])]
    return ["crash", "queue"]


def _crash_upload(args):
    return ["crash", "upload"] + [str(item) for item in args.get("names") or []]


_BUILDERS = {
    "status": lambda args: ["status"],
    "version": lambda args: ["version"],
    "profile_list": lambda args: ["profile", "list"],
    "profile_get": lambda args: (
        ["profile", "get"] + ([str(args["name"])] if _present(args, "name") else [])
    ),
    "profile_set": _profile_set,
    "profile_apply": _profile_apply,
    "tdp_get": lambda args: ["tdp", "get"],
    "tdp_set": _tdp_set,
    "tdp_reset": lambda args: ["tdp", "reset"],
    "fans_curve_get": lambda args: ["fans", "curve", "get"],
    "fans_curve_set": lambda args: [
        "fans",
        "curve",
        "set",
        json.dumps(args["curve"]),
    ],
    "max_fans_status": lambda args: ["fans", "max-fans", "status"],
    "max_fans_set": lambda args: [
        "fans",
        "max-fans",
        "on" if args["enabled"] else "off",
    ],
    "battery_get": lambda args: ["battery", "get"],
    "battery_set": lambda args: ["battery", "set", str(args["limit"])],
    "brightness_get": lambda args: ["brightness", "get"],
    "brightness_set": _brightness_set,
    "screen_brightness_get": lambda args: ["brightness", "screen"],
    "screen_brightness_set": lambda args: [
        "brightness",
        "screen",
        str(args["percent"]),
    ],
    "lighting_get": lambda args: ["lighting", "get"],
    "lighting_set": _lighting_set,
    "lighting_clear": lambda args: ["lighting", "clear", str(args["device"])],
    "boondock_status": lambda args: ["boondock", "status"],
    "boondock_set": lambda args: [
        "boondock",
        "on" if args["enabled"] else "off",
    ],
    "button_status": lambda args: ["button", "status"],
    "post_update_list": lambda args: ["post-update", "list"],
    "post_update_run": _post_update_run,
    "crash_queue": _crash_queue,
    "crash_list": lambda args: ["crash", "list"],
    "crash_upload": _crash_upload,
}


# ----------------------------------------------------------------------
# Tool call -> CLI dispatch
# ----------------------------------------------------------------------


def _run_cli(argv):
    """Run the CLI with --json and return (payload, return code)."""
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            rc = cli.main([*argv, "--json"])
    except SystemExit as exc:
        rc = int(exc.code or 0)
    text = buffer.getvalue().strip()
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        payload = {"ok": False, "error": text or "the CLI produced no output"}
    return payload, rc


def _matches_type(value, expected):
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True


def _validate_value(key, value, schema):
    expected = schema.get("type")
    if expected and not _matches_type(value, expected):
        raise ToolError(f"{key} must be of type {expected}")
    enum = schema.get("enum")
    if enum is not None and value not in enum:
        raise ToolError(f"{key} must be one of: " + ", ".join(map(str, enum)))
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ToolError(f"{key} must be at least {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ToolError(f"{key} must be at most {schema['maximum']}")
    if expected == "array":
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ToolError(f"{key} needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ToolError(f"{key} accepts at most {schema['maxItems']} item(s)")
        items = schema.get("items")
        if items is not None:
            for item in value:
                _validate_value(key, item, items)


def _check_tdp_numbers(numbers, pl1_first=True):
    if pl1_first and numbers and numbers[0] > cli.PL1_SAFE_MAX:
        raise ToolError(
            f"PL1 above {cli.PL1_SAFE_MAX} W needs an explicit override "
            "that MCP does not offer"
        )
    for value in numbers:
        if not cli.PL_MIN <= value <= cli.PL_MAX:
            raise ToolError(f"TDP values must be {cli.PL_MIN}..{cli.PL_MAX} W")
    if list(numbers) != sorted(numbers):
        raise ToolError("TDP values must satisfy PL1 <= PL2 <= PL3")


def _validate_tdp_set(arguments):
    if arguments.get("values") is not None:
        try:
            numbers = cli._parse_tdp_values(arguments["values"], allow_one=True)
        except cli.CliError as exc:
            raise ToolError(str(exc)) from None
        _check_tdp_numbers(numbers)
    elif arguments.get("pl1") is None:
        raise ToolError("tdp_set needs values or pl1")
    rails = [
        arguments[rail]
        for rail in ("pl1", "pl2", "pl3")
        if arguments.get(rail) is not None
    ]
    _check_tdp_numbers(rails, pl1_first=arguments.get("pl1") is not None)


def _validate_profile_set(arguments):
    rails = [
        arguments[rail]
        for rail in ("pl1", "pl2", "pl3")
        if arguments.get(rail) is not None
    ]
    if rails:
        _check_tdp_numbers(rails, pl1_first=arguments.get("pl1") is not None)
    if arguments.get("tdp") is not None:
        try:
            numbers = cli._parse_tdp_values(arguments["tdp"])
        except cli.CliError as exc:
            raise ToolError(str(exc)) from None
        _check_tdp_numbers(numbers)
    curve = arguments.get("curve")
    if curve is not None:
        try:
            points = cli._parse_curve(curve)
        except (cli.CliError, ValueError) as exc:
            raise ToolError(str(exc)) from None
        _validate_curve({"curve": points})


def _validate_curve(arguments):
    curve = arguments.get("curve")
    if curve is None:
        return
    points = []
    for point in curve:
        if not isinstance(point, list) or len(point) != 2:
            raise ToolError("curve points must be [temp_c, percent]")
        temp, percent = point
        if not isinstance(temp, int) or isinstance(temp, bool):
            raise ToolError("curve temperatures must be whole numbers")
        if not isinstance(percent, int) or isinstance(percent, bool):
            raise ToolError("curve percentages must be whole numbers")
        if not 20 <= temp <= 110:
            raise ToolError("curve temperatures must be 20..110 C")
        if not 0 <= percent <= 100:
            raise ToolError("curve percentages must be 0..100")
        points.append([temp, percent])
    try:
        cli.profiles.validate_curve(points)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


def _validated(name, arguments):
    tool = _BY_NAME.get(name)
    if tool is None:
        raise ToolError(f"unknown tool {name!r}")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ToolError("tool arguments must be an object")
    properties = tool["inputSchema"].get("properties") or {}
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise ToolError("unknown argument(s): " + ", ".join(unknown))
    missing = [
        key for key in tool["inputSchema"].get("required", []) if key not in arguments
    ]
    if missing:
        raise ToolError("missing argument(s): " + ", ".join(missing))
    for key, value in arguments.items():
        _validate_value(key, value, properties[key])
    if name == "tdp_set":
        _validate_tdp_set(arguments)
    if name == "post_update_run":
        if not (arguments.get("ids") or arguments.get("dry_run")):
            raise ToolError("post_update_run needs ids, or dry_run to plan")
        if any(not item or item.startswith("-") for item in arguments.get("ids", [])):
            raise ToolError("recipe IDs must be nonempty and must not start with '-'")
    if name in ("crash_queue", "crash_upload"):
        if name == "crash_queue":
            names = [arguments["file"]] if _present(arguments, "file") else []
        else:
            names = arguments.get("names")
            if not names:
                raise ToolError("crash_upload needs the report names to upload")
        for item in names:
            if item != os.path.basename(item) or not item.endswith(".zip"):
                raise ToolError("crash report names must be plain .zip file names")
    if name == "fans_curve_set":
        _validate_curve(arguments)
    if name == "profile_set":
        _validate_profile_set(arguments)
    return arguments


def call_tool(name, arguments=None):
    """Dispatch one tool and return an MCP tools/call result object."""
    try:
        args = _validated(name, arguments)
        argv = _BUILDERS[name](args)
        payload, rc = _run_cli(argv)
        error = rc != 0 or (isinstance(payload, dict) and payload.get("ok") is False)
    except Exception as exc:  # never let a tool call kill the server
        payload, error = {"ok": False, "error": str(exc)}, True
    return {
        "content": [{"type": "text", "text": json.dumps(payload, sort_keys=True)}],
        "isError": bool(error),
    }


# ----------------------------------------------------------------------
# JSON-RPC 2.0 over stdio
# ----------------------------------------------------------------------


def _error(message_id, code, message):
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": code, "message": message},
    }


def _initialize(params):
    requested = params.get("protocolVersion") if isinstance(params, dict) else None
    protocol = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
    return {
        "protocolVersion": protocol,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": APP_VERSION},
    }


def handle_message(message):
    """Answer one decoded JSON-RPC message; None means it was a notification."""
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    message_id = message.get("id")
    method = message.get("method")
    if not isinstance(method, str):
        if message_id is None:
            return None
        return _error(message_id, -32600, "Invalid Request")
    if method.startswith("notifications/"):
        return None
    if message_id is None:
        return None
    params = message.get("params") or {}
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": message_id, "result": _initialize(params)}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": message_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": message_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _error(message_id, -32602, "tools/call needs a tool name")
        result = call_tool(params["name"], params.get("arguments"))
        return {"jsonrpc": "2.0", "id": message_id, "result": result}
    return _error(message_id, -32601, f"Method not found: {method}")


def serve(stdin=None, stdout=None):
    """Read newline-delimited JSON-RPC from stdin until EOF; write to stdout."""
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except (ValueError, RecursionError):
            response = _error(None, -32700, "Parse error")
        else:
            response = handle_message(message)
        if response is not None:
            stdout.write(json.dumps(response, sort_keys=True) + "\n")
            stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(serve())

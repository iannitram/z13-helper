"""MCP server: tool list, safe dispatch through the state owner, JSON-RPC round trip."""

import io
import json

import pytest

import mcp_server
import z13_helper_cli as cli

EXPECTED_TOOLS = {
    "status",
    "version",
    "profile_list",
    "profile_get",
    "profile_set",
    "profile_apply",
    "tdp_get",
    "tdp_set",
    "tdp_reset",
    "fans_curve_get",
    "fans_curve_set",
    "max_fans_status",
    "max_fans_set",
    "battery_get",
    "battery_set",
    "brightness_get",
    "brightness_set",
    "screen_brightness_get",
    "screen_brightness_set",
    "lighting_get",
    "lighting_set",
    "lighting_clear",
    "boondock_status",
    "boondock_set",
    "button_status",
    "post_update_list",
    "post_update_run",
    "crash_queue",
    "crash_list",
    "crash_upload",
}


def test_tool_list_covers_every_cli_leaf():
    names = {tool["name"] for tool in mcp_server.TOOLS}
    assert names == EXPECTED_TOOLS
    assert "mcp" not in names


@pytest.mark.parametrize("tool", mcp_server.TOOLS, ids=lambda t: t["name"])
def test_every_tool_has_a_description_and_object_schema(tool):
    assert tool["description"]
    schema = tool["inputSchema"]
    assert schema["type"] == "object"
    assert isinstance(schema["properties"], dict)
    for missing in schema.get("required", []):
        assert missing in schema["properties"]


def payload_of(result):
    assert result["isError"] is False, result
    return json.loads(result["content"][0]["text"])


def test_version_tool_dispatches_read_only(profiles_env):
    data = payload_of(mcp_server.call_tool("version"))
    assert data["name"] == "z13-helper"
    assert data["version"]


def test_profile_list_reads_the_shared_store(profiles_env):
    data = payload_of(mcp_server.call_tool("profile_list"))
    assert data["active"] == "Balanced"
    assert {row["name"] for row in data["profiles"]} >= {"Balanced", "Turbo"}


@pytest.fixture
def owner(monkeypatch):
    calls = []

    def record(name, result=None):
        def action(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        monkeypatch.setattr(cli.authority, name, action)

    return calls, record


def test_tdp_set_dispatches_with_the_cli_ranges(owner, monkeypatch):
    calls, record = owner
    record("set_field")
    monkeypatch.setattr(
        cli.power_range,
        "detect_ranges",
        lambda: {"pl1": (20, 40), "pl2": (20, 60), "pl3": (20, 93)},
    )
    data = payload_of(mcp_server.call_tool("tdp_set", {"values": "39,50,60"}))
    assert data["tdp"] == {"pl1": 39, "pl2": 50, "pl3": 60, "force": False}
    assert calls == [("set_field", ("tdp", data["tdp"]), {})]


@pytest.mark.parametrize(
    "arguments",
    [
        {"values": "100,10,50"},
        {"values": "40,30,50"},
        {"values": "40,50"},
        {"pl1": 4},
        {"pl1": 100},
        {"pl1": 40, "pl2": 30},
        {"pl1": 40, "pl2": 50, "pl3": 20},
    ],
)
def test_tdp_set_rejects_out_of_range_or_ordered_pls_before_the_owner(owner, arguments):
    calls, record = owner
    record("set_field")
    result = mcp_server.call_tool("tdp_set", arguments)
    assert result["isError"] is True
    assert calls == []


@pytest.mark.parametrize(
    "name, combined",
    [("tdp_set", "values"), ("profile_set", "tdp")],
)
@pytest.mark.parametrize(
    "rails",
    [
        {"pl1": 40},
        {"pl2": 50},
        {"pl3": 60},
        {"pl1": 40, "pl2": 50},
        {"pl1": 40, "pl3": 60},
        {"pl2": 50, "pl3": 60},
        {"pl1": 40, "pl2": 50, "pl3": 60},
    ],
)
def test_combined_tdp_and_rails_are_rejected_before_cli_dispatch(
    monkeypatch, name, combined, rails
):
    calls = []

    def dispatch(argv):
        calls.append(argv)
        return {"ok": True}, 0

    monkeypatch.setattr(mcp_server, "_run_cli", dispatch)
    arguments = {combined: "40,50,60", **rails}
    if name == "profile_set":
        arguments["name"] = "Turbo"
    result = mcp_server.call_tool(name, arguments)
    assert result["isError"] is True
    error = json.loads(result["content"][0]["text"])["error"]
    assert error == f"{combined} cannot be combined with pl1, pl2, or pl3"
    assert calls == []


@pytest.mark.parametrize(
    "name, arguments, argv",
    [
        ("tdp_set", {"values": "40,50,60"}, ["tdp", "set", "40,50,60"]),
        (
            "tdp_set",
            {"pl1": 40, "pl2": 50, "pl3": 60},
            ["tdp", "set", "--pl1", "40", "--pl2", "50", "--pl3", "60"],
        ),
        (
            "profile_set",
            {"name": "Turbo", "tdp": "40,50,60"},
            ["profile", "set", "Turbo", "--tdp", "40,50,60"],
        ),
        (
            "profile_set",
            {"name": "Turbo", "pl1": 40, "pl2": 50, "pl3": 60},
            ["profile", "set", "Turbo", "--pl1", "40", "--pl2", "50", "--pl3", "60"],
        ),
    ],
)
def test_each_tdp_argument_form_dispatches_unchanged(monkeypatch, name, arguments, argv):
    calls = []

    def dispatch(actual):
        calls.append(actual)
        return {"ok": True}, 0

    monkeypatch.setattr(mcp_server, "_run_cli", dispatch)
    payload_of(mcp_server.call_tool(name, arguments))
    assert calls == [argv]


def test_battery_set_rounds_and_clamps_through_the_owner(owner):
    calls, record = owner
    record("set_field")
    data = payload_of(mcp_server.call_tool("battery_set", {"limit": 87}))
    assert data["limit"] == 85
    assert calls == [("set_field", ("battery_limit", 85), {})]


def test_fans_curve_set_dispatches_a_valid_point_array(owner):
    calls, record = owner
    record("set_field", "Fan curve applied")
    curve = [[temp, temp - 20] for temp in range(20, 100, 10)]
    data = payload_of(mcp_server.call_tool("fans_curve_set", {"curve": curve}))
    assert data["curve"] == curve
    assert calls == [("set_field", ("fan_curve", curve), {})]


@pytest.mark.parametrize(
    "curve",
    [
        "20:20,30:30,40:40,50:50,60:60,70:70,80:80,90:90",
        [[20, 20]],
        [
            [20, 20],
            [30, 30],
            [40, 40],
            [50, 50],
            [60, 60],
            [70, 70],
            [80, 80],
            [90, 101],
        ],
        [
            [10, 20],
            [30, 30],
            [40, 40],
            [50, 50],
            [60, 60],
            [70, 70],
            [80, 80],
            [90, 90],
        ],
        [
            [20, 20],
            [30, 30],
            [40, 40],
            [50, 50],
            [60, 60],
            [70, 70],
            [80, 80],
            [90, "90"],
        ],
        [
            [20, 20, 1],
            [30, 30],
            [40, 40],
            [50, 50],
            [60, 60],
            [70, 70],
            [80, 80],
            [90, 90],
        ],
        [
            [30, 20],
            [20, 30],
            [40, 40],
            [50, 50],
            [60, 60],
            [70, 70],
            [80, 80],
            [90, 90],
        ],
        [
            [20, 30],
            [30, 20],
            [40, 40],
            [50, 50],
            [60, 60],
            [70, 70],
            [80, 80],
            [90, 90],
        ],
    ],
)
def test_fans_curve_set_rejects_bad_curves_before_the_owner(owner, curve):
    calls, record = owner
    record("set_field")
    result = mcp_server.call_tool("fans_curve_set", {"curve": curve})
    assert result["isError"] is True
    assert calls == []


def test_max_fans_set_uses_the_owner(owner):
    calls, record = owner
    record("set_max_fans", "Max Fans ON")
    result = mcp_server.call_tool("max_fans_set", {"enabled": True})
    assert payload_of(result) == {"max_fans": True, "message": "Max Fans ON"}
    assert calls == [("set_max_fans", (True,), {})]


def test_profile_apply_uses_the_owner(owner):
    calls, record = owner
    record("apply_profile", [("profile", True, "Turbo")])
    data = payload_of(mcp_server.call_tool("profile_apply", {"name": "Turbo"}))
    assert data["ok"] is True and data["steps"] == [["profile", True, "Turbo"]]
    assert calls == [("apply_profile", ("Turbo",), {"skip_hooks": False})]


def test_profile_set_dispatches_a_clamped_field(owner, profiles_env):
    calls, record = owner
    record("edit")
    data = payload_of(
        mcp_server.call_tool("profile_set", {"name": "Turbo", "battery_limit": 87})
    )
    assert data["updated"] == ["battery_limit"]
    assert calls and calls[0][0] == "edit"
    assert calls[0][1][0]["battery_limit"] == 85


@pytest.mark.parametrize(
    "arguments",
    [
        {"pl1": 4},
        {"pl1": 100},
        {"pl2": 4},
        {"pl2": 100},
        {"pl3": 4},
        {"pl3": 100},
        {"pl1": 40, "pl2": 30},
        {"pl1": 40, "pl2": 50, "pl3": 20},
    ],
)
def test_profile_set_rejects_bad_pl_fields_before_the_owner(
    owner, profiles_env, arguments
):
    calls, record = owner
    record("edit")
    result = mcp_server.call_tool("profile_set", {"name": "Turbo", **arguments})
    assert result["isError"] is True
    assert calls == []


@pytest.mark.parametrize(
    "tdp",
    ["4,50,60", "40,50,100", "40,30,50", "40,50"],
)
def test_profile_set_rejects_bad_tdp_string_before_the_owner(owner, profiles_env, tdp):
    calls, record = owner
    record("edit")
    result = mcp_server.call_tool("profile_set", {"name": "Turbo", "tdp": tdp})
    assert result["isError"] is True
    assert calls == []


@pytest.mark.parametrize(
    "curve",
    [
        "20:20,30:30,40:40,50:50,60:60,70:70,80:80,90:101",
        "10:20,30:30,40:40,50:50,60:60,70:70,80:80,90:90",
        "20:20,30:30,40:40,50:50,60:60,70:70,80:80",
        "20:20,30:30,40:40,50:50,60:60,70:70,80:80,90:90,100:100",
        "20:20,30:30,40:40,50:50,60:60,70:70,80:80,90:90,bad",
    ],
)
def test_profile_set_rejects_bad_curve_string_before_the_owner(
    owner, profiles_env, curve
):
    calls, record = owner
    record("edit")
    result = mcp_server.call_tool("profile_set", {"name": "Turbo", "curve": curve})
    assert result["isError"] is True
    assert calls == []


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("max_fans_set", {"enabled": "false"}),
        ("boondock_set", {"enabled": 0}),
        ("battery_set", {"limit": "85"}),
        ("battery_set", {"limit": 85.0}),
    ],
)
def test_wrong_types_are_rejected_before_the_owner(owner, name, arguments):
    calls, record = owner
    record("set_field")
    record("set_max_fans")
    result = mcp_server.call_tool(name, arguments)
    assert result["isError"] is True
    assert "must be of type" in result["content"][0]["text"]
    assert calls == []


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("battery_set", {"limit": 39}),
        ("battery_set", {"limit": 101}),
        ("profile_set", {"name": "Turbo", "battery_limit": 39}),
        ("profile_set", {"name": "Turbo", "battery_limit": 101}),
        ("screen_brightness_set", {"percent": -1}),
        ("screen_brightness_set", {"percent": 101}),
    ],
)
def test_out_of_range_limits_are_rejected_before_the_owner(
    owner, profiles_env, name, arguments
):
    calls, record = owner
    record("set_field")
    record("edit")
    result = mcp_server.call_tool(name, arguments)
    assert result["isError"] is True
    assert "must be at" in result["content"][0]["text"]
    assert calls == []


def test_missing_and_unknown_arguments_are_tool_errors(profiles_env):
    missing = mcp_server.call_tool("battery_set", {})
    assert missing["isError"] is True
    assert "missing argument" in missing["content"][0]["text"]

    unknown_tool = mcp_server.call_tool("not_a_tool", {})
    assert unknown_tool["isError"] is True
    assert "unknown tool" in unknown_tool["content"][0]["text"]

    unknown_arg = mcp_server.call_tool("version", {"verbose": True})
    assert unknown_arg["isError"] is True
    assert "unknown argument" in unknown_arg["content"][0]["text"]


def test_cli_errors_come_back_as_tool_errors(profiles_env):
    result = mcp_server.call_tool("profile_get", {"name": "missing"})
    assert result["isError"] is True
    data = json.loads(result["content"][0]["text"])
    assert data["ok"] is False and "no profile" in data["error"]


@pytest.mark.parametrize(
    "name, arguments",
    [
        ("post_update_run", {"ids": ["--json"]}),
        ("post_update_run", {"ids": ["--dry-run"]}),
        ("post_update_run", {"ids": ["--"]}),
        ("post_update_run", {"ids": ["-h"]}),
        ("post_update_run", {"ids": [""]}),
        ("post_update_run", {"ids": ["amdgpu", "--json"]}),
        ("post_update_run", {"ids": ["--json"], "dry_run": True}),
        ("post_update_run", {}),
        ("crash_queue", {"file": "../report.zip"}),
        ("crash_queue", {"file": "/tmp/report.zip"}),
        ("crash_queue", {"file": "nested/report.zip"}),
        ("crash_queue", {"file": "./report.zip"}),
        ("crash_queue", {"file": "report.txt"}),
        ("crash_upload", {"names": ["../report.zip"]}),
        ("crash_upload", {"names": ["/tmp/report.zip"]}),
    ],
)
def test_unsafe_arguments_are_rejected_before_cli_dispatch(monkeypatch, name, arguments):
    calls = []

    def dispatch(argv):
        calls.append(argv)
        return {"ok": True}, 0

    monkeypatch.setattr(mcp_server, "_run_cli", dispatch)
    result = mcp_server.call_tool(name, arguments)
    assert result["isError"] is True
    assert calls == []


@pytest.mark.parametrize(
    "arguments, ids, dry_run",
    [
        ({"ids": ["amdgpu"]}, ["amdgpu"], False),
        ({"ids": ["amdgpu", "keyring"]}, ["amdgpu", "keyring"], False),
        ({"ids": ["amdgpu"], "dry_run": True}, ["amdgpu"], True),
        ({"dry_run": True}, [], True),
    ],
)
def test_post_update_selection_survives_cli_parsing(monkeypatch, arguments, ids, dry_run):
    calls = []

    def dispatch(argv):
        calls.append(cli._parser().parse_args([*argv, "--json"]))
        return {"ok": True}, 0

    monkeypatch.setattr(mcp_server, "_run_cli", dispatch)
    payload_of(mcp_server.call_tool("post_update_run", arguments))
    assert len(calls) == 1
    assert calls[0].ids == ids
    assert calls[0].dry_run is dry_run


def test_crash_queue_accepts_a_plain_zip_name(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "CRASH_DIR", str(tmp_path / "queue"))
    (tmp_path / "report.zip").write_bytes(b"PK")
    assert payload_of(mcp_server.call_tool("crash_queue"))["pending"] == []
    data = payload_of(mcp_server.call_tool("crash_queue", {"file": "report.zip"}))
    assert data["queued"] == "report.zip"
    assert data["pending"] == ["report.zip"]
    assert (tmp_path / "queue" / "report.zip").read_bytes() == b"PK"


def test_json_rpc_round_trip_on_fake_stdio(profiles_env):
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"},
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "version", "arguments": {}},
        },
        {"jsonrpc": "2.0", "id": 4, "method": "does/not/exist"},
    ]
    stdin = io.StringIO("\n".join(json.dumps(message) for message in messages) + "\n")
    stdout = io.StringIO()
    assert mcp_server.serve(stdin, stdout) == 0
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r["id"] for r in responses] == [1, 2, 3, 4]
    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert responses[0]["result"]["serverInfo"]["name"] == "z13-helper"
    assert {tool["name"] for tool in responses[1]["result"]["tools"]} == EXPECTED_TOOLS
    assert responses[2]["result"]["isError"] is False
    assert responses[3]["error"]["code"] == -32601


def test_parse_error_gets_a_json_rpc_error():
    stdout = io.StringIO()
    mcp_server.serve(io.StringIO("{not json\n"), stdout)
    response = json.loads(stdout.getvalue())
    assert response["error"]["code"] == -32700
    assert response["id"] is None


@pytest.mark.parametrize("opening, closing", [("[", "]"), ('{"a":', "}")])
def test_json_parser_recursion_error_is_reported_and_server_continues(
    monkeypatch, opening, closing
):
    depth = 2000
    nested = opening * depth + "0" + closing * depth
    loads = json.loads

    def parse(text):
        # JSON decoder nesting limits vary between Python versions.
        if text == nested:
            raise RecursionError("maximum recursion depth exceeded")
        return loads(text)

    monkeypatch.setattr(mcp_server.json, "loads", parse)
    ping = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
    stdout = io.StringIO()
    assert mcp_server.serve(
        io.StringIO(nested + "\n" + json.dumps(ping) + "\n"), stdout
    ) == 0
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert responses == [
        {
            "jsonrpc": "2.0",
            "id": None,
            "error": {"code": -32700, "message": "Parse error"},
        },
        {"jsonrpc": "2.0", "id": 1, "result": {}},
    ]

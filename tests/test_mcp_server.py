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


def test_tdp_set_clamps_with_the_cli_ranges(owner, monkeypatch):
    calls, record = owner
    record("set_field")
    monkeypatch.setattr(
        cli.power_range,
        "detect_ranges",
        lambda: {"pl1": (20, 40), "pl2": (20, 60), "pl3": (20, 93)},
    )
    data = payload_of(mcp_server.call_tool("tdp_set", {"values": "100,10,50"}))
    assert data["tdp"] == {"pl1": 40, "pl2": 40, "pl3": 50, "force": False}
    assert calls == [("set_field", ("tdp", data["tdp"]), {})]


def test_battery_set_rounds_and_clamps_through_the_owner(owner):
    calls, record = owner
    record("set_field")
    data = payload_of(mcp_server.call_tool("battery_set", {"limit": 87}))
    assert data["limit"] == 85
    assert calls == [("set_field", ("battery_limit", 85), {})]


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

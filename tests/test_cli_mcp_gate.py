"""#22: CLI and MCP refuse every hardware write on an unsupported machine; reads stay."""

import json

import pytest

import mcp_server
import platform_support as ps
import z13_helper_cli as cli

REASON = "Z13-Helper supports the ASUS ROG Flow Z13 GZ302EA only (this is Other Laptop)"

WRITES = [
    ["profile", "apply", "Turbo"],
    ["profile", "set", "Turbo", "--pl1", "40"],
    ["tdp", "set", "40,50,60"],
    ["tdp", "--pl1", "40"],
    ["tdp", "reset"],
    ["fans", "curve", "set", "40:10,50:20,60:30,70:40,80:50,85:60,90:80,95:100"],
    ["fans", "max-fans", "on"],
    ["battery", "set", "80"],
    ["brightness", "set", "low"],
    ["brightness", "screen", "50"],
    ["lighting", "set", "keyboard", "--mode", "static"],
    ["boondock", "on"],
]
READS = [
    ["profile", "list"],
    ["tdp", "get"],
    ["fans", "max-fans", "status"],
    ["battery", "get"],
    ["brightness", "get"],
    ["boondock", "status"],
]


@pytest.fixture
def unsupported(monkeypatch):
    monkeypatch.setattr(cli, "_unsupported_reason", lambda: REASON)
    touched = []

    def trap(*a, **k):
        touched.append(a)
        return []

    for name in ("apply_profile", "set_field", "set_max_fans", "edit"):
        monkeypatch.setattr(cli.authority, name, trap)
    return touched


@pytest.mark.parametrize("argv", WRITES, ids=lambda a: " ".join(a[:3]))
def test_cli_refuses_each_write_with_the_platform_reason(argv, unsupported, capsys):
    assert cli.main([*argv, "--json"]) == 1
    out = json.loads(capsys.readouterr().out)
    assert out == {"ok": False, "error": REASON}
    assert unsupported == []


@pytest.mark.parametrize("argv", READS, ids=lambda a: " ".join(a))
def test_reads_are_not_gated(argv):
    key_args = cli._parser().parse_args(argv)
    key = (key_args.cmd,) if key_args.cmd not in ("profile", "fans") else tuple(
        v for v in (key_args.cmd, getattr(key_args, "profile_cmd", None) or getattr(key_args, "fans_cmd", None)) if v)
    assert cli._is_hardware_write(key, key_args) is False


def test_cli_write_proceeds_on_a_supported_machine(monkeypatch, capsys, profiles_env):
    monkeypatch.setattr(cli, "_unsupported_reason", lambda: "")
    calls = []
    monkeypatch.setattr(cli.authority, "set_field", lambda *a, **k: calls.append(a))
    assert cli.main(["battery", "set", "80", "--json"]) == 0
    assert calls == [("battery_limit", 80)]


@pytest.mark.parametrize("tool,arguments", [
    ("tdp_set", {"values": "40,50,60"}),
    ("battery_set", {"limit": 80}),
    ("max_fans_set", {"enabled": True}),
    ("boondock_set", {"enabled": True}),
])
def test_mcp_write_tools_return_the_reason_as_an_error(tool, arguments, unsupported):
    result = mcp_server.call_tool(tool, arguments)
    assert result["isError"] is True
    assert json.loads(result["content"][0]["text"])["error"] == REASON
    assert unsupported == []


def test_mcp_read_tool_still_works_when_unsupported(unsupported, profiles_env):
    result = mcp_server.call_tool("profile_list")
    assert result["isError"] is False


def test_unsupported_reason_comes_from_platform_support(monkeypatch):
    monkeypatch.undo()          # drop the conftest stub: test the real function
    real = cli._unsupported_reason
    monkeypatch.setattr(ps, "detect", lambda *a, **k: {"supported": False, "reason": "not a Z13"})
    assert real() == "not a Z13"
    monkeypatch.setattr(ps, "detect", lambda *a, **k: {"supported": True, "reason": ""})
    assert real() == ""

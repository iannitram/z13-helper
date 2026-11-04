"""z13-helper CLI: argparse surface and dispatch through the shared state owner."""

import json

import pytest

import hardware_controller
import profiles
import versions
import z13_helper_cli as cli

CURVE = [
    [20, 30],
    [30, 40],
    [40, 50],
    [50, 60],
    [60, 70],
    [70, 80],
    [80, 90],
    [90, 100],
]
CURVE_SPEC = ",".join(f"{t}:{p}" for t, p in CURVE)

COMMANDS = [
    ["status"],
    ["profile", "list"],
    ["profile", "get"],
    ["profile", "get", "Turbo"],
    ["profile", "set", "Turbo", "--pl1", "40"],
    ["profile", "apply", "Turbo"],
    ["tdp"],
    ["tdp", "set", "40,50,60"],
    ["tdp", "reset"],
    ["fans", "curve", "get"],
    ["fans", "curve", "set", CURVE_SPEC],
    ["fans", "max-fans", "status"],
    ["fans", "max-fans", "on"],
    ["battery"],
    ["battery", "set", "80"],
    ["brightness"],
    ["brightness", "set", "low"],
    ["brightness", "screen", "50"],
    ["lighting"],
    ["lighting", "set", "lightbar", "--mode", "static"],
    ["lighting", "clear", "keyboard"],
    ["boondock", "status"],
    ["button"],
    ["post-update", "list"],
    ["post-update", "run", "amdgpu", "--dry-run"],
    ["crash", "queue"],
    ["crash", "list"],
    ["crash", "upload"],
    ["version"],
]


@pytest.mark.parametrize("argv", COMMANDS)
def test_parser_accepts_every_command(argv):
    args = cli._parser().parse_args(argv)
    assert args.cmd == argv[0]


@pytest.mark.parametrize("argv", COMMANDS)
def test_every_command_accepts_json(argv):
    args = cli._parser().parse_args(argv + ["--json"])
    assert args.json is True


def test_parse_error_honours_json(capsys):
    assert cli.main(["bogus", "--json"]) == 2
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["ok"] is False and "invalid choice" in data["error"]
    assert "usage:" not in captured.out.lower()
    assert captured.err == ""


@pytest.mark.parametrize("device", ["both", "all"])
def test_lighting_rejects_combined_devices_in_argparse(device, capsys):
    assert cli.main(["lighting", "--device", device, "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "invalid choice" in data["error"]


def run_json(capsys, argv):
    assert cli.main(argv + ["--json"]) == 0
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def owner(monkeypatch):
    calls = []

    def record(name, result=None):
        def action(*args, **kwargs):
            calls.append((name, args, kwargs))
            return result

        monkeypatch.setattr(cli.authority, name, action)

    return calls, record


def test_profile_apply_goes_through_the_owner(owner, capsys):
    calls, record = owner
    record("apply_profile", [("profile", True, "Turbo")])
    data = run_json(capsys, ["profile", "apply", "Turbo"])
    assert data["ok"] is True and data["steps"] == [["profile", True, "Turbo"]]
    assert calls == [("apply_profile", ("Turbo",), {"skip_hooks": False})]


def test_fans_max_fans_goes_through_the_owner(owner, capsys):
    calls, record = owner
    record("set_max_fans", "Max Fans ON")
    data = run_json(capsys, ["fans", "max-fans", "on"])
    assert data == {"max_fans": True, "message": "Max Fans ON"}
    assert calls == [("set_max_fans", (True,), {})]


def test_tdp_set_clamps_with_the_gui_ranges(owner, monkeypatch, capsys):
    calls, record = owner
    record("set_field")
    monkeypatch.setattr(
        cli.power_range,
        "detect_ranges",
        lambda: {"pl1": (20, 40), "pl2": (20, 60), "pl3": (20, 93)},
    )
    data = run_json(capsys, ["tdp", "set", "100,10,50"])
    assert data["tdp"] == {"pl1": 40, "pl2": 40, "pl3": 50, "force": False}
    assert calls == [("set_field", ("tdp", data["tdp"]), {})]


def test_battery_set_rounds_and_clamps(owner, capsys):
    calls, record = owner
    record("set_field")
    data = run_json(capsys, ["battery", "set", "87"])
    assert data["limit"] == 85
    assert calls == [("set_field", ("battery_limit", 85), {})]


@pytest.mark.parametrize("value", ["inf", "nan", "1e309"])
def test_battery_set_rejects_non_finite_values(owner, capsys, value):
    calls, record = owner
    record("set_field")
    assert cli.main(["battery", "set", value, "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "finite" in data["error"]
    assert calls == []


def test_brightness_set_passes_devices(owner, capsys):
    calls, record = owner
    record("set_field", [])
    data = run_json(capsys, ["brightness", "set", "low", "--device", "lightbar"])
    assert data["level"] == "low" and data["devices"] == ["lightbar"]
    assert calls == [("set_field", ("brightness", "low"), {"devices": ("lightbar",)})]


def test_lighting_set_goes_through_the_owner(owner, profiles_env, capsys):
    calls, record = owner
    record("set_lighting", "applied")
    data = run_json(
        capsys,
        [
            "lighting",
            "set",
            "lightbar",
            "--mode",
            "breathe",
            "--color",
            "blue",
            "--brightness",
            "high",
        ],
    )
    assert data["device"] == "lightbar" and data["mode"] == "breathe"
    assert calls == [
        (
            "set_lighting",
            ("Balanced", "lightbar", {"mode": "breathe", "color": "blue"}, "high"),
            {},
        )
    ]


def test_profile_set_uses_edit_for_a_hardware_field(owner, profiles_env, capsys):
    calls, record = owner
    record("edit")
    data = run_json(capsys, ["profile", "set", "Turbo", "--pl1", "40"])
    assert data["updated"] == ["tdp"]
    assert calls and calls[0][0] == "edit" and calls[0][2] == {}
    assert calls[0][1][0]["tdp"]["pl1"] == 40


def test_profile_set_applies_every_non_hardware_field(owner, profiles_env, capsys):
    calls, record = owner
    record("edit")
    data = run_json(
        capsys, ["profile", "set", "Turbo", "--base", "quiet", "--max-fans", "on"]
    )
    assert data["updated"] == ["base", "max_fans"]
    assert [call[1][1] for call in calls] == ["base", "max_fans"]
    assert all(call[1][0]["base"] == "quiet" for call in calls)
    assert all(call[1][0]["max_fans"] is True for call in calls)


def test_profile_set_requires_a_field(profiles_env, capsys):
    assert cli.main(["profile", "set", "Turbo", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "at least one field" in data["error"]


def test_fans_curve_set_validates_and_dispatches(owner, capsys):
    calls, record = owner
    record("set_field", "custom curve")
    data = run_json(capsys, ["fans", "curve", "set", CURVE_SPEC])
    assert data["curve"] == CURVE
    assert calls == [("set_field", ("fan_curve", CURVE), {})]


def test_fans_curve_set_rejects_a_bad_curve(profiles_env, capsys):
    assert cli.main(["fans", "curve", "set", "20:20,30:10", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False


def test_profile_list_and_get(profiles_env, capsys):
    data = run_json(capsys, ["profile", "list"])
    assert data["active"] == "Balanced"
    assert {row["name"] for row in data["profiles"]} >= {
        "Silent",
        "Balanced",
        "Turbo",
        "Boondock",
    }
    turbo = run_json(capsys, ["profile", "get", "Turbo"])
    assert turbo["name"] == "Turbo" and turbo["base"] == "performance"


def test_version(profiles_env, capsys):
    data = run_json(capsys, ["version"])
    assert data == {"name": "z13-helper", "version": versions.APP_VERSION}


class FakeReader:
    def get_fan_speeds(self):
        return 1200, 1300

    def get_platform_profile(self):
        return "balanced"

    def custom_fan_curve_enabled(self):
        return True

    def get_apu_temp(self):
        return 45.0

    def get_gpu_temp(self):
        return 42.0

    def get_battery(self):
        return {"capacity": 70, "status": "Discharging", "power_w": 12.5}


class FakeController:
    def get_battery_limit(self):
        return 80

    def get_fancurve(self):
        return {
            "mode": "manual",
            "rpm": 1200,
            "points": [
                (t, pwm, p)
                for (t, p), pwm in ((point, int(point[1] * 2.55)) for point in CURVE)
            ],
        }


def test_status_reads_sensors_and_profile(monkeypatch, profiles_env, capsys):
    monkeypatch.setattr(cli.sensor_reader, "SensorReader", FakeReader)
    monkeypatch.setattr(cli, "_screen_percent", lambda: 55)
    monkeypatch.setattr(hardware_controller, "HardwareController", FakeController)
    data = run_json(capsys, ["status"])
    assert data["active"] == "Balanced"
    assert data["fans"]["rpm"] == [1200, 1300]
    assert data["temperatures"] == {"apu": 45.0, "gpu": 42.0}
    assert data["battery"]["charge_limit"] == 80
    assert data["screen_brightness"] == 55


def test_fans_curve_get_reports_live_and_stored(monkeypatch, profiles_env, capsys):
    monkeypatch.setattr(hardware_controller, "HardwareController", FakeController)
    data = run_json(capsys, ["fans", "curve", "get"])
    assert data["live"]["mode"] == "manual"
    assert data["profile"] == "Balanced"


def test_crash_list_and_queue(tmp_path, monkeypatch, profiles_env, capsys):
    monkeypatch.setattr(cli, "CRASH_DIR", str(tmp_path / "crash"))
    assert run_json(capsys, ["crash", "list"])["pending"] == []
    report = tmp_path / "report.zip"
    report.write_bytes(b"PK")
    data = run_json(capsys, ["crash", "queue", str(report)])
    assert data["queued"] == "report.zip"
    assert data["pending"] == ["report.zip"]


def test_crash_upload_refuses_without_a_build_config(
    tmp_path, monkeypatch, profiles_env, capsys
):
    import crash_report

    monkeypatch.setattr(cli, "CRASH_DIR", str(tmp_path / "crash"))
    monkeypatch.setattr(crash_report, "BUILD_CONFIG", tmp_path / "missing.json")
    assert cli.main(["crash", "upload", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "build-time crash drop" in data["error"]


def test_post_update_list_uses_the_recipe_package(
    fake_host, tmp_path, monkeypatch, profiles_env, capsys
):
    monkeypatch.setattr(cli, "POST_UPDATE_LOCAL_DIR", str(tmp_path / "none"))
    data = run_json(capsys, ["post-update", "list"])
    ids = {row["id"] for row in data["recipes"]}
    assert {"amdgpu", "keyring", "carry-forward", "etc-snapshot", "rocm"} <= ids
    assert data["errors"] == []


def test_post_update_dry_run_plans_without_applying(
    fake_host, tmp_path, monkeypatch, profiles_env, capsys
):
    monkeypatch.setattr(cli, "POST_UPDATE_LOCAL_DIR", str(tmp_path / "none"))
    data = run_json(capsys, ["post-update", "run", "amdgpu", "--dry-run"])
    assert data["dry_run"] is True and data["ids"] == ["amdgpu"]
    assert not any("update-grub" in line for line in fake_host.lines)
    assert not any("tee" in argv for argv in fake_host.argvs)

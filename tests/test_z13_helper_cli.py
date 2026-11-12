"""z13-helper CLI: argparse surface and dispatch through the shared state owner."""

import json
from contextlib import contextmanager

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


def test_battery_set_rounds_step(owner, capsys):
    calls, record = owner
    record("set_field")
    data = run_json(capsys, ["battery", "set", "87"])
    assert data["limit"] == 85
    assert calls == [("set_field", ("battery_limit", 85), {})]


@pytest.mark.parametrize("value", ["30", "39", "101", "120", "-10"])
def test_battery_set_rejects_out_of_range(owner, capsys, value):
    calls, record = owner
    record("set_field")
    assert cli.main(["battery", "set", value, "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "out of range" in data["error"]
    assert calls == []


def test_profile_set_rejects_out_of_range_battery(owner, profiles_env, capsys):
    calls, record = owner
    record("edit")
    assert cli.main(["profile", "set", "Turbo", "--battery-limit", "30", "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "out of range" in data["error"]
    assert calls == []


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
    assert cli.main(["profile", "set", "Turbo", "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "at least one field" in data["error"]


def test_fans_curve_set_validates_and_dispatches(owner, capsys):
    calls, record = owner
    record("set_field", "custom curve")
    data = run_json(capsys, ["fans", "curve", "set", CURVE_SPEC])
    assert data["curve"] == CURVE
    assert calls == [("set_field", ("fan_curve", CURVE), {})]


def test_fans_curve_set_rejects_a_bad_curve(profiles_env, capsys):
    assert cli.main(["fans", "curve", "set", "20:20,30:10", "--json"]) == 2
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


def _flat_ranges(monkeypatch):
    monkeypatch.setattr(
        cli.power_range,
        "detect_ranges",
        lambda: {"pl1": (5, 93), "pl2": (5, 93), "pl3": (5, 93)},
    )


def test_tdp_set_positional_keeps_pl2_and_pl3_flags(owner, monkeypatch, capsys):
    calls, record = owner
    record("set_field")
    _flat_ranges(monkeypatch)
    data = run_json(capsys, ["tdp", "set", "30", "--pl2", "40", "--pl3", "50"])
    assert data["tdp"] == {"pl1": 30, "pl2": 40, "pl3": 50, "force": False}


@pytest.mark.parametrize(
    "argv",
    [["tdp", "set", "30", "--pl1", "20"], ["tdp", "set", "30,40,50", "--pl2", "45"]],
)
def test_tdp_set_rejects_conflicting_forms(owner, monkeypatch, capsys, argv):
    calls, record = owner
    record("set_field")
    _flat_ranges(monkeypatch)
    assert cli.main(argv + ["--json"]) == 2
    assert calls == []


def test_tdp_set_above_75_needs_force(owner, monkeypatch, capsys):
    calls, record = owner
    record("set_field")
    _flat_ranges(monkeypatch)
    assert cli.main(["tdp", "set", "80", "--json"]) == 2
    capsys.readouterr()
    assert calls == []
    data = run_json(capsys, ["tdp", "set", "80", "--force"])
    assert data["tdp"]["force"] is True


def test_profile_set_above_75_needs_force(owner, profiles_env, monkeypatch, capsys):
    calls, record = owner
    record("edit")
    _flat_ranges(monkeypatch)
    assert cli.main(["profile", "set", "Turbo", "--pl1", "80", "--json"]) == 2
    capsys.readouterr()
    assert calls == []
    data = run_json(capsys, ["profile", "set", "Turbo", "--pl1", "80", "--force"])
    assert data["profile"]["tdp"]["force"] is True
    # Verify profile set does not turn force on when pl1 <= 75
    data_low = run_json(capsys, ["profile", "set", "Turbo", "--pl1", "40"])
    assert data_low["profile"]["tdp"]["force"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["tdp", "set", "abc"],
        ["tdp", "set", "1,2"],
        ["fans", "curve", "set", "nan:50"],
        ["fans", "curve", "set", "120:100"],
        ["fans", "curve", "set", "20:30"],
    ],
)
def test_bad_input_exits_2(owner, capsys, argv):
    calls, record = owner
    record("set_field")
    assert cli.main(argv + ["--json"]) == 2
    assert calls == []


def test_profile_set_max_fans_on_active_profile_goes_live(
    owner, profiles_env, monkeypatch, capsys
):
    calls, record = owner
    record("edit")
    record("set_max_fans")
    monkeypatch.setattr(cli.profiles, "active_name", lambda: "Turbo")
    run_json(capsys, ["profile", "set", "Turbo", "--max-fans", "on"])
    assert ("set_max_fans", (True,), {}) in calls


@pytest.mark.parametrize(
    "command", [["fans", "curve", "set"], ["profile", "set", "Turbo", "--curve"]]
)
@pytest.mark.parametrize(
    "temp,minimum", [(71, 2), (75, 6), (80, 12), (100, 36), (110, 48)]
)
def test_curve_safety_floor(owner, profiles_env, capsys, command, temp, minimum):
    calls, record = owner
    record("set_field")
    record("edit")
    points = [[t, 0] for t in range(20, 27)] + [[temp, minimum - 1]]
    assert cli.main(command + [json.dumps(points), "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert "at least" in data["error"]
    assert calls == []
    points[-1][1] = minimum
    assert run_json(capsys, command + [json.dumps(points)])["ok"] is True
    assert calls


def test_all_zero_fan_curve_is_rejected(owner, capsys):
    calls, record = owner
    record("set_field")
    curve = [[t, 0] for t in (20, 30, 40, 50, 55, 60, 65, 70)]
    assert cli.main(["fans", "curve", "set", json.dumps(curve), "--json"]) == 2
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "all-zero" in data["error"]
    assert calls == []


def test_curve_allows_zero_at_or_below_70c_when_not_all_zero(owner, capsys):
    calls, record = owner
    record("set_field")
    curve = [[20, 0], [30, 0], [40, 0], [50, 0], [55, 0], [60, 0], [65, 0], [75, 10]]
    assert run_json(capsys, ["fans", "curve", "set", json.dumps(curve)])["curve"] == curve
    assert calls == [("set_field", ("fan_curve", curve), {})]


@pytest.mark.parametrize("rail", ["--pl1", "--pl2", "--pl3"])
def test_profile_set_rejects_mixed_tdp_forms(owner, profiles_env, capsys, rail):
    calls, record = owner
    record("edit")
    assert cli.main(
        ["profile", "set", "Turbo", "--tdp", "30,40,50", rail, "60", "--json"]
    ) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert calls == []


@pytest.mark.parametrize("field_args", [["--base", "quiet"], ["--max-fans", "on"]])
def test_profile_set_reports_failed_apply_steps(
    owner, profiles_env, monkeypatch, capsys, field_args
):
    calls, record = owner
    record("set_max_fans")
    monkeypatch.setattr(
        profiles, "apply", lambda *a, **kw: [
            ("profile", True, "balanced"), ("fans", False, "curve write failed")
        ]
    )
    assert cli.main(["profile", "set", "Balanced", *field_args, "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "fans: curve write failed" in data["error"]
    assert calls == []


def test_profile_set_checks_and_writes_max_fans_in_one_transaction(
    profiles_env, monkeypatch, capsys
):
    locked = False
    events = []

    @contextmanager
    def transaction():
        nonlocal locked
        assert not locked
        locked = True
        events.append("lock")
        try:
            yield
        finally:
            locked = False
            events.append("unlock")

    def edit(profile, field):
        assert locked
        events.append(field)

    def active_name():
        assert locked
        events.append("active")
        return "Turbo"

    def set_max_fans(enabled):
        assert locked and enabled
        events.append("toggle")

    monkeypatch.setattr(cli.authority, "transaction", transaction)
    monkeypatch.setattr(cli.authority, "edit", edit)
    monkeypatch.setattr(cli.profiles, "active_name", active_name)
    monkeypatch.setattr(cli.authority, "set_max_fans", set_max_fans)
    run_json(capsys, ["profile", "set", "Turbo", "--max-fans", "on"])
    assert events == ["lock", "max_fans", "active", "toggle", "unlock"]


@pytest.mark.parametrize(
    "limits",
    [
        ["30"], ["--pl1", "30"], ["--pl2", "40"], ["--pl3", "50"],
        ["30", "--pl2", "40"], ["--force"],
    ],
)
def test_tdp_reset_rejects_limits(owner, capsys, limits):
    calls, record = owner
    record("set_field")
    assert cli.main(["tdp", "reset", *limits, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert calls == []


def test_tdp_reset_without_limits(owner, capsys):
    calls, record = owner
    record("set_field")
    assert run_json(capsys, ["tdp", "reset"])["tdp"] is None
    assert calls == [("set_field", ("tdp", None), {})]


def test_tdp_reset_reports_failure_on_failed_apply_step(owner, capsys):
    calls, record = owner
    record("set_field", [("profile", True, "balanced"), ("fans", False, "curve write failed")])
    assert cli.main(["tdp", "reset", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "fans: curve write failed" in data["error"]


@pytest.mark.parametrize("as_json", [False, True])
@pytest.mark.parametrize(
    "argv",
    [
        ["battery", "set"],
        ["battery", "bogus", "30"],
        ["tdp", "set"],
        ["tdp", "bogus"],
        ["brightness", "set"],
        ["brightness", "screen", "abc"],
        ["brightness", "bogus"],
        ["lighting", "set"],
        ["lighting", "set", "keyboard"],
        ["lighting", "bogus"],
        ["profile", "set", "Turbo", "--pl2", "40"],
        ["profile", "set", "Turbo", "--undervolt", "-41"],
        ["profile", "set", "Turbo"],
        ["crash", "queue", "report.txt"],
    ],
)
def test_manual_usage_errors_exit_2(owner, profiles_env, capsys, argv, as_json):
    calls, record = owner
    record("set_field")
    record("edit")
    assert cli.main(argv + (["--json"] if as_json else [])) == 2
    captured = capsys.readouterr()
    if as_json:
        assert json.loads(captured.out)["ok"] is False
        assert captured.err == ""
    else:
        assert captured.err and captured.out == ""
    assert calls == []


@pytest.mark.parametrize("failed_command", ["mkdir", "cp"])
def test_post_update_backup_failure_prevents_apply(
    monkeypatch, capsys, failed_command
):
    from post_update.recipe import Recipe

    calls = []
    applied = []

    def host_run(argv, privileged=False, input=None):
        calls.append((argv[0], privileged))
        return (1, "backup failed") if argv[0] == failed_command else (0, "")

    recipe = Recipe(
        id="test", title="Test", fixes="test", check=lambda: "needs",
        apply=lambda: applied.append(True), replaces=("/etc/test.conf",),
    )
    monkeypatch.setattr(cli, "_all_recipes", lambda **kw: ([recipe], []))
    monkeypatch.setattr(cli, "_host_run", host_run)
    assert cli.main(["post-update", "run", "test", "--json"]) == 1
    assert json.loads(capsys.readouterr().out)["results"] == [["test", "failed"]]
    assert applied == []
    expected = [("mkdir", True)]
    if failed_command == "cp":
        expected.append(("cp", True))
    assert calls == expected


@pytest.fixture
def crash_upload_env(tmp_path, monkeypatch):
    import crash_report

    monkeypatch.setattr(cli, "CRASH_DIR", str(tmp_path / "crash"))
    build_path = tmp_path / "build.json"
    build_path.write_text(json.dumps({
        "base": "https://example.invalid",
        "token": "test",
        "version": "1",
        "commit": "test",
    }))
    monkeypatch.setattr(crash_report, "BUILD_CONFIG", build_path)
    monkeypatch.setattr(crash_report, "reporting_enabled", lambda path: True)
    uploads = []
    monkeypatch.setattr(crash_report, "upload_report", lambda *args: uploads.append(args))
    queue = cli._crash_queue()
    queue.add("report.zip", b"PK")
    return queue, build_path, uploads


@pytest.mark.parametrize("build", [[], ["base"], None, "config", 123, True, {}])
def test_crash_upload_rejects_invalid_build_config(crash_upload_env, capsys, build):
    queue, build_path, uploads = crash_upload_env
    build_path.write_text(json.dumps(build))
    assert cli.main(["crash", "upload", "--json"]) == 1
    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False and "build-time crash drop" in data["error"]
    assert uploads == [] and queue.pending() == ["report.zip"]
    assert not (queue.directory / "sent.json").exists()


@pytest.mark.parametrize(
    "name",
    ["absolute", "../outside.zip", "sub/../../outside.zip", "missing.zip", "sent.json"],
)
def test_crash_upload_rejects_names_outside_queue(crash_upload_env, capsys, name):
    queue, build_path, uploads = crash_upload_env
    outside = build_path.parent / "outside.zip"
    outside.write_bytes(b"keep")
    if name == "absolute":
        name = str(outside)
    assert cli.main(["crash", "upload", "report.zip", name, "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert outside.read_bytes() == b"keep"
    assert uploads == [] and queue.pending() == ["report.zip"]
    assert not (queue.directory / "sent.json").exists()


def test_crash_upload_rejects_symlinks(crash_upload_env, capsys):
    queue, build_path, uploads = crash_upload_env
    outside = build_path.parent / "outside.zip"
    outside.write_bytes(b"keep")
    (queue.directory / "linked.zip").symlink_to(outside)
    assert cli.main(["crash", "upload", "linked.zip", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False
    assert outside.read_bytes() == b"keep" and uploads == []


@pytest.mark.parametrize("names", [[], ["report.zip"]])
def test_crash_upload_removes_only_uploaded_report(crash_upload_env, capsys, names):
    queue, build_path, uploads = crash_upload_env
    data = run_json(capsys, ["crash", "upload", *names])
    assert data["uploaded"] == ["report.zip"] and data["pending"] == []
    assert uploads == [(json.loads(build_path.read_text()), "report.zip", b"PK")]
    assert queue.pending() == [] and (queue.directory / "sent.json").exists()


# ----------------------------------------------------------------------
# #227: profile create / rename / delete
# ----------------------------------------------------------------------

@pytest.mark.parametrize("argv", [
    ["profile", "create", "65 watt charger"],
    ["profile", "create", "Copy", "--from", "Turbo"],
    ["profile", "rename", "Old", "New"],
    ["profile", "delete", "Old"],
])
def test_profile_create_rename_delete_parse(argv):
    assert cli._parser().parse_args(argv).cmd == "profile"


def test_profile_create_goes_through_the_owner(owner, capsys):
    calls, record = owner
    record("create_profile", {"name": "65 watt charger"})
    data = run_json(capsys, ["profile", "create", "65 watt charger", "--from", "Turbo"])
    assert data["ok"] is True and data["name"] == "65 watt charger"
    assert calls == [("create_profile", ("65 watt charger",), {"from_name": "Turbo"})]


def test_profile_rename_and_delete_go_through_the_owner(owner, capsys):
    calls, record = owner
    record("rename_profile", {"name": "New"})
    record("delete_profile", {"removed": "Old", "active": "Balanced", "steps": []})
    assert run_json(capsys, ["profile", "rename", "Old", "New"])["ok"] is True
    assert run_json(capsys, ["profile", "delete", "Old"])["ok"] is True
    assert calls == [
        ("rename_profile", ("Old", "New"), {}),
        ("delete_profile", ("Old",), {}),
    ]


def test_profile_create_then_get_works_end_to_end(profiles_env, capsys):
    run_json(capsys, ["profile", "create", "65 watt charger"])
    data = run_json(capsys, ["profile", "get", "65 watt charger"])
    assert data["name"] == "65 watt charger" and data["builtin"] is False


def test_profile_create_reports_a_clash_as_a_failure(profiles_env, capsys):
    assert cli.main(["profile", "create", "Turbo", "--json"]) == 1

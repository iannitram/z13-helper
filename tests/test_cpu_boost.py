"""CPU Boost (amd-pstate EPP), issue #64: the profile field, the host write and the read.

The pure parsing lives in src/epp.py (tests/test_epp.py). These tests cover what
is left: the profile field, the argv that writes every CPU, where it sits in
apply(), the read/write helpers, and the Performance-panel combo dispatch.
"""

import ast
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import epp
import hardware_controller
import profiles
from hardware_controller import HardwareController
from test_profiles_apply import Z, bare_profile, full_profile, names

GLOB = "/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference"


def _methods(module, class_name, method_names, namespace):
    """Exercise UI methods without importing PyQt6, as required by SPEC.md."""
    path = Path(__file__).resolve().parents[1] / "src" / module
    tree = ast.parse(path.read_text())
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in method_names]
    assert {n.name for n in methods} == set(method_names)
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(path), "exec"), namespace)
    return namespace


@pytest.fixture
def boost_ui(profiles_env, monkeypatch):
    import state_authority as authority
    # The real edit() runs (store merge, active/inactive routing); only its
    # edges are replaced: no z13ctl backend, no hardware push, no mirror.
    monkeypatch.setattr(authority, "named_client", lambda: None)

    class Signal:
        def connect(self, callback):
            self.callback = callback

        def emit(self, *args):
            if hasattr(self, "callback"):
                self.callback(*args)

    class Combo:
        def __init__(self):
            self.items = []
            self.index = 0
            self.blocked = False
            self.currentIndexChanged = Signal()

        def addItem(self, label, value):
            self.items.append((label, value))

        def findData(self, value):
            return next((i for i, item in enumerate(self.items) if item[1] == value), -1)

        def blockSignals(self, blocked):
            self.blocked = blocked

        def setCurrentIndex(self, index):
            changed = index != self.index
            self.index = index
            if changed and not self.blocked:
                self.currentIndexChanged.emit(index)

        def currentData(self):
            return self.items[self.index][1]

    class Layout:
        def __init__(self):
            self.widgets = []

        def addWidget(self, widget, *args):
            self.widgets.append(widget)

    class Label:
        def __init__(self, text=""):
            self.text = text

        def setObjectName(self, name):
            pass

        def setText(self, text):
            self.text = text

    jobs, writes, rows, mirrored = [], [], [], []
    live = {"epp": "balance_power"}
    monkeypatch.setattr(authority, "mirror_profile", lambda p: mirrored.append(p["name"]))
    # writes records the value each live push receives (None = "Unchanged").
    monkeypatch.setattr(authority, "_push_field",
                        lambda profile, field, devices=None, before=None: writes.append(profile.get(field)))
    controller = SimpleNamespace(get_epp_choices=lambda: ["power", "performance"],
                                 get_epp=lambda: live["epp"])
    control = SimpleNamespace(controller=controller, changed=Signal(),
                              active_name=lambda: "Balanced",
                              shown=lambda: profiles.get_profile("Balanced"),
                              _submit=lambda label, fn: jobs.append(fn))
    methods = _methods("profile_control.py", "ProfileControl", ["save", "_pusher"],
                       {"profiles": profiles, "authority": authority})
    for name in ("save", "_pusher"):
        setattr(control, name, methods[name].__get__(control))
    methods = _methods("dashboard_ui.py", "DashboardUI",
                       ["_build_epp_ui", "_refresh_epp_ui", "_on_epp"],
                       {"QComboBox": Combo, "QHBoxLayout": Layout, "QLabel": Label,
                        "epp": epp})
    ui = SimpleNamespace(control=control, controller=controller,
                         perf=SimpleNamespace(body=SimpleNamespace(addLayout=rows.append)))
    for name in ("_build_epp_ui", "_refresh_epp_ui", "_on_epp"):
        setattr(ui, name, methods[name].__get__(ui))
    ui._build_epp_ui()
    control.changed.connect(lambda: ui._refresh_epp_ui(control.shown()))
    return SimpleNamespace(ui=ui, jobs=jobs, writes=writes, rows=rows, live=live,
                           mirrored=mirrored)


def test_performance_panel_contains_cpu_boost(boost_ui):
    b = boost_ui
    labels = [w.text if hasattr(w, "text") else w for w in b.rows[0].widgets]
    assert b.rows[0].widgets[1] is b.ui.epp_combo and labels[0] == "CPU Boost"
    assert b.rows[0].widgets[2] is b.ui.epp_now
    assert b.ui.epp_combo.items == [("Unchanged", ""), ("Power", "power"),
                                     ("Performance", "performance")]
    # Verify the real window construction and profile refresh call these methods.
    source = (Path(__file__).resolve().parents[1] / "src/dashboard_ui.py").read_text()
    cls = next(n for n in ast.parse(source).body if isinstance(n, ast.ClassDef)
               and n.name == "DashboardUI")
    for method, called in [("_build_ui", "_build_epp_ui"),
                           ("_refresh_profile_ui", "_refresh_epp_ui")]:
        fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
        assert any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                   and n.func.attr == called for n in ast.walk(fn))


def test_performance_cpu_boost_persists_and_dispatches_only_epp(boost_ui):
    b = boost_ui
    before = profiles.get_profile("Balanced")
    b.ui.epp_combo.setCurrentIndex(b.ui.epp_combo.findData("power"))
    # The UI thread stores nothing; the worker's edit() does, and pushes it live.
    assert profiles.get_profile("Balanced") == before
    assert b.writes == []
    assert len(b.jobs) == 1
    b.jobs.pop()()
    assert profiles.get_profile("Balanced") == dict(before, epp="power")
    assert b.writes == ["power"]


@pytest.mark.parametrize("live,text", [("balance_power", "Now: Balance power"),
                                       ("mixed", "Now: mixed"), (None, "Now: unavailable")])
def test_performance_cpu_boost_shows_the_live_value(boost_ui, live, text):
    b = boost_ui
    b.live["epp"] = live
    b.ui._refresh_epp_ui(profiles.get_profile("Balanced"))   # profile says "Unchanged"
    assert b.ui.epp_now.text == text
    assert b.ui.epp_combo.currentData() == ""
    assert b.writes == [] and b.jobs == []


def test_dashboard_and_fans_window_share_the_epp_helpers():
    src = Path(__file__).resolve().parents[1] / "src"
    for module in ("dashboard_ui.py", "fans_window.py"):
        text = (src / module).read_text()
        for helper in ("fill_combo", "show_profile_epp", "now_text"):
            assert f"epp.{helper}(" in text, (module, helper)
        assert "EPP_LABELS" not in text and 'addItem("Unchanged"' not in text


@pytest.mark.parametrize("value", [None, "power", "performance"])
def test_performance_cpu_boost_refresh_does_not_write(boost_ui, value):
    b = boost_ui
    b.ui._refresh_epp_ui(dict(profiles.get_profile("Balanced"), epp=value))
    assert b.ui.epp_combo.currentData() == (value or "")
    assert b.jobs == [] and b.writes == []


def test_performance_cpu_boost_unchanged_clears_override(boost_ui):
    b = boost_ui
    p = profiles.get_profile("Balanced")
    profiles.upsert_profile(dict(p, epp="power"))
    b.ui._refresh_epp_ui(dict(p, epp="power"))
    b.ui.epp_combo.setCurrentIndex(0)
    assert profiles.get_profile("Balanced")["epp"] == "power"   # not until the worker runs
    assert len(b.jobs) == 1
    b.jobs.pop()()
    assert profiles.get_profile("Balanced")["epp"] is None
    # "Unchanged" reaches the owner with epp=None; state_authority decides what that does.
    assert b.writes == [None]


def test_performance_cpu_boost_edits_the_shown_pending_profile(boost_ui):
    b = boost_ui
    before = profiles.get_profile("Balanced")
    b.ui.control.shown = lambda: profiles.get_profile("Turbo")
    b.ui.epp_combo.setCurrentIndex(b.ui.epp_combo.findData("performance"))
    assert profiles.get_profile("Balanced") == before
    assert len(b.jobs) == 1
    b.jobs.pop()()
    assert profiles.get_profile("Turbo")["epp"] == "performance"
    assert profiles.get_profile("Balanced") == before
    assert b.mirrored == ["Turbo"] and b.writes == []


def test_saving_a_field_on_an_inactive_profile_goes_through_the_owner(boost_ui):
    """Regression: save() must not upsert the widget's copy on the UI thread.

    The copy is stale (tdp changed in the store since it was taken). Only the
    field being saved may change, and the save must reach authority.mirror_profile.
    """
    b = boost_ui
    widget_copy = profiles.get_profile("Turbo")
    stored_tdp = {"pl1": 40, "pl2": 40, "pl3": 40, "force": False}
    profiles.upsert_profile(dict(widget_copy, tdp=stored_tdp))
    b.ui.control.save(dict(widget_copy, epp="performance"), "epp")
    assert profiles.get_profile("Turbo")["tdp"] == stored_tdp
    assert len(b.jobs) == 1
    b.jobs.pop()()
    after = profiles.get_profile("Turbo")
    assert after["epp"] == "performance"
    assert after["tdp"] == stored_tdp           # a field it did not edit is kept
    assert b.mirrored == ["Turbo"] and b.writes == []


# ----------------------------------------------------------------------
# profile field
# ----------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, *profiles.EPP_CHOICES])
def test_validate_accepts_null_and_each_choice(value):
    profiles.validate(bare_profile(epp=value))


@pytest.mark.parametrize("value", ["default", "Performance", "balance", "", 5, "performance power"])
def test_validate_rejects_anything_else(value):
    with pytest.raises(ValueError, match="epp must be"):
        profiles.validate(bare_profile(epp=value))


def test_choices_match_the_app_copy():
    # profiles.py keeps its own copy (z13-profile runs without src/epp.py); keep it in step.
    assert profiles.EPP_CHOICES == epp.EPP_CHOICES


def test_builtin_profiles_do_not_set_epp():
    assert all(p.get("epp") is None for p in profiles.default_profiles())


# ----------------------------------------------------------------------
# argv
# ----------------------------------------------------------------------

@pytest.mark.parametrize("value", profiles.EPP_CHOICES)
def test_epp_argv_writes_the_value_to_each_cpu_node(value):
    argv = profiles.epp_argv(value)

    assert argv[:2] == ["sh", "-c"]
    assert argv[3] == "sh"
    assert argv[4:] == [value]                 # the value is an argument, not script text
    assert GLOB in argv[2]                     # policy directories are globbed on the host
    assert value not in argv[2].replace(GLOB, "")   # never spliced into the script
    assert "|| exit 1" in argv[2] and '[ -e "$f" ] || continue' in argv[2]
    assert '[ "$n" -gt 0 ]' in argv[2]         # no node written is a failure, not success


def test_epp_argv_refuses_an_unknown_value():
    with pytest.raises(ValueError):
        profiles.epp_argv("performance; reboot")


def test_write_epp_reports_success_failure_and_timeout(fake_host):
    assert profiles.write_epp("power") == (True, "power")
    assert fake_host.calls[-1].argv == profiles.epp_argv("power")
    assert fake_host.calls[-1].timeout == profiles.Z13CTL_WRITE_TIMEOUT_S

    fake_host.fail("printf", rc=1, out="sh: 1: cannot create /sys/x: Permission denied", times=1)
    assert profiles.write_epp("power") == (False, "sh: 1: cannot create /sys/x: Permission denied")

    fake_host.fail("printf", rc=124, out="timed out after 20 s", timed_out=True)
    ok, msg = profiles.write_epp("power")
    assert not ok and "timed out" in msg


# ----------------------------------------------------------------------
# apply() ordering and independence
# ----------------------------------------------------------------------

def test_apply_writes_epp_after_the_platform_profile_and_before_tdp(profiles_env, fake_host):
    p = full_profile(epp="power")

    results = profiles.apply(p, z13ctl=Z)

    assert names(results)[:3] == ["profile", "cpu boost", "tdp"]
    assert results[1] == ("cpu boost", True, "power")
    lines = fake_host.lines
    assert lines.index(" ".join(profiles.epp_argv("power"))) == lines.index(
        f"{Z} profile --set performance") + 1


def test_apply_without_epp_runs_no_epp_write(profiles_env, fake_host):
    results = profiles.apply(full_profile(), z13ctl=Z)

    assert "cpu boost" not in names(results)
    assert not any(argv[:2] == ["sh", "-c"] for argv in fake_host.argvs)


def test_failed_epp_write_does_not_stop_the_other_steps(profiles_env, fake_host):
    fake_host.fail("printf", rc=1, out="Permission denied")

    results = profiles.apply(full_profile(epp="balance_power"), z13ctl=Z)

    assert ("cpu boost", False, "Permission denied") in results
    assert "tdp" in names(results) and "battery limit" in names(results)
    assert any(ok for step, ok, _ in results if step == "tdp")


def test_apply_epp_helper_returns_nothing_when_unset():
    assert profiles.apply_epp(bare_profile()) == []


# ----------------------------------------------------------------------
# read side (hardware_controller)
# ----------------------------------------------------------------------

def _write(path, text):
    path.write_text(text)
    return str(path)


def test_read_epp_values_all_same_is_one_choice(tmp_path):
    paths = [_write(tmp_path / "cpu0", "performance\n"), _write(tmp_path / "cpu1", "performance\n")]
    assert epp.summarize_epp(hardware_controller.read_epp_values(paths)) == "performance"


def test_read_epp_values_skips_offline_cpus(tmp_path):
    paths = [_write(tmp_path / "cpu0", "power\n"), str(tmp_path / "cpu1-offline")]
    assert hardware_controller.read_epp_values(paths) == ["power\n"]


def test_read_epp_values_unreadable_node_gives_none(tmp_path):
    unreadable = tmp_path / "is-a-dir"
    unreadable.mkdir()
    assert hardware_controller.read_epp_values([str(unreadable)]) == [None]


def test_get_epp_reports_mixed(monkeypatch, tmp_path):
    a = _write(tmp_path / "a", "power\n")
    b = _write(tmp_path / "b", "performance\n")
    monkeypatch.setattr(epp, "policy_paths", lambda: [a, b])
    assert HardwareController(z13ctl_path="z13ctl").get_epp() == "mixed"


def test_get_epp_choices_from_the_kernel_list(monkeypatch, tmp_path):
    avail = _write(tmp_path / "avail", "default performance balance_power power\n")
    monkeypatch.setattr(hardware_controller, "_EPP_AVAILABLE", avail)
    assert HardwareController(z13ctl_path="z13ctl").get_epp_choices() == [
        "power", "balance_power", "performance"]


def test_get_epp_choices_falls_back_to_all_when_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware_controller, "_EPP_AVAILABLE", str(tmp_path / "missing"))
    assert HardwareController(z13ctl_path="z13ctl").get_epp_choices() == list(epp.EPP_CHOICES)


def test_set_epp_writes_through_the_host_helper(fake_host):
    c = HardwareController(z13ctl_path="z13ctl")
    assert c.set_epp("balance_performance") == "balance_performance"
    assert fake_host.calls[-1].argv == profiles.epp_argv("balance_performance")


def test_set_epp_raises_with_the_host_message(fake_host):
    fake_host.fail("printf", rc=1, out="Permission denied")
    with pytest.raises(RuntimeError, match="CPU Boost not set: Permission denied"):
        HardwareController(z13ctl_path="z13ctl").set_epp("power")


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX sh")
def test_epp_script_fails_when_no_node_exists_and_writes_when_one_does(tmp_path, real_subprocess):
    argv = profiles.epp_argv("power")
    script = argv[2].replace(profiles.EPP_SYSFS_GLOB, str(tmp_path / "policy*" / "epp"))
    assert real_subprocess(["sh", "-c", script, "sh", "power"]).returncode != 0
    node = tmp_path / "policy3"
    node.mkdir()
    assert real_subprocess(["sh", "-c", script, "sh", "power"]).returncode != 0  # dir, no node
    (node / "epp").write_text("x")
    assert real_subprocess(["sh", "-c", script, "sh", "power"]).returncode == 0
    assert (node / "epp").read_text() == "power"


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX sh")
def test_write_and_read_agree_when_policy_numbers_are_not_cpu_numbers(tmp_path, real_subprocess):
    # 4 CPUs sharing 2 policies numbered 0 and 8: policy{n} for n in range(cpu_count)
    # would miss policy8, and cpu{n}/cpufreq would not be the written tree at all.
    for n in (0, 8):
        (tmp_path / f"policy{n}").mkdir()
        (tmp_path / f"policy{n}" / "epp").write_text("balance_performance\n")
    pattern = str(tmp_path / "policy*" / "epp")
    script = profiles.epp_argv("power")[2].replace(profiles.EPP_SYSFS_GLOB, pattern)
    assert real_subprocess(["sh", "-c", script, "sh", "power"]).returncode == 0
    nodes = epp.policy_paths(pattern)
    assert [Path(p).parent.name for p in nodes] == ["policy0", "policy8"]
    assert epp.summarize_epp(hardware_controller.read_epp_values(nodes)) == "power"


def test_policy_paths_sorts_numerically(tmp_path):
    for n in (10, 2, 0):
        (tmp_path / f"policy{n}").mkdir()
        (tmp_path / f"policy{n}" / "epp").write_text("power")
    pattern = str(tmp_path / "policy*" / "epp")
    assert [Path(p).parent.name for p in epp.policy_paths(pattern)] == ["policy0", "policy2", "policy10"]


def test_read_and_write_use_the_same_policy_tree():
    assert epp.EPP_POLICY_GLOB == profiles.EPP_SYSFS_GLOB


def test_epp_route_runs_unelevated_in_flatpak_and_on_host():
    import host
    argv = profiles.epp_argv("power")
    assert GLOB in argv[2]
    assert host.command(argv, mode="host") == (argv, None)
    assert host.command(argv, mode="flatpak") == (["flatpak-spawn", "--host", *argv], None)

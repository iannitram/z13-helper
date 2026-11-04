"""CPU Boost (amd-pstate EPP), issue #64: the profile field, the host write and the read.

The pure parsing lives in src/epp.py (tests/test_epp.py). These tests cover what
is left: the profile field, the argv that writes every CPU, where it sits in
apply(), and the read/write helpers the Fans + Power combo calls.
"""

import os

import pytest

import epp
import hardware_controller
import profiles
from hardware_controller import HardwareController
from test_profiles_apply import Z, bare_profile, full_profile, names

PATHS2 = [epp.EPP_PATH.format(n=0), epp.EPP_PATH.format(n=1)]


@pytest.fixture
def two_cpus(monkeypatch):
    monkeypatch.setattr(profiles.os, "cpu_count", lambda: 2)


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
    argv = profiles.epp_argv(value, 2)

    assert argv[:2] == ["sh", "-c"]
    assert argv[3] == "sh"
    assert argv[4] == value                    # the value is an argument, not script text
    assert argv[5:] == PATHS2
    assert value not in argv[2]                # never spliced into the script
    assert "|| exit 1" in argv[2] and '[ -e "$f" ] || continue' in argv[2]
    assert '[ "$n" -gt 0 ]' in argv[2]         # no node written is a failure, not success


def test_epp_argv_refuses_an_unknown_value():
    with pytest.raises(ValueError):
        profiles.epp_argv("performance; reboot", 2)


def test_write_epp_reports_success_failure_and_timeout(fake_host, two_cpus):
    assert profiles.write_epp("power") == (True, "power")
    assert fake_host.calls[-1].argv == profiles.epp_argv("power", 2)
    assert fake_host.calls[-1].timeout == profiles.Z13CTL_WRITE_TIMEOUT_S

    fake_host.fail("printf", rc=1, out="sh: 1: cannot create /sys/x: Permission denied", times=1)
    assert profiles.write_epp("power") == (False, "sh: 1: cannot create /sys/x: Permission denied")

    fake_host.fail("printf", rc=124, out="timed out after 20 s", timed_out=True)
    ok, msg = profiles.write_epp("power")
    assert not ok and "timed out" in msg


def test_write_epp_without_cpus_does_not_run_anything(fake_host):
    assert profiles.write_epp("power", cpu_count=0) == (False, "no CPUs found")
    assert fake_host.calls == []


# ----------------------------------------------------------------------
# apply() ordering and independence
# ----------------------------------------------------------------------

def test_apply_writes_epp_after_the_platform_profile_and_before_tdp(profiles_env, fake_host, two_cpus):
    p = full_profile(epp="power")

    results = profiles.apply(p, z13ctl=Z)

    assert names(results)[:3] == ["profile", "cpu boost", "tdp"]
    assert results[1] == ("cpu boost", True, "power")
    lines = fake_host.lines
    assert lines.index(" ".join(profiles.epp_argv("power", 2))) == lines.index(
        f"{Z} profile --set performance") + 1


def test_apply_without_epp_runs_no_epp_write(profiles_env, fake_host, two_cpus):
    results = profiles.apply(full_profile(), z13ctl=Z)

    assert "cpu boost" not in names(results)
    assert not any(argv[:2] == ["sh", "-c"] for argv in fake_host.argvs)


def test_failed_epp_write_does_not_stop_the_other_steps(profiles_env, fake_host, two_cpus):
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
    monkeypatch.setattr(epp, "epp_paths", lambda n: [a, b])
    monkeypatch.setattr(hardware_controller.os, "cpu_count", lambda: 2)
    assert HardwareController(z13ctl_path="z13ctl").get_epp() == "mixed"


def test_get_epp_choices_from_the_kernel_list(monkeypatch, tmp_path):
    avail = _write(tmp_path / "avail", "default performance balance_power power\n")
    monkeypatch.setattr(hardware_controller, "_EPP_AVAILABLE", avail)
    assert HardwareController(z13ctl_path="z13ctl").get_epp_choices() == [
        "power", "balance_power", "performance"]


def test_get_epp_choices_falls_back_to_all_when_unreadable(monkeypatch, tmp_path):
    monkeypatch.setattr(hardware_controller, "_EPP_AVAILABLE", str(tmp_path / "missing"))
    assert HardwareController(z13ctl_path="z13ctl").get_epp_choices() == list(epp.EPP_CHOICES)


def test_set_epp_writes_through_the_host_helper(fake_host, two_cpus):
    c = HardwareController(z13ctl_path="z13ctl")
    assert c.set_epp("balance_performance") == "balance_performance"
    assert fake_host.calls[-1].argv == profiles.epp_argv("balance_performance", 2)


def test_set_epp_raises_with_the_host_message(fake_host, two_cpus):
    fake_host.fail("printf", rc=1, out="Permission denied")
    with pytest.raises(RuntimeError, match="CPU Boost not set: Permission denied"):
        HardwareController(z13ctl_path="z13ctl").set_epp("power")


@pytest.mark.skipif(os.name == "nt", reason="needs a POSIX sh")
def test_epp_script_fails_when_no_node_exists_and_writes_when_one_does(tmp_path, real_subprocess):
    script = profiles.epp_argv("power", 1)[2]
    missing = str(tmp_path / "none")
    assert real_subprocess(["sh", "-c", script, "sh", "power", missing]).returncode != 0
    node = tmp_path / "node"
    node.write_text("x")
    assert real_subprocess(["sh", "-c", script, "sh", "power", missing, str(node)]).returncode == 0
    assert node.read_text() == "power"

"""CPU Boost combo (issue #64, part b): the EPP read, the host-helper write argv, the per-profile field.

No host access: the `fake_host` fixture answers every host.run call.
"""

import os

import pytest

import epp
import profiles
from hardware_controller import HardwareController

CPU0 = "/sys/devices/system/cpu/cpu0/cpufreq/energy_performance_preference"
CPU1 = "/sys/devices/system/cpu/cpu1/cpufreq/energy_performance_preference"


@pytest.fixture
def two_cpus(monkeypatch):
    monkeypatch.setattr(os, "cpu_count", lambda: 2)


def test_get_epp_reads_each_cpu_and_summarizes(fake_host, two_cpus):
    fake_host.fail("cpu0/cpufreq", rc=0, out="performance\n")
    fake_host.fail("cpu1/cpufreq", rc=0, out="performance\n")
    assert HardwareController(z13ctl_path="z13ctl").get_epp() == "performance"
    assert fake_host.argvs == [["cat", CPU0], ["cat", CPU1]]


def test_get_epp_reports_mixed_when_cpus_differ(fake_host, two_cpus):
    fake_host.fail("cpu0/cpufreq", rc=0, out="power\n")
    fake_host.fail("cpu1/cpufreq", rc=0, out="performance\n")
    assert HardwareController(z13ctl_path="z13ctl").get_epp() == "mixed"


def test_get_epp_is_none_when_a_read_fails(fake_host, two_cpus):
    fake_host.fail("cpu1/cpufreq", rc=1, out="No such file")
    assert HardwareController(z13ctl_path="z13ctl").get_epp() is None


def test_set_epp_writes_every_cpu_through_tee(fake_host, two_cpus):
    HardwareController(z13ctl_path="z13ctl").set_epp("balance_power")
    assert fake_host.argvs == [["tee", CPU0], ["tee", CPU1]]
    assert [c.input for c in fake_host.calls] == ["balance_power\n", "balance_power\n"]


def test_set_epp_rejects_unknown_value_without_touching_the_host(fake_host, two_cpus):
    with pytest.raises(ValueError):
        HardwareController(z13ctl_path="z13ctl").set_epp("default")
    assert fake_host.argvs == []


def test_set_epp_raises_when_a_write_fails(fake_host, two_cpus):
    fake_host.fail("cpu1/cpufreq", rc=1, out="Permission denied")
    with pytest.raises(RuntimeError):
        HardwareController(z13ctl_path="z13ctl").set_epp("power")


def test_profile_epp_defaults_to_none_on_builtins():
    assert all(p["epp"] is None for p in profiles.default_profiles())


def test_profile_epp_choices_match_the_epp_module():
    assert profiles.EPP_VALUES == epp.EPP_CHOICES


def test_profile_epp_round_trips_through_the_store(profiles_env):
    p = profiles._profile("CPU Boost test", "balanced", epp="balance_performance")
    profiles.upsert_profile(p)
    assert profiles.get_profile("CPU Boost test")["epp"] == "balance_performance"


def test_validate_accepts_null_and_known_epp_and_rejects_others():
    p = profiles._profile("Boost check", "balanced")
    profiles.validate(p)
    p["epp"] = "power"
    profiles.validate(p)
    p["epp"] = "default"
    with pytest.raises(ValueError):
        profiles.validate(p)

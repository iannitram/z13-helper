"""packaging/host/systemd/system/z13-fan-resume.service: issue #81.

The resume restore must run after every sleep state that powers the SoC down,
including hibernate (SteamOS 3.9 preliminary), not only suspend.
"""

import configparser
from pathlib import Path

UNIT = Path(__file__).resolve().parent.parent / "packaging/host/systemd/system/z13-fan-resume.service"
SLEEP_TARGETS = {"suspend.target", "hibernate.target", "hybrid-sleep.target",
                 "suspend-then-hibernate.target"}


def _unit():
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(UNIT.read_text())
    return cp


def test_runs_after_every_sleep_target():
    assert SLEEP_TARGETS <= set(_unit()["Unit"]["After"].split())


def test_wanted_by_every_sleep_target():
    assert SLEEP_TARGETS <= set(_unit()["Install"]["WantedBy"].split())


def test_is_oneshot_running_the_resume_script():
    svc = _unit()["Service"]
    assert svc["Type"] == "oneshot"
    assert svc["ExecStart"].endswith("/z13-fan-resume.sh")

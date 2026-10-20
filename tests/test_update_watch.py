"""SteamOS row of update_watch and the SteamOS installer's check gate.

host.run and installers.run are faked (FakeHost / a recorder), so nothing runs.
A failed `steamos-update check` must be reported as an error, never as
"up to date", and the installer must not start the updater after a failed check.
"""

import pytest

import installers
import update_watch
from fake_host import FakeHost


@pytest.fixture
def steamos_check(monkeypatch):
    """Returns a function that answers `steamos-update check` with (rc, out)."""
    def answer(rc, out):
        fh = FakeHost().fail("steamos-update check", rc=rc, out=out).install(monkeypatch.setattr)
        return fh
    return answer


def test_missing_tool_is_reported_as_error(steamos_check):
    steamos_check(127, "steamos-update: command not found")
    item = update_watch._fetch_steamos({})
    assert item["error"], "exit 127 must set error, not look like 'no update'"
    assert item["latest"] is None


def test_nonzero_exit_is_reported_as_error(steamos_check):
    steamos_check(1, "Permission denied")
    item = update_watch._fetch_steamos({})
    assert item["error"], "a nonzero check exit must set error"
    assert item["latest"] is None


def test_up_to_date_output_is_not_an_error(steamos_check):
    steamos_check(7, "up to date")
    item = update_watch._fetch_steamos({})
    assert item["error"] is None


def test_exit0_saying_no_update_is_a_contradiction_not_up_to_date(steamos_check):
    steamos_check(0, "No update available")
    item = update_watch._fetch_steamos({})
    assert item["error"], "exit 0 means update available; text saying none must not pass as up-to-date"


def test_install_refuses_after_failed_check(monkeypatch, capsys):
    calls = []

    def fake_run(argv, timeout=600, check=True, **kw):
        calls.append(list(argv))
        if argv[:2] == ["steamos-update", "check"]:
            return 127, "steamos-update: command not found"
        return 0, ""

    monkeypatch.setattr(installers, "run", fake_run)
    rc = installers.install_steamos()
    assert rc != 0, "a failed check must make the install fail"
    assert ["sudo", "-n", "steamos-update"] not in calls, "the updater must not run after a failed check"


def test_install_still_runs_updater_when_update_available(monkeypatch):
    calls = []

    def fake_run(argv, timeout=600, check=True, **kw):
        calls.append(list(argv))
        if argv[:2] == ["steamos-update", "check"]:
            return 0, "SteamOS update available: 3.7.1"
        return 0, ""

    monkeypatch.setattr(installers, "run", fake_run)
    assert installers.install_steamos() == 0
    assert ["sudo", "-n", "steamos-update"] in calls


def test_rc7_no_update_is_up_to_date_not_error(steamos_check):
    # Measured on real hardware: `steamos-update check` prints "No update available" and exits 7.
    steamos_check(7, "No update available")
    item = update_watch._fetch_steamos({})
    assert item["error"] is None, "exit 7 is the up-to-date code, not a failure"
    assert item["latest"] != "update available"


def test_install_treats_rc7_no_update_as_up_to_date(monkeypatch, capsys):
    calls = []

    def fake_run(argv, timeout=600, check=True, **kw):
        calls.append(list(argv))
        if argv[:2] == ["steamos-update", "check"]:
            return 7, "No update available"
        return 0, ""

    monkeypatch.setattr(installers, "run", fake_run)
    assert installers.install_steamos() == 0
    assert ["sudo", "-n", "steamos-update"] not in calls

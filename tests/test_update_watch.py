"""SteamOS row of update_watch and the SteamOS installer's check gate.

host.run and installers.run are faked (FakeHost / a recorder), so nothing runs.
A failed `steamos-update check` must be reported as an error, never as
"up to date", and the installer must not start the updater after a failed check.
"""

import pytest

import installers
import host
import update_watch
from fake_host import FakeHost


@pytest.fixture(autouse=True)
def legacy_updater(monkeypatch):
    """These check-gate tests exercise the older SteamOS tool name."""
    monkeypatch.setattr(host, "_HOLO_TOOLS", {"update": "steamos-update"})


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


# ---- own release source (issue 202): fake HTTP, no network ----

def _own(monkeypatch, tmp_path, status, body="", cached=None):
    import json as _json
    monkeypatch.setattr(update_watch, "CACHE_PATH", str(tmp_path / "updates.json"))
    calls = []

    def fake_get(url, etag=None):
        calls.append(url)
        return status, "etag1", body

    monkeypatch.setattr(update_watch, "_get", fake_get)
    monkeypatch.setattr(update_watch, "SOURCES",
                        [s for s in update_watch.SOURCES if s["id"] == "z13-helper"])
    if cached:
        (tmp_path / "updates.json").write_text(_json.dumps(cached))
    rows = update_watch.check(force=True)
    assert calls and "iannitram/z13-helper/releases/latest" in calls[0]
    return rows[0]


def _release(tag):
    import json as _json
    return _json.dumps({"tag_name": tag, "published_at": "2026-12-01T00:00:00Z",
                        "html_url": "https://example.invalid/r", "body": "notes"})


def test_own_release_source_uses_app_version():
    from versions import APP_VERSION
    src = next(s for s in update_watch.SOURCES if s["id"] == "z13-helper")
    assert src["repo"] == "iannitram/z13-helper" and src["installed"]() == APP_VERSION


def test_own_release_none_published_is_neutral(monkeypatch, tmp_path):
    row = _own(monkeypatch, tmp_path, 404, '{"message":"Not Found"}')
    assert row["error"] is None and row["state"] == "none" and not row["new"]
    assert "no release published yet" in row["notes"]


def test_own_release_newer_is_moved_with_link(monkeypatch, tmp_path):
    row = _own(monkeypatch, tmp_path, 200, _release("v99.0.0"))
    assert row["state"] == "moved" and row["new"] and row["url"] == "https://example.invalid/r"


def test_own_release_equal_is_current(monkeypatch, tmp_path):
    from versions import APP_VERSION
    row = _own(monkeypatch, tmp_path, 200, _release("v" + APP_VERSION))
    assert row["state"] == "current" and not row["new"] and row["error"] is None


def test_own_release_offline_is_neutral_not_error(monkeypatch, tmp_path):
    row = _own(monkeypatch, tmp_path, 0, "")
    assert row["error"] is None and row["state"] == "unreachable" and not row["new"]


def test_own_release_offline_keeps_cached_release(monkeypatch, tmp_path):
    cached = {"checked_at": 0, "items": {"z13-helper": {"latest": "99.0.0", "url": "u"}}}
    row = _own(monkeypatch, tmp_path, 0, "", cached)
    assert row["latest"] == "99.0.0" and row["error"] is None

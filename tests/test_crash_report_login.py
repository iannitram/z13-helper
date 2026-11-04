"""A crash report still goes out when the login name cannot be read (issue #82)."""

import getpass
import io
import json
import logging
import zipfile

import pytest

pytest.importorskip("pwd")  # _report reads the passwd entry; Linux CI only

import crash_report as cr  # noqa: E402


def _boom():
    try:
        raise ValueError("bad slider")
    except ValueError as e:
        return e


def test_report_is_built_when_login_name_cannot_be_read(tmp_path, monkeypatch):
    def no_login():
        raise OSError("no passwd entry and no USER/LOGNAME")

    monkeypatch.setattr(getpass, "getuser", no_login)
    monkeypatch.setattr(cr.platform, "freedesktop_os_release",
                        lambda: {"PRETTY_NAME": "SteamOS 3.9.5"}, raising=False)
    build = {"version": "0.5.1", "commit": "34a1284", "base": "https://example.invalid", "token": "tok"}
    reporter = cr.CrashReporter(build, str(tmp_path / "settings.json"), tmp_path,
                                logging.getLogger("z13-login-test"))

    name, zip_bytes = reporter._report(_boom(), None, None)

    assert name.startswith("z13-helper-") and name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
        report = json.loads(z.read("report.json"))
    assert report["app"] == "z13-helper"
    assert "bad slider" in report["message"]

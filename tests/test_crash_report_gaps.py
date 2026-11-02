"""Expectation gaps for crash_report that the locked test does not cover (R28 review)."""

import io
import zipfile
from datetime import datetime, timezone

import pytest

import crash_report as cr

NOW = datetime(2026, 10, 31, 17, 42, 34, tzinfo=timezone.utc)


@pytest.mark.parametrize("rand", ["0a1b2c3g", "0a1b2c3", "0a1b2c3d\n", "0a1b2c3-"])
def test_drop_filename_rejects_non_hex_or_wrong_length(rand):
    with pytest.raises(ValueError):
        cr.drop_filename("linux", NOW, rand)


def test_log_tail_lines_each_end_in_newline_when_text_has_no_final_newline():
    out = cr.log_tail("a\nb")
    assert out == "a\nb\n"


def test_log_tail_zero_lines_is_empty():
    assert cr.log_tail("a\nb\n", 0) == ""


def test_log_tail_short_log_kept_whole():
    assert cr.log_tail("only\n") == "only\n"


def test_build_zip_app_log_ends_in_newline_for_unterminated_log():
    rep = {"app": "z13-helper"}
    with zipfile.ZipFile(io.BytesIO(cr.build_zip(rep, "x\ny"))) as z:
        assert z.read("app.log").decode() == "x\ny\n"

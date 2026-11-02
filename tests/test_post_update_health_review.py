"""R28 review tests for issue #113: Expectation sentences the locked test does not cover.

Pure checks on sample strings; the exists() callback is a recording fake, so no
path is ever read from disk.
"""

from post_update.health import KEYRING_MIN, carry_forward_missing, keyring_status


def test_keyring_min_is_fifty():
    assert KEYRING_MIN == 50


def test_keyring_pub_needs_whitespace_after_the_word():
    # "pubkey" / "pubring" are not "pub" followed by whitespace.
    assert keyring_status("pubkey x\n" * 60) == "needs"


def test_keyring_counts_pub_lines_with_leading_pub_only():
    lines = "pub   rsa4096 2020-01-01 [SC]\n" * 50
    assert keyring_status(lines) == "ok"


def test_carry_forward_never_checks_comment_or_blank_lines():
    seen = []

    def exists(p):
        seen.append(p)
        return False

    assert carry_forward_missing(("# /etc/zzz\n\n   \n",), exists) == []
    assert seen == []


def test_carry_forward_checks_each_distinct_path_once_and_reports_nothing_for_present_paths():
    seen = []

    def exists(p):
        seen.append(p)
        return p == "/etc/ok"

    assert carry_forward_missing(("/etc/ok\n/etc/bad\n/etc/ok\n",), exists) == ["/etc/bad"]
    assert sorted(seen) == ["/etc/bad", "/etc/ok"]

"""Keyring and carry-forward health (issue #69, part b): read-only checks on sample outputs."""

from post_update.health import carry_forward_missing, keyring_status


def keys(n):
    return "/etc/pacman.d/gnupg/pubring.gpg\n-------\n" + "".join(
        f"pub   rsa4096 2020-01-01 [SC]\n      ABCD{i:04d}\nuid   [ full ] Dev {i}\nsub   rsa4096 [E]\n\n"
        for i in range(n))


def test_keyring_empty_after_an_update_needs_repair():
    assert keyring_status("") == "needs"
    assert keyring_status("error: keyring is not writable\n") == "needs"


def test_keyring_healthy_counts_pub_lines_only():
    assert keyring_status(keys(180)) == "ok"
    assert keyring_status(keys(50)) == "ok"
    assert keyring_status(keys(49)) == "needs"


def test_carry_forward_all_present():
    confs = ("/etc/a\n/etc/b\n",)
    assert carry_forward_missing(confs, lambda p: True) == []


def test_carry_forward_lists_missing_in_order_skipping_comments_and_blanks():
    confs = ("# ours\n/etc/a\n\n  /etc/b  \n", "/etc/c\n# /etc/zzz\n")
    present = {"/etc/b"}
    assert carry_forward_missing(confs, lambda p: p in present) == ["/etc/a", "/etc/c"]


def test_carry_forward_dedupes_across_files():
    confs = ("/etc/a\n", "/etc/a\n")
    assert carry_forward_missing(confs, lambda p: False) == ["/etc/a"]


def test_carry_forward_no_files():
    assert carry_forward_missing((), lambda p: False) == []

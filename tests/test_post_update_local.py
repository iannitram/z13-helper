"""Local post-update recipes (issue #72, part a): the TOML loader."""

import pytest

from post_update.local import load_dir, parse_local

GOOD = '''
id = "tailscale-up"
title = "Tailscale up"
fixes = "Tailscale is down after an update"
check = "tailscale status"
apply = "tailscale up"
sudo = true
'''


def test_parse_local_fields_and_flag_defaults():
    assert parse_local(GOOD) == {
        "id": "tailscale-up", "title": "Tailscale up", "fixes": "Tailscale is down after an update",
        "check": "tailscale status", "apply": "tailscale up",
        "sudo": True, "network": False, "reboot": False, "pacman": False, "default_on": False}


@pytest.mark.parametrize("field", ["id", "title", "fixes", "check", "apply"])
def test_parse_local_missing_field(field):
    text = "\n".join(l for l in GOOD.splitlines() if not l.startswith(field + " "))
    with pytest.raises(ValueError, match=f"missing or empty field: {field}"):
        parse_local(text)


def test_parse_local_bad_id_flag_and_toml():
    with pytest.raises(ValueError, match="bad id"):
        parse_local(GOOD.replace("tailscale-up", "Tail Scale"))
    with pytest.raises(ValueError, match="flag sudo must be true or false"):
        parse_local(GOOD.replace("sudo = true", 'sudo = "yes"'))
    with pytest.raises(ValueError, match="invalid TOML"):
        parse_local("id = ")


def test_load_dir_good_bad_and_non_toml(tmp_path):
    (tmp_path / "b.toml").write_text(GOOD.replace("tailscale-up", "bbb"))
    (tmp_path / "a.toml").write_text(GOOD.replace("tailscale-up", "aaa"))
    (tmp_path / "broken.toml").write_text("id = ")
    (tmp_path / "notes.txt").write_text("ignore me")
    recipes, errors = load_dir(str(tmp_path))
    assert [r["id"] for r in recipes] == ["aaa", "bbb"]
    assert len(errors) == 1 and errors[0].startswith("broken.toml: invalid TOML")


def test_load_dir_duplicate_id_is_an_error_row_not_a_stop(tmp_path):
    (tmp_path / "1.toml").write_text(GOOD)
    (tmp_path / "2.toml").write_text(GOOD)
    (tmp_path / "3.toml").write_text(GOOD.replace("tailscale-up", "other"))
    recipes, errors = load_dir(str(tmp_path))
    assert [r["id"] for r in recipes] == ["tailscale-up", "other"]
    assert errors == ["2.toml: duplicate id: tailscale-up"]


def test_load_dir_missing_folder_is_empty(tmp_path):
    assert load_dir(str(tmp_path / "nope")) == ([], [])

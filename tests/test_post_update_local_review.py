"""Review checks for the local recipe TOML loader (PR #145, issue #115)."""

from post_update.local import load_dir, parse_local

GOOD = (
    'id = "tailscale-up"\r\n'
    'title = "Tailscale up"\r\n'
    'fixes = "Tailscale is down after an update"\r\n'
    'check = "tailscale status"\r\n'
    'apply = "tailscale up"\r\n'
    "sudo = true\r\n"
)


def test_load_dir_accepts_utf8_bom_and_crlf(tmp_path):
    (tmp_path / "bom.toml").write_bytes(b"\xef\xbb\xbf" + GOOD.encode("utf-8"))
    (tmp_path / "crlf.toml").write_bytes(GOOD.replace("tailscale-up", "crlf-up").encode("utf-8"))
    recipes, errors = load_dir(str(tmp_path))
    assert errors == []
    assert [r["id"] for r in recipes] == ["tailscale-up", "crlf-up"]
    assert recipes[0]["sudo"] is True


def test_load_dir_non_utf8_file_is_an_error_row(tmp_path):
    (tmp_path / "bad.toml").write_bytes(b"\xff\xfe\x00 not utf-8")
    (tmp_path / "ok.toml").write_text(GOOD.replace("tailscale-up", "ok-one"), encoding="utf-8")
    recipes, errors = load_dir(str(tmp_path))
    assert [r["id"] for r in recipes] == ["ok-one"]
    assert errors == ["bad.toml: unable to read file"]


def test_parse_local_unknown_keys_are_dropped():
    recipe = parse_local(GOOD.replace("\r\n", "\n") + 'extra = 1\n')
    assert "extra" not in recipe and len(recipe) == 11


def test_parse_local_non_string_id_is_missing_field():
    try:
        parse_local(GOOD.replace('id = "tailscale-up"', "id = 5"))
    except ValueError as e:
        assert str(e) == "missing or empty field: id"
    else:
        raise AssertionError("expected ValueError")

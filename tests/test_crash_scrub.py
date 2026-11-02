"""Crash-report scrubber (issue #82, part a): planted secrets and identifiers must be gone."""

from crash_scrub import scrub

HOME, HOST, USER = "/home/deck", "z13-steamdeck", "deck"


def s(text, **kw):
    return scrub(text, home=HOME, hostname=HOST, user=USER, **kw)


def test_home_path_becomes_tilde():
    assert s("File /home/deck/dev/z13-helper/main.py, line 3") == "File ~/dev/z13-helper/main.py, line 3"


def test_mac_addresses_colon_and_dash_any_case():
    out = s("wlan0 aa:BB:cc:11:22:33 and AA-BB-CC-11-22-33 up")
    assert out == "wlan0 <mac> and <mac> up"


def test_not_a_mac():
    assert s("version 1:2:3 and 12:34:56:78:9a") == "version 1:2:3 and 12:34:56:78:9a"


def test_secrets_by_key_name():
    out = s("password=hunter2 psk: abcd1234 Token = xyz api_key=K1 secret=s3")
    assert "hunter2" not in out and "abcd1234" not in out and "xyz" not in out
    assert out == "password=<redacted> psk: <redacted> Token = <redacted> api_key=<redacted> secret=<redacted>"


def test_bearer_token():
    assert s("Authorization: Bearer eyJhbGciOi.abc.def") == "Authorization: Bearer <redacted>"


def test_serial_number():
    out = s("serial number: R9N0CV123456 SerialNumber=ABC123")
    assert out == "serial number: <redacted> SerialNumber=<redacted>"


def test_hostname_and_user_whole_words_only():
    out = s("host z13-steamdeck user deck ran deckard on Z13-STEAMDECK")
    assert out == "host <host> user <user> ran deckard on <host>"


def test_hostname_and_user_optional():
    assert scrub("deck on z13-steamdeck", home=HOME) == "deck on z13-steamdeck"


def test_home_replaced_before_user_so_paths_stay_readable():
    assert s("/home/deck/.config and deck") == "~/.config and <user>"


def test_root_home_is_not_replaced_everywhere():
    assert scrub("/usr/bin/python", home="/") == "/usr/bin/python"
    assert scrub("/usr/bin/python", home="") == "/usr/bin/python"


def test_clean_text_unchanged():
    t = "Traceback (most recent call last):\n  ZeroDivisionError: division by zero\n"
    assert s(t) == t

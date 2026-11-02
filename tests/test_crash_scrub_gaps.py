"""Expectation gaps for crash_scrub.scrub that the locked test does not cover (R28 review)."""

from crash_scrub import scrub

HOME, HOST, USER = "/home/deck", "z13-steamdeck", "deck"


def s(text, **kw):
    return scrub(text, home=HOME, hostname=HOST, user=USER, **kw)


def test_passwd_apikey_and_dashed_api_key():
    assert (
        s("passwd=p1 apikey: k2 api-key=k3")
        == "passwd=<redacted> apikey: <redacted> api-key=<redacted>"
    )


def test_mixed_separators_are_not_a_mac():
    assert s("id aa:bb-cc:dd:ee:ff end") == "id aa:bb-cc:dd:ee:ff end"


def test_seven_pairs_are_not_a_mac():
    assert s("id aa:bb:cc:dd:ee:ff:00 end") == "id aa:bb:cc:dd:ee:ff:00 end"


def test_plain_serial_and_serial_underscore_number():
    assert (
        s("serial: ABC serial_number=DEF")
        == "serial: <redacted> serial_number=<redacted>"
    )


def test_user_next_to_dash_or_underscore_is_not_whole_word():
    assert (
        scrub("deck-old deck_x deck.py", home=HOME, user="deck")
        == "deck-old deck_x <user>.py"
    )


def test_every_home_occurrence_replaced():
    assert s("/home/deck/a /home/deck/b") == "~/a ~/b"

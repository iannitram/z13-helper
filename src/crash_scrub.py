"""Scrub secrets and identifiers out of crash-report text (#117)."""

import re

_MAC = re.compile(
    r"(?<![0-9A-Za-z:-])[0-9a-f]{2}([:-])[0-9a-f]{2}(?:\1[0-9a-f]{2}){4}(?![0-9A-Za-z:-])",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r'''(?<![A-Za-z0-9_])(password|passwd|psk|token|secret|api_key|api-key|apikey)(["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|\S+)''',
    re.IGNORECASE,
)
_SERIAL = re.compile(
    r'''(?<![A-Za-z0-9_])(serial(?:[ _]?number)?)(["']?\s*[=:]\s*)(?:"[^"]*"|'[^']*'|\S+)''',
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b((?:Bearer|Basic)\s+)\S+", re.IGNORECASE)


def _whole_word(word: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(word)}(?![A-Za-z0-9_-])", re.IGNORECASE)


def scrub(text: str, *, home: str, hostname: str | None = None, user: str | None = None) -> str:
    """Return ``text`` with the home path, MACs, secrets, serials, bearer tokens, host and user removed."""
    if home and home != "/":
        text = text.replace(home, "~")
    text = _MAC.sub("<mac>", text)
    text = _SECRET.sub(r"\1\2<redacted>", text)
    text = _SERIAL.sub(r"\1\2<redacted>", text)
    text = _BEARER.sub(r"\1<redacted>", text)
    if hostname:
        text = _whole_word(hostname).sub("<host>", text)
    if user:
        text = _whole_word(user).sub("<user>", text)
    return text

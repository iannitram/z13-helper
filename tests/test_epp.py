"""amd-pstate EPP helpers (issue #64, part a): parsing what the CPU Boost combo shows."""

import pytest

import epp


@pytest.mark.parametrize("text, expected", [
    ("performance\n", "performance"),
    ("  balance_power ", "balance_power"),
    ("default\n", None),
    ("", None),
    ("Performance", None),
])
def test_parse_epp(text, expected):
    assert epp.parse_epp(text) == expected


def test_parse_available_keeps_known_values_in_choice_order():
    text = "default performance balance_performance balance_power power\n"
    assert epp.parse_available(text) == ["power", "balance_power", "balance_performance", "performance"]
    assert epp.parse_available("performance power\n") == ["power", "performance"]
    assert epp.parse_available("") == []


def test_summarize_all_same_mixed_and_unreadable():
    assert epp.summarize_epp(("performance\n", "performance\n")) == "performance"
    assert epp.summarize_epp(("power", "performance")) == "mixed"
    assert epp.summarize_epp(("power", None)) is None
    assert epp.summarize_epp(("power", "bogus")) is None
    assert epp.summarize_epp(()) is None


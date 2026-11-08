"""Review probe for src/epp.py: stated behaviour the locked tests in tests/test_epp.py do not pin."""

import epp


def test_choices_are_the_display_order():
    assert epp.EPP_CHOICES == ("power", "balance_power", "balance_performance", "performance")


def test_parse_epp_rejects_multi_word_and_partial_text():
    assert epp.parse_epp("balance power") is None
    assert epp.parse_epp("performance power") is None
    assert epp.parse_epp("balance") is None


def test_parse_available_unknown_only_and_whitespace_only():
    assert epp.parse_available("default") == []
    assert epp.parse_available("  \n") == []
    assert epp.parse_available("balance") == []


def test_summarize_single_cpu_and_all_unreadable():
    assert epp.summarize_epp(("power",)) == "power"
    assert epp.summarize_epp(("power\n",)) == "power"
    assert epp.summarize_epp((None,)) is None
    assert epp.summarize_epp((None, None)) is None

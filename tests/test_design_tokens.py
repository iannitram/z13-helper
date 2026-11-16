"""The design tokens: one complete set per theme, readable in both (#229).

Checks src/design_tokens.json through src/design_tokens.py: both themes carry
the same names, every pair of ink and surface the app draws meets its WCAG
contrast ratio, the saved theme choice resolves as documented, the stylesheet
theme.py builds uses token colours only, and the Decky plugin reads the same
file.
"""
import re
from pathlib import Path

import pytest

import design_tokens as tokens

REPO = Path(__file__).resolve().parents[1]
HEX = re.compile(r"#[0-9A-Fa-f]{6}")

# (ink, surfaces it is drawn on, smallest contrast ratio)
PAIRS = [
    ("text", ("window", "card", "control", "control_hover", "inset"), 7.0),
    ("text_muted", ("window", "card", "control", "inset"), 4.5),
    ("text_faint", ("card", "control"), 3.0),
    ("on_accent", ("accent", "accent_hover", "custom"), 4.5),
    ("on_tooltip", ("tooltip",), 7.0),
    ("ok", ("window", "card"), 4.5),
    ("warn", ("window", "card"), 4.5),
    ("danger", ("window", "card", "inset"), 4.5),
    ("accent", ("window", "card"), 4.5),          # link text, table cells
    ("accent", ("control",), 3.0),                # slider, focus ring, tile outline
    ("quiet", ("card", "control"), 3.0),
    ("balanced", ("card", "control"), 3.0),
    ("performance", ("card", "control"), 3.0),
    ("custom", ("card", "control", "inset"), 3.0),
    ("border_strong", ("window", "card"), 3.0),
    ("chart_cpu", ("inset",), 3.0),
    ("chart_gpu", ("inset",), 3.0),
    ("chart_fan1", ("inset",), 3.0),
    ("chart_fan2", ("inset",), 3.0),
]


def test_both_themes_carry_the_same_tokens():
    dark, light = tokens.colours("dark"), tokens.colours("light")
    assert set(dark) == set(light)
    assert dark != light
    for mode in tokens.MODES:
        for name, value in tokens.colours(mode).items():
            assert HEX.fullmatch(value), f"{mode}.{name} = {value!r}"


@pytest.mark.parametrize("mode", tokens.MODES)
def test_every_ink_is_readable_on_its_surfaces(mode):
    c = tokens.colours(mode)
    low = [f"{mode}: {ink} on {surface} is {tokens.contrast(c[ink], c[surface]):.2f}, needs {need}"
           for ink, surfaces, need in PAIRS for surface in surfaces
           if tokens.contrast(c[ink], c[surface]) < need]
    assert low == []


def test_contrast_is_the_wcag_ratio():
    assert tokens.contrast("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert tokens.contrast("#FFFFFF", "#000000") == pytest.approx(21.0)
    assert tokens.contrast("#777777", "#777777") == pytest.approx(1.0)


def test_the_theme_choice_follows_the_system_unless_pinned():
    assert tokens.DEFAULT_CHOICE == "system"
    assert tokens.resolve("system", "light") == "light"
    assert tokens.resolve("system", "dark") == "dark"
    assert tokens.resolve("system", None) == tokens.FALLBACK_MODE     # the desktop does not say
    assert tokens.resolve("dark", "light") == "dark"                  # manual override wins
    assert tokens.resolve("light", "dark") == "light"
    assert tokens.resolve("purple", "light") == "light"               # unknown setting = system
    assert tokens.resolve(None, None) == tokens.FALLBACK_MODE


def test_sizes_keep_the_touch_minimum():
    assert tokens.SIZE["touch_min_px"] >= 44                          # ADR 0002
    assert tokens.SIZE["tile_h"] >= tokens.SIZE["touch_min_px"]
    assert tokens.FONT["caption_pt"] < tokens.FONT["body_pt"] < tokens.FONT["heading_pt"] < tokens.FONT["stat_pt"]


def test_css_variables_are_named_after_the_tokens():
    css = tokens.css_variables("dark")
    assert css["--z13-on-accent"] == tokens.colours("dark")["on_accent"]
    assert len(css) == len(tokens.colours("dark"))


@pytest.mark.parametrize("mode", tokens.MODES)
def test_the_stylesheet_uses_token_colours_only(mode):
    theme = pytest.importorskip("theme")
    allowed = {v.upper() for v in tokens.colours(mode).values()}
    used = {h.upper() for h in HEX.findall(theme.stylesheet(mode))}
    assert used and used <= allowed


def test_the_decky_plugin_reads_the_same_file():
    source = (REPO / "decky" / "src" / "tokens.ts").read_text(encoding="utf-8")
    assert 'from "../../src/design_tokens.json"' in source
    assert "design.color.dark" in source           # Steam's Quick Access Menu is always dark
    names = set(tokens.colours("dark"))
    used = set()
    for path in (REPO / "decky" / "src").rglob("*.ts*"):
        used |= set(re.findall(r'\bcolour\("([a-z0-9_]+)"\)', path.read_text(encoding="utf-8")))
    assert used and used <= names

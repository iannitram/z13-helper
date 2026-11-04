"""Shared design tokens (#133, ADR 0002): schema, parity and touch minimums. No PyQt6 import."""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import design_tokens  # noqa: E402

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")
SRC = {n: (ROOT / "src" / n).read_text(encoding="utf-8")
       for n in ("theme.py", "dashboard_ui.py", "fans_window.py", "fan_curve_widget.py")}


def test_schema_and_hex_colours():
    t = design_tokens.TOKENS
    for key in ("colors", "accents", "radii", "spacing", "font_pt", "touch"):
        assert t[key]
    for group in ("colors", "accents"):
        for name, value in t[group].items():
            assert HEX.match(value), (group, name)
    assert {"quiet", "balanced", "performance", "custom"} <= set(t["accents"])


def test_touch_minimums_at_least_44():
    touch = design_tokens.TOKENS["touch"]
    assert touch["touch_min_px"] >= 44
    assert touch["tile_secondary_px"] >= 44
    assert touch["tile_primary_px"] >= touch["tile_secondary_px"]
    # The slider's own min-height gives a >= 44 px hit box around the handle.
    assert "QSlider:horizontal {{ min-height: {TOUCH_MIN}px; }}" in SRC["theme.py"]
    assert "QCheckBox {{ spacing: {_S[\"md\"]}px; min-height: {TOUCH_MIN}px; }}" in SRC["theme.py"]


def test_theme_has_no_palette_literals_and_reads_tokens():
    theme = SRC["theme.py"]
    assert "design_tokens" in theme
    assert not re.search(r'"#[0-9A-Fa-f]{6}"', theme)


def test_theme_values_match_tokens():
    # theme.py imports PyQt6, so check its token wiring textually.
    theme = SRC["theme.py"]
    for name, group, key in (("FORM", "_C", "form"), ("ECO", "_A", "quiet"),
                             ("STANDARD", "_A", "balanced"), ("TURBO", "_A", "performance"),
                             ("CUSTOM", "_A", "custom")):
        assert f'{name} = {group}["{key}"]' in theme


def test_decky_tokens_import_shared_json_and_have_no_stray_hex():
    ts = (ROOT / "decky" / "src" / "tokens.ts").read_text(encoding="utf-8")
    assert "../../src/design_tokens.json" in ts
    comp = (ROOT / "decky" / "src" / "components" / "KeyboardBrightness.tsx").read_text(encoding="utf-8")
    assert "tokens" in comp and "#1a9fff" not in comp and "#ffb02e" not in comp
    assert json.loads((ROOT / "decky" / "tsconfig.json").read_text())["compilerOptions"]["resolveJsonModule"]


def test_no_fixed_sizes_below_touch_min_in_windows():
    for name in ("dashboard_ui.py", "fans_window.py"):
        for m in re.finditer(r"ModeTile\([^)]*height=(\d+)", SRC[name]):
            assert int(m.group(1)) >= 44, name
        for m in re.finditer(r"setFixedHeight\((\d+)\)", SRC[name]):
            assert int(m.group(1)) >= 44, name


def test_fan_curve_points_have_touch_hit_area():
    m = re.search(r"^HIT_R = (\d+)", SRC["fan_curve_widget.py"], re.M)
    # Manhattan reach r gives an inscribed square of side r*sqrt(2) >= 44.
    assert int(m.group(1)) * 2 ** 0.5 >= 44


def test_every_control_still_present():
    ui, fw = SRC["dashboard_ui.py"], SRC["fans_window.py"]
    for attr in ("maxfans_pill", "profile_combo", "light_target", "screen_slider", "battery_slider",
                 "batt_100", "startup", "btn_updates", "btn_quit", "error_badge", "fans_tile"):
        assert f"self.{attr} =" in ui, attr
    for attr in ("btn_cpu", "btn_adv", "combo", "btn_add", "btn_rename", "btn_remove", "clamp",
                 "btn_defaults", "max_fans_cb", "apply_curve", "base_combo", "epp_combo",
                 "apply_power", "auto_uv"):
        assert f"self.{attr} =" in fw, attr

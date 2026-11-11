"""#86: no fan curve may start above 20 C (the EC can hold its previous duty below
the first anchor), and the legacy scripts must match the app's built-in curves."""

import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import profiles  # noqa: E402

LITERAL = re.compile(r'fancurve\s+--set\s+"(\d[^"]*)"')  # numeric specs; "$CURVE" is checked in test_fan_resume_script


def _literals():
    out = []
    files = list((ROOT / "tooling").rglob("*")) + list((ROOT / "packaging").rglob("*"))
    for path in sorted(files):
        if not path.is_file() or path.suffix in (".png", ".svg", ".pyc"):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        for spec in LITERAL.findall(text):
            out.append(pytest.param(spec, id=f"{path.relative_to(ROOT)}"))
    return out


@pytest.mark.parametrize("spec", _literals())
def test_fancurve_literal_starts_at_or_below_20c(spec):
    first_temp = int(spec.split(",")[0].split(":")[0])
    assert first_temp <= 20


def test_literals_are_found():
    assert len(_literals()) >= 3


def _script_spec(name):
    text = (ROOT / "tooling/profiles" / name).read_text()
    return LITERAL.search(text).group(1)


def _spec(curve):
    return ",".join(f"{t}:{p}%" for t, p in curve)


def test_z13_max_matches_turbo_curve():
    assert _script_spec("z13-max") == _spec(profiles.TURBO_CURVE)


def test_z13_balanced_matches_balanced_curve():
    assert _script_spec("z13-balanced") == _spec(profiles.BALANCED_CURVE)


def test_z13_max_has_no_live_status_read():
    assert "z13ctl status" not in (ROOT / "tooling/profiles/z13-max").read_text()


def test_widget_default_curve_first_anchor_and_shape():
    import fan_curve_widget
    curve = fan_curve_widget.DEFAULT_CURVE
    assert curve[0][0] <= 20
    assert curve == profiles.BALANCED_CURVE

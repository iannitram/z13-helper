"""Exercise control gating without importing Qt or starting the desktop app."""

import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

import platform_support as ps


def _method(filename, cls, name):
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src" / filename).read_text())
    klass = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls)
    method = next(n for n in klass.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {"logging": logging}
    exec(compile(ast.Module(body=[method], type_ignores=[]), filename, "exec"), namespace)
    return namespace[name]


class Widget:
    def __init__(self):
        self.enabled = self.visible = True
        self.tooltip = ""

    def setEnabled(self, on):
        self.enabled = on

    def isEnabled(self):
        return self.enabled

    def setVisible(self, on):
        self.visible = on

    def setToolTip(self, reason):
        self.tooltip = reason

    def toolTip(self):
        return self.tooltip

    def isChecked(self):
        return True


def _support(missing=()):
    return {"supported": True, "reason": "", "battery_present": True,
            "capabilities": {n: {"available": n not in missing,
                                 "reason": f"missing {n}" if n in missing else ""}
                             for n in ps.CAPABILITIES}}


def _fans():
    names = ("base_combo combo btn_add btn_defaults chart clamp apply_curve max_fans_cb "
             "apply_power uv auto_uv btn_adv spl sppt fppt epp_combo").split()
    w = SimpleNamespace(**{n: Widget() for n in names}, _nav=lambda idx: None)
    apply = _method("fans_window.py", "FansWindow", "apply_capabilities")
    w.apply_capabilities = lambda support: apply(w, support)
    return w


@pytest.mark.parametrize("missing,disabled,unaffected", [
    ("platform_profile", "base_combo", "apply_power"),
    ("fan_curve", "apply_curve", "apply_power"),
    ("tdp", "apply_power", "apply_curve"),
    ("undervolt", "auto_uv", "apply_power"),
])
def test_fans_controls_follow_individual_capabilities_and_reprobe(missing, disabled, unaffected):
    w = _fans()
    w.apply_capabilities(_support((missing,)))
    assert not getattr(w, disabled).enabled
    assert missing in getattr(w, disabled).tooltip
    assert getattr(w, unaffected).enabled
    if missing == "tdp":
        assert not any(row.enabled for row in (w.spl, w.sppt, w.fppt))
    w.apply_capabilities(_support())
    assert getattr(w, disabled).enabled and not getattr(w, disabled).tooltip


def test_dashboard_gates_individual_controls_and_existing_fans_window():
    names = ("fans_tile profile_combo maxfans_pill light_target perf light batt _batt_sep "
             "battery_slider batt_100").split()
    w = SimpleNamespace(**{n: Widget() for n in names}, _support=None, _fans_window=_fans(),
                        _tiles={"Balanced": Widget()}, _bright_tiles={1: Widget()},
                        _lighting_combos={"keyboard": (Widget(), Widget())},
                        control=SimpleNamespace(shown=lambda: {}),
                        _refresh_lighting_ui=lambda p: None)
    apply = _method("dashboard_ui.py", "DashboardUI", "_apply_capabilities")
    apply(w, _support(("fan_curve", "tdp", "undervolt")))
    assert not w.maxfans_pill.enabled
    assert w.fans_tile.enabled and w.profile_combo.enabled and w.light_target.enabled
    assert not w._fans_window.apply_curve.enabled and not w._fans_window.apply_power.enabled
    support = _support(("platform_profile", "fan_curve", "tdp", "undervolt", "keyboard_backlight"))
    apply(w, support)
    assert not any(widget.enabled for widget in (
        *w._tiles.values(), w.profile_combo, w.maxfans_pill, w.fans_tile,
        w.light_target, *w._bright_tiles.values(), *w._lighting_combos["keyboard"]))
    assert w.battery_slider.enabled
    assert not w._fans_window.apply_power.enabled
    apply(w, _support())
    assert w.fans_tile.enabled and w.maxfans_pill.enabled and w._fans_window.apply_power.enabled
    unsupported = _support(ps.CAPABILITIES)
    unsupported.update(supported=False, reason="unsupported device", battery_present=False)
    apply(w, unsupported)
    assert not w.batt.visible and not w._batt_sep.visible
    assert not w.fans_tile.enabled and not w.battery_slider.enabled
    assert not w._fans_window.epp_combo.enabled


def test_lighting_refresh_cannot_reenable_unavailable_keyboard():
    class Combo(Widget):
        def blockSignals(self, on):
            pass

        def findData(self, value):
            return 0

        def setCurrentIndex(self, index):
            pass

    w = SimpleNamespace(_support=_support(("keyboard_backlight",)),
                        _lighting_combos={"keyboard": (Combo(), Combo())})
    refresh = _method("dashboard_ui.py", "DashboardUI", "_refresh_lighting_ui")
    profile = {"lighting": {"keyboard": {"mode": "static", "color": "red"}}}
    refresh(w, profile)
    assert not w._lighting_combos["keyboard"][1].enabled
    w._support = _support()
    refresh(w, profile)
    assert w._lighting_combos["keyboard"][1].enabled

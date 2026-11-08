"""Locked tests for #22 capability gating (the three gaps in the fix brief).

PyQt6 is not installed in the test venv, so these read the source with ast instead
of constructing the windows. Each test pins one gap; they fail until it is fixed.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _method(path, class_name, method_name):
    tree = ast.parse((SRC / path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == method_name:
                    return ast.get_source_segment((SRC / path).read_text(encoding="utf-8"), item)
    raise AssertionError(f"{class_name}.{method_name} not found in {path}")


def test_fans_window_consults_platform_detection():
    """Gap 1: the Fans + Power window must read platform_support (detect) and disable
    its controls on an unsupported machine. Today fans_window.py never mentions it."""
    text = (SRC / "fans_window.py").read_text(encoding="utf-8")
    assert "platform_support" in text, "fans_window.py does not use platform_support.detect()"


def test_battery_widgets_hidden_when_no_battery():
    """Gap 2: no battery means the battery widgets are hidden, on any machine. Today
    `has_battery = support["battery_present"] or not ok` keeps them visible when unsupported."""
    body = _method("dashboard_ui.py", "DashboardUI", "_apply_capabilities")
    assert 'has_battery = support["battery_present"] or not ok' not in body, (
        "battery visibility still ORs in `not ok`, so an unsupported machine with no battery shows the widgets")
    assert 'has_battery = support["battery_present"]' in body


def test_unsupported_machine_never_queries_battery_limit():
    """Gap 3: _load_initial_state must not call controller.get_battery_limit() (a z13ctl
    host call) unless platform_support says the machine is supported."""
    body = _method("dashboard_ui.py", "DashboardUI", "_load_initial_state")
    call = body.find("get_battery_limit")
    assert call != -1, "expected _load_initial_state to still be the place that reads the limit"
    guard = body.find('supported')
    assert guard != -1 and guard < call, (
        "get_battery_limit() runs without a preceding platform_support 'supported' check")

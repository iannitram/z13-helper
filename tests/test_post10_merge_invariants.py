"""Invariants for merging main into post-1.0 (#210).

main brings #22 capability gating; post-1.0 brings the #133 touch layout and the
#129/#130 lightbar signals. Both must survive every main -> post-1.0 merge, and the
host CLI must stay free of the desktop/Decky-only hw_signals module. Source is read
with ast, so no PyQt6 import is needed.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def _local_imports(module):
    tree = ast.parse((SRC / f"{module}.py").read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return {n for n in names if (SRC / f"{n}.py").exists()}


def _closure(root):
    seen, stack = set(), [root]
    while stack:
        mod = stack.pop()
        if mod not in seen:
            seen.add(mod)
            stack.extend(_local_imports(mod))
    return seen


def _fans_window_init():
    text = (SRC / "fans_window.py").read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.ClassDef) and node.name == "FansWindow":
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    return ast.get_source_segment(text, item)
    raise AssertionError("FansWindow.__init__ not found")


def test_host_cli_does_not_import_hw_signals():
    assert "hw_signals" not in _closure("host")


def test_fans_window_keeps_capability_gate_and_touch_size():
    init = _fans_window_init()
    assert "self._support = None" in init                 # #22, main
    assert "self.resize(1100, 760)" in init                # #133, post-1.0
    assert "self.setMinimumSize(760, 600)" in init         # #133, post-1.0
    text = (SRC / "fans_window.py").read_text(encoding="utf-8")
    assert "def apply_capabilities(self, support)" in text


def test_dashboard_file_menu_keeps_timeline_and_compute():
    text = (SRC / "dashboard_ui.py").read_text(encoding="utf-8")
    assert "self._open_timeline" in text                   # #135, post-1.0
    assert "self._open_compute" in text                    # #26, main
    assert "TOUCH_TARGET_MIN" in text                      # #133, post-1.0
    assert "platform_support.detect()" in text             # #22, main


def test_decky_map_lists_hw_signals_copy():
    text = (ROOT / "MAP" / "decky.md").read_text(encoding="utf-8")
    assert "py_modules/hw_signals.py" in text

"""No hard-coded colours outside the design tokens (#229).

Every colour the desktop app or the Decky plugin draws must come from
src/design_tokens.json, so both themes stay complete and the two frontends
stay in step. This scan fails on a colour literal anywhere else:

* desktop app (main.py, src/*.py), read with `ast` so comments and docstrings
  do not count: hex colours, rgb()/hsl() strings, colour names in stylesheet
  text, QColor(...) built from literals, Qt.GlobalColor.* (transparent is not
  a colour), and .lighter()/.darker() (a shade nobody declared);
* Decky plugin (decky/src/*.ts, *.tsx): hex colours, rgb()/hsl() and colour
  names in style values.

No file is exempt: src/design_tokens.py only loads the JSON and
decky/src/tokens.ts only imports it.
"""
import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_HEX_LONG = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6})(?![0-9a-zA-Z_])")
# A short hex colour needs a letter, so issue numbers such as "#229" pass.
_HEX_SHORT = re.compile(r"#(?=[0-9]*[a-fA-F])[0-9a-fA-F]{3,4}(?![0-9a-zA-Z_])")
_FUNC = re.compile(r"\b(?:rgba?|hsla?|hsva?)\s*\(", re.I)
_NAMES = ("white|black|red|green|blue|yellow|orange|gray|grey|cyan|magenta|"
          "purple|pink|brown|silver|gold|navy|teal|lime|maroon|olive")
_NAMED_QSS = re.compile(
    r"(?:color|background(?:-color)?|border(?:-[a-z]+)*)\s*:[^;{}]*?\b(" + _NAMES + r")\b", re.I)
_NAMED_TSX = re.compile(
    r"(?:color|background|backgroundColor|borderColor|border|fill|stroke)\s*:\s*"
    r"[^,;}\n]*[\"'`](?:[^\"'`]*\s)?(" + _NAMES + r")[\"'`]", re.I)
_ALLOWED_GLOBAL = {"transparent"}


def _text_hits(text):
    hits = [m.group(0) for rx in (_HEX_LONG, _HEX_SHORT, _FUNC) for m in rx.finditer(text)]
    hits += [f"colour name '{m.group(1)}'" for m in _NAMED_QSS.finditer(text)]
    return hits


def _docstring_nodes(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                out.add(id(body[0].value))
    return out


def scan_python(source, name="<src>"):
    """Return ["name:line: what"] for each hard-coded colour in Python source."""
    tree = ast.parse(source)
    skip = _docstring_nodes(tree)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in skip:
            for hit in _text_hits(node.value):
                found.append(f"{name}:{node.lineno}: {hit}")
        elif isinstance(node, ast.Call):
            fn = node.func
            fn_name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            literal = [a for a in node.args if isinstance(a, ast.Constant)]
            if fn_name == "QColor" and literal:
                found.append(f"{name}:{node.lineno}: QColor({', '.join(repr(a.value) for a in literal)})")
            elif fn_name in ("lighter", "darker") and isinstance(fn, ast.Attribute):
                found.append(f"{name}:{node.lineno}: .{fn_name}() makes a shade outside the tokens")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) \
                and node.value.attr == "GlobalColor" and node.attr not in _ALLOWED_GLOBAL:
            found.append(f"{name}:{node.lineno}: Qt.GlobalColor.{node.attr}")
    return sorted(set(found), key=lambda s: (s.split(":")[0], int(s.split(":")[1])))


def scan_tsx(source, name="<tsx>"):
    found = []
    for n, line in enumerate(source.splitlines(), 1):
        code = line.split("//", 1)[0]
        hits = [m.group(0) for rx in (_HEX_LONG, _HEX_SHORT, _FUNC) for m in rx.finditer(code)]
        hits += [f"colour name '{m.group(1)}'" for m in _NAMED_TSX.finditer(code)]
        found += [f"{name}:{n}: {h}" for h in hits]
    return found


def _desktop_files():
    return [REPO / "main.py"] + sorted((REPO / "src").glob("*.py"))


def _decky_files():
    root = REPO / "decky" / "src"
    return sorted(list(root.rglob("*.ts")) + list(root.rglob("*.tsx")))


def test_desktop_app_has_no_colour_outside_the_tokens():
    found = []
    for path in _desktop_files():
        found += scan_python(path.read_text(encoding="utf-8"), str(path.relative_to(REPO)))
    assert not found, "hard-coded colours outside src/design_tokens.json:\n" + "\n".join(found)


def test_decky_plugin_has_no_colour_outside_the_tokens():
    found = []
    for path in _decky_files():
        found += scan_tsx(path.read_text(encoding="utf-8"), str(path.relative_to(REPO)))
    assert not found, "hard-coded colours outside src/design_tokens.json:\n" + "\n".join(found)


def test_the_scan_catches_each_kind_of_colour():
    bad = '''
A = "#3AAEEF"
B = f"color: {A}; background: white;"
C = "border: 1px solid rgba(0, 0, 0, 0.4);"
D = QColor(0, 0, 0, 170)
E = QColor("red")
F = Qt.GlobalColor.white
G = base.lighter(115)
H = "#fff"
'''
    lines = {int(s.split(":")[1]) for s in scan_python(bad)}
    assert lines == {2, 3, 4, 5, 6, 7, 8, 9}


def test_the_scan_leaves_ordinary_code_alone():
    good = '''
"""Module docstring may say #FF2020 when it explains history."""
A = "Fixed in (#229), see #31 and #1234"
B = QColor(palette["accent"])
C = Qt.GlobalColor.transparent
D = f"color: {token}; border: 1px solid {other};"
E = "Colour: red is the Turbo mode"   # prose, not a stylesheet value
'''
    assert scan_python(good) == []


def test_the_decky_scan_catches_inline_colours():
    bad = 'const s = { background: "#1a9fff", color: "#fff" };\nconst t = { color: "white" };\n'
    assert len(scan_tsx(bad)) == 3
    assert scan_tsx('const s = { color: "var(--z13-warn)" }; // was #ffb02e\n') == []

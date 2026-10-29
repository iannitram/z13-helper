"""The Flatpak's stdin installer carries its matching host helper."""

import ast
import sys
import types
from pathlib import Path


def test_stdin_installer_runs_without_repo_on_import_path(tmp_path, real_subprocess):
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "src" / "updates_dialog.py").read_text())
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                    and n.name == "_installer_source")
    namespace = {"host": types.SimpleNamespace(__file__=str(root / "src" / "host.py")),
                 "_INSTALLERS_SRC": str(root / "src" / "installers.py")}
    # Load only the pure source builder, without importing PyQt6.
    exec(compile(ast.Module(body=[function], type_ignores=[]), "updates_dialog.py", "exec"),
         namespace)
    result = real_subprocess([sys.executable, "-I", "-", "plan", "steamos"],
                             input=namespace["_installer_source"](), text=True,
                             capture_output=True, cwd=tmp_path, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "sudo holo-update (or steamos-update on older SteamOS)" in result.stdout

"""The post_update folder is an importable package (issue #111 Expectation)."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def test_post_update_has_an_init_file():
    assert (REPO / "tooling" / "post_update" / "__init__.py").is_file()

"""ConfigManager load: wrong-shaped (but valid) JSON is kept aside, not trusted."""

import pytest

from config_manager import ConfigManager


@pytest.mark.parametrize("content", ["[]", "null", '"x"', "42"])
def test_non_dict_config_loads_as_empty_dict_with_backup(tmp_path, content, caplog):
    path = tmp_path / "config.json"
    path.write_text(content)
    cm = ConfigManager(str(path))
    assert cm.config == {}
    assert cm.get("anything", "dflt") == "dflt"
    cm.set("k", 1)                       # callers treat it as a dict
    kept = list(tmp_path.glob("config.json.corrupt-*"))
    assert len(kept) == 1 and kept[0].read_text() == content
    assert "not an object" in caplog.text


def test_dict_config_makes_no_backup(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"a": 1}')
    assert ConfigManager(str(path)).get("a") == 1
    assert not list(tmp_path.glob("config.json.corrupt-*"))

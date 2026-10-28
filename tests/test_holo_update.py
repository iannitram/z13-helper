"""holo-update first, steamos-update as fallback (issue #76)."""

import importlib
import re
from pathlib import Path

import pytest

import host
import installers
import update_watch

REPO = Path(__file__).resolve().parent.parent
PROBE = "command -v holo-update"


@pytest.fixture(autouse=True)
def fresh_host_module():
    """Reloading host empties the helper's per-process cache (and restores host.run)."""
    importlib.reload(host)
    yield
    importlib.reload(host)


def test_holo_or_steamos_returns_holo_when_probe_succeeds(fake_host):
    assert host.holo_or_steamos("update") == "holo-update"
    assert ["sh", "-c", PROBE] in fake_host.argvs
    assert [c.timeout for c in fake_host.calls if c.argv == ["sh", "-c", PROBE]] == [5]


def test_holo_or_steamos_falls_back_when_probe_fails(fake_host):
    fake_host.fail(PROBE, rc=1, out="")
    assert host.holo_or_steamos("update") == "steamos-update"


def test_holo_or_steamos_probes_once_per_name(fake_host):
    host.holo_or_steamos("update")
    host.holo_or_steamos("update")
    assert fake_host.argvs.count(["sh", "-c", PROBE]) == 1


def test_fetch_steamos_uses_holo_update_when_present(fake_host):
    update_watch._fetch_steamos({})
    assert ["holo-update", "check"] in fake_host.argvs
    assert ["steamos-update", "check"] not in fake_host.argvs


def test_fetch_steamos_uses_steamos_update_when_holo_missing(fake_host):
    fake_host.fail(PROBE, rc=1, out="")
    update_watch._fetch_steamos({})
    assert ["steamos-update", "check"] in fake_host.argvs
    assert ["holo-update", "check"] not in fake_host.argvs


def _recorder(monkeypatch):
    calls = []

    def fake_run(argv, timeout=600, check=True, **kw):
        calls.append(list(argv))
        if argv[1:2] == ["check"]:
            return 0, "SteamOS update available: 3.9.5"
        return 0, ""

    monkeypatch.setattr(installers, "run", fake_run)
    return calls


def test_install_steamos_uses_holo_update_when_present(fake_host, monkeypatch):
    calls = _recorder(monkeypatch)
    assert installers.install_steamos() == 0
    assert ["holo-update", "check"] in calls
    assert ["sudo", "-n", "holo-update"] in calls
    assert ["sudo", "-n", "steamos-update"] not in calls


def test_install_steamos_uses_steamos_update_when_holo_missing(fake_host, monkeypatch):
    fake_host.fail(PROBE, rc=1, out="")
    calls = _recorder(monkeypatch)
    assert installers.install_steamos() == 0
    assert ["steamos-update", "check"] in calls
    assert ["sudo", "-n", "steamos-update"] in calls
    assert ["sudo", "-n", "holo-update"] not in calls


def test_plan_steamos_text(fake_host):
    assert "sudo holo-update (or steamos-update on older SteamOS)" in installers.plan_steamos()


def test_docs_use_holo_readonly():
    text = (REPO / "docs" / "hardware-enablement.md").read_text()
    line = "sudo holo-readonly disable   # steamos-readonly on SteamOS before 3.9"
    assert text.count(line) == 2
    assert not re.search(r"^sudo steamos-readonly disable$", text, re.M)

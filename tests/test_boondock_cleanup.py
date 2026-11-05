"""tooling/boondock cleanup (issue #65): no Docker, no VM, user-level llama-swap unit."""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "tooling" / "boondock"


def _text():
    return SCRIPT.read_text()


def test_no_docker_virsh_vm_or_sudo_systemctl_left():
    assert not re.search(r"docker|virsh|pause.vm|BG_CONTAINERS|sudo systemctl", _text())


def test_usage_and_comments_do_not_mention_the_vm():
    assert not re.search(r"\bvm\b", _text(), re.I)


def test_background_comment_updated():
    assert "20W keeps background work usable" in _text()


def test_never_reads_live_ppt_nodes_or_shows_them_in_status():
    t = _text()
    assert "ppt_pl1_spl" not in t
    assert "ppt_pl2_sppt" not in t
    assert "ppt_fppt" not in t
    assert "TDP(PL1)" not in t
    assert "z13-profile" in t


def test_llama_swap_service_kept_and_driven_with_systemctl_user():
    t = _text()
    assert "BG_SERVICES=(llama-swap)" in t
    assert t.count('systemctl --user is-active "$s"') == 2
    assert t.count('systemctl --user stop "$s"') == 1
    assert t.count('systemctl --user start "$s"') == 2


def test_state_file_keeps_svc_and_drops_ctr_and_vm_keys():
    t = _text()
    assert "svc=" in t
    assert "ctr=" not in t
    assert not re.search(r"\bvm=", t)


def test_script_passes_bash_syntax_check(real_subprocess):
    r = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr

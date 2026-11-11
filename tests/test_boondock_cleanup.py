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


def test_tdp_defaults_are_the_firmware_floors():
    # #203: 20/30/40 W sat below the asus-armoury floors and was clamped silently.
    assert "TDP_PL1=28; TDP_PL2=32; TDP_PL3=45" in _text()


def test_never_reads_live_ppt_nodes_or_shows_them_in_status():
    t = _text()
    # Only range metadata (min_value / max_value) is read, through fit_rail.
    assert "current_value" not in t
    for name in ("ppt_pl1_spl", "ppt_pl2_sppt", "ppt_pl3_fppt"):
        assert all(re.search(r'fit_rail "\$tdp\d" ' + name + r"\b", line)
                   for line in t.splitlines() if name in line)
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

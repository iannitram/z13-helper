from pathlib import Path

import pytest

from tooling.rss_watch import process_start_ticks, sample


def test_process_start_ticks_handles_parentheses_in_comm():
    fields = ["S", *(str(i) for i in range(4, 23))]
    assert process_start_ticks("42 (worker (child)) " + " ".join(fields)) == 22


@pytest.mark.parametrize("line", ["broken", "42 (worker) S 4", "42 no-close S"])
def test_process_start_ticks_rejects_truncated_or_malformed_data(line):
    with pytest.raises(ValueError):
        process_start_ticks(line)


def _fake_proc(root: Path, *, start_ticks=22, cmdline=b"plugin/main.py\0"):
    proc = root / "42"
    proc.mkdir(parents=True)
    fields = ["S", *(str(i) for i in range(4, 23))]
    fields[19] = str(start_ticks)
    (proc / "stat").write_text("42 (worker) " + " ".join(fields))
    (proc / "cmdline").write_bytes(cmdline)
    (proc / "status").write_text("Name:\tworker\nVmRSS:\t4096 kB\n")
    (root / "meminfo").write_text("MemAvailable: 8192 kB\n")


def test_sampler_stops_if_process_identity_changes(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _fake_proc(proc_root, start_ticks=23)
    output = tmp_path / "result.log"
    result = sample(42, 22, "plugin/main.py", output, interval_s=1,
                    duration_hours=1, proc_root=proc_root)
    assert result == 2
    assert "STOP identity_changed samples=0" in output.read_text()
    assert "COMPLETE" not in output.read_text()
    assert "plugin/main.py" not in output.read_text()


def test_sampler_writes_bounded_sample_for_the_expected_process(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _fake_proc(proc_root)
    output = tmp_path / "result.log"
    result = sample(42, 22, "plugin/main.py", output, interval_s=60,
                    duration_hours=1, proc_root=proc_root,
                    monotonic=lambda: 0.0, sleep=lambda _seconds: None)
    rows = output.read_text().splitlines()
    assert result == 0
    assert "max_samples=61" in rows[0]
    assert len(rows) == 63
    assert "sample=0 pid=42 start_ticks=22 rss_mib=4.0 mem_available_mib=8.0" in rows[1]
    assert "COMPLETE samples=61 elapsed_s=0.0 max_duration_s=3600" in rows[-1]
    assert "plugin/main.py" not in output.read_text()


def test_sampler_refuses_to_overwrite_existing_output(tmp_path):
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    _fake_proc(proc_root)
    output = tmp_path / "result.log"
    output.write_text("keep")
    with pytest.raises(FileExistsError):
        sample(42, 22, "plugin/main.py", output, interval_s=60,
               duration_hours=1, proc_root=proc_root)
    assert output.read_text() == "keep"

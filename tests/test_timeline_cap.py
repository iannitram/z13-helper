"""Size cap for the machine timeline store (#134 review fix).

Locked test for src/timeline.py: an entry whose encoded line is larger than
`max_bytes` is never written to the active file, and the active file never
grows past `max_bytes` through append(). Normal entries and rotation are
unchanged. Uses tmp_path only; no HOME, hardware or network.
"""

import timeline

MAX = 600
TS = "2026-11-05T07:00:00+0000"


def _entry(idx, summary=None):
    return {
        "id": f"e-{idx:03d}",
        "kind": "profile",
        "summary": summary if summary is not None else f"switch {idx}",
        "ts": TS,
    }


def _oversized(eid="big-1"):
    # A 600-character summary encodes to well over MAX bytes.
    return {"id": eid, "kind": "profile", "summary": "x" * 600, "ts": TS}


def test_oversized_single_entry_is_not_written_to_active_file(tmp_path):
    p = tmp_path / "timeline.jsonl"
    rec = timeline.append(_oversized(), path=str(p), max_bytes=MAX, journal=False)
    assert rec["id"] == "big-1"  # the call still returns the normalized record
    assert not p.exists() or p.stat().st_size == 0
    assert timeline.read_entries(str(p)) == []


def test_oversized_entry_after_normal_entries_is_dropped_and_history_survives(tmp_path):
    p = tmp_path / "timeline.jsonl"
    first = timeline.append(_entry(1), path=str(p), max_bytes=MAX, backups=2, journal=False)
    timeline.append(_oversized(), path=str(p), max_bytes=MAX, backups=2, journal=False)
    # Rotation may move the earlier entry to .1, but the oversized line never lands in the active file.
    assert not p.exists() or p.stat().st_size == 0
    assert [e["id"] for e in timeline.read_entries(str(p), backups=2)] == [first["id"]]


def test_active_file_stays_at_or_under_max_bytes_after_many_appends(tmp_path):
    p = tmp_path / "timeline.jsonl"
    for idx in range(80):
        # Every tenth entry is oversized; the rest are normal sizes.
        summary = "x" * 600 if idx % 10 == 9 else "s" * (idx * 7 % 150)
        timeline.append(
            {"id": f"m-{idx:03d}", "kind": "profile", "summary": summary, "ts": TS},
            path=str(p), max_bytes=MAX, backups=2, journal=False,
        )
        if p.exists():
            assert p.stat().st_size <= MAX, f"active file over cap after append {idx}"
    assert not any(e["id"].endswith("9") and e["summary"] == "x" * 600
                   for e in timeline.read_entries(str(p)))


def test_normal_entries_are_still_written_and_read_back_in_order(tmp_path):
    p = tmp_path / "timeline.jsonl"
    ids = [
        timeline.append(_entry(idx), path=str(p), max_bytes=MAX, journal=False)["id"]
        for idx in range(3)
    ]
    assert [e["id"] for e in timeline.read_entries(str(p))] == ids
    assert p.read_bytes().count(b"\n") == 3


def test_entry_exactly_at_max_bytes_is_still_written(tmp_path):
    probe = tmp_path / "probe.jsonl"
    timeline.append(_entry(7, summary="edge"), path=str(probe), max_bytes=10**6, journal=False)
    line_len = probe.stat().st_size

    p = tmp_path / "timeline.jsonl"
    rec = timeline.append(_entry(7, summary="edge"), path=str(p), max_bytes=line_len, journal=False)
    assert p.stat().st_size == line_len
    assert [e["id"] for e in timeline.read_entries(str(p))] == [rec["id"]]


def test_rotation_still_works_for_normal_size_entries(tmp_path):
    p = tmp_path / "timeline.jsonl"
    ids = [
        timeline.append(_entry(idx), path=str(p), max_bytes=MAX, backups=2, journal=False)["id"]
        for idx in range(10)
    ]
    assert (tmp_path / "timeline.jsonl.1").exists()
    assert (tmp_path / "timeline.jsonl.2").exists()
    assert not (tmp_path / "timeline.jsonl.3").exists()
    assert p.stat().st_size <= MAX
    chronological = [e["id"] for e in timeline.read_entries(str(p), backups=2)]
    assert 0 < len(chronological) < len(ids)
    assert chronological == ids[-len(chronological):]

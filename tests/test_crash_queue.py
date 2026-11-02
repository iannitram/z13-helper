"""Crash-report queue and daily limit (issue #82, part c)."""

from crash_queue import CrashQueue

DAY = 86400
T0 = 1793000000  # 2026-10-26 in UTC; a fixed instant


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def q(tmp_path, clock=None, **kw):
    return CrashQueue(str(tmp_path / "queue"), clock=clock or Clock(), **kw)


def test_empty_queue(tmp_path):
    assert q(tmp_path).pending() == []


def test_add_read_remove_round_trip_oldest_name_first(tmp_path):
    c = q(tmp_path)
    c.add("b.zip", b"BB")
    c.add("a.zip", b"AA")
    assert c.pending() == ["a.zip", "b.zip"]
    assert c.read("b.zip") == b"BB"
    c.remove("a.zip")
    c.remove("a.zip")  # removing twice is fine
    assert c.pending() == ["b.zip"]


def test_survives_a_new_instance(tmp_path):
    q(tmp_path).add("a.zip", b"x")
    assert q(tmp_path).pending() == ["a.zip"]


def test_at_most_max_items_oldest_dropped(tmp_path):
    c = q(tmp_path, max_items=3)
    for i in range(5):
        c.add(f"r{i}.zip", b"x")
    assert c.pending() == ["r2.zip", "r3.zip", "r4.zip"]


def test_default_max_is_ten(tmp_path):
    c = q(tmp_path)
    for i in range(12):
        c.add(f"r{i:02d}.zip", b"x")
    assert len(c.pending()) == 10 and c.pending()[0] == "r02.zip"


def test_daily_limit_of_five_by_default(tmp_path):
    c = q(tmp_path)
    for _ in range(5):
        assert c.can_send()
        c.mark_sent()
    assert not c.can_send()


def test_limit_persists_and_resets_the_next_utc_day(tmp_path):
    clock = Clock()
    c = q(tmp_path, clock, daily_limit=2)
    c.mark_sent()
    c.mark_sent()
    assert not q(tmp_path, clock, daily_limit=2).can_send()
    clock.t += DAY
    assert c.can_send()
    c.mark_sent()
    assert c.can_send()


def test_corrupt_limit_file_counts_as_zero(tmp_path):
    c = q(tmp_path)
    (tmp_path / "queue" / "sent.json").write_text("{nope")
    assert c.can_send()


def test_limit_file_is_not_listed_as_pending(tmp_path):
    c = q(tmp_path)
    c.mark_sent()
    assert c.pending() == []

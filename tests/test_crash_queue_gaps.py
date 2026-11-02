"""Crash-report queue: stated behaviour the locked test does not cover (#119)."""

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


def test_constructor_creates_missing_directory(tmp_path):
    target = tmp_path / "a" / "b"
    CrashQueue(str(target))
    assert target.is_dir()


def test_wrong_shape_limit_file_counts_as_zero(tmp_path):
    c = q(tmp_path)
    (tmp_path / "queue" / "sent.json").write_text("[]")
    assert c.can_send()
    c.mark_sent()
    assert c.can_send()
    assert (tmp_path / "queue" / "sent.json").read_text() == (
        '{"date": "2026-10-26", "count": 1}'
    )


def test_earlier_day_record_counts_as_zero_when_marking(tmp_path):
    clock = Clock()
    c = q(tmp_path, clock, daily_limit=2)
    c.mark_sent()
    c.mark_sent()
    assert not c.can_send()
    clock.t += DAY
    c.mark_sent()
    assert c.can_send()  # new day: count restarted at 1, not 3
    c.mark_sent()
    assert not c.can_send()

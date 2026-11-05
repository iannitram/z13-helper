"""Bounded in-memory history of the live readings (#31). Pure Python, nothing on disk.

The desktop app adds one sample per telemetry tick; the Fans + Power window draws them.
Memory is capped twice: by sample count (`maxlen`) and by age (`window_s`).
"""

import collections

WINDOW_S = 3600.0          # keep the last hour
MIN_INTERVAL_S = 1.5       # never store samples closer together than this
FIELDS = ("cpu", "gpu", "fan1", "fan2")


class TelemetryHistory:
    def __init__(self, window_s=WINDOW_S, min_interval_s=MIN_INTERVAL_S):
        self.window_s = float(window_s)
        self.min_interval_s = float(min_interval_s)
        # Samples older than window_s are dropped anyway; maxlen is the hard cap.
        self._buf = collections.deque(maxlen=int(self.window_s / self.min_interval_s) + 16)

    def __len__(self):
        return len(self._buf)

    def add(self, now, cpu=None, gpu=None, fan1=None, fan2=None):
        """Record one sample at monotonic time `now` (seconds). None = unavailable (a gap).

        Returns False when the sample was skipped for arriving too soon.
        """
        if self._buf and now - self._buf[-1][0] < self.min_interval_s:
            return False
        self._buf.append((now, cpu, gpu, fan1, fan2))
        while self._buf and now - self._buf[0][0] > self.window_s:
            self._buf.popleft()
        return True

    def series(self, field, now=None):
        """[(seconds_ago, value_or_None)] oldest first, for one of FIELDS.

        `now` defaults to the newest sample's time.
        """
        i = FIELDS.index(field) + 1
        if not self._buf:
            return []
        ref = self._buf[-1][0] if now is None else now
        return [(ref - s[0], s[i]) for s in self._buf]

    def span_s(self):
        """Seconds between the oldest and newest sample."""
        return self._buf[-1][0] - self._buf[0][0] if len(self._buf) > 1 else 0.0

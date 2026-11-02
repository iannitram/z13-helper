"""On-disk crash report queue and daily upload limit (#119)."""

import json
import time
from pathlib import Path


class CrashQueue:
    def __init__(
        self,
        directory: str,
        *,
        max_items: int = 10,
        daily_limit: int = 5,
        clock=time.time,
    ):
        self.directory = Path(directory)
        self.max_items = max_items
        self.daily_limit = daily_limit
        self.clock = clock
        self.directory.mkdir(parents=True, exist_ok=True)

    def add(self, name: str, data: bytes) -> None:
        """Write a crash report to the queue."""
        file_path = self.directory / name
        # The caller holds the shared queue lock. Interrupted writes must never
        # become a pending (and subsequently uploaded) truncated zip.
        tmp = file_path.with_suffix(".tmp")
        try:
            tmp.write_bytes(data)
            tmp.replace(file_path)
        finally:
            tmp.unlink(missing_ok=True)

        # Clean up old items if needed
        self._cleanup_old_items()

    def pending(self) -> list:
        """Get list of pending crash reports."""
        files = [
            f for f in self.directory.iterdir() if f.is_file() and f.suffix == ".zip"
        ]
        return sorted([f.name for f in files])

    def read(self, name: str) -> bytes:
        """Read a crash report from the queue."""
        file_path = self.directory / name
        return file_path.read_bytes()

    def remove(self, name: str) -> None:
        """Remove a crash report from the queue."""
        file_path = self.directory / name
        try:
            file_path.unlink()
        except FileNotFoundError:
            pass  # Removing a missing file is fine

    def can_send(self) -> bool:
        """Check if we can send another crash report today."""
        today = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        sent_file = self.directory / "sent.json"

        if not sent_file.exists():
            return True

        try:
            with sent_file.open("r") as f:
                data = json.load(f)
                if data.get("date") == today:
                    return data.get("count", 0) < self.daily_limit
                else:
                    return True
        except (ValueError, AttributeError, TypeError, OSError):
            # Corrupt or invalid file, treat as 0 sent
            return True

    def mark_sent(self) -> None:
        """Mark a crash report as sent for today."""
        today = time.strftime("%Y-%m-%d", time.gmtime(self.clock()))
        sent_file = self.directory / "sent.json"

        # Read existing data
        count = 0
        if sent_file.exists():
            try:
                with sent_file.open("r") as f:
                    data = json.load(f)
                    if data.get("date") == today:
                        count = int(data.get("count", 0))
            except (ValueError, AttributeError, TypeError, OSError):
                # Corrupt or invalid file, treat as 0 sent
                pass

        # Increment and write back
        count += 1
        tmp = sent_file.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps({"date": today, "count": count}))
            tmp.replace(sent_file)
        finally:
            tmp.unlink(missing_ok=True)

    def _cleanup_old_items(self) -> None:
        """Remove old items if we exceed max_items."""
        files = [
            f for f in self.directory.iterdir() if f.is_file() and f.suffix == ".zip"
        ]
        if len(files) > self.max_items:
            # Several reports can share a second; their random suffixes do not
            # encode age. Use arrival time when dropping the oldest report.
            files.sort(key=lambda f: (f.stat().st_mtime_ns, f.name))
            # Remove the oldest files
            for f in files[: -self.max_items]:
                f.unlink()

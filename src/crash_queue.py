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
        file_path.write_bytes(data)

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
        with sent_file.open("w") as f:
            json.dump({"date": today, "count": count}, f)

    def _cleanup_old_items(self) -> None:
        """Remove old items if we exceed max_items."""
        files = [
            f for f in self.directory.iterdir() if f.is_file() and f.suffix == ".zip"
        ]
        if len(files) > self.max_items:
            # Sort by name (which starts with timestamp) to get oldest first
            files.sort(key=lambda f: f.name)
            # Remove the oldest files
            for f in files[: -self.max_items]:
                f.unlink()

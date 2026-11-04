"""Cancellable host event reader shared by desktop and Decky (no Qt).

The host profile engine owns socket access and reconciliation. Consumers receive
JSON records after initial/reconnect snapshots and z13ctl events; no state poll.
"""
import json
import os
import threading
import subprocess

import host


class StateStream:
    def __init__(self, on_event, *, command=None, on_error=None):
        self.on_event = on_event
        self.on_error = on_error or (lambda _exc: None)
        self.command = command or [os.path.expanduser('~/.local/bin/z13-profile'), 'watch']
        self.stop = threading.Event()
        self._mutex = threading.Lock()
        self._process = None
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, name='z13-state-events', daemon=True)
        self._thread.start()
        return self

    @staticmethod
    def _close(process):
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdout:
            process.stdout.close()

    def close(self):
        self.stop.set()
        with self._mutex:
            process = self._process
            if process is not None and process.poll() is None:
                process.terminate()
        if process is not None:
            # terminate alone cannot wake stdout if a child ignores SIGTERM.
            # Escalate before joining the reader, which owns stdout cleanup.
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1)
        if self._thread is not None:
            self._thread.join(timeout=3)

    def _run(self):
        while not self.stop.is_set():
            process = None
            try:
                with self._mutex:
                    if self.stop.is_set():
                        return
                    process = self._process = host.open_stream(self.command, as_deck=True)
                for line in process.stdout:
                    if self.stop.is_set():
                        break
                    try:
                        record = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(record, dict) and isinstance(record.get('event'), str):
                        self.on_event(record.get('state'), record['event'])
            except (OSError, ValueError) as exc:
                if not self.stop.is_set():
                    self.on_error(exc)
            finally:
                with self._mutex:
                    self._process = None
                self._close(process)
            self.stop.wait(1)

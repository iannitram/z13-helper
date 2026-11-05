import io
import os
import threading

import host
from state_stream import StateStream


class Process:
    def __init__(self, lines):
        self.stdout = io.StringIO(lines)
        self.terminated = False
        self.waited = False

    def poll(self):
        return 0 if self.terminated else None

    def terminate(self):
        self.terminated = True

    def wait(self, timeout):
        self.waited = True
        return 0


def test_event_reader_delivers_records_and_owns_host_process_cleanup(monkeypatch):
    process = Process('bad json\n{"event":"state-changed","state":{"profile":"Turbo"}}\n')
    commands = []
    def open_stream(argv, **kw):
        commands.append((argv, kw))
        return process
    monkeypatch.setattr(host, 'open_stream', open_stream)
    seen = []
    stream = StateStream(lambda state, event: (seen.append((state, event)), stream.stop.set()))
    stream.start()
    stream._thread.join(1)
    stream.close()
    assert seen == [({'profile': 'Turbo'}, 'state-changed')]
    assert commands == [([os.path.expanduser('~/.local/bin/z13-profile'), 'watch'], {'as_deck': True})]
    assert process.terminated and process.waited and process.stdout.closed
    assert not stream._thread.is_alive()


def test_close_before_start_never_launches_a_host_process(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('must not launch')
    monkeypatch.setattr(host, 'open_stream', forbidden)
    stream = StateStream(lambda *_: None)
    stream.close()
    stream.start()
    stream._thread.join(1)
    assert not stream._thread.is_alive()


def test_idle_host_child_that_ignores_terminate_is_killed_and_reaped(real_subprocess, monkeypatch):
    import sys
    monkeypatch.setattr(host, "MODE", "host")
    ready = threading.Event()
    code = "import signal,threading; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('{\"event\":\"ready\"}',flush=True); threading.Event().wait()"
    stream = StateStream(lambda *_: ready.set(), command=[sys.executable, '-u', '-c', code])
    stream.start()
    try:
        assert ready.wait(3)
        process = stream._process
    finally:
        stream.close()
    assert process.poll() is not None
    assert not stream._thread.is_alive()

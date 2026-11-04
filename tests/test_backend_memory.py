"""Isolated backend RSS guard: never terminate the test process or write hardware."""

import asyncio
import threading

import pytest


@pytest.mark.parametrize('text, expected', [
    ('VmPeak:\t99999999 kB\nVmRSS:\t44032 kB\n', 44032),
    ('VmRSS: 0 kB\n', 0),
])
def test_reads_current_rss_only(main, tmp_path, text, expected):
    status = tmp_path / 'status'
    status.write_text(text)
    assert main.backend_rss_kib(status) == expected


@pytest.mark.parametrize('text', ['VmSize: 42 kB', 'VmRSS: -1 kB',
                                 'VmRSS: abc kB', 'VmRSS: 500 MB', 'VmRSS: 10'])
def test_rejects_missing_or_invalid_rss(main, tmp_path, text):
    status = tmp_path / 'status'
    status.write_text(text)
    with pytest.raises(ValueError):
        main.backend_rss_kib(status)


@pytest.mark.parametrize('rss, trips', [(43 * 1024, False), (500 * 1024 - 1, False),
                                      (500 * 1024, True), (43 * 1024 * 1024, True)])
def test_threshold_exits_only_plugin_process(main, rss, trips, capfd):
    exits = []
    guard = main.BackendMemoryGuard(lambda: rss, exits.append)
    assert guard.check() is trips
    assert exits == ([1] if trips else [])
    err = capfd.readouterr().err
    assert ('exiting plugin process' in err) is trips


def test_cannot_skip_exit_when_stderr_fails(main, monkeypatch):
    exits = []
    def fail(*args):
        raise OSError('closed stderr')
    monkeypatch.setattr(main.os, 'write', fail)
    guard = main.BackendMemoryGuard(lambda: main.BACKEND_RSS_LIMIT_KIB, exits.append)
    with pytest.raises(OSError):
        guard.check()
    assert exits == [1]


def test_read_errors_retry_without_log_growth(main, caplog):
    exits = []
    values = iter([OSError(), ValueError(), 44032, OSError(), 512000])
    def read():
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value
    guard = main.BackendMemoryGuard(read, exits.append)
    for _ in range(4):
        assert not guard.check()
    assert guard.check()
    assert exits == [1]
    assert sum('RSS unavailable' in r.message for r in caplog.records) == 2


def test_monitor_checks_without_event_loop_and_joins_on_close(main):
    checked = threading.Event()
    def read():
        checked.set()
        return 44032
    guard = main.BackendMemoryGuard(read, lambda code: pytest.fail('unexpected exit'))
    guard.start()
    try:
        assert checked.wait(2)
    finally:
        guard.close()
    assert not guard._thread.is_alive()


def test_monitor_ends_after_threshold_trip(main):
    exits = []
    guard = main.BackendMemoryGuard(lambda: 512000, exits.append)
    guard.start()
    guard._thread.join(timeout=2)
    assert not guard._thread.is_alive()
    assert exits == [1]


def test_plugin_lifecycle_starts_and_stops_guard(main, monkeypatch):
    calls = []
    class Guard:
        def start(self):
            calls.append('start')
        def close(self):
            calls.append('close')
    monkeypatch.setattr(main, 'BackendMemoryGuard', Guard)
    monkeypatch.setattr(main.crash_report, 'install', lambda **kwargs: None)
    monkeypatch.setattr(main, '_load_max_fans', lambda: {})
    async def idle(self):
        await asyncio.Event().wait()
    monkeypatch.setattr(main.Plugin, '_enforce_max_fans', idle)
    monkeypatch.setattr(main.Plugin, '_watch_desktop_app', idle)
    async def run():
        plugin = main.Plugin()
        await plugin._main()
        await plugin._unload()
        await asyncio.gather(plugin._enforce_task, plugin._desktop_task, return_exceptions=True)
    asyncio.run(run())
    assert calls == ['start', 'close']


def test_opt_out_trip_is_not_fatal(main, monkeypatch):
    monkeypatch.setenv('Z13_RSS_GUARD_EXIT', '0')
    monkeypatch.setattr(main.os, '_exit', lambda c: pytest.fail('unexpected exit'))
    guard = main.BackendMemoryGuard(lambda: main.BACKEND_RSS_LIMIT_KIB)
    assert guard.check()
    assert guard.check()


def test_default_termination_is_process_exit(main, monkeypatch):
    monkeypatch.delenv('Z13_RSS_GUARD_EXIT', raising=False)
    exits = []
    monkeypatch.setattr(main.os, '_exit', exits.append)
    guard = main.BackendMemoryGuard(lambda: main.BACKEND_RSS_LIMIT_KIB)
    assert guard.check()
    assert exits == [1]


def test_monitor_retries_on_cadence_then_stops(main):
    reads, waits = [], []
    values = iter([OSError('temporary'), 44032])
    def read():
        reads.append(True)
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value
    class Stop:
        def is_set(self):
            return False
        def wait(self, seconds):
            waits.append(seconds)
            return len(waits) == 2
    guard = main.BackendMemoryGuard(read, lambda code: pytest.fail('unexpected exit'))
    guard._stop = Stop()
    guard._run()
    assert len(reads) == 2
    assert waits == [main.BACKEND_RSS_CHECK_S] * 2

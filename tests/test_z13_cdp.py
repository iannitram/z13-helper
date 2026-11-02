"""Frontend health probes use fake HTTP and CDP; never connect to Steam."""

import json
import shutil
import urllib.error
from email.message import Message
from types import SimpleNamespace

import pytest

import z13_cdp as cdp


@pytest.fixture
def entry(monkeypatch):
    headers = Message()
    headers['Content-Type'] = 'application/javascript; charset=utf-8'
    response = SimpleNamespace(status=200, headers=headers, read=lambda n: b'export default () => ({});')
    calls = []

    class Response:
        def __enter__(self):
            return response

        def __exit__(self, *args):
            pass

    def fetch(request, timeout):
        calls.append(request.full_url)
        assert timeout == 3
        return Response()

    monkeypatch.setattr(cdp.urllib.request, 'urlopen', fetch)
    response.calls = calls
    return response


def event(stamp, message, method='Runtime.consoleAPICalled'):
    return {'method': method, 'params': {'timestamp': stamp, 'args': [{'description': message}]}}


@pytest.mark.parametrize('events, expected', [
    ([], 0),
    ([event(999_000, 'Failed to fetch dynamically imported module')], 0),
    ([event(1_001_000, 'Failed to fetch dynamically imported module')], 1),
    ([event(1_001_000, 'Importing a module script failed', 'Runtime.exceptionThrown')], 1),
    ([event(1_001_000, 'unrelated console message')], 0),
])
def test_frontend_error_delta(entry, monkeypatch, events, expected):
    def evaluate(expression, timeout, events):
        events.extend(buffered)
        return {'ready': True}
    buffered = events
    monkeypatch.setattr(cdp, 'eval_js', evaluate)
    rc, reason = cdp.decky_frontend_check(1000, wait=0)
    assert rc == expected, reason
    assert entry.calls[0].startswith('http://127.0.0.1:1337/plugins/Z13-Helper/dist/index.js?t=')


@pytest.mark.parametrize('status, mime, body', [
    (404, 'application/javascript', b'not found'),
    (200, 'text/html', b'<html>error</html>'),
    (200, 'application/javascript', b''),
    (200, 'text/javascript', b'<!DOCTYPE html><html>error</html>'),
])
def test_entry_failure_precedes_cef_check(entry, monkeypatch, status, mime, body):
    entry.status = status
    entry.headers.replace_header('Content-Type', mime)
    entry.read = lambda n: body
    # eval_js is deliberately not stubbed: entry failure must never connect to CEF.
    rc, reason = cdp.decky_frontend_check(1000, wait=0)
    assert rc == 1 and 'entry URL' in reason


def test_entry_transport_failure(monkeypatch):
    def fetch(*args, **kwargs):
        raise urllib.error.URLError('loader unreachable')
    monkeypatch.setattr(cdp.urllib.request, 'urlopen', fetch)
    assert cdp.decky_frontend_check(1000, wait=0)[0] == 1


@pytest.mark.parametrize('state', [
    {'ready': False, 'reason': 'registration missing'},
    {'ready': False, 'reason': 'registry unavailable'},
    {'error': 'JS exception'}, None,
])
def test_missing_or_broken_registration_fails(entry, monkeypatch, state):
    monkeypatch.setattr(cdp, 'eval_js', lambda *a, **kw: state)
    assert cdp.decky_frontend_check(1000, wait=0)[0] == 1


@pytest.mark.parametrize('error, expected', [
    (cdp.SteamUIUnavailable('no gamepad UI'), 3),
    (urllib.error.URLError('connection refused'), 3),
    (ConnectionError('socket closed during probe'), 1),
    (ValueError('malformed CDP response'), 1),
])
def test_absent_ui_is_distinct_from_probe_failure(entry, monkeypatch, error, expected):
    def evaluate(*args, **kwargs):
        raise error
    monkeypatch.setattr(cdp, 'eval_js', evaluate)
    rc, reason = cdp.decky_frontend_check(1000, wait=0)
    assert rc == expected, reason
    assert reason


def test_waits_for_registration(entry, monkeypatch):
    states = iter([{'ready': False, 'reason': 'reload pending'}, {'ready': True}])
    monkeypatch.setattr(cdp, 'eval_js', lambda *a, **kw: next(states))
    monkeypatch.setattr(cdp.time, 'sleep', lambda seconds: None)
    assert cdp.decky_frontend_check(1000)[0] == 0


def test_disappearing_ui_does_not_hide_observed_missing_plugin(entry, monkeypatch):
    times = iter([0, 0, 1])
    monkeypatch.setattr(cdp.time, 'monotonic', lambda: next(times))
    monkeypatch.setattr(cdp.time, 'sleep', lambda seconds: None)
    states = iter([{'ready': False, 'reason': 'registration missing'}, None])

    def evaluate(*args, **kwargs):
        state = next(states)
        if state is None:
            raise cdp.SteamUIUnavailable('UI disappeared')
        return state

    monkeypatch.setattr(cdp, 'eval_js', evaluate)
    assert cdp.decky_frontend_check(1000, wait=1)[0] == 1


def test_eval_collects_buffered_runtime_events(monkeypatch):
    sock = SimpleNamespace(settimeout=lambda t: None, close=lambda: None)
    monkeypatch.setattr(cdp, '_discover_ws_url', lambda t: 'ws://unused')
    monkeypatch.setattr(cdp, '_ws_connect', lambda *a: sock)
    sent = []
    monkeypatch.setattr(cdp, '_ws_send', lambda s, op, payload: sent.append(json.loads(payload)))
    buffered = event(1_001_000, 'Failed to fetch dynamically imported module')
    messages = iter([buffered, {'id': 2, 'result': {}},
                     {'id': 1, 'result': {'result': {'value': {'ready': True}}}}])
    monkeypatch.setattr(cdp, '_ws_recv', lambda s: (1, json.dumps(next(messages)).encode()))
    events = []
    assert cdp.eval_js('probe()', events=events) == {'ready': True}
    assert sent[0]['method'] == 'Runtime.enable'
    assert events == [buffered]


@pytest.mark.skipif(shutil.which('node') is None, reason='needs Node to evaluate registry JS')
def test_registry_rejects_decky_error_placeholder(real_subprocess):
    script = '''
const probe = () => eval(process.argv[1]);
global.window = {};
if (probe().ready) process.exit(1);
const l = window.DeckyPluginLoader = {plugins: [], reloadLock: false, pluginReloadQueue: []};
if (probe().ready) process.exit(2);
l.plugins = [{name: 'Z13-Helper', content: {type: function TheError() {}}}];
if (probe().ready) process.exit(3);
l.plugins = [{name: 'Z13-Helper', content: {type: function Content() {}}, titleView: {}, onDismount() {}}];
if (!probe().ready) process.exit(4);
l.reloadLock = true;
if (probe().ready) process.exit(5);
'''
    result = real_subprocess(['node', '-e', script, cdp._JS_DECKY_CHECK],
                             capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr

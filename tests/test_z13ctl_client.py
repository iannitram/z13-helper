import json
import socket
import socketserver
import threading
import time

import pytest

import z13ctl_client
import profiles


class UnixServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    allow_reuse_address = True


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        request = json.loads(self.rfile.readline())
        self.server.requests.append(request)
        if request["cmd"] == "subscribe":
            self.wfile.write(b'{"ok":true}\n')
            self.wfile.flush()
            self.server.subscribed.set()
            for event in self.server.events:
                self.wfile.write((json.dumps({"ok": True, "event": event}) + "\n").encode())
                self.wfile.flush()
        elif request["cmd"] == "get-state":
            self.wfile.write(b'{"ok":true,"state":{"profile":"Turbo","on_ac":false}}\n')
        elif request["cmd"] == "profile-list":
            self.wfile.write((json.dumps({"ok": True, "value": json.dumps(self.server.profiles)}) + "\n").encode())
        elif request["cmd"] == "bad":
            self.wfile.write(b'{"ok":false,"error":"unknown command"}\n')
        elif request["cmd"] == "profile-create":
            self.server.profiles.append({"name": request["set"]})
            self.wfile.write(b'{"ok":true}\n')
        else:
            self.wfile.write(b'{"ok":true}\n')
        self.wfile.flush()


@pytest.fixture
def daemon(tmp_path):
    path = str(tmp_path / "z13ctl.sock")
    server = UnixServer(path, Handler)
    server.requests = []
    server.events = ["power-source", "gui-toggle", "state-changed"]
    server.profiles = []
    server.subscribed = threading.Event()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, z13ctl_client.Z13CtlClient(path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_request_and_state_use_one_json_line(daemon):
    server, client = daemon
    assert client.get_state() == {"profile": "Turbo", "on_ac": False}
    assert client.profile_list() == "[]"
    assert [r["cmd"] for r in server.requests] == ["get-state", "profile-list"]


def test_subscribe_reads_ack_then_event_names_and_filters_unknown(daemon):
    server, client = daemon
    events = client.subscribe()
    assert next(events) == "connected"
    assert next(events) == "power-source"
    assert next(events) == "gui-toggle"
    assert next(events) == "state-changed"
    with pytest.raises(StopIteration):
        next(events)
    assert server.requests[0] == {"cmd": "subscribe", "events": ["state-changed", "power-source", "gui-toggle"]}


def test_non_ok_response_is_visible(daemon):
    _, client = daemon
    with pytest.raises(z13ctl_client.Z13CtlError, match="unknown command"):
        client.request("bad")


def test_follow_state_dispatches_known_events_and_refreshes_snapshot(daemon):
    server, client = daemon
    stop = threading.Event()
    seen = []
    worker = threading.Thread(
        target=z13ctl_client.follow_state,
        args=(client, lambda state, event: (seen.append((state, event)), stop.set()), stop),
        daemon=True,
    )
    worker.start()
    assert server.subscribed.wait(1)
    worker.join(2)
    assert not worker.is_alive()
    assert seen == [({"profile": "Turbo", "on_ac": False}, "state-changed")]
    assert [r["cmd"] for r in server.requests] == ["subscribe", "get-state"]


def test_named_profile_mirror_probes_and_targets_every_setting(daemon):
    server, client = daemon
    profile = {"name": "Daily / travel", "base": "balanced", "fan_curve": [[30, 25], [80, 100]],
               "tdp": {"pl1": 35, "pl2": 45, "pl3": 50}, "undervolt": -10}
    backend_name = client.ensure_named_profile(profile)
    assert backend_name.startswith("z13h-daily-travel-")
    writes = [r for r in server.requests if r["cmd"] not in ("profile-list",)]
    assert writes[0] == {"cmd": "profile-create", "set": backend_name}
    assert all(r.get("profile") == backend_name for r in writes[1:])
    assert [r["cmd"] for r in writes] == ["profile-create", "tdp-reset", "fancurve", "tdp", "undervolt"]


def test_activation_sets_firmware_base_before_named_profile(daemon):
    server, client = daemon
    profile = {"name": "Balanced", "base": "balanced", "fan_curve": None,
               "tdp": None, "undervolt": None}
    assert client.activate_app_profile(profile) == "balanced"
    assert server.requests[0] == {"cmd": "profile", "set": "balanced"}
    assert any(r["cmd"] == "profile-create" for r in server.requests)
    assert not any(r == {"cmd": "profile", "set": "Balanced"} for r in server.requests)


def test_backend_event_sync_changes_selected_app_profile_without_applying_hardware(tmp_path, monkeypatch):
    store_path = tmp_path / "profiles.json"
    monkeypatch.setattr(profiles, "STORE_PATH", str(store_path))
    monkeypatch.setattr(profiles, "LEGACY_CURRENT_PATH", str(tmp_path / "legacy-current"))
    profiles.save_store({"version": 1, "active": "Balanced", "profiles": profiles.default_profiles()})
    backend_name = z13ctl_client.Z13CtlClient.mapped_profile_name({"name": "Turbo"})

    assert profiles.sync_active_from_backend({"profile": backend_name}) == "Turbo"
    assert profiles.load_store()["active"] == "Turbo"
    assert (tmp_path / "legacy-current").read_text().strip() == "maximum-throughput"


def test_follow_state_reconnects_after_backend_stream_closes():
    class RestartingClient:
        def __init__(self):
            self.connections = 0
        def subscribe(self, _events):
            self.connections += 1
            if self.connections == 1:
                return iter(())  # daemon disconnected; the next connection succeeds
            return iter(("state-changed",))
        def get_state(self):
            return {"profile": "balanced"}

    client = RestartingClient()
    stop = threading.Event()
    seen = []
    def on_state(state, event):
        seen.append((state, event))
        stop.set()

    z13ctl_client.follow_state(client, on_state, stop, reconnect_delay=0.001)
    assert client.connections == 2
    assert seen == [({"profile": "balanced"}, "state-changed")]



def test_reconnect_snapshots_even_when_no_new_change_event_arrives():
    class Restart:
        connects = 0
        def subscribe(self, _events):
            self.connects += 1
            yield 'connected'
        def get_state(self):
            return {'profile': 'Balanced' if self.connects == 1 else 'Turbo'}
    c = Restart()
    stop = threading.Event()
    seen = []
    def receive(state, event):
        seen.append((state, event))
        if len(seen) == 2:
            stop.set()
    z13ctl_client.follow_state(c, receive, stop, reconnect_delay=0)
    assert seen == [({'profile': 'Balanced'}, 'state-changed'),
                    ({'profile': 'Turbo'}, 'state-changed')]


def test_flatpak_socket_bridge_is_valid_python_and_preserves_missing_daemon(fake_host, monkeypatch):
    import host
    monkeypatch.setattr(host, 'MODE', 'flatpak')
    fake_host.fail(lambda a: a[:2] == ['python3', '-'], rc=0,
                   out='{"ok":false,"unavailable":true,"error":"missing"}')
    with pytest.raises(ConnectionRefusedError):
        z13ctl_client.Z13CtlClient().get_state()
    compile(fake_host.calls[0].input, '<host socket bridge>', 'exec')
    assert fake_host.calls[0].as_deck is True
    assert json.loads(fake_host.calls[0].argv[-1]) == {'cmd': 'get-state'}


def test_explicit_test_socket_is_native_even_under_root(daemon, monkeypatch):
    import host
    server, client = daemon
    monkeypatch.setattr(host, 'MODE', 'root')
    assert client.get_state()['profile'] == 'Turbo'
    assert server.requests == [{'cmd': 'get-state'}]

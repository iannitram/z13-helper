"""Small stdlib client for z13ctl's documented Unix-socket API.

The API is deliberately independent from the CLI: requests are one JSON line
per connection, while subscriptions stay open and stream event-name records.
No hardware fallback belongs here; z13ctl is the only hardware writer.
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import socket

import host
from z13ctl_profiles import named_profile_for, profile_target_supported

CONNECT_TIMEOUT_S = 1.0
REQUEST_TIMEOUT_S = 10.0
EVENTS = ("state-changed", "power-source", "gui-toggle")


class Z13CtlError(RuntimeError):
    """The daemon was reached but returned an invalid or failed response."""


class Z13CtlClient:
    def __init__(self, socket_path=None, *, connect_timeout=CONNECT_TIMEOUT_S,
                 request_timeout=REQUEST_TIMEOUT_S):
        self._explicit_socket = socket_path is not None
        runtime = os.environ.get("XDG_RUNTIME_DIR") or "/tmp"
        self.socket_path = socket_path or os.path.join(runtime, "z13ctl", "z13ctl.sock")
        self._subscription = None
        self.connect_timeout = connect_timeout
        self.request_timeout = request_timeout

    def _connect(self, timeout):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        try:
            conn.connect(self.socket_path)
            return conn
        except BaseException:
            conn.close()
            raise

    @staticmethod
    def _readline(stream, *, allow_eof=False):
        line = stream.readline()
        if not line:
            if allow_eof:
                return None
            raise Z13CtlError("z13ctl closed the connection without a response")
        try:
            return json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise Z13CtlError("z13ctl returned invalid JSON") from exc

    def request(self, cmd, **fields):
        """Send one bounded request. Connection failures are surfaced to caller."""
        payload = {"cmd": cmd, **fields}
        if not self._explicit_socket and (host.in_flatpak() or host.MODE == "root"):
            # Host Python reaches the session socket; no sandbox socket grant or
            # app-owned daemon is needed. Input is code, argv carries JSON data.
            script = r"""import socket, os, sys, json
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(20)
try:
    s.connect(os.path.join(os.environ.get('XDG_RUNTIME_DIR', '/tmp'), 'z13ctl', 'z13ctl.sock'))
except (FileNotFoundError, ConnectionRefusedError) as exc:
    print(json.dumps({'ok': False, 'unavailable': True, 'error': str(exc)}))
    sys.exit(0)
s.sendall((sys.argv[1] + '\n').encode())
with s.makefile('rb') as f:
    print(f.readline().decode(), end='')
s.close()
"""
            result = host.run(["python3", "-", json.dumps(payload)], input=script,
                              timeout=host.Z13CTL_WRITE_TIMEOUT_S, as_deck=True)
            if result.timed_out:
                raise TimeoutError("z13ctl write timed out after 20 s (kernel asus-wmi hang?)")
            if result.rc:
                raise OSError(result.out)
            try:
                reply = json.loads(result.out)
            except ValueError as exc:
                raise Z13CtlError("z13ctl returned invalid JSON") from exc
            return self._validate_reply(cmd, reply)
        with self._connect(self.connect_timeout) as conn:
            conn.settimeout(self.request_timeout)
            conn.sendall((json.dumps(payload, separators=(",", ":")) + "\n").encode())
            with conn.makefile("rb") as stream:
                reply = self._readline(stream)
        return self._validate_reply(cmd, reply)

    @staticmethod
    def _validate_reply(cmd, reply):
        if isinstance(reply, dict) and reply.get("unavailable"):
            raise ConnectionRefusedError(reply.get("error", "daemon unavailable"))
        if not isinstance(reply, dict) or reply.get("ok") is not True:
            message = reply.get("error", "request failed") if isinstance(reply, dict) else "invalid response"
            raise Z13CtlError(f"z13ctl {cmd}: {message}")
        return reply

    def get_state(self):
        """Return a fresh daemon snapshot; event payloads are intentionally empty."""
        state = self.request("get-state").get("state")
        if not isinstance(state, dict):
            raise Z13CtlError("z13ctl returned invalid state")
        return state

    def profile_list(self):
        """Probe named-profile support; legacy daemons reject this command."""
        return self.request("profile-list").get("value")

    @staticmethod
    def backend_profile_name(app_name):
        """Stable, daemon-safe name that cannot collide with a firmware profile."""
        slug = re.sub(r"[^a-z0-9-]+", "-", app_name.lower()).strip("-")[:24] or "profile"
        suffix = hashlib.sha256(app_name.encode("utf-8")).hexdigest()[:8]
        return f"z13h-{slug}-{suffix}"

    @classmethod
    def mapped_profile_name(cls, profile):
        return (profile.get("z13ctl_profile") or named_profile_for(profile["name"])
                or cls.backend_profile_name(profile["name"]))

    def ensure_named_profile(self, profile):
        """Mirror performance settings to a daemon-owned profile without applying it.

        `profile-list` is the mandatory capability probe. An old daemon rejects it;
        callers must then use their existing compatibility path. All setting writes
        name the target explicitly so an inactive edit cannot change current hardware.
        """
        name = self.mapped_profile_name(profile)
        listed = self.profile_list()
        try:
            saved = json.loads(listed or "[]")
        except (TypeError, json.JSONDecodeError) as exc:
            raise Z13CtlError("z13ctl profile-list returned invalid JSON") from exc
        if not profile_target_supported(listed):
            raise Z13CtlError("z13ctl profile-list did not return a list")
        exists = any(isinstance(item, dict) and item.get("name") == name for item in saved)
        if not exists:
            self.request("profile-create", set=name)
        # Clear an inactive target's old limit before replacing its curve:
        # otherwise the old high-TDP floor can reject a new lower-power curve.
        if exists and self.get_state().get("profile") == name:
            raise Z13CtlError("Refusing to rebuild an active named profile; select its base first")
        self.request("tdp-reset", profile=name)
        curve = profile.get("fan_curve")
        if curve:
            self.request("fancurve", set=_curve_spec(curve), profile=name)
        else:
            self.request("fancurve-reset", profile=name)
        tdp = profile.get("tdp")
        if tdp:
            self.request("tdp", set=str(tdp["pl1"]), pl1=str(tdp["pl1"]), pl2=str(tdp["pl2"]),
                         pl3=str(tdp["pl3"]), force=bool(tdp.get("force")), profile=name)
        uv = profile.get("undervolt")
        if uv is not None:
            self.request("undervolt", set=str(uv), profile=name)
        else:
            self.request("undervolt-reset", profile=name)
        return name

    def activate_app_profile(self, profile):
        """Select firmware base, then the mapped z13ctl profile if it has overrides."""
        # Select the base before editing: resets on an active custom profile
        # have live side effects. Prepare the now-inactive target in full before
        # selecting it; failures never fall through to the default custom profile.
        self.request("profile", set=profile["base"])
        name = self.ensure_named_profile(profile)
        has_overrides = any(profile.get(k) is not None for k in ("tdp", "fan_curve", "undervolt"))
        if has_overrides:
            self.request("profile", set=name)
            return name
        return profile["base"]

    def close(self):
        """Interrupt an idle subscription during shutdown."""
        conn = self._subscription
        if conn is not None:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()

    def subscribe(self, events=EVENTS):
        """Yield event names until the daemon closes the stream.

        The 10-second timeout covers only the subscription acknowledgement. The
        open stream is intentionally unbounded and is closed by the caller.
        """
        conn = self._connect(self.connect_timeout)
        self._subscription = conn
        stream = None
        try:
            conn.settimeout(self.request_timeout)
            request = {"cmd": "subscribe", "events": list(events)}
            conn.sendall((json.dumps(request, separators=(",", ":")) + "\n").encode())
            stream = conn.makefile("rb")
            ack = self._readline(stream)
            if not isinstance(ack, dict) or ack.get("ok") is not True:
                message = ack.get("error", "subscription rejected") if isinstance(ack, dict) else "invalid response"
                raise Z13CtlError(f"z13ctl subscribe: {message}")
            conn.settimeout(None)
            # Yield readiness after the ACK, before consuming queued events.
            # follow_state snapshots here on every reconnect, closing the gap
            # between taking an initial snapshot and subscribing.
            yield "connected"
            while True:
                record = self._readline(stream, allow_eof=True)
                if record is None:
                    return
                event = record.get("event") if isinstance(record, dict) else None
                if event:
                    yield event
        finally:
            self._subscription = None
            if stream is not None:
                stream.close()
            conn.close()


def _curve_spec(points):
    """z13ctl stores PWM as integer percent; app values are [temperature, percent]."""
    return ",".join(f"{int(temp)}:{int(percent)}%" for temp, percent in points)


def follow_state(client, on_state, stop_event, *, reconnect_delay=1.0, on_error=None, read_state=None):
    """Subscribe before snapshot; reconnect and resnapshot without state polling."""
    read_state = read_state or client.get_state
    while not stop_event.is_set():
        try:
            for event in client.subscribe(EVENTS):
                if stop_event.is_set():
                    return
                if event == "gui-toggle":
                    on_state(None, event)
                elif event in ("connected", "state-changed", "power-source"):
                    on_state(read_state(), "state-changed" if event == "connected" else event)
                if stop_event.is_set():
                    return
        except (OSError, TimeoutError, Z13CtlError) as exc:
            if on_error:
                on_error(exc)
        if stop_event.wait(reconnect_delay):
            return

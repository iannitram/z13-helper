"""Dependency-free CDP bridge to Steam's Game-Mode UI (SharedJSContext).

Opens/closes the Steam side menus with a pure-JS `MenuStore` call over Steam's CEF
remote-debug socket — no input emulation, no virtual controller, so it CANNOT repollute
Steam's controller list (the deck-uhid phantom failure mode that forced InputPlumber to be
masked on this Z13). This is the mechanism proven live on the Z13 (2026-07-15).

Why stdlib-only (no `websocket-client`): this module backs a persistent systemd --user
service (`z13-button-watcher`). `pip --user` packages live under
`~/.local/lib/python3.13/...` and silently stop importing the moment a SteamOS update bumps
the interpreter's minor version — an unacceptable failure mode for an always-on daemon. So
the WebSocket client here is a small hand-rolled RFC 6455 client using only the standard
library (socket/base64/os/struct/json/urllib).

Requires: Steam CEF debugging enabled (`~/.steam/steam/.cef-enable-remote-debugging`, on by
default because Decky needs it) + steamwebhelper listening on 127.0.0.1:8080. Present only
when the Steam gamepad UI is up (Game Mode, or Big Picture in desktop).

Menu API (verified live on this machine):
    SteamUIStore.WindowStore.GamepadUIMainWindowInstance.MenuStore
      .OpenSideMenu(2)  -> Quick Access Menu (right, ESideMenu.QuickAccess)
      .OpenSideMenu(1)  -> Steam menu        (left,  ESideMenu.Main)
      .CloseSideMenus() -> dismiss both
      .IsAnySideMenuVisible() / .GetOpenSideMenu()
`window.DFL.Navigation.{OpenQuickAccessMenu,OpenMainMenu,CloseSideMenus}` (Decky's globally
injected decky-frontend-lib) is used as a fallback — it is bound to the same MenuStore.
"""

import base64
import json
import os
import socket
import struct
import urllib.request

CDP_JSON = "http://127.0.0.1:8080/json"
TARGET_TITLE = "SharedJSContext"

# ---------------------------------------------------------------------------
# Minimal stdlib RFC 6455 WebSocket client (text frames, one request/response).
# ---------------------------------------------------------------------------


def _discover_ws_url(timeout):
    """Return the SharedJSContext target's webSocketDebuggerUrl.

    Re-enumerated on every call: the page GUID churns across Steam restarts, so it must
    never be cached/hardcoded.
    """
    raw = urllib.request.urlopen(CDP_JSON, timeout=timeout).read()
    for tgt in json.loads(raw):
        if tgt.get("title") == TARGET_TITLE:
            url = tgt.get("webSocketDebuggerUrl")
            if url:
                return url
    raise RuntimeError(
        f"CDP target {TARGET_TITLE!r} not found — is the Steam gamepad UI running?")


def _ws_connect(url, timeout):
    if not url.startswith("ws://"):
        raise ValueError(f"unexpected ws url: {url!r}")
    hostport, _, path = url[len("ws://"):].partition("/")
    host, _, port = hostport.partition(":")
    sock = socket.create_connection((host, int(port or 80)), timeout=timeout)
    sock.settimeout(timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    handshake = (
        f"GET /{path} HTTP/1.1\r\n"
        f"Host: {host}:{port or 80}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(handshake.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = sock.recv(4096)
        if not chunk:
            raise ConnectionError("WebSocket handshake failed (connection closed)")
        resp += chunk
    status_line = resp.split(b"\r\n", 1)[0]
    if b" 101 " not in status_line:
        raise ConnectionError(f"WebSocket handshake not 101: {status_line!r}")
    return sock


def _ws_send(sock, opcode, payload):
    """Send one final, client-masked frame (client->server frames MUST be masked)."""
    header = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        header.append(0x80 | n)
    elif n < 65536:
        header.append(0x80 | 126)
        header += struct.pack(">H", n)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", n)
    mask = os.urandom(4)
    header += mask
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    sock.sendall(bytes(header) + masked)


def _ws_recv(sock):
    def readn(n):
        buf = b""
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("WebSocket closed mid-frame")
            buf += chunk
        return buf

    b0, b1 = readn(2)
    opcode = b0 & 0x0F
    length = b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", readn(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", readn(8))[0]
    mask = readn(4) if (b1 & 0x80) else b""
    payload = readn(length)
    if mask:
        payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
    return opcode, payload


def eval_js(expression, timeout=3.0, await_promise=True):
    """Evaluate JS in SharedJSContext; return the JSON-serializable result value.

    Raises on transport/target errors; returns {"error": "..."} on a JS exception.
    """
    sock = _ws_connect(_discover_ws_url(timeout), timeout)
    try:
        _ws_send(sock, 0x1, json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": await_promise,
                "userGesture": True,
                "returnByValue": True,
            },
        }).encode())
        while True:
            opcode, payload = _ws_recv(sock)
            if opcode == 0x8:  # close
                raise ConnectionError("WebSocket closed by server")
            if opcode == 0x9:  # ping -> pong
                _ws_send(sock, 0xA, payload)
                continue
            if opcode != 0x1:  # ignore non-text (pong/binary)
                continue
            msg = json.loads(payload.decode("utf-8"))
            if msg.get("id") != 1:
                continue
            result = msg.get("result", {})
            if "exceptionDetails" in result:
                exc = result["exceptionDetails"].get("exception", {})
                return {"error": exc.get("description", "JS exception")}
            return result.get("result", {}).get("value")
    finally:
        try:
            sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Menu actions. Each JS blob resolves the MenuStore via the stable semantic path
# (raw SteamUIStore first — does NOT need Decky loaded), then falls back to the
# Decky-injected window.DFL.Navigation wrapper, then reports 'unavailable'.
# ---------------------------------------------------------------------------

_MENUSTORE = (
    "var S=window.SteamUIStore,W=S&&S.WindowStore,"
    "i=(W&&W.GamepadUIMainWindowInstance)||(S&&S.ActiveWindowInstance),"
    "m=i&&i.MenuStore;"
)

# Single tap: toggle. If a side menu is showing, dismiss it (controller-free close);
# otherwise open the Quick Access Menu (right).
_JS_TAP = (
    "(function(){" + _MENUSTORE +
    "if(m&&typeof m.OpenSideMenu==='function'){"
    "if(m.IsAnySideMenuVisible&&m.IsAnySideMenuVisible()){m.CloseSideMenus();return 'closed';}"
    "m.OpenSideMenu(2);return 'qam';}"
    "if(window.DFL&&window.DFL.Navigation){window.DFL.Navigation.OpenQuickAccessMenu();return 'qam-dfl';}"
    "return 'unavailable';})()"
)

# Force-open the Quick Access Menu (right), no toggle.
_JS_QAM = (
    "(function(){" + _MENUSTORE +
    "if(m&&typeof m.OpenSideMenu==='function'){m.OpenSideMenu(2);return 'qam';}"
    "if(window.DFL&&window.DFL.Navigation){window.DFL.Navigation.OpenQuickAccessMenu();return 'qam-dfl';}"
    "return 'unavailable';})()"
)

# Steam menu (left): app switching / library / power.
_JS_MAIN = (
    "(function(){" + _MENUSTORE +
    "if(m&&typeof m.OpenSideMenu==='function'){m.OpenSideMenu(1);return 'main';}"
    "if(window.DFL&&window.DFL.Navigation){window.DFL.Navigation.OpenMainMenu();return 'main-dfl';}"
    "return 'unavailable';})()"
)

_JS_CLOSE = (
    "(function(){" + _MENUSTORE +
    "if(m&&typeof m.CloseSideMenus==='function'){m.CloseSideMenus();return 'closed';}"
    "if(window.DFL&&window.DFL.Navigation){window.DFL.Navigation.CloseSideMenus();return 'closed-dfl';}"
    "return 'unavailable';})()"
)


def tap(timeout=3.0):
    """Single-tap action: open the QAM, or dismiss whatever side menu is open."""
    return eval_js(_JS_TAP, timeout)


def open_qam(timeout=3.0):
    """Open the Quick Access Menu (right)."""
    return eval_js(_JS_QAM, timeout)


def open_main_menu(timeout=3.0):
    """Open the Steam menu (left)."""
    return eval_js(_JS_MAIN, timeout)


def close_side_menus(timeout=3.0):
    """Dismiss the QAM / Steam menu."""
    return eval_js(_JS_CLOSE, timeout)


def focused_window_present(timeout=3.0):
    """True if the gamepad-UI overlay currently holds input focus."""
    return bool(eval_js("(window.SteamUIStore.GetFocusedWindowInstance()!=null)", timeout))


if __name__ == "__main__":
    import sys
    actions = {
        "tap": tap, "qam": open_qam, "menu": open_main_menu,
        "close": close_side_menus, "focus": focused_window_present,
    }
    name = sys.argv[1] if len(sys.argv) > 1 else "qam"
    fn = actions.get(name)
    if fn is None:
        print(f"usage: {sys.argv[0]} {{{'|'.join(actions)}}}")
        sys.exit(2)
    print(f"{name} -> {fn()}")

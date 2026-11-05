"""The one way Z13-Helper runs a command on the host.

Stdlib only. The desktop app imports it inside the Flatpak, the `z13-profile`
CLI imports it from ~/.local/lib/z13-helper/, and the Decky backend imports a
root-owned copy from its py_modules/ (docs/adr/0001-decky-imports-root-owned-copies.md).

The mode is detected once, at import:

- "flatpak": inside the sandbox (FLATPAK_ID set). Commands are prefixed with
  `flatpak-spawn --host`, since z13ctl, systemd and the hardware live outside.
- "root":    euid 0 (the Decky backend if Decky ever honours its root flag).
- "decky":   the deck user under Decky Loader (what the Decky backend is today).
- "host":    a plain host user (the `z13-profile` CLI, a terminal).

"root" and "decky" run children with the deck env: deck's runtime dir and
session bus (so z13ctl reaches its daemon socket and `systemctl --user` works),
and the loader's LD_LIBRARY_PATH swapped back for the host's. Decky Loader is a
PyInstaller bundle whose bundled libcrypto is too old for the host's systemctl
("OPENSSL_3.4.0 not found"): every systemctl call used to fail.

run() never raises: a timeout gives rc 124, a missing binary rc 127 (but inside
the Flatpak, flatpak-spawn reports a missing host binary as rc 1).
"""

import collections
import os
import re
import select
import subprocess
import threading
import time

DECK_HOME = "/home/deck"
DECK_RUNTIME_DIR = "/run/user/1000"
DECK_BUS = f"unix:path={DECK_RUNTIME_DIR}/bus"

RC_TIMEOUT = 124
RC_NOT_FOUND = 127
PIPE_GRACE_S = 1.0               # extra wait for pipe EOF after the child exits
MAX_OUTPUT_BYTES = 1024 * 1024   # captured child output kept in memory (issue #30)

Result = collections.namedtuple("Result", "rc out timed_out")

# Maximum time for one command that writes Z13 hardware state. Profile apply
# has a separate aggregate budget because it runs several such writes in order.
Z13CTL_WRITE_TIMEOUT_S = 20
Result.__doc__ = "rc, stdout+stderr (stripped), and whether the timeout killed it."


def detect(environ=None, euid=None):
    environ = os.environ if environ is None else environ
    euid = os.geteuid() if euid is None else euid
    if "FLATPAK_ID" in environ:
        return "flatpak"
    if euid == 0:
        return "root"
    # The loader exports DECKY_* to every plugin backend; LD_LIBRARY_PATH_ORIG
    # is its PyInstaller bootloader's mark.
    if "DECKY_PLUGIN_DIR" in environ or "LD_LIBRARY_PATH_ORIG" in environ:
        return "decky"
    return "host"


MODE = detect()


def in_flatpak():
    return MODE == "flatpak"


def deck_env(environ=None):
    """environ with deck's runtime dir and session bus, and the host's library path."""
    env = dict(os.environ if environ is None else environ)
    env["XDG_RUNTIME_DIR"] = DECK_RUNTIME_DIR
    env["DBUS_SESSION_BUS_ADDRESS"] = DECK_BUS
    env.pop("LD_LIBRARY_PATH", None)
    if env.get("LD_LIBRARY_PATH_ORIG"):
        env["LD_LIBRARY_PATH"] = env["LD_LIBRARY_PATH_ORIG"]
    return env


def command(argv, *, as_deck=False, mode=None, environ=None):
    """(argv, env) to hand to subprocess for `argv` in `mode`. env None = inherit.

    as_deck: run as the deck user with deck's HOME. Only root needs a hop for
    that (runuser refuses to run at all when you're deck already); under Decky
    the loader's HOME is pinned too, as the backend always did.
    """
    mode = mode or MODE
    argv = list(argv)
    if mode == "flatpak":
        return ["flatpak-spawn", "--host"] + argv, None
    if mode == "host":
        return argv, None
    if as_deck:
        pin = ["env", f"HOME={DECK_HOME}", f"XDG_RUNTIME_DIR={DECK_RUNTIME_DIR}",
               f"DBUS_SESSION_BUS_ADDRESS={DECK_BUS}"]
        argv = (["runuser", "-u", "deck", "--"] if mode == "root" else []) + pin + argv
    return argv, deck_env(environ)


_HOLO_TOOLS = {}


def holo_or_steamos(name: str) -> str:
    """Pick the installed SteamOS tool name, once per process per tool."""
    if not re.fullmatch(r"[a-z][a-z0-9-]*", name, flags=re.ASCII):
        raise ValueError("invalid SteamOS tool name")
    if name not in _HOLO_TOOLS:
        found = run(["sh", "-c", "command -v holo-" + name], timeout=5).rc == 0
        _HOLO_TOOLS[name] = ("holo-" if found else "steamos-") + name
    return _HOLO_TOOLS[name]


def _drain(stream, chunks, cap, stop):
    """Read to EOF or until `stop` is set and nothing is pending; keep at most `cap`
    bytes, drop the rest. Returns whatever is available instead of waiting for a full
    block, and polls `stop` so a grandchild holding the pipe cannot pin the thread."""
    kept = 0
    try:
        fd = stream.fileno()
    except (OSError, ValueError, AttributeError):    # no real fd (in-memory stream)
        while True:
            block = stream.read(65536)
            if not block:
                return
            if kept < cap:
                chunks.append(block[:cap - kept])
                kept += len(chunks[-1])
    while True:
        try:
            ready = select.select([fd], [], [], 0.1)[0]
        except (OSError, ValueError):
            return
        if not ready:
            if stop.is_set():
                return
            continue
        try:
            block = os.read(fd, 65536)
        except OSError:
            return
        if not block:
            return
        if kept < cap:
            chunks.append(block[:cap - kept])
            kept += len(chunks[-1])


def _feed(stream, data):
    try:
        stream.write(data)
    except OSError:      # child closed stdin early
        pass
    finally:
        try:
            stream.close()
        except OSError:
            pass


def _reap(p):
    try:
        p.kill()
    except OSError:
        pass
    try:
        p.wait(5)
    except (OSError, subprocess.SubprocessError):
        pass


def run(argv, timeout=30, *, as_deck=False, input=None):
    """Run argv on the host and wait. Returns Result(rc, out, timed_out); never raises.

    Output is stdout followed by stderr (as before); each stream keeps only its first
    MAX_OUTPUT_BYTES in memory. Text is UTF-8, undecodable bytes replaced.
    `timeout` bounds the child; after it exits (or is killed) the pipes get at most
    PIPE_GRACE_S more to reach EOF, so a grandchild that keeps them open cannot delay
    the return, and every thread and fd is released before returning.
    """
    cmd, env = command(argv, as_deck=as_deck)
    out_chunks, err_chunks, threads = [], [], []
    stop = threading.Event()
    try:
        p = subprocess.Popen(cmd, env=env, stdin=None if input is None else subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        return Result(RC_NOT_FOUND, str(e), False)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return Result(1, str(e), False)
    timed_out = False
    try:
        threads.append(threading.Thread(target=_drain, args=(p.stdout, out_chunks, MAX_OUTPUT_BYTES, stop),
                                        daemon=True))
        threads.append(threading.Thread(target=_drain, args=(p.stderr, err_chunks, MAX_OUTPUT_BYTES, stop),
                                        daemon=True))
        if input is not None:
            threads.append(threading.Thread(target=_feed, args=(p.stdin, input.encode()),
                                            daemon=True))
        for t in threads:
            t.start()
        try:
            rc = p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            _reap(p)
            return Result(RC_TIMEOUT, f"timed out after {timeout} s", True)
        deadline = time.monotonic() + PIPE_GRACE_S
        for t in threads:
            t.join(max(0.0, deadline - time.monotonic()))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        _reap(p)
        return Result(1, str(e), False)
    finally:
        # Drain threads poll `stop`, so they end within ~0.1 s even if a grandchild
        # still holds the pipe; only then close the pipes (no close-while-reading).
        stop.set()
        for t in threads:
            if t.ident is not None:      # never started if thread creation failed
                t.join(1)
        for pipe in (p.stdout, p.stderr, p.stdin):
            if pipe is not None and not any(t.is_alive() for t in threads):
                try:
                    pipe.close()
                except OSError:
                    pass
    text = b"".join(out_chunks).decode(errors="replace") + b"".join(err_chunks).decode(errors="replace")
    return Result(rc, text.strip(), False)


def open_stream(argv, *, as_deck=False, mode=None, environ=None, stderr=None):
    """Start a host command whose stdout is a long-lived event stream.

    The caller owns the returned Popen and must terminate/wait it during teardown.
    This preserves the same Flatpak bridge and Decky host environment as ``run``.
    """
    cmd, env = command(argv, as_deck=as_deck, mode=mode, environ=environ)
    if (mode or MODE) == "flatpak":
        # The host child must exit with this proxy, including forced shutdown.
        cmd.insert(2, "--watch-bus")
    return subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=stderr,
                            text=True, bufsize=1)

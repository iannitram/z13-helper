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
import subprocess

DECK_HOME = "/home/deck"
DECK_RUNTIME_DIR = "/run/user/1000"
DECK_BUS = f"unix:path={DECK_RUNTIME_DIR}/bus"

RC_TIMEOUT = 124
RC_NOT_FOUND = 127

Result = collections.namedtuple("Result", "rc out timed_out")
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


def run(argv, timeout=30, *, as_deck=False, input=None):
    """Run argv on the host and wait. Returns Result(rc, out, timed_out); never raises."""
    cmd, env = command(argv, as_deck=as_deck)
    try:
        p = subprocess.run(cmd, env=env, input=input, capture_output=True, text=True,
                           timeout=timeout)
    except subprocess.TimeoutExpired:
        return Result(RC_TIMEOUT, f"timed out after {timeout} s", True)
    except FileNotFoundError as e:
        return Result(RC_NOT_FOUND, str(e), False)
    except (OSError, ValueError, subprocess.SubprocessError) as e:
        return Result(1, str(e), False)
    return Result(p.returncode, ((p.stdout or "") + (p.stderr or "")).strip(), False)

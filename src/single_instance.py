"""Single-instance guard for the Z13-Helper.

Two live copies of the app means two host-side side-button readers (each press
toggles the window twice) and two Max Fans watchdogs fighting over the fan
curve. systemd can't reliably prevent that on its own: Flatpak moves the real
app process out of z13-helper.service's cgroup into its own
app-flatpak-*.scope, so `systemctl --user stop/restart` leaves it running.

The first instance (the *primary*) holds an exclusive flock on a lock file and
listens on a Unix socket next to it. A later launch finds the lock taken, sends
the primary a one-line command over the socket, and exits:

    show  -- raise the existing window (desktop launcher / `--show`)
    ping  -- no-op (a plain hidden launch while one is already resident)
    quit  -- primary quits cleanly; the client waits until it has exited
             (`--quit`, used by the unit's ExecStop; and `--resident`, the
             unit's ExecStart, to take over from an instance started any
             other way, e.g. by Plasma session restore)

A deliberate Quit (menu, tray, Quit button) leaves a `user-quit` marker next to
the lock, so the Decky plugin's desktop-app check doesn't start the app again
behind the user's back. The next start removes it. Paths here are mirrored in
tooling/z13-button-watcher and decky/main.py (both host-side, outside Flatpak).

flock is released by the kernel when the primary dies, so a crash never leaves
a stale lock. The files live in $XDG_RUNTIME_DIR/app/$FLATPAK_ID, which Flatpak
shares between all sandbox instances of the same app (the sandbox's /tmp is not
shared). Pure stdlib on purpose: PyQt6.QtNetwork (QLocalServer) fails to import
in the KDE 6.10 runtime (missing libgssapi_krb5), and the client side must stay
cheap -- it runs before PyQt6 is imported at all.
"""

import errno
import fcntl
import os
import socket
import time

_NAME = "z13-helper"


def runtime_dir() -> str:
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/{_NAME}-{os.getuid()}"
    app_id = os.environ.get("FLATPAK_ID")
    path = os.path.join(base, "app", app_id) if app_id else base
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def _paths():
    d = runtime_dir()
    return os.path.join(d, _NAME + ".lock"), os.path.join(d, _NAME + ".sock")


def user_quit_path():
    return os.path.join(runtime_dir(), _NAME + ".user-quit")


def mark_user_quit():
    try:
        with open(user_quit_path(), "w") as f:
            f.write(f"{time.time():.0f}\n")
    except OSError:
        pass


def clear_user_quit():
    try:
        os.unlink(user_quit_path())
    except OSError:
        pass


def acquire_lock(wait: float = 0.0):
    """Return the held lock fd if we are the primary, else None.
    With `wait`, keeps trying that long (for a primary that is exiting)."""
    deadline = time.monotonic() + wait
    while True:
        fd = _try_lock()
        if fd is not None or time.monotonic() >= deadline:
            return fd
        time.sleep(0.1)


def _try_lock():
    lock_path, _ = _paths()
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        os.close(fd)
        if exc.errno in (errno.EWOULDBLOCK, errno.EAGAIN):
            return None
        raise
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode())
    return fd


def send_command(cmd: str, wait_exit: float = 0.0, connect_timeout: float = 3.0) -> bool:
    """Send `cmd` to the primary. Returns True if it was delivered.

    Retries the connect for up to `connect_timeout` s, since the primary takes
    the lock before its socket is listening. With `wait_exit`, blocks up to that
    long until the primary closes the connection (i.e. its process has exited).
    """
    _, sock_path = _paths()
    deadline = time.monotonic() + connect_timeout
    while True:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        try:
            s.connect(sock_path)
            break
        except OSError:
            s.close()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.1)
    try:
        s.sendall(cmd.encode() + b"\n")
        if wait_exit:
            s.settimeout(wait_exit)
            try:
                while s.recv(64):
                    pass
            except OSError:
                pass
        return True
    finally:
        s.close()


class InstanceServer:
    """Primary-side listener, pumped by a QSocketNotifier on the Qt main loop.

    `handler(cmd, conn)` runs on the main thread for each command line. It may
    keep `conn` open (the `quit` case, so the client sees EOF only once this
    process is gone) by returning True; otherwise the connection is closed.
    """

    def __init__(self, handler):
        from PyQt6.QtCore import QSocketNotifier

        _, self._path = _paths()
        try:
            os.unlink(self._path)  # stale socket from a dead primary; we hold the lock
        except FileNotFoundError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        self._sock.bind(self._path)
        self._sock.listen(4)
        self._sock.setblocking(False)
        self._handler = handler
        self._held = []  # connections kept open until exit
        self._notifier = QSocketNotifier(self._sock.fileno(), QSocketNotifier.Type.Read)
        self._notifier.activated.connect(self._accept)

    def _accept(self, *_):
        try:
            conn, _ = self._sock.accept()
        except OSError:
            return
        try:
            conn.settimeout(1.0)
            cmd = conn.recv(64).decode(errors="replace").strip()
        except OSError:
            conn.close()
            return
        if self._handler(cmd, conn):
            self._held.append(conn)
        else:
            conn.close()

    def close(self):
        self._notifier.setEnabled(False)
        self._sock.close()
        try:
            os.unlink(self._path)
        except OSError:
            pass

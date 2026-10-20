"""Max Fans state shared by the desktop app and the Decky plugin.

Both frontends toggle Max Fans, so the ON/OFF flag and the curve snapshot to
restore on OFF live in one small file that each side re-reads instead of
caching:

    {"enabled": bool, "saved_curve": [[temp, pwm], ... 8 points]}

decky/main.py carries a copy of this logic (it can't import from the app).
The legacy config.json keys `fans_max` / `fan_curve_saved` are read only to
seed this file the first time.
"""

import contextlib
import stat
import errno
import fcntl
import json
import os
import threading
import time

STATE_PATH = os.path.expanduser("~/.config/z13-helper/max-fans.json")


# One stable lock file next to each store, held around a whole read-modify-write
# (never the JSON itself: os.replace swaps its inode). Re-entrant per thread, and
# serialises threads and processes alike (flock is per open file). Copy of profiles.file_lock
# (z13-profile is a single installed file, so it can't import this).
_lock_state = threading.local()
LOCK_TIMEOUT = 10.0   # seconds to wait for another writer before giving up


class StoreLockError(OSError):
    """The lock file is unsafe to use, or another writer held it too long."""


def _open_lock(lock_path):
    """Open (creating if needed) the lock file without following links.

    The directory is writable by the deck user and the Decky backend runs as root, so
    a planted symlink or hard link must never be followed, chowned or chmodded. Only a
    file we just created gets its owner and mode set.
    """
    base = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC
    created = True
    try:
        fd = os.open(lock_path, base | os.O_CREAT | os.O_EXCL, 0o660)
    except FileExistsError:
        created = False
        try:
            fd = os.open(lock_path, base)
        except OSError as e:
            if e.errno == errno.ELOOP:
                raise StoreLockError(f"{lock_path} is a symlink; refusing") from e
            raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise StoreLockError(f"{lock_path} is not a plain single-link file; refusing")
        if created:
            os.fchmod(fd, 0o660)
        elif st.st_uid not in {0, os.geteuid()}:
            raise StoreLockError(f"{lock_path} has an unexpected owner; refusing")
    except BaseException:
        os.close(fd)
        raise
    return fd


def _acquire(fd, lock_path):
    deadline = time.monotonic() + LOCK_TIMEOUT
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise StoreLockError(f"timed out waiting for {lock_path}") from None
            time.sleep(0.05)


@contextlib.contextmanager
def file_lock(path):
    held = _lock_state.__dict__.setdefault("held", {})
    if held.get(path):
        held[path] += 1
        try:
            yield
        finally:
            held[path] -= 1
        return
    fd = None
    lock_path = f"{path}.lock"
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        fd = _open_lock(lock_path)
        _acquire(fd, lock_path)
    except StoreLockError:
        if fd is not None:
            os.close(fd)
        raise
    except OSError:
        if fd is not None:
            os.close(fd)
        fd = None   # can't lock (read-only dir?): behave as before, unlocked
    held[path] = 1
    try:
        yield
    finally:
        held.pop(path, None)
        if fd is not None:
            os.close(fd)   # closing releases the flock




def load() -> dict | None:
    """Current state, or None if the file is missing or unreadable."""
    try:
        with open(STATE_PATH) as f:
            state = json.load(f)
        return state if isinstance(state, dict) else None
    except (OSError, ValueError):
        return None


def is_enabled() -> bool:
    return bool((load() or {}).get("enabled", False))


def saved_curve():
    return (load() or {}).get("saved_curve")


def restore_plan():
    """What Max Fans OFF should restore, as (kind, curve).

    ("curve", pts): put the saved [temp, pwm] snapshot back.
    ("auto", None): the snapshot is explicitly null, i.e. the profile has no
        custom curve, so release the fans to firmware auto.
    ("unknown", None): no snapshot key, or the file is missing/unreadable;
        the caller falls back to the active profile's curve, else auto.
    """
    state = load()
    if state is None or "saved_curve" not in state:
        return ("unknown", None)
    curve = state["saved_curve"]
    if curve is None:
        return ("auto", None)
    return ("curve", curve) if curve else ("unknown", None)


def record_profile_curve(curve):
    """Max Fans is ON and the profile's curve ([temp, pct]) changed: keep the
    restore snapshot in step. None (custom curve disabled) clears it to auto."""
    if curve:
        save(saved_curve=[[int(t), round(int(p) * 255 / 100)] for t, p in curve])
    else:
        save(saved_curve=None)


def save(**changes):
    """Merge `changes` into the state file atomically (tmp + rename)."""
    with file_lock(STATE_PATH):
        state = load() or {}
        state.update(changes)
        os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
        tmp = f"{STATE_PATH}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, STATE_PATH)


def migrate_legacy(config):
    """Seed the shared file from the app's old config.json keys, once."""
    with file_lock(STATE_PATH):
        if load() is not None:
            return
        seed = {"enabled": bool(config.get("fans_max", False))}
        if config.get("fan_curve_saved") is not None:
            seed["saved_curve"] = config["fan_curve_saved"]   # absent = unknown, null = auto
        save(**seed)

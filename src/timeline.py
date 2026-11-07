#!/usr/bin/env python3
"""Append-only machine timeline store (Phase 16f, #134).

Records every hardware or host change as one JSON Lines entry under
`~/.local/state/z13-helper/timeline.jsonl` with `before` / `after` snapshots
and, where reversible, a structured rollback record (Phase 12c backups, prior
profile, or prior goal). Host-level entries (`install`, `post_update`,
`steamos_update`, `host`) emit Principle 7's `logger` journal line from the
same `append()` call so the timeline and the system journal never drift.

Stdlib + `host` only: imported by the desktop Flatpak, `z13-profile` on the
host, `install.sh` / `installers.py`, `tooling/post_update`, and Decky's
`py_modules/timeline.py` (ADR 0001).
"""

import argparse
import atexit
import contextlib
import copy
import errno
import fcntl
import json
import logging
import os
import pwd
import queue
import secrets
import stat
import sys
import threading
import time

_HOST_LIB = os.path.expanduser("~/.local/lib/z13-helper")
if os.path.isdir(_HOST_LIB) and _HOST_LIB not in sys.path:
    sys.path.append(_HOST_LIB)
import host  # noqa: E402

SCHEMA_VERSION = 1
STATE_DIR = os.path.expanduser("~/.local/state/z13-helper")
# The user's logging choice lives here ("logging": false = no files at all, AGENTS.md).
# Decky points this at its own copy of the constant.
INSTALL_JSON = os.path.expanduser("~/.config/z13-helper/install.json")
TIMELINE_PATH = os.path.join(STATE_DIR, "timeline.jsonl")
MAX_BYTES = 1 << 20  # 1 MiB per segment
BACKUP_COUNT = 3
LOCK_TIMEOUT_S = 10.0
LOCK_POLL_S = 0.05
DECK_UID = 1000
DECK_GID = 1000

KINDS = ("profile", "goal", "install", "post_update", "steamos_update", "host",
         "tdp", "battery_limit", "brightness")
# Principle 7 applies to host-level changes (/etc, z13ctl, units, OS slot).
HOST_KINDS = ("install", "post_update", "steamos_update", "host")
# Manual hardware settings the frontends write on demand (never Principle 7 host changes).
SETTING_KINDS = ("tdp", "battery_limit", "brightness")

_LOCK_STATE = threading.local()
logger = logging.getLogger(__name__)


class TimelineError(OSError):
    """Refused unsafe path (symlink, non-regular file) or invalid timeline state."""


class TimelineLockError(TimelineError):
    """Timed out or failed acquiring the inter-process timeline lock."""


def _lock_counts():
    counts = getattr(_LOCK_STATE, "counts", None)
    if counts is None:
        counts = {}
        _LOCK_STATE.counts = counts
    return counts


def _resolve_owner(dir_path, owner=None, dir_fd=None):
    """Target (uid, gid) when running as root so deck and Flatpak can still read/append.

    With `dir_fd` the folder is inspected by descriptor, never looked up again by path."""
    if owner is not None:
        return owner
    if os.geteuid() != 0:
        return None
    try:
        st = os.fstat(dir_fd) if dir_fd is not None else os.lstat(dir_path)
        if st.st_uid != 0:
            return (st.st_uid, st.st_gid)
    except OSError:
        pass
    try:
        pw = pwd.getpwuid(DECK_UID)
        return (pw.pw_uid, pw.pw_gid)
    except KeyError:
        return (DECK_UID, DECK_GID)


def _open_dir_chain(dir_path, owner=None):
    """Create the missing components of `dir_path` (0o755) and return an open
    descriptor for the leaf folder; the caller closes it.

    Each component is opened from its parent with `O_NOFOLLOW`, so a symlink
    (or any non-directory) anywhere in the path is refused with TimelineError
    instead of being followed. Every later operation (lock, append, rotation)
    goes through the returned descriptor, so replacing an ancestor folder after
    this check cannot redirect them. When running as root, a root-owned leaf
    folder is handed back to `owner` by descriptor (`fchown`), never by path.
    The parent descriptor is closed as soon as its child is open, on every path.
    """
    target = os.path.abspath(dir_path)
    parts = [p for p in target.split(os.sep) if p]
    nofollow = getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(os.sep, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        for name in parts:
            try:
                child = os.open(name, os.O_RDONLY | nofollow, dir_fd=fd)
            except OSError as exc:
                if exc.errno != errno.ENOENT:
                    # ELOOP (symlink), ENOTDIR (file component) or a real
                    # permission/IO problem: refuse, never follow.
                    raise TimelineError(exc.errno, f"refusing non-directory component {name!r} of {target}", target) from exc
                try:
                    os.mkdir(name, 0o755, dir_fd=fd)
                except OSError as mk_exc:
                    if mk_exc.errno != errno.EEXIST:
                        raise TimelineError(mk_exc.errno, f"cannot create timeline directory {target}", target) from mk_exc
                try:
                    child = os.open(name, os.O_RDONLY | nofollow, dir_fd=fd)
                except OSError as exc2:
                    raise TimelineError(exc2.errno, f"refusing non-directory component {name!r} of {target}", target) from exc2
            os.close(fd)   # the parent is not needed once the child is open
            fd = child
        resolved = _resolve_owner(target, owner, dir_fd=fd)
        if target != os.sep and resolved is not None and os.geteuid() == 0:
            try:
                st = os.fstat(fd)
            except OSError:
                st = None
            if st is not None and st.st_uid == 0:
                try:
                    os.fchown(fd, resolved[0], resolved[1])
                except OSError:
                    pass
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_regular_nofollow(path, flags, mode=0o644, owner=None, dir_fd=None):
    fd = os.open(path, flags | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                 mode, dir_fd=dir_fd)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise TimelineError(errno.EPERM, "refusing non-regular or multi-link timeline file", path)
        os.fchmod(fd, mode)
        if os.geteuid() == 0 and owner is not None:
            os.fchown(fd, owner[0], owner[1])
        return fd, st
    except BaseException:
        os.close(fd)
        raise


@contextlib.contextmanager
def timeline_lock(path=None, timeout=LOCK_TIMEOUT_S, owner=None):
    """Inter-process exclusive flock on `<path>.lock` (re-entrant per thread).

    Yields an open descriptor of the timeline folder. Callers do their file
    work relative to it (`dir_fd=`), so nothing re-resolves the folder's path
    after it was checked."""
    target = os.path.abspath(path or TIMELINE_PATH)
    lock_path = target + ".lock"
    counts = _lock_counts()
    held = counts.get(lock_path)
    if held is not None:
        held[0] += 1
        try:
            yield held[1]
        finally:
            held[0] -= 1
        return

    parent = os.path.dirname(target)
    dir_fd = _open_dir_chain(parent, owner=owner)
    try:
        resolved_owner = _resolve_owner(parent, owner, dir_fd=dir_fd)
        try:
            fd, _ = _open_regular_nofollow(
                os.path.basename(lock_path), os.O_RDWR | os.O_CREAT, mode=0o600,
                owner=resolved_owner, dir_fd=dir_fd,
            )
        except OSError as exc:
            raise TimelineLockError(exc.errno or errno.EIO, f"cannot open lock {lock_path}: {exc}", lock_path) from exc

        deadline = time.monotonic() + max(0.0, float(timeout))
        acquired = False
        try:
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise TimelineLockError(exc.errno, f"flock failed on {lock_path}: {exc}", lock_path) from exc
                    if time.monotonic() >= deadline:
                        raise TimelineLockError(errno.ETIMEDOUT, f"timed out locking {lock_path}", lock_path) from exc
                    time.sleep(LOCK_POLL_S)
            counts[lock_path] = [1, dir_fd]
            try:
                yield dir_fd
            finally:
                counts.pop(lock_path, None)
        finally:
            try:
                if acquired:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
    finally:
        os.close(dir_fd)


def _lstat_name(name, dir_fd):
    """lstat of `name` inside the open folder `dir_fd` (or by path when None)."""
    if dir_fd is None:
        return os.lstat(name)
    return os.lstat(os.path.basename(name), dir_fd=dir_fd)


def _check_not_symlink(path, dir_fd=None):
    """Return lstat result if `path` exists as a single-link regular file, else None."""
    try:
        st = _lstat_name(path, dir_fd)
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        raise TimelineError(errno.ELOOP, "refusing symlink or non-regular timeline file", path)
    return st


def _rotate_locked(path, backups, dir_fd=None):
    """Rotate `path` -> `path.1` .. `path.<backups>` under `timeline_lock`.

    With `dir_fd` every step is relative to that folder descriptor."""
    def nm(p):
        return os.path.basename(p) if dir_fd is not None else p

    def exists(p):
        try:
            _lstat_name(p, dir_fd)
            return True
        except OSError:
            return False

    def unlink(p):
        os.unlink(nm(p), dir_fd=dir_fd)

    def replace(src, dst):
        os.replace(nm(src), nm(dst), src_dir_fd=dir_fd, dst_dir_fd=dir_fd)

    _check_not_symlink(path, dir_fd)
    if backups <= 0:
        try:
            unlink(path)
        except FileNotFoundError:
            pass
        return
    for idx in range(1, backups + 1):
        _check_not_symlink(f"{path}.{idx}", dir_fd)
    oldest = f"{path}.{backups}"
    try:
        unlink(oldest)
    except FileNotFoundError:
        pass
    for idx in range(backups - 1, 0, -1):
        src = f"{path}.{idx}"
        dst = f"{path}.{idx + 1}"
        if exists(src):
            replace(src, dst)
    if exists(path):
        replace(path, f"{path}.1")


def format_journal_message(entry):
    """One-line summary for Principle 7 (`logger -t <tag> ...`)."""
    status = "ok" if entry.get("ok", True) else "FAILED"
    summary = str(entry.get("summary") or entry.get("kind") or "change")
    changed = entry.get("changed") or []
    parts = [f"[{entry.get('id', '?')}] {entry.get('kind', 'host')} ({status}): {summary}"]
    if changed:
        shown = ", ".join(str(x) for x in changed[:6])
        if len(changed) > 6:
            shown += f" (+{len(changed) - 6} more)"
        parts.append(f"changed=[{shown}]")
    rollback = entry.get("rollback")
    if isinstance(rollback, dict) and rollback.get("backup_dir"):
        parts.append(f"backup={rollback['backup_dir']}")
    elif isinstance(rollback, dict) and rollback.get("files"):
        parts.append(f"backups={len(rollback['files'])}")
    return " ".join(parts)


def _normalize_entry(entry):
    if not isinstance(entry, dict):
        raise TypeError("timeline entry must be a dict")
    rec = dict(entry)
    rec["v"] = SCHEMA_VERSION
    rec["id"] = str(entry.get("id") or f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{secrets.token_hex(4)}")
    rec["ts"] = str(entry.get("ts") or time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    rec["kind"] = str(entry.get("kind") or "host")
    rec["summary"] = str(entry.get("summary") or rec["kind"])
    rec["ok"] = bool(entry.get("ok", True))
    rec["before"] = dict(entry["before"]) if isinstance(entry.get("before"), dict) else {}
    rec["after"] = dict(entry["after"]) if isinstance(entry.get("after"), dict) else {}
    rec["changed"] = [str(x) for x in (entry.get("changed") or [])]
    rb = entry.get("rollback")
    if isinstance(rb, dict) and rb:
        rec["rollback"] = dict(rb)
        rec["no_rollback_reason"] = None
    else:
        rec["rollback"] = None
        rec["no_rollback_reason"] = str(entry.get("no_rollback_reason") or "Not reversible")
    return rec


def logging_enabled():
    """True unless install.json says "logging": false (missing/unreadable = on)."""
    try:
        with open(INSTALL_JSON) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return True
    return not (isinstance(rec, dict) and rec.get("logging") is False)


def _emit_journal(rec, journal, journal_tag, journal_fn):
    emit = (rec["kind"] in HOST_KINDS) if journal is None else bool(journal)
    if emit:
        msg = format_journal_message(rec)
        if journal_fn is not None:
            journal_fn(journal_tag, msg)
        else:
            host.run(["logger", "-t", journal_tag, msg], timeout=5)


def _needs_recovery_newline(target, size, dir_fd=None):
    """True when a non-empty segment's last byte is not the newline of a complete record."""
    try:
        name = os.path.basename(target) if dir_fd is not None else target
        fd = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
                     dir_fd=dir_fd)
    except OSError:
        return False
    try:
        return os.pread(fd, 1, size - 1) != b"\n"
    except OSError:
        return False
    finally:
        os.close(fd)


def _append_locked(line, target, max_b, bak_n, owner, dir_fd=None):
    """Append `line` to `target`; every step is relative to the folder `dir_fd`
    (opened here, no-follow, when the caller holds none)."""
    if dir_fd is None:
        folder = _open_dir_chain(os.path.dirname(os.path.abspath(target)), owner=owner)
        try:
            return _append_locked(line, target, max_b, bak_n, owner, dir_fd=folder)
        finally:
            os.close(folder)
    name = os.path.basename(target)
    resolved_owner = _resolve_owner(os.path.dirname(os.path.abspath(target)), owner, dir_fd=dir_fd)
    st = _check_not_symlink(name, dir_fd)
    existing_size = st.st_size if st is not None else 0
    # A crashed writer can leave a partial last line; the recovery newline we
    # would prepend must count toward the segment cap, so check before rotating.
    separator = 1 if existing_size > 0 and _needs_recovery_newline(name, existing_size, dir_fd) else 0
    if existing_size > 0 and max_b > 0 and existing_size + separator + len(line) > max_b:
        _rotate_locked(name, bak_n, dir_fd)
        separator = 0
    if max_b > 0 and len(line) > max_b:
        # Larger than a whole segment: it can never fit, so never write it.
        return

    fd, open_st = _open_regular_nofollow(
        name, os.O_RDWR | os.O_CREAT | os.O_APPEND, mode=0o644, owner=resolved_owner, dir_fd=dir_fd
    )
    try:
        payload = line
        if open_st.st_size > 0:
            # If a prior writer crashed mid-line without '\n', isolate the new
            # record on a fresh line without truncating or rewriting old bytes.
            tail = os.pread(fd, 1, open_st.st_size - 1)
            if tail != b"\n":
                payload = b"\n" + line
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def append(entry, *, path=None, max_bytes=MAX_BYTES, backups=BACKUP_COUNT,
           journal=None, journal_tag="z13-timeline", journal_fn=None, owner=None,
           _journal_now=True):
    """Append one JSONL entry (rotating if over `max_bytes`) and emit Principle 7's
    journal line from the same call when `journal` is True (default for `HOST_KINDS`).

    With "logging": false in install.json nothing is written to disk (the journal
    line, which is not a file, is still emitted)."""
    target = path or TIMELINE_PATH
    rec = _normalize_entry(entry)
    if logging_enabled():
        line = (json.dumps(rec, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        with timeline_lock(target, owner=owner) as dir_fd:
            _append_locked(line, target, max(0, int(max_bytes)), max(0, int(backups)), owner, dir_fd)
    if _journal_now:
        _emit_journal(rec, journal, journal_tag, journal_fn)
    return rec


# ----------------------------------------------------------------------
# Deferred recording: callers that sit inside a hardware transaction hand the
# entry to a background writer, so no lock wait, write or fsync of the
# timeline can ever delay a hardware write or watchdog recovery.
# ----------------------------------------------------------------------

MAX_PENDING = 256            # entries waiting for the writer; more are dropped (and logged)
FLUSH_AT_EXIT_S = 5.0
_PENDING = queue.Queue(maxsize=MAX_PENDING)
_WRITER_LOCK = threading.Lock()
_writer = None
_THREAD_ACK = threading.local()


def _log_failure(exc, what):
    """Bounded warning that never carries entry contents (only the error class and text)."""
    logger.warning("timeline: %s failed: %s: %s", what, type(exc).__name__, str(exc)[:200])


def _writer_loop():
    while True:
        job = _PENDING.get()
        try:
            rec, target = job
            append(rec, path=target, journal=False)
        except Exception as exc:  # noqa: BLE001 - recording is best-effort
            _log_failure(exc, "background write")
        finally:
            _PENDING.task_done()


def _ensure_writer():
    global _writer
    with _WRITER_LOCK:
        if _writer is None or not _writer.is_alive():
            _writer = threading.Thread(target=_writer_loop, name="z13-timeline-writer", daemon=True)
            _writer.start()


def enqueue(entry, *, path=None):
    """Queue `entry` for the background writer and return at once.

    The entry is normalised and deep-copied here (so the caller may keep
    mutating its own dicts) and never blocks: a full queue drops the entry
    with a warning. Returns the normalised record, or None when dropped."""
    target = path or TIMELINE_PATH
    rec = copy.deepcopy(_normalize_entry(entry))
    try:
        _PENDING.put_nowait((rec, target))
    except queue.Full:
        logger.warning("timeline: %d entries already waiting; dropped one %s entry", MAX_PENDING, rec.get("kind"))
        return None
    _ensure_writer()
    _THREAD_ACK.count = getattr(_THREAD_ACK, "count", 0) + 1
    return rec


def accepted_count():
    """Entries this thread has handed to `enqueue` so far (lets a caller see
    that a deeper layer already recorded the change, without reading the file)."""
    return getattr(_THREAD_ACK, "count", 0)


def flush(timeout=FLUSH_AT_EXIT_S):
    """Wait up to `timeout` s for queued entries to reach disk; True when none are left."""
    deadline = time.monotonic() + max(0.0, float(timeout))
    while _PENDING.unfinished_tasks:
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)
    return True


atexit.register(flush)


def _submit(entry, *, path, defer):
    return enqueue(entry, path=path) if defer else append(entry, path=path, journal=False)


def _read_single_file(file_path):
    """Return valid complete JSONL dicts in `file_path` (oldest first).

    Any line not terminated by `\\n` (such as a partly written last line from an
    interrupted append) or failing UTF-8 / JSON object parsing is skipped.
    """
    try:
        # O_NONBLOCK: opening a planted FIFO must not hang the reader; the
        # fstat below rejects anything that is not a regular file.
        fd = os.open(file_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return []
    except OSError:
        return []
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return []
        chunks = []
        while True:
            buf = os.read(fd, 65536)
            if not buf:
                break
            chunks.append(buf)
    except OSError:
        return []
    finally:
        os.close(fd)

    raw = b"".join(chunks)
    entries = []
    for raw_line in raw.splitlines(keepends=True):
        if not raw_line.endswith(b"\n"):
            continue
        stripped = raw_line.rstrip(b"\r\n")
        if not stripped:
            continue
        try:
            obj = json.loads(stripped.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and ("id" in obj or "kind" in obj):
            entries.append(obj)
    return entries


def read_entries(path=None, *, limit=None, newest_first=False,
                 include_rotated=True, backups=BACKUP_COUNT):
    """Read timeline entries across rotated segments and active `timeline.jsonl`.

    Read-only: takes no lock and creates no file or folder. A rotation racing a
    read can at worst repeat or miss a segment for that one read.
    """
    target = path or TIMELINE_PATH
    return _read_entries_unlocked(target, limit, newest_first, include_rotated, backups)


def rollback_spec(entry):
    """Return a validated allowlisted rollback action, or None when unsupported.

    Timeline data is persisted input. This deliberately accepts only structured
    profile, Max Fans goal, and single-setting records; backup paths and command
    strings are never interpreted here.
    """
    if not isinstance(entry, dict):
        return None
    rb = entry.get("rollback")
    if not isinstance(rb, dict):
        return None
    kind = rb.get("kind")
    if kind == "profile":
        name = rb.get("profile")
        if (isinstance(name, str) and name and not name.startswith("-")
                and isinstance(rb.get("snapshot"), dict)
                and rb["snapshot"].get("name") == name):
            return {"kind": "profile", "profile": name}
        return None
    if kind == "goal" and rb.get("goal") in ("max", "custom"):
        return {"kind": "goal", "goal": rb["goal"]}
    if kind in SETTING_KINDS:
        state = rb.get("state")
        if not isinstance(state, dict) or set(state) != {kind}:
            return None
        value = state[kind]
        if kind == "tdp":
            if value is not None and (not isinstance(value, dict) or set(value) != {"pl1", "pl2", "pl3"}
                                      or any(type(value[k]) is not int or not 5 <= value[k] <= 93
                                             for k in ("pl1", "pl2", "pl3"))
                                      or value["pl1"] > value["pl2"] or value["pl2"] > value["pl3"]):
                return None
        elif kind == "battery_limit":
            if type(value) is not int or not 40 <= value <= 100:
                return None
        elif kind == "brightness" and value not in ("off", "low", "medium", "high"):
            return None
        return {"kind": kind, "value": value}
    return None


def _read_entries_unlocked(target, limit, newest_first, include_rotated, backups):
    if limit is not None and int(limit) <= 0:
        return []
    bak_n = max(0, int(backups)) if include_rotated else 0

    if newest_first:
        files = [target] + [f"{target}.{i}" for i in range(1, bak_n + 1)]
        collected = []
        for fp in files:
            part = _read_single_file(fp)
            part.reverse()
            collected.extend(part)
            if limit is not None and len(collected) >= int(limit):
                return collected[:int(limit)]
        return collected

    files = [f"{target}.{i}" for i in range(bak_n, 0, -1)] + [target]
    collected = []
    for fp in files:
        collected.extend(_read_single_file(fp))
    if limit is not None:
        return collected[-int(limit):]
    return collected


# ----------------------------------------------------------------------
# Domain recorders (profiles, goals, installers, post-update, BUILD_ID)
# ----------------------------------------------------------------------

def profile_snapshot(profile, *, max_fans_active=None):
    """Scrubbed hardware snapshot of a profile dict (omits raw shell hook commands)."""
    if not isinstance(profile, dict):
        return None
    hooks = profile.get("hooks") if isinstance(profile.get("hooks"), dict) else {}
    snap = {
        "name": profile.get("name"),
        "base": profile.get("base"),
        "tdp": dict(profile["tdp"]) if isinstance(profile.get("tdp"), dict) else None,
        "fan_curve": [list(pt) for pt in profile["fan_curve"]] if profile.get("fan_curve") else None,
        "max_fans": bool(profile.get("max_fans", False)),
        "undervolt": profile.get("undervolt"),
        "epp": profile.get("epp"),
        "brightness": profile.get("brightness"),
        "lighting": {k: dict(v) for k, v in (profile.get("lighting") or {}).items() if isinstance(v, dict)} or None,
        "battery_limit": profile.get("battery_limit"),
        "has_hooks": bool((hooks.get("enter") or "").strip() or (hooks.get("exit") or "").strip()),
    }
    if max_fans_active is not None:
        snap["max_fans_active"] = bool(max_fans_active)
    return snap


def _scrub_step_message(step_name, ok, message):
    """A hook step's message can carry the hook command or its output; store only fixed text."""
    if str(step_name) in ("enter hook", "exit hook") or str(message).startswith("ran:"):
        return "hook ran" if ok else "hook failed"
    return str(message)


def record_profile_apply(*, profile, previous=None, results=(),
                         before_max_fans=None, after_max_fans=None, path=None, defer=False):
    """Record a `profiles.apply()` transition.

    Passes `journal=False` on purpose: Principle 7's journal line covers only
    host changes (`/etc`, z13ctl binary, unit files). A profile apply changes
    hardware state through z13ctl and is already logged by its own steps, so it
    gets a timeline entry but no extra journal line.
    """
    prev_snap = profile_snapshot(previous, max_fans_active=before_max_fans)
    cur_snap = profile_snapshot(profile, max_fans_active=after_max_fans)
    prev_name = prev_snap.get("name") if prev_snap else None
    cur_name = cur_snap.get("name") if cur_snap else "?"
    switching = bool(prev_name and prev_name != cur_name)
    ok = bool(results) and all(bool(step_ok) for _, step_ok, _ in results)
    changed = [str(step) for step, step_ok, _ in results if step_ok]
    steps = [{"step": str(s), "ok": bool(step_ok),
              "message": _scrub_step_message(s, step_ok, msg)} for s, step_ok, msg in results]

    if switching:
        summary = f"Profile {prev_name} -> {cur_name}"
        rollback = {
            "kind": "profile",
            "profile": prev_name,
            "snapshot": prev_snap,
            "max_fans": before_max_fans,
        }
        reason = None
    else:
        summary = f"Profile {cur_name} applied"
        rollback = None
        reason = ("Re-applied the already active profile"
                  if prev_name == cur_name and cur_name else "No previous profile recorded")

    if not ok:
        summary += " (failed)"

    return _submit(
        {
            "kind": "profile",
            "summary": summary,
            "ok": ok,
            "before": prev_snap if prev_snap is not None else {"profile": None, "max_fans_active": before_max_fans},
            "after": cur_snap if cur_snap is not None else {"profile": None, "max_fans_active": after_max_fans},
            "changed": changed,
            "steps": steps,
            "rollback": rollback,
            "no_rollback_reason": reason,
        },
        path=path,
        defer=defer,
    )


def record_goal_change(*, goal, previous_goal=None, before=None, after=None,
                       profile=None, ok=True, summary=None, path=None, defer=False):
    """Record a goal change (today's Max Fans toggle, and Phase 15's goal runner)."""
    before_d = dict(before) if isinstance(before, dict) else {"goal": previous_goal}
    after_d = dict(after) if isinstance(after, dict) else {"goal": goal}
    before_d.setdefault("goal", previous_goal)
    after_d.setdefault("goal", goal)
    if profile:
        before_d.setdefault("profile", profile)
        after_d.setdefault("profile", profile)

    # Derive from the actual before/after state, not the requested goal.
    previous_goal = before_d.get("goal")
    goal = after_d.get("goal")
    changed = []
    if previous_goal != goal:
        changed.append("goal")
    if before_d.get("max_fans") != after_d.get("max_fans"):
        changed.append("max_fans")

    if previous_goal and previous_goal != goal:
        rb = {"kind": "goal", "goal": previous_goal, "state": before_d}
        reason = None
        default_summary = f"Goal {previous_goal} -> {goal}"
    else:
        rb = None
        reason = "Goal unchanged" if previous_goal == goal and goal else "No previous goal recorded"
        default_summary = f"Goal {goal}"
    if not ok:
        default_summary += " (failed)"

    return _submit(
        {
            "kind": "goal",
            "summary": summary or default_summary,
            "ok": bool(ok),
            "before": before_d,
            "after": after_d,
            "changed": changed,
            "rollback": rb,
            "no_rollback_reason": reason,
        },
        path=path,
        defer=defer,
    )


def record_setting_change(*, kind, summary, before, after, ok=True, path=None, defer=False):
    """Record a manual hardware setting write (TDP, battery limit, brightness).

    A failed write (`ok=False`) records the unchanged state as `after`
    (`before == after`) and a `(failed)` summary. A changed key that `before`
    does not know at all is not reversible. Writes `journal=False`: hardware
    settings are not Principle 7 host changes.
    """
    if kind not in SETTING_KINDS:
        raise ValueError(f"unknown setting kind {kind!r}; expected one of: {', '.join(SETTING_KINDS)}")
    before_d = dict(before) if isinstance(before, dict) else {}
    after_d = dict(after) if isinstance(after, dict) else {}
    if not ok:
        after_d = dict(before_d)   # a failed write leaves the setting as it was
    changed = sorted(k for k in set(before_d) | set(after_d) if before_d.get(k) != after_d.get(k))
    if not changed:
        rb = None
        reason = "Value unchanged"
    else:
        # Callers pass None for "could not read the previous value", so a
        # missing or None previous value cannot be restored, with one
        # exception: a TDP of None is a real previous state (no override on
        # the profile), so putting it back is a valid reset.
        known = [k for k in changed
                 if before_d.get(k) is not None or (kind == "tdp" and k in before_d)]
        if known:
            rb = {"kind": kind, "state": {k: before_d[k] for k in known}}
            reason = None
        else:
            rb = None
            reason = "Previous value not known"
    if not ok and not summary.endswith("(failed)"):
        summary += " (failed)"
    return _submit(
        {
            "kind": kind,
            "summary": summary,
            "ok": bool(ok),
            "before": before_d,
            "after": after_d,
            "changed": changed,
            "rollback": rb,
            "no_rollback_reason": reason,
        },
        path=path,
        defer=defer,
    )


def max_fans_goal_states(before_mf, after_mf, target_on, ok=True):
    """Max Fans toggle before/after states for `record_goal_change`.

    `before_mf` / `after_mf` are `max-fans.json` state dicts (or None). A failed
    toggle (`ok=False`) records the state as it was before, so `before == after`
    even if `saved_curve` drifted on disk before the write failed.
    """
    before_mf = dict(before_mf) if isinstance(before_mf, dict) else {}
    after_mf = dict(after_mf) if isinstance(after_mf, dict) else {}
    before_on = bool(before_mf.get("enabled"))
    before_d = {
        "goal": "max" if before_on else "custom",
        "max_fans": before_on,
        "saved_curve": before_mf.get("saved_curve"),
    }
    if not ok:
        return before_d, dict(before_d)
    after_d = {
        "goal": "max" if target_on else "custom",
        "max_fans": bool(after_mf.get("enabled")),
        "saved_curve": after_mf.get("saved_curve"),
    }
    return before_d, after_d


def collect_backup_tree(backup_dir):
    """Inspect an `install.sh` backup folder (`$BACKUP`), returning `(files_map, archives)`.

    `install.sh` copies `/etc/...` and `/home/...` to `$BACKUP/<abs-path>`, and
    packs Decky plugin backups as `$BACKUP/*.tar`.
    """
    files = {}
    archives = []
    if not backup_dir or not os.path.isdir(backup_dir) or os.path.islink(backup_dir):
        return files, archives
    root = os.path.abspath(backup_dir)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for fname in sorted(filenames):
            full = os.path.join(dirpath, fname)
            if os.path.islink(full):
                continue
            rel = os.path.relpath(full, root)
            if os.path.dirname(rel) == "" and fname.endswith(".tar"):
                archives.append(full)
            else:
                files["/" + rel.replace(os.sep, "/")] = full
    return files, archives


def record_install(*, mode="install", target="host", before=None, after=None,
                   changed=(), failed=(), backup_dir=None, backed_up_files=None,
                   ok=True, rollback=None, no_rollback_reason=None, summary=None,
                   journal_tag="z13-install", journal_fn=None, path=None):
    """Record an `install.sh` or `installers.py` run and emit Principle 7's journal line."""
    files, archives = collect_backup_tree(backup_dir)
    if isinstance(backed_up_files, dict):
        for orig, bak in backed_up_files.items():
            if orig and bak:
                files[str(orig)] = str(bak)

    rb = rollback
    reason = no_rollback_reason
    if rb is None and (files or archives):
        rb = {"kind": "backup_restore", "files": files, "reboot_needed": False}
        if archives:
            rb["archives"] = archives
        if backup_dir:
            rb["backup_dir"] = str(backup_dir)
    if rb is None and reason is None:
        if not ok and any("restored" in str(c) for c in changed):
            reason = "Installer already restored the previous binary after failure"
        elif not changed and not failed:
            reason = "No host files were modified"
        else:
            reason = "No pre-existing host files were backed up"

    changed_list = [str(x) for x in (changed or [])]
    failed_list = [str(x) for x in (failed or [])]
    if not summary:
        verb = "Repair" if mode == "repair" else "Install"
        label = target if target != "host" else f"host ({mode})"
        if failed_list:
            summary = f"{verb} {label}: {len(failed_list)} failed"
        elif changed_list:
            summary = f"{verb} {label}: {len(changed_list)} change(s)"
        else:
            summary = f"{verb} {label}: nothing needed changing"

    entry = {
        "kind": "install",
        "mode": str(mode),
        "target": str(target),
        "summary": summary,
        "ok": bool(ok) and not failed_list,
        "before": dict(before) if isinstance(before, dict) else {},
        "after": dict(after) if isinstance(after, dict) else {},
        "changed": changed_list,
        "rollback": rb,
        "no_rollback_reason": reason,
    }
    if failed_list:
        entry["failed"] = failed_list
    return append(entry, path=path, journal=True, journal_tag=journal_tag, journal_fn=journal_fn)


def record_post_update(*, recipes, results, backups=None, before_checks=None,
                       after_checks=None, backup_dir=None, summary=None,
                       journal_tag="z13-post-update", journal_fn=None, path=None):
    """Record a Phase 12c post-update repair run with its file-backup rollback record."""
    res_list = [(str(rid), str(status)) for rid, status in results]
    done_ids = [rid for rid, status in res_list if status == "done"]
    failed_ids = [rid for rid, status in res_list if status == "failed"]
    ok = not failed_ids

    bak_map = {str(k): str(v) for k, v in (backups or {}).items() if k and v}
    recipe_by_id = {getattr(r, "id", ""): r for r in (recipes or ())}
    backed_recipes = []
    unbacked_done = []
    reboot_needed = False
    for rid in done_ids + failed_ids:
        r = recipe_by_id.get(rid)
        replaces = getattr(r, "replaces", ()) if r is not None else ()
        if any(p in bak_map for p in replaces):
            backed_recipes.append(rid)
            if getattr(r, "reboot", False):
                reboot_needed = True
        elif rid in done_ids:
            unbacked_done.append(rid)

    if bak_map:
        rb = {
            "kind": "post_update_backup",
            "files": bak_map,
            "recipes": backed_recipes,
            "reboot_needed": reboot_needed,
        }
        if unbacked_done:
            rb["unbacked_recipes"] = unbacked_done
        if backup_dir:
            rb["backup_dir"] = str(backup_dir)
        reason = None
    else:
        rb = None
        reason = ("No tasks ran" if not done_ids and not failed_ids
                  else "Applied tasks did not replace host files with backups")

    if not summary:
        if failed_ids:
            summary = f"Post-update repair: {len(done_ids)} done, {len(failed_ids)} failed ({', '.join(failed_ids)})"
        elif done_ids:
            summary = f"Post-update repair: {', '.join(done_ids)}"
        else:
            summary = "Post-update repair: no tasks needed"

    return append(
        {
            "kind": "post_update",
            "summary": summary,
            "ok": ok,
            "before": {"checks": dict(before_checks or {})},
            "after": {
                "results": {rid: st for rid, st in res_list},
                "checks": dict(after_checks or {}),
            },
            "changed": done_ids,
            "rollback": rb,
            "no_rollback_reason": reason,
        },
        path=path,
        journal=True,
        journal_tag=journal_tag,
        journal_fn=journal_fn,
    )


def record_steamos_update(*, before_build_id, after_build_id, before=None, after=None,
                          changed=(), summary=None, journal_tag="z13-steamos-update",
                          journal_fn=None, path=None, deduplicate=True):
    """Record a SteamOS `BUILD_ID` transition as a single timeline entry."""
    prev_id = (before_build_id or "").strip()
    cur_id = (after_build_id or "").strip()
    if not prev_id or not cur_id or prev_id == cur_id:
        return None

    before_d = {"build_id": prev_id}
    if isinstance(before, dict):
        before_d.update(before)
        before_d["build_id"] = prev_id
    after_d = {"build_id": cur_id}
    if isinstance(after, dict):
        after_d.update(after)
        after_d["build_id"] = cur_id

    changed_list = [str(x) for x in (changed or [])]
    if not changed_list:
        changed_list.append(f"BUILD_ID {prev_id} -> {cur_id}")
        for key in sorted(set(before_d) | set(after_d)):
            if key != "build_id" and before_d.get(key) != after_d.get(key):
                changed_list.append(f"{key}: {before_d.get(key)} -> {after_d.get(key)}")

    entry = {
        "kind": "steamos_update",
        "summary": summary or f"SteamOS updated ({prev_id} -> {cur_id})",
        "ok": True,
        "before": before_d,
        "after": after_d,
        "changed": changed_list,
        "rollback": None,
        "no_rollback_reason": (
            "SteamOS OS updates switch A/B image slots; choose the previous "
            "slot in the SteamOS boot menu to roll back"
        ),
    }

    if not logging_enabled():
        # "logging": false means no files and no folders: the journal line (not
        # a file) still goes out, but no lock is taken and nothing touches disk.
        rec = _normalize_entry(entry)
        _emit_journal(rec, True, journal_tag, journal_fn)
        return rec

    target = path or TIMELINE_PATH
    with timeline_lock(target):
        if deduplicate:
            for existing in read_entries(target, newest_first=True):
                if not isinstance(existing, dict) or existing.get("kind") != "steamos_update":
                    continue
                # A usable record needs dict `before`/`after`; skip corrupt shapes.
                if not isinstance(existing.get("before"), dict) or not isinstance(existing.get("after"), dict):
                    continue
                if existing["after"].get("build_id") == cur_id:
                    return existing
                break

        rec = append(
            entry,
            path=target,
            journal=True,
            journal_tag=journal_tag,
            journal_fn=journal_fn,
            _journal_now=False,
        )

    # The journal line goes out after the lock is released: a slow `logger`
    # must not make other writers time out.
    _emit_journal(rec, True, journal_tag, journal_fn)
    return rec


# ----------------------------------------------------------------------
# CLI (`python3 src/timeline.py read [--limit N] [--json]`)
# ----------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="z13-timeline", description="Read the Z13-Helper machine timeline.")
    sub = ap.add_subparsers(dest="cmd")
    rd = sub.add_parser("read", help="list recorded timeline entries (newest first)")
    rd.add_argument("--limit", type=int, default=20)
    rd.add_argument("--path", default=TIMELINE_PATH)
    rd.add_argument("--json", action="store_true")
    args = ap.parse_args(argv if argv is not None else sys.argv[1:])

    cmd = args.cmd or "read"
    if cmd == "read":
        limit = getattr(args, "limit", 20)
        path = getattr(args, "path", TIMELINE_PATH)
        as_json = getattr(args, "json", False)
        entries = read_entries(path, limit=limit, newest_first=True)
        if as_json:
            print(json.dumps(entries, indent=2))
        else:
            for e in entries:
                mark = "ok  " if e.get("ok", True) else "FAIL"
                rb = "reversible" if e.get("rollback") else f"no-rollback: {e.get('no_rollback_reason')}"
                print(f"{e.get('ts', '?')}  {mark}  {e.get('kind', '?'):<14}  {e.get('summary', '')}  [{rb}]")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())

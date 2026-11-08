"""Z13-Helper — Decky Loader backend.

Runs as the deck user: Decky Loader only runs a backend as root for the flag "root",
and plugin.json says "_root" (checked live 2026-10-15). Every command goes through the
shared host seam (host.py), which works either way (it adds `runuser -u deck` only under
root), so the flag can change without code changes. Shells to the host z13ctl / boondock
CLIs — the same hardware backend the desktop PyQt app uses — so Game Mode and Desktop
Mode share one contract.
"""

import asyncio
import contextlib
import errno
import fcntl
import json
import os
import re
import secrets
import stat
import sys
import threading
import time

import decky  # modern API (NOT decky_plugin)

# Shared with the desktop app: install.sh copies these from src/ into this plugin's
# py_modules/ as root-owned files (docs/adr/0001-decky-imports-root-owned-copies.md).
# The loader puts py_modules on sys.path too; adding it here keeps that explicit.
_PY_MODULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py_modules")
if os.path.isdir(_PY_MODULES) and _PY_MODULES not in sys.path:
    sys.path.insert(0, _PY_MODULES)
import crash_report
import host
import state_authority as authority
from state_stream import StateStream
import state_events
import power_range
import timeline
from fan_watchdog import FanTrace, FanWatchdog
from sensor_reader import SensorReader

# This process is always a Decky backend. host.detect() spots that from the loader's
# env, but the deck env it implies (the host's LD_LIBRARY_PATH above all) is too
# important to leave to a guess: without it every systemctl call fails.
if host.MODE == "host":
    host.MODE = "decky"

Z13CTL = os.path.expanduser("~/.local/bin/z13ctl")
# The app's profile engine (src/profiles.py installed as ~/.local/bin/z13-profile).
# Always run as deck (as_deck=True) so the store stays deck-owned and hooks (boondock)
# behave as on the desktop.
Z13_PROFILE = ["/home/deck/.local/bin/z13-profile"]
# The firmware base profiles the QAM switch accepts (src/profiles.py BASES), and
# the built-in app profile that carries each one through the profile engine.
PROFILE_BASES = {"quiet": "Silent", "balanced": "Balanced", "performance": "Turbo"}
BOONDOCK = os.path.expanduser("~/.local/bin/boondock")
SHARED_CONFIG = os.path.expanduser("~/.config/z13-helper/config.json")
# Max Fans state shared with the desktop app (mirror of src/max_fans_state.py):
# {"enabled": bool, "saved_curve": [[temp, pwm], ...]}. Re-read, never cached,
# so a toggle in either frontend is seen by the other.
MAX_FANS_STATE = os.path.expanduser("~/.config/z13-helper/max-fans.json")

# Children get the deck env from host.run (deck's runtime dir and session bus, so HID
# commands use the z13ctl daemon socket; the host's LD_LIBRARY_PATH, not the loader's).

# Max Fans = 8 monotonic temp anchors, all at 100%.
MAX_FAN_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)   # Low anchor; firmware response still needs verification.
MAX_FANS_SPEC = ",".join(f"{t}:100%" for t in MAX_FAN_TEMPS)
# Fan watchdog poll cadence (its thresholds live in fan_watchdog.py).
ENFORCE_INTERVAL_S = 2
# Its trace, next to the desktop app's (Help -> Error log -> Fan trace shows both).
# Written as root, handed to deck so the app and deck can read and rotate it.
FAN_TRACE_DIR = "/home/deck/.local/state/z13-helper"
FAN_TRACE = os.path.join(FAN_TRACE_DIR, "fan-trace-gamemode.log")
TIMELINE_PATH = os.path.join(FAN_TRACE_DIR, "timeline.jsonl")
OS_RELEASE_PATH = "/etc/os-release"
DECK_IDS = (1000, 1000)
# The user's logging choice (mirror of src/install_record.py logging_enabled()):
# install.sh asks, the desktop app's Help -> Error log changes it. "logging": false
# = no fan trace file; missing = on. Read once, when the watchdog starts.
INSTALL_JSON = os.path.expanduser("~/.config/z13-helper/install.json")
timeline.INSTALL_JSON = INSTALL_JSON   # the timeline honours the same logging choice

# Desktop app check: in Desktop Mode the resident desktop app should be running (it
# owns the side button there). Its single-instance socket (src/single_instance.py)
# answers when it is; a deliberate Quit leaves the user-quit marker next to it.
DESKTOP_APP_UNIT = "z13-helper.service"
DESKTOP_APP_DIR = "/run/user/1000/app/io.github.iannitram.Z13Helper"
DESKTOP_APP_SOCKET = os.path.join(DESKTOP_APP_DIR, "z13-helper.sock")
DESKTOP_APP_USER_QUIT = os.path.join(DESKTOP_APP_DIR, "z13-helper.user-quit")
DESKTOP_CHECK_S = 30
DESKTOP_MISSES = 2   # consecutive failed checks (~1 min) before a restart: covers the
                     # unit's 10 s tray wait and a takeover from a session-restored copy

# ---- Phase 11 (Decky parity): TDP / battery / brightness / telemetry ----
# The app profile store (schema owned by src/profiles.py; never change it from here).
PROFILES_STORE = os.path.expanduser("~/.config/z13-helper/profiles.json")
DECK_UID = DECK_GID = 1000
# Power limits, same bounds as the desktop Fans + Power window (src/fans_window.py):
# 5..93 W (G-Helper's IsZ1325 cap); z13ctl wants --force for PL1 above 75 W.
PL_MIN, PL_MAX = 5, 93
PL1_SAFE_MAX = 75
# Shown when the active profile has no TDP override. Deliberately NOT read live:
# `z13ctl tdp --get` may go through the asus-armoury ppt_* files, which can hard-lock
# the EC (ROADMAP principle 5). Same fallback values the desktop window uses.
DEFAULT_TDP = (70, 86, 86)
BATTERY_MIN, BATTERY_MAX, BATTERY_STEP = 40, 100, 5
BRIGHTNESS_LEVELS = ("off", "low", "medium", "high")
Z13CTL_WRITE_TIMEOUT_S = host.Z13CTL_WRITE_TIMEOUT_S
PROFILE_APPLY_TIMEOUT_S = 180


def post_update_argv(repo, ack=False):
    """Host command for the After SteamOS update tab (copy of src/post_update_view.tab_argv:
    this backend runs as root and can't import src/ modules that are not in py_modules/)."""
    if not isinstance(repo, str) or not os.path.isabs(repo) or "\n" in repo:
        raise ValueError("repo path must be absolute")
    return ["env", f"PYTHONPATH={repo}/tooling:{repo}/src", "python3", "-m", "post_update",
            "--tab-json"] + (["--ack"] if ack else [])


def parse_post_update(out):
    """The tab status dict from the command output, or None when it is not that JSON."""
    start = out.find("{")
    try:
        data = json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and isinstance(data.get("rows"), list) else None


_STEP_ID = re.compile(r"[A-Za-z0-9@:._/-]+\Z")


def post_repair_argv(repo, ids=None):
    """Host command for the repair plan (copy of src/post_update_view.repair_argv): with no ids it
    prints the plan as JSON and changes nothing; with ids it runs exactly those confirmed steps."""
    if not isinstance(repo, str) or not os.path.isabs(repo) or "\n" in repo:
        raise ValueError("repo path must be absolute")
    argv = ["env", f"PYTHONPATH={repo}/tooling:{repo}/src", "python3", "-m", "post_update",
            "repair", "--json"]
    if ids is not None:
        if not isinstance(ids, (list, tuple)) or not ids or not all(
                isinstance(i, str) and _STEP_ID.fullmatch(i) for i in ids):
            raise ValueError("ids must be non-empty step ids")
        argv += ["--run", ",".join(ids)]
    return argv


def parse_json_key(out, key):
    """The JSON object in the output when it has `key`, else None."""
    start = out.find("{")
    try:
        data = json.loads(out[start:]) if start >= 0 else None
    except ValueError:
        return None
    return data if isinstance(data, dict) and key in data else None


def _repo_path():
    try:
        with open(os.path.join(host.DECK_HOME, ".config", "z13-helper", "install.json")) as f:
            repo = json.load(f).get("repo_path")
    except (OSError, ValueError, AttributeError):
        return None
    return repo if isinstance(repo, str) and os.path.isabs(repo) and "\n" not in repo else None


def _run(args, timeout=15, *, as_deck=False):
    """host.run, logged. Returns (rc, out)."""
    rc, out, timed_out = authority.run(args, timeout=timeout, as_deck=as_deck)
    if timed_out and os.path.basename(args[0]) == os.path.basename(Z13CTL):
        out = f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
    # Output can be profile config (hooks may hold tokens or private paths): it goes to
    # DEBUG only. INFO/WARNING carry just the command name, rc and output length.
    name = os.path.basename(args[0])
    decky.logger.info("cmd %s -> rc=%s", name, rc)
    if rc != 0:
        decky.logger.warning("cmd %s failed: rc=%s output_len=%d", name, rc, len(out))
    decky.logger.debug("cmd %s output: %s", name, out)
    return rc, out


_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_dir_chain(d, owner=None):
    """Open directory `d`, creating missing parts, without following any symlink.

    Runs as root over paths the deck user can rewrite, so every component is opened
    with O_NOFOLLOW|O_DIRECTORY relative to its parent's fd. A path under the owner's
    home (from the owner uid's passwd entry) is anchored at that home, which must be
    a real directory owned by the owner; any other path is walked from "/". A missing
    component is made with mkdir(dir_fd) and handed to `owner` by fchown on its fd.
    Raises OSError on a symlink, a non-directory, or a home owned by someone else.
    Returns an open fd the caller must close.
    """
    d = os.path.abspath(d)
    parts = [p for p in d.split(os.sep) if p]
    start = "/"
    anchor_uid = None
    if owner is not None:
        try:
            import pwd
            home = os.path.abspath(pwd.getpwuid(owner[0]).pw_dir)
        except (KeyError, ImportError) as e:
            raise OSError(errno.ENOENT, f"no home directory for uid {owner[0]}") from e
        hp = [p for p in home.split(os.sep) if p]
        if hp and parts[:len(hp)] == hp:
            start, parts, anchor_uid = home, parts[len(hp):], owner[0]
    fd = os.open(start, _DIR_FLAGS)
    try:
        if anchor_uid is not None and os.fstat(fd).st_uid != anchor_uid:
            raise OSError(errno.EPERM, "home directory is not owned by the target user", d)
        for name in parts:
            try:
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
            except FileNotFoundError:
                try:
                    os.mkdir(name, 0o755, dir_fd=fd)
                except FileExistsError:
                    pass
                nfd = os.open(name, _DIR_FLAGS, dir_fd=fd)
                if owner is not None and os.geteuid() == 0:
                    try:
                        os.fchown(nfd, *owner)
                    except BaseException:
                        os.close(nfd)
                        raise
            os.close(fd)
            fd = nfd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _write_in_dir(dfd, name, text, mode=0o644, owner=None):
    """Atomically replace `name` inside the directory open at `dfd`.

    The temp file has a random name and is created O_EXCL|O_NOFOLLOW relative to
    `dfd`; mode and owner are set on its fd; replace and cleanup also go through
    `dfd`. A symlink at `name` is refused. Owner applies only when running as root.
    """
    try:
        if stat.S_ISLNK(os.lstat(name, dir_fd=dfd).st_mode):
            raise OSError(errno.ELOOP, "refusing to write through a symlink", name)
    except FileNotFoundError:
        pass
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    for _ in range(100):
        tmp = f".{name}.{secrets.token_hex(8)}.tmp"
        try:
            fd = os.open(tmp, flags, 0o600, dir_fd=dfd)
            break
        except FileExistsError:
            continue
    else:
        raise OSError(errno.EEXIST, "no free temp name", name)
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fchmod(f.fileno(), mode)
            if owner is not None and os.geteuid() == 0:
                os.fchown(f.fileno(), *owner)
            os.fsync(f.fileno())
        os.replace(tmp, name, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp, dir_fd=dfd)
        raise


def _atomic_write_text(path, text, *, mode=0o644, owner=None):
    """Replace `path` with `text`; every directory on the way is opened no-follow
    (see _open_dir_chain), the file via _write_in_dir. `owner` is (uid, gid) or None."""
    path = os.path.abspath(path)
    dfd = _open_dir_chain(os.path.dirname(path), owner)
    try:
        _write_in_dir(dfd, os.path.basename(path), text, mode, owner)
    finally:
        os.close(dfd)


def _state_path():
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "state.json")


def _load_state():
    try:
        with open(_state_path()) as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _shared_config():
    """Read the desktop app's config.json (legacy Max Fans keys, for migration)."""
    try:
        with open(SHARED_CONFIG) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


# Copy of profiles.file_lock (this backend can't import src/): one stable lock file
# next to each shared store, held around the whole read-modify-write, so the desktop
# app, z13-profile and this backend can't lose each other's updates. The lock file is
# kept deck-owned because we run as root and the other writers run as deck.
_lock_state = threading.local()
_lock_fallback_warned = False
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
            if os.geteuid() == 0:
                os.fchown(fd, DECK_UID, DECK_GID)
            os.fchmod(fd, 0o660)
        elif st.st_uid not in {0, DECK_UID, os.geteuid()}:
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
def _file_lock(path):
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
        os.close(_open_dir_chain(os.path.dirname(path), (DECK_UID, DECK_GID)))
        fd = _open_lock(lock_path)
        _acquire(fd, lock_path)
    except StoreLockError:
        if fd is not None:
            os.close(fd)
        raise
    except OSError as e:
        if fd is not None:
            os.close(fd)
        if e.errno != errno.EROFS:
            # Only a read-only filesystem is safe to write unlocked; EACCES and
            # the like mean another writer may hold a lock we cannot open.
            raise
        global _lock_fallback_warned
        if not _lock_fallback_warned:
            _lock_fallback_warned = True
            decky.logger.warning(
                "Cannot lock %s (%s); continuing without a lock", lock_path, e
            )
        fd = None   # read-only filesystem: nothing else can be writing either
    held[path] = 1
    try:
        yield
    finally:
        held.pop(path, None)
        if fd is not None:
            os.close(fd)   # closing releases the flock


def _load_max_fans():
    """Shared Max Fans state; seeds it from the legacy stores on first use."""
    try:
        with open(MAX_FANS_STATE) as f:
            state = json.load(f)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    # Same precedence as the desktop app's migration: its config.json first,
    # then this plugin's old private state.json.
    with _file_lock(MAX_FANS_STATE):
        try:   # another writer may have seeded it while we waited
            with open(MAX_FANS_STATE) as f:
                state = json.load(f)
            if isinstance(state, dict):
                return state
        except (OSError, ValueError):
            pass
        legacy, old = _shared_config(), _load_state()
        state = {"enabled": bool(legacy.get("fans_max", old.get("max_fans", False)))}
        seeded = legacy.get("fan_curve_saved") or old.get("saved_curve")
        if seeded is not None:   # absent = unknown, null = firmware auto
            state["saved_curve"] = seeded
        _save_max_fans(state)
        return state


def _peek_max_fans():
    """Max Fans state for the timeline: strictly read-only.

    Unlike `_load_max_fans` it never takes the Max Fans lock, never seeds or
    migrates the state file from the legacy stores, and never writes. When the
    file is missing it reports what the seed would be (legacy keys), so the
    first toggle still restores exactly what it would have restored."""
    try:
        with open(MAX_FANS_STATE) as f:
            state = json.load(f)
        if isinstance(state, dict):
            return dict(state)
    except (OSError, ValueError):
        pass
    legacy, old = _shared_config(), _load_state()
    if not isinstance(legacy, dict):
        legacy = {}
    state = {"enabled": bool(legacy.get("fans_max", old.get("max_fans", False)))}
    seeded = legacy.get("fan_curve_saved") or old.get("saved_curve")
    if seeded is not None:
        state["saved_curve"] = seeded
    return state


def _save_max_fans(state):
    """Write the shared state atomically, owned by deck even if we run as root."""
    _atomic_write_text(MAX_FANS_STATE, json.dumps(state), owner=(DECK_UID, DECK_GID))


_sensors = SensorReader()


def _read_int(path):
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def _current_platform_profile():
    """Firmware profile in force, from sysfs. `z13ctl profile --get` says
    "custom" whenever one of z13ctl's own (1.3+) profiles is active."""
    try:
        with open("/sys/firmware/acpi/platform_profile") as f:
            v = f.read().strip().lower()
        if v in ("quiet", "balanced", "performance"):
            return v
    except OSError:
        pass
    _, out = _run([Z13CTL, "profile", "--get"])
    low = out.lower()
    for tok in ("performance", "balanced", "quiet"):
        if tok in low:
            return tok
    return "balanced"


def _in_gamemode():
    """True iff the deck user's Steam gamescope (Game Mode) session is active.

    Same check as z13-button-watcher. In Desktop Mode the resident PyQt app owns
    Max Fans enforcement, so this watchdog stands down there rather than both
    frontends re-applying the same curve.
    """
    return host.run(["systemctl", "--user", "is-active", "--quiet", "gamescope-session.target"],
                    timeout=5, as_deck=True).rc == 0


# ----------------------------------------------------------------------
# Phase 11 helpers (pure where possible; decky/tests/ covers them)
# ----------------------------------------------------------------------

def tdp_ranges():
    """Per-rail bounds from host permissions and kernel range metadata.
    Never opens live PPT current_value nodes (ROADMAP principle 5)."""
    return power_range.detect_ranges()


def clamp_tdp(pl1, pl2, pl3, ranges=None):
    """Clamp each rail to its range (default PL_MIN..PL_MAX) and enforce sPPT >= SPL, fPPT >= sPPT.

    Mirrors the desktop window's slider rule (a lower rail pushes the higher ones
    up; a higher rail can't go below the lower one). Returns a profiles.json `tdp`
    dict. `force` follows the desktop app (pl1 > 75 W) because profiles.validate()
    and z13ctl both require it there; it is False for every SPL <= 75 W.
    """
    r = ranges or {}

    def one(v, k):
        lo, hi = r.get(k, (PL_MIN, PL_MAX))
        return max(lo, min(hi, round(float(v))))
    a = one(pl1, "pl1")
    b = max(one(pl2, "pl2"), a)
    c = max(one(pl3, "pl3"), b)
    return {"pl1": a, "pl2": b, "pl3": c, "force": a > PL1_SAFE_MAX}


def clamp_battery(value):
    """Clamp battery limit to nearest BATTERY_STEP within BATTERY_MIN..BATTERY_MAX range."""
    v = int(round(float(value) / BATTERY_STEP) * BATTERY_STEP)
    return max(BATTERY_MIN, min(BATTERY_MAX, v))


def tdp_argv(tdp):
    """`z13ctl tdp --set` arguments for a profiles.json tdp dict (as src/profiles.py builds them)."""
    argv = [Z13CTL, "tdp", "--set", str(tdp["pl1"]),
            "--pl2", str(tdp["pl2"]), "--pl3", str(tdp["pl3"])]
    if tdp.get("force"):
        argv.append("--force")
    return argv


def _read_store(path=None):
    """profiles.json as a dict, or None if missing / unreadable / not a store."""
    try:
        with open(path or PROFILES_STORE) as f:
            store = json.load(f)
    except (OSError, ValueError):
        return None
    if isinstance(store, dict) and isinstance(store.get("profiles"), list):
        return store
    return None


def _ensure_store():
    """The store, seeded through the engine (as deck) if it doesn't exist yet."""
    store = _read_store()
    if store is None:
        _run(Z13_PROFILE + ["list", "--json"], as_deck=True)
        store = _read_store()
    return store


def active_profile(store):
    """The active profile dict inside `store` (same object), or None."""
    if not store:
        return None
    name = store.get("active")
    for p in store.get("profiles", []):
        if p.get("name") == name:
            return p
    return None


def _live_battery_limit():
    """charge_control_end_threshold from sysfs (a read, never a write), or None."""
    base = "/sys/class/power_supply"
    try:
        names = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
    except OSError:
        return None
    for n in names:
        v = _read_int(os.path.join(base, n, "charge_control_end_threshold"))
        if v is not None:
            return v
    return None


def telemetry():
    """CPU / GPU temps (degC) with their sensor names and both fan RPMs.

    A value that cannot be read is None (shown as unavailable), never 0.
    hwmon nodes are found by `name` (k10temp / acpitz / amdgpu / asus), never
    by index; the GPU is the integrated one, picked by device. Key `apu` keeps
    its name for the plugin bundle; `apu_src` / `gpu_src` say where each came
    from. Reads only hwmon; never the asus-armoury ppt_* files.
    """
    f1, f2 = _sensors.get_fan_speeds()
    cpu = _sensors.get_cpu_temp()
    gpu = _sensors.get_gpu_temp_labeled()
    return {"apu": cpu[0] if cpu else None, "apu_src": cpu[1] if cpu else None,
            "gpu": gpu[0] if gpu else None, "gpu_src": gpu[1] if gpu else None,
            "fan1": f1, "fan2": f2}


def _save_plugin_state(**changes):
    """The plugin's private state.json (remembers the last brightness for the UI)."""
    state = _load_state()
    state.update(changes)
    try:
        _atomic_write_text(_state_path(), json.dumps(state))
    except OSError as e:
        decky.logger.error("state.json write failed: %s", e)


def power_state():
    """What the TDP / battery / brightness controls should show on open."""
    p = active_profile(_ensure_store()) or {}
    tdp = p.get("tdp")
    pl = (tdp["pl1"], tdp["pl2"], tdp["pl3"]) if tdp else DEFAULT_TDP
    ranges = tdp_ranges()
    note = ""
    if tdp:   # show the stored values clamped into the kernel's range; the store is untouched
        st = power_range.tdp_status(tdp, ranges)
        pl = (st["accepted"]["pl1"], st["accepted"]["pl2"], st["accepted"]["pl3"])
        note = st["note"]
    live_bl = _live_battery_limit()
    bl = live_bl if live_bl is not None else p.get("battery_limit")
    # Last level set from this panel, else the desktop app's (config.json, read only),
    # else the active profile's. Only used to highlight a button.
    brightness = (_load_state().get("brightness") or _shared_config().get("brightness")
                  or p.get("brightness"))
    return {
        "active": p.get("name", ""),
        "tdp_enabled": bool(tdp),
        "pl1": int(pl[0]), "pl2": int(pl[1]), "pl3": int(pl[2]),
        "pl_min": min(r[0] for r in ranges.values()), "pl_max": max(r[1] for r in ranges.values()),
        "pl_ranges": {k: list(v) for k, v in ranges.items()}, "tdp_note": note,
        "pl1_safe_max": PL1_SAFE_MAX,
        "battery_limit": clamp_battery(bl) if bl is not None else BATTERY_MAX,
        "brightness": brightness if brightness in BRIGHTNESS_LEVELS else "",
    }


def _extract_z13ctl_timeout(output):
    """Return the per-write timeout, including its profile step when available."""
    prefix = "z13ctl write timed out after "
    for line in output.splitlines():
        index = line.find(prefix)
        if index >= 0:
            message = line[index:].strip()
            # _print_results emits: "  ERR  {step:<22}  {message}".
            row = line[:index]
            marker = row.find("ERR  ")
            if marker >= 0:
                step = row[marker + len("ERR  "):].strip()
                if step:
                    return f"{message} (step: {step})"
            return message
    return ""


class DeckyFans:
    """The fan commands the shared watchdog needs, as z13ctl calls from here.

    Each raises on a non-zero exit, so a failed profile rewrite stops the recovery
    before a bare curve write (the watchdog reports the error).
    """

    def _z13ctl(self, *args, as_deck=False):
        rc, out = _run([Z13CTL, *args], timeout=Z13CTL_WRITE_TIMEOUT_S, as_deck=as_deck)
        if rc != 0:
            if out.startswith("z13ctl write timed out after "):
                raise RuntimeError(out)
            raise RuntimeError(f"z13ctl {' '.join(args)} failed (rc={rc}): {out}")

    def rewrite_profile(self):
        self._z13ctl("profile", "--set", _current_platform_profile())

    # What the firmware profile write above resets (z13ctl: it hands the power
    # limits back to firmware and clears the undervolt). Same commands as
    # src/profiles.py apply_tdp / apply_undervolt, for the active profile. Each
    # returns a problem text, or None; the watchdog reports it and carries on.

    def restore_tdp(self):
        tdp = (active_profile(_read_store()) or {}).get("tdp")
        if not tdp:
            return None
        rc, out = _run(tdp_argv(tdp), timeout=Z13CTL_WRITE_TIMEOUT_S, as_deck=True)
        if rc != 0:
            if out.startswith("z13ctl write timed out after "):
                return out
            return f"TDP not restored ({out[-120:] or f'rc={rc}'})"
        return None  # no readback: reading the PPT values can lock the EC

    def restore_undervolt(self):
        uv = (active_profile(_read_store()) or {}).get("undervolt")
        if uv is None:
            return None
        rc, out = _run([Z13CTL, "undervolt", "--set", str(uv)],
                       timeout=Z13CTL_WRITE_TIMEOUT_S, as_deck=True)
        if rc != 0:
            if out.startswith("z13ctl write timed out after "):
                return out
            return f"undervolt not restored ({out[-120:] or f'rc={rc}'})"
        rc, out = _run([Z13CTL, "undervolt", "--get"], as_deck=True)
        m_ = re.search(r"-?\d+", out) if rc == 0 else None
        if m_ and int(m_.group(0)) != uv:
            return f"undervolt reads {m_.group(0)}, wanted {uv}"
        return None

    def set_fans_max(self):
        self._z13ctl("fancurve", "--set", MAX_FANS_SPEC)

    def set_fancurve_pwm(self, points):
        self._z13ctl("fancurve", "--set", ",".join(f"{int(t)}:{int(p)}" for t, p in points))

    def apply_tdp(self, tdp):
        # As deck, like apply_tdp() (the TDP slider's write).
        self._z13ctl(*tdp_argv(tdp)[1:], as_deck=True)

    def reset_fancurve(self):
        self._z13ctl("fancurve", "--reset")


def _desktop_session_active():
    """True iff the deck user's Plasma (Desktop Mode) session is up."""
    # host.run, not _run: quiet on purpose, this runs every DESKTOP_CHECK_S
    return host.run(["systemctl", "--user", "is-active", "--quiet", "plasma-workspace.target"],
                    timeout=5, as_deck=True).rc == 0


def _desktop_app_answers(path=DESKTOP_APP_SOCKET):
    import socket
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(1.0)
    try:
        s.connect(path)
        s.sendall(b"ping\n")
        return True
    except OSError:
        return False
    finally:
        s.close()


class DesktopAppCheck:
    """Restart the desktop app when it's missing in Desktop Mode.

    One check() per DESKTOP_CHECK_S. Restarts after DESKTOP_MISSES misses in a row,
    never when the user quit the app on purpose, and at most once per miss streak
    until the app answers again (a broken app isn't restarted in a loop).
    """

    def __init__(self, in_desktop=_desktop_session_active, app_up=_desktop_app_answers,
                 user_quit=lambda: os.path.exists(DESKTOP_APP_USER_QUIT), restart=None):
        self.in_desktop, self.app_up, self.user_quit = in_desktop, app_up, user_quit
        self.restart = restart or (lambda: _run(
            ["systemctl", "--user", "restart", DESKTOP_APP_UNIT], timeout=30, as_deck=True)[0] == 0)
        self.misses = 0
        self.restarted = False

    def check(self):
        """Returns what it did: "ok", "away" (not Desktop Mode), "user-quit", "miss",
        "restarted", "restart-failed" or "gave-up"."""
        if not self.in_desktop():
            self.misses = 0
            return "away"
        if self.app_up():
            self.misses, self.restarted = 0, False
            return "ok"
        if self.user_quit():
            self.misses = 0
            return "user-quit"
        self.misses += 1
        if self.misses < DESKTOP_MISSES:
            return "miss"
        if self.restarted:
            return "gave-up"
        self.restarted = True
        ok = self.restart()
        decky.logger.warning("Desktop app wasn't running in Desktop Mode; restarted %s: %s",
                             DESKTOP_APP_UNIT, "ok" if ok else "FAILED")
        return "restarted" if ok else "restart-failed"


def _check_steamos_build_id():
    """Record a single `steamos_update` entry when `/etc/os-release` BUILD_ID moved."""
    try:
        with open(INSTALL_JSON) as f:
            rec = json.load(f)
        if not isinstance(rec, dict):
            return None
    except (OSError, ValueError):
        return None
    prev = rec.get("build_id")
    if not (isinstance(prev, str) and prev.strip() and prev.strip() != "?"):
        m = re.search(r"\(([^()?]+)\)\s*$", str(rec.get("steamos") or "").strip())
        prev = m.group(1).strip() if m else None
    if not prev:
        return None
    try:
        with open(OS_RELEASE_PATH) as f:
            os_text = f.read()
    except OSError:
        return None
    cur = None
    ver = None
    for line in os_text.splitlines():
        if line.startswith("BUILD_ID="):
            val = line[9:].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            cur = val or None
        elif line.startswith("VERSION_ID="):
            val = line[11:].strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                val = val[1:-1]
            ver = val or None
    if not cur or cur == prev:
        return None
    try:
        return timeline.record_steamos_update(
            before_build_id=prev,
            after_build_id=cur,
            before={"steamos": rec.get("steamos"), "kernel": rec.get("kernel")},
            after={"steamos": f"{ver or '?'} ({cur})", "kernel": os.uname().release},
            path=TIMELINE_PATH,
        )
    except Exception as e:
        decky.logger.warning("SteamOS build ID timeline record failed: %s", e)
        return None


def logging_enabled():
    try:
        with open(INSTALL_JSON) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        return True
    return not (isinstance(rec, dict) and rec.get("logging") is False)


def make_fan_trace(path=None, owner=DECK_IDS):
    """The Game Mode watchdog's trace file, or None if logging is off or it
    can't be opened."""
    if not logging_enabled():
        decky.logger.info("Logging is off (install.json); no fan trace file")
        return None
    path = path or FAN_TRACE
    try:
        d = os.path.dirname(path)
        os.close(_open_dir_chain(d, owner))   # no symlink on the way; new dirs go to `owner`
        return FanTrace.to_file(path, _sensors, "gamemode", owner=owner)
    except OSError as e:
        decky.logger.error("No fan trace: %s", e)
        return None


def make_fan_watchdog(sensors=None, fans=None, may_act=None, clock=time.monotonic, trace=None):
    return FanWatchdog(sensors or _sensors, fans or DeckyFans(),
                       max_fans_on=lambda: bool(_load_max_fans().get("enabled", False)),
                       suspended=lambda: _load_max_fans().get("suspended") is not None,
                       may_act=may_act or _in_gamemode,
                       active_tdp=lambda: (active_profile(_read_store()) or {}).get("tdp"),
                       clock=clock, trace=trace)


async def fan_watchdog_tick(watchdog):
    """One watchdog tick under the write lock; skipped while a write holds it.
    Returns the watchdog's message (None when it didn't act or was skipped)."""
    lock = _lock()
    if lock.locked() or not _writes_allowed():
        return None
    try:
        async with lock:
            msg = await asyncio.to_thread(watchdog.tick)
    except Exception as e:  # noqa: BLE001
        decky.logger.error("Fan watchdog error: %s", e)
        return None
    if msg:
        decky.logger.info("Fan watchdog: %s", msg)
    return msg


_write_lock = None


DMI_PRODUCT_NAME = "/sys/class/dmi/id/product_name"
UNSUPPORTED_MESSAGE = "Unsupported machine: Z13-Helper controls the ROG Flow Z13 (GZ302) only"


def _writes_allowed():
    """False on any machine that is not the Z13 (same rule as src/platform_support.py,
    which this root-owned backend cannot import). Read-only: DMI product name."""
    try:
        with open(DMI_PRODUCT_NAME, encoding="utf-8", errors="replace") as f:
            return "GZ302" in f.read().upper()
    except OSError:
        return False


def _unsupported():
    return {"ok": False, "message": UNSUPPORTED_MESSAGE}


def _lock():
    """One store write + hardware push at a time (slider releases can queue up)."""
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


# A sampled fail-stop threshold, not a kernel-enforced allocation limit. Check
# independently of the event loop/write lock so a slow hardware call cannot
# postpone it. Assumes (UNVERIFIED here) that Decky Loader runs each plugin
# backend as its own process, so os._exit ends only this plugin; no Decky
# source/version has been checked for that or for any respawn. Needs a device check.
BACKEND_RSS_LIMIT_KIB = 500 * 1024
BACKEND_RSS_CHECK_S = 5


def backend_rss_kib(path="/proc/self/status"):
    """Current resident memory, not peak RSS or virtual address space."""
    with open(path, encoding="ascii") as stream:
        for line in stream:
            fields = line.split()
            if fields[:1] == ["VmRSS:"]:
                if len(fields) != 3 or fields[2] != "kB" or int(fields[1]) < 0:
                    raise ValueError("invalid VmRSS")
                return int(fields[1])
    raise ValueError("missing VmRSS")


class BackendMemoryGuard:
    """One stoppable monitor per backend; no sample history or child processes.

    A trip exits this backend (hard cap) unless Z13_RSS_GUARD_EXIT=0 is set in the
    plugin_loader environment, which makes it log once instead. Exit rather than
    restarting plugin_loader (which would disrupt all plugins).
    Per-plugin process isolation and any respawn are UNVERIFIED assumptions
    (no Decky version/source cited); reloading this plugin after a trip is a
    device/operator recovery step.
    """

    def __init__(self, read_rss=None, terminate=None):
        self._read_rss = read_rss or backend_rss_kib
        self._terminate = terminate or self._default_terminate
        self._tripped = False
        self._stop = threading.Event()
        self._read_failed = False
        self._thread = threading.Thread(target=self._run, name="z13-rss-guard", daemon=True)

    def _default_terminate(self, code):
        """Exit by default; Z13_RSS_GUARD_EXIT=0 (operator opt-out, see
        docs/fan-watchdog-memory.md) logs once and keeps the plugin running."""
        if os.environ.get("Z13_RSS_GUARD_EXIT") != "0":
            os._exit(code)
        if not self._tripped:
            self._tripped = True
            decky.logger.error("Backend RSS over limit; not exiting (Z13_RSS_GUARD_EXIT=0)")

    def start(self):
        self._thread.start()

    def close(self):
        self._stop.set()
        self._thread.join()

    def check(self):
        try:
            rss = self._read_rss()
        except (OSError, ValueError):
            if not self._read_failed:
                decky.logger.warning("Backend RSS unavailable; memory guard will retry")
            self._read_failed = True
            return False
        self._read_failed = False
        if rss < BACKEND_RSS_LIMIT_KIB:
            return False
        # os._exit bypasses cleanup that might hang or allocate more memory.
        # Report to stderr first; do not depend on a logging handler's lock.
        try:
            os.write(2, f"z13-helper backend RSS guard: rss_kib={rss} "
                     f"limit_kib={BACKEND_RSS_LIMIT_KIB}; exiting plugin process\n".encode())
        finally:
            self._terminate(1)
        return True

    def _run(self):
        while not self._stop.is_set():
            if self.check():
                return
            if self._stop.wait(BACKEND_RSS_CHECK_S):
                return


def _edit_field(field, value, expected):
    try:
        result = authority.set_field(field, value, expected)
        if isinstance(result, list) and result and isinstance(result[0], tuple):
            failed = [message for _, ok, message in result if not ok]
            return {"ok": not failed, "saved": True,
                    "message": "; ".join(failed) if failed else "Applied", "failed": failed}
        if isinstance(result, list) and result:
            return {"ok": False, "saved": True, "message": "Failed: " + ", ".join(result), "failed": result}
        return {"ok": True, "saved": True, "message": "Applied", "failed": []}
    except (OSError, RuntimeError, ValueError) as exc:
        return {"ok": False, "saved": False, "message": str(exc), "failed": []}


def _timeline_setting(kind, summary, before, after, ok):
    """Best-effort timeline entry for a manual setting write; never blocks or fails it."""
    try:
        timeline.record_setting_change(
            kind=kind, summary=summary, before=before, after=after,
            ok=ok, path=TIMELINE_PATH, defer=True,
        )
    except Exception as exc:  # noqa: BLE001 - timeline recording is best-effort
        _log_timeline_failure(f"{kind} record", exc)


def _log_timeline_failure(what, exc):
    decky.logger.warning("Timeline %s failed: %s: %s", what, type(exc).__name__, str(exc)[:200])


def _before_snapshot(read):
    """Run a read-only 'before' lookup for the timeline; None if it fails."""
    try:
        return read()
    except Exception:  # noqa: BLE001 - timeline recording is best-effort
        return None


def _set_max_fans_recorded(enabled):
    """authority.set_max_fans plus a best-effort goal-change timeline entry.
    The hardware write is exactly authority.set_max_fans(enabled); reads happen around it."""
    before_mf = _before_snapshot(_peek_max_fans)
    prof_name = _before_snapshot(lambda: (active_profile(_read_store()) or {}).get("name") or None)
    try:
        message = authority.set_max_fans(enabled)
    except Exception:
        try:
            after_mf = _peek_max_fans()
            before, after = timeline.max_fans_goal_states(before_mf or {}, after_mf, enabled, ok=False)
            timeline.record_goal_change(
                goal=after["goal"], previous_goal=before["goal"],
                before=before, after=after, profile=prof_name, ok=False,
                path=TIMELINE_PATH, defer=True,
            )
        except Exception as rec_exc:  # noqa: BLE001 - timeline recording is best-effort
            _log_timeline_failure("goal record", rec_exc)
        raise
    try:
        after_mf = _peek_max_fans()
        before, after = timeline.max_fans_goal_states(before_mf or {}, after_mf, enabled, ok=True)
        timeline.record_goal_change(
            goal=after["goal"], previous_goal=before["goal"],
            before=before, after=after, profile=prof_name, ok=True,
            path=TIMELINE_PATH, defer=True,
        )
    except Exception as rec_exc:  # noqa: BLE001 - timeline recording is best-effort
        _log_timeline_failure("goal record", rec_exc)
    return message


def _edit_field_recorded(field, value, expected):
    """_edit_field; setting changes are recorded by state_authority._push_field."""
    # The layer that pushes the change (state_authority._push_field) records it
    # and counts that on this thread; only when it did not do we record here.
    # Counting handed-over entries (not file lines) stays correct across rotation.
    accepted_before = timeline.accepted_count()

    if field == "tdp":
        def _read():
            v = (active_profile(_read_store()) or {}).get("tdp")
            return dict(v) if isinstance(v, dict) else v
    elif field == "battery_limit":
        def _read():
            v = _live_battery_limit()
            return v if v is not None else (active_profile(_read_store()) or {}).get("battery_limit")
    else:
        def _read():
            return _load_state().get("brightness")
    before_v = _before_snapshot(_read)

    result = _edit_field(field, value, expected)

    if timeline.accepted_count() == accepted_before:
        ok = bool(result.get("ok"))
        if field == "tdp":
            summary = (f"TDP {value['pl1']}/{value['pl2']}/{value['pl3']} W" if value else "TDP off")
        elif field == "battery_limit":
            summary = f"Charge limit {value}%"
        else:
            summary = f"Brightness {value}"
        _timeline_setting(field, summary, {field: before_v}, {field: value}, ok)
    return result


class Plugin:
    # ---- Max Fans (mirrors the desktop app: 100% <-> custom aggressive curve) ----
    async def get_max_fans(self) -> bool:
        return bool(_load_max_fans().get("enabled", False))

    async def set_max_fans(self, enabled: bool) -> dict:
        if not _writes_allowed():
            return _unsupported()
        async with _lock():
            try:
                message = await asyncio.to_thread(_set_max_fans_recorded, bool(enabled))
                return {"ok": True, "message": message}
            except (OSError, RuntimeError, ValueError) as exc:
                return {"ok": False, "message": str(exc)}

    async def _enforce_max_fans(self):
        """Run the shared fan watchdog (src/fan_watchdog.py) in Game Mode.

        In Desktop Mode the resident desktop app ticks its own instance, so this one
        only acts in Game Mode. Each tick holds the write lock, so a recovery can't
        interleave with a profile apply; a tick is skipped while a write holds it.
        """
        watchdog = make_fan_watchdog(trace=await asyncio.to_thread(make_fan_trace))
        while True:
            await asyncio.sleep(ENFORCE_INTERVAL_S)
            await fan_watchdog_tick(watchdog)

    async def _watch_desktop_app(self):
        """Keep the desktop app running in Desktop Mode (DesktopAppCheck)."""
        check = DesktopAppCheck()
        while True:
            await asyncio.sleep(DESKTOP_CHECK_S)
            try:
                await asyncio.to_thread(check.check)
            except Exception as e:  # noqa: BLE001
                decky.logger.error("Desktop app check error: %s", e)

    # ---- After SteamOS update (tooling/post_update, run as deck; the repair needs a confirmed run) ----
    async def post_update_status(self, ack: bool = False) -> dict:
        """{"ok", "message", "status"}; status is the --tab-json dict (None when unavailable)."""
        repo = _repo_path()
        if not repo:
            return {"ok": False, "message": "Run ./install.sh once so the plugin knows the repo.",
                    "status": None}
        rc, out = await asyncio.to_thread(
            lambda: _run(post_update_argv(repo, bool(ack)), 60, as_deck=True))
        status = parse_post_update(out) if rc == 0 else None
        return {"ok": status is not None,
                "message": "" if status is not None else "Could not read the post-update status.",
                "status": status}

    async def post_update_repair_plan(self) -> dict:
        """{"ok", "message", "plan"}: the repair plan (steps with exact commands, root flags,
        undo notes). Changes nothing; the Deck UI shows it and asks before the run."""
        repo = _repo_path()
        if not repo:
            return {"ok": False, "message": "Run ./install.sh once so the plugin knows the repo.",
                    "plan": None}
        rc, out = await asyncio.to_thread(
            lambda: _run(post_repair_argv(repo), 120, as_deck=True))
        plan = parse_json_key(out, "steps") if rc == 0 else None
        return {"ok": plan is not None,
                "message": "" if plan is not None else "Could not build the repair plan.",
                "plan": plan}

    async def post_update_repair_run(self, ids: list) -> dict:
        """{"ok", "message", "result"}: run exactly the step ids the user confirmed (root steps go
        through sudo -n as deck; nothing escalates silently). Each step is verified, rolled back on
        failure and written to the rollback record."""
        repo = _repo_path()
        argv = None
        if repo and isinstance(ids, (list, tuple)):   # never fall back to the plan call
            try:
                argv = post_repair_argv(repo, list(ids))
            except ValueError:
                argv = None
        if argv is None:
            return {"ok": False, "message": "No repo or no valid steps to run.", "result": None}
        rc, out = await asyncio.to_thread(lambda: _run(argv, 900, as_deck=True))
        result = parse_json_key(out, "results")
        # rc 1 = a step did not apply; the JSON still says which and why
        return {"ok": result is not None and rc in (0, 1),
                "message": "" if result is not None else "The repair did not report back.",
                "result": result}

    # ---- App profiles (profiles.json, shared with the desktop app) ----
    async def get_profiles(self) -> dict:
        """{"names": [...], "active": name}. Empty names if the engine is missing."""
        rc, out = await asyncio.to_thread(
            lambda: _run(Z13_PROFILE + ["list", "--json"], as_deck=True))
        if rc != 0:
            return {"names": [], "active": ""}
        try:
            store = json.loads(out[out.index("{"):])
            return {"names": [p["name"] for p in store.get("profiles", [])],
                    "active": store.get("active", "")}
        except (ValueError, KeyError):
            return {"names": [], "active": ""}

    async def set_app_profile(self, name: str) -> dict:
        if not _writes_allowed():
            return _unsupported()
        # A full apply takes seconds (longer with a wedged lightbar): keep it off the
        # loader's event loop, and serialised with the other writes.
        async with _lock():
            rc, out = await asyncio.to_thread(
                lambda: _run(Z13_PROFILE + ["apply", name], PROFILE_APPLY_TIMEOUT_S, as_deck=True))
        ok = rc == 0 and "ERR  profile" not in out
        timeout_message = _extract_z13ctl_timeout(out)
        message = (f"{name} applied" if ok else timeout_message or
                   f"{name}: profile apply failed ({out[-160:] or f'rc={rc}'})")
        decky.logger.info("profile apply %s -> rc=%s", name, rc)
        return {"ok": ok, "message": message}

    # ---- Performance profile ----
    async def get_profile(self) -> str:
        return await asyncio.to_thread(_current_platform_profile)

    async def set_profile(self, profile: str) -> dict:
        """Switch the firmware base through the profile engine.

        The QAM sends a base name. Applying the matching built-in app profile
        re-applies TDP, curve, undervolt and EPP, so the hardware and the
        active profile cannot drift apart the way a bare `profile --set` let
        them. Unknown names never reach z13ctl.
        """
        if not _writes_allowed():
            return _unsupported()
        name = PROFILE_BASES.get(profile)
        if name is None:
            return {"ok": False, "message": f"unknown profile {profile!r}"}
        async with _lock():
            rc, out = await asyncio.to_thread(
                lambda: _run(
                    Z13_PROFILE + ["apply", name],
                    PROFILE_APPLY_TIMEOUT_S,
                    as_deck=True,
                )
            )
        ok = rc == 0 and "ERR  profile" not in out
        timeout_message = _extract_z13ctl_timeout(out)
        message = (
            f"{profile} applied"
            if ok
            else timeout_message
            or f"{profile}: profile apply failed ({out[-160:] or f'rc={rc}'})"
        )
        decky.logger.info("profile apply %s -> rc=%s", profile, rc)
        return {"ok": ok, "message": message}

    # ---- Power limits / battery / lighting / telemetry (Phase 11) ----
    # Blocking work runs in a thread so the fan watchdog keeps ticking meanwhile.
    async def get_power_state(self) -> dict:
        result = await asyncio.to_thread(power_state)
        state = getattr(self, '_backend_state', {})
        level = state_events.lighting_level(state)
        if isinstance(level, int) and 0 <= level <= 3:
            result['brightness'] = BRIGHTNESS_LEVELS[level]
        if isinstance(state.get('battery_limit'), int):
            result['battery_limit'] = state['battery_limit']
        return result

    # `profile` = the profile the user was editing; omitted (older frontend) = the active one.
    async def set_tdp(self, enabled: bool, pl1: int, pl2: int, pl3: int,
                      profile: str = "") -> dict:
        if not _writes_allowed():
            return _unsupported()
        async with _lock():
            tdp = clamp_tdp(pl1, pl2, pl3, tdp_ranges()) if enabled else None
            result = await asyncio.to_thread(_edit_field_recorded, 'tdp', tdp, profile)
            result['tdp'] = tdp
            return result

    async def set_battery_limit(self, value: int, profile: str = "") -> dict:
        if not _writes_allowed():
            return _unsupported()
        async with _lock():
            value = clamp_battery(value)
            result = await asyncio.to_thread(_edit_field_recorded, 'battery_limit', value, profile)
            result['value'] = value
            return result

    async def set_brightness(self, level: str) -> dict:
        if not _writes_allowed():
            return _unsupported()
        async with _lock():
            if level not in BRIGHTNESS_LEVELS:
                return {"ok": False, "message": "bad brightness level", "failed": []}
            res = await asyncio.to_thread(_edit_field_recorded, 'brightness', level, None)
        if res["ok"]:
            _save_plugin_state(brightness=level)
        return res

    async def get_telemetry(self) -> dict:
        return telemetry()   # a handful of sysfs reads; cheap enough inline

    # ---- Machine timeline (Phase 16f, #134) ----
    async def get_timeline(self, limit: int = 50) -> list:
        cap = max(0, int(limit))
        return await asyncio.to_thread(
            lambda: timeline.read_entries(TIMELINE_PATH, limit=cap, newest_first=True)
        )

    # ---- Lifecycle ----
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self._memory_guard = BackendMemoryGuard()
        self._memory_guard.start()
        self._crash_reporter = crash_report.install(
            config_path=INSTALL_JSON, logger=decky.logger, loop=self.loop,
            fatal_name="fatal-decky.log",
        )
        # Off the startup path: a slow or contended timeline must not delay the watchdog.
        self._steamos_task = self.loop.create_task(asyncio.to_thread(_check_steamos_build_id))
        self._steamos_task.add_done_callback(
            lambda t: t.exception() if not t.cancelled() else None)
        def on_state(state, event):
            if state_events.event_action(event, False) == "refresh":
                self._backend_state = state or {}
                future = asyncio.run_coroutine_threadsafe(
                    decky.emit("profile_state", {"event": event, "state": state}), self.loop)
                # Consume errors when unloading races the final notification.
                future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        self._state_stream = StateStream(on_state, command=Z13_PROFILE + ["watch"]).start()
        self._enforce_task = self.loop.create_task(self._enforce_max_fans())
        self._desktop_task = self.loop.create_task(self._watch_desktop_app())
        if self._crash_reporter:
            for task in (self._enforce_task, self._desktop_task):
                task.add_done_callback(self._crash_reporter.task_done)
        decky.logger.info("z13-helper up. z13ctl=%s exists=%s max_fans=%s",
                          Z13CTL, os.path.exists(Z13CTL),
                          _load_max_fans().get("enabled"))

    async def _unload(self):
        if getattr(self, "_memory_guard", None):
            self._memory_guard.close()
        if getattr(self, "_state_stream", None):
            await asyncio.to_thread(self._state_stream.close)
        if getattr(self, "_crash_reporter", None):
            self._crash_reporter.close()
        for task in (getattr(self, "_enforce_task", None), getattr(self, "_desktop_task", None)):
            if task:
                task.cancel()
        decky.logger.info("z13-helper unloading")

    async def _uninstall(self):
        decky.logger.info("z13-helper uninstalled")

#!/usr/bin/env python3
"""Profiles: named bundles of everything the Z13 can be set to at once.

A profile is the unit the user picks from the dropdown on the main window
(the G-Helper "mode" idea): a base platform profile plus optional overrides
for TDP, fan curve, undervolt, keyboard/lightbar brightness and battery
limit, and an optional enter/exit hook (a host command). Boondock is a
profile whose hooks run the boondock script for its non-hardware effects
(screen dim, stopping background services).

Store: ~/.config/z13-helper/profiles.json

    {"version": 1, "active": "Balanced",
     "profiles": [{"name": "Balanced", "builtin": true, "base": "balanced",
                   "tdp": null | {"pl1": W, "pl2": W, "pl3": W, "force": bool},
                   "fan_curve": null | [[temp_c, pct] x 8],
                   "undervolt": null | int (0..-40),
                   "brightness": null | "off"|"low"|"medium"|"high",
                   "battery_limit": null | 40..100,
                   "epp": null | "power"|"balance_power"|"balance_performance"|"performance",
                   "hooks": {"enter": "", "exit": ""}}, ...]}

null means "leave that subsystem to the firmware / don't touch it".

This file is stdlib-only and needs nothing but host.py (the host seam) on
purpose: the desktop app imports it inside the Flatpak, a copy runs on the host
as the `z13-profile` CLI (what z13-restore-profile calls at login), and the
Decky backend shells to that CLI in Game Mode. One engine, three callers.

CLI:
    z13-profile list [--json]      names (and which is active)
    z13-profile active             the active profile's name
    z13-profile show <name>        the profile as JSON
    z13-profile apply <name>       apply it (prints one line per step)
    z13-profile restore            re-apply the active profile (login/resume)
"""

import argparse
import contextlib
import stat
import errno
import fcntl
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time

logger = logging.getLogger(__name__)

# Installed as ~/.local/bin/z13-profile, the host seam sits in ~/.local/lib/z13-helper/
# (install.sh puts it there). In the Flatpak and the tests it's next to this file.
_HOST_LIB = os.path.expanduser("~/.local/lib/z13-helper")
if (__name__ == "__main__" and os.path.basename(__file__) == "z13-profile"
        and os.path.isdir(_HOST_LIB) and _HOST_LIB not in sys.path):
    sys.path.append(_HOST_LIB)
if __name__ == "__main__":
    sys.modules["profiles"] = sys.modules[__name__]
import host  # noqa: E402
try:
    import timeline  # noqa: E402
except ImportError:
    timeline = None
import state_authority as authority  # noqa: E402
from state_events import lighting_level  # noqa: E402

Z13CTL_WRITE_TIMEOUT_S = host.Z13CTL_WRITE_TIMEOUT_S
# Enter/exit hooks are arbitrary host commands, not z13ctl writes: keep their
# original generous bound instead of the short z13ctl write cap.
HOOK_TIMEOUT_S = 120

STORE_PATH = os.path.expanduser("~/.config/z13-helper/profiles.json")
MAX_FANS_PATH = os.path.expanduser("~/.config/z13-helper/max-fans.json")
# The z13-{max,balanced,cool} scripts and z13-restore-profile keep their own
# notion of "current profile" in this file; we keep it in sync so the old
# restore path still lands on the right profile if it ever runs.
LEGACY_CURRENT_PATH = os.path.expanduser("~/.config/z13-profiles/current-profile")
Z13CTL = os.path.expanduser("~/.local/bin/z13ctl")

BASES = ("quiet", "balanced", "performance")
BRIGHTNESS_LEVELS = ("off", "low", "medium", "high")
LIGHTING_DEVICES = ("keyboard", "lightbar")
LIGHTING_MODES = ("static", "breathe", "cycle", "rainbow", "strobe")
LIGHTING_COLORS = ("red", "orange", "yellow", "green", "cyan", "blue", "purple", "white")
# CPU Boost: amd-pstate energy_performance_preference, display order. Same list as
# src/epp.py (the app's copy); kept here too because z13-profile runs without it.
EPP_CHOICES = ("power", "balance_power", "balance_performance", "performance")
EPP_SYSFS_GLOB = "/sys/devices/system/cpu/cpufreq/policy*/energy_performance_preference"

# Max Fans = every point at 100%. Must match the desktop app and the Decky plugin.
MAX_FAN_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)   # Low anchor; firmware response still needs verification.
MAX_FANS_SPEC = ",".join(f"{t}:100%" for t in MAX_FAN_TEMPS)

# Curves carried over from the z13-max / z13-balanced host scripts (the
# "hardware-longevity" curves; factory auto tops out at 40% @ 76 °C). The
# explicit 20 C anchor keeps the first PWM plateau defined below 40 C; the
# redundant 85 C plateau point is removed to retain z13ctl's eight-point limit.
LEGACY_TURBO_CURVE = [[40, 50], [50, 75], [60, 90], [70, 100],
                      [75, 100], [80, 100], [85, 100], [90, 100]]
LEGACY_BALANCED_CURVE = [[40, 20], [50, 35], [60, 55], [70, 75],
                         [75, 90], [80, 100], [85, 100], [90, 100]]
TURBO_CURVE = [[20, 50], [40, 50], [50, 75], [60, 90],
               [70, 100], [75, 100], [80, 100], [90, 100]]
BALANCED_CURVE = [[20, 20], [40, 20], [50, 35], [60, 55],
                  [70, 75], [75, 90], [80, 100], [90, 100]]

# Built-in names follow G-Helper (Silent / Balanced / Turbo); z13ctl's bases
# are quiet / balanced / performance.
BUILTIN_FOR_BASE = {"quiet": "Silent", "balanced": "Balanced", "performance": "Turbo"}

LEGACY_NAMES = {            # z13-* script name  ->  profile name
    "maximum-throughput": "Turbo",
    "balanced-daily-driver": "Balanced",
    "longevity-cool-stable": "Silent",
}
LEGACY_NAMES_REV = {v: k for k, v in LEGACY_NAMES.items()}

BOONDOCK_BIN = os.path.expanduser("~/.local/bin/boondock")

# Wedged rear lightbar (USB 0b05:18c6): every z13ctl write to it blocks ~5 s and
# fails with "... write /dev/hidraw5: connection timed out". A root oneshot
# (packaging/host/systemd/system/z13-lightbar-reset.{sh,service}) re-authorizes
# the USB device; a polkit rule lets deck start it (and only start it).
#
# The reset makes the lightbar re-enumerate under NEW hidraw nodes, but the
# z13ctl user daemon (socket-activated, holds the HID device) caches the old
# path and keeps writing to the now-dead node ("write /dev/hidrawN: no such
# device") -- seen on the device 2026-10-11. So a reset alone isn't enough:
# the daemon has to be restarted too, which re-applies its saved state itself
# on the way back up. The same stale-handle symptom (ENODEV) can also show up
# without any USB reset (the daemon's cached node going away on its own);
# that case needs only the daemon restart, never the reset unit.
LIGHTBAR_RESET_UNIT = "z13-lightbar-reset.service"
LIGHTBAR_RESET_MIN_GAP_S = 120     # at most one reset per 2 minutes
LIGHTBAR_RESET_SETTLE_S = 3        # the device re-enumerates after the reset
Z13CTL_SERVICE = "z13ctl.service"  # the user daemon that holds the HID device
LIGHTBAR_DAEMON_RESTART_WAIT_S = 2  # wait for the daemon socket before retrying


def _profile(name, base, *, builtin=False, tdp=None, fan_curve=None,
             undervolt=None, brightness=None, battery_limit=None,
             max_fans=False, enter="", exit_=""):
    p = {
        "name": name, "builtin": builtin, "base": base,
        "tdp": tdp, "fan_curve": fan_curve, "undervolt": undervolt,
        "brightness": brightness, "battery_limit": battery_limit,
        # Switching TO this profile turns Max Fans ON; switching away turns it
        # OFF again. A restore of the same profile leaves the user's toggle alone.
        "max_fans": max_fans,
        "hooks": {"enter": enter, "exit": exit_},
    }
    if max_fans:
        p["goal"] = {"kind": "max"}     # max_fans is the back-compat mirror of this goal
    return p


# ----------------------------------------------------------------------
# Goal (Phase 15b): optional {"kind": ..., <target>} on a profile
# ----------------------------------------------------------------------

# kind -> (target key, low, high); Max has no target. A profile without a goal
# keeps today's fixed curve and TDP. `max_fans` stays on the profile as the
# back-compat mirror of goal "max" (older readers, the apply engine).
GOAL_TARGETS = {
    "quiet": ("max_rpm", 1000, 10000),
    "comfort": ("target_c", 25, 60),
    "fps": ("target_fps", 20, 240),
    "max": None,
}


def validate_goal(goal):
    if goal is None:
        return
    kind = goal.get("kind") if isinstance(goal, dict) else None
    if not isinstance(kind, str) or kind not in GOAL_TARGETS:
        raise ValueError(f"goal needs a kind: one of {tuple(GOAL_TARGETS)}")
    target = GOAL_TARGETS[kind]
    if target is None:
        return
    key, low, high = target
    v = goal.get(key)
    if isinstance(v, bool) or not isinstance(v, int) or not low <= v <= high:
        raise ValueError(f"goal {goal['kind']} needs {key} {low}..{high}")


def goal_kind(p):
    """The profile's goal kind, treating a bare max_fans profile as "max"."""
    goal = p.get("goal")
    kind = goal.get("kind") if isinstance(goal, dict) else None
    if isinstance(kind, str) and kind in GOAL_TARGETS:
        return kind
    return "max" if p.get("max_fans") else None


def _sync_goal(p):
    """Keep goal and max_fans consistent: max_fans -> goal max, and back.

    The `goal` key is only ever written when a goal is set; an invalid one
    from a hand-edited file is dropped (the profile keeps its fixed values).
    """
    try:
        validate_goal(p.get("goal"))
    except ValueError as e:
        print(f"z13-profile: ignoring invalid goal on {p.get('name')!r}: {e}", file=sys.stderr)
        p.pop("goal", None)
    if p.get("goal") is None:
        p.pop("goal", None)
        if p.get("max_fans"):
            p["goal"] = {"kind": "max"}
    else:
        p["max_fans"] = p["goal"]["kind"] == "max"


def default_profiles():
    """The built-in set: mirrors the three z13-* host scripts plus Boondock."""
    return [
        _profile("Silent", "quiet", builtin=True, brightness="low", battery_limit=80),
        _profile("Balanced", "balanced", builtin=True, fan_curve=BALANCED_CURVE,
                 brightness="medium", battery_limit=80),
        _profile("Turbo", "performance", builtin=True, fan_curve=TURBO_CURVE,
                 brightness="high", battery_limit=80, max_fans=True),
        # Hardware side of boondock is explicit here; the hooks run only the script's
        # session half (screen dim, background services), never its power writes,
        # which would overwrite this profile's own settings.
        _profile("Boondock", "quiet", builtin=True,
                 tdp={"pl1": 20, "pl2": 30, "pl3": 40, "force": False},
                 enter=f"{BOONDOCK_BIN} session-on", exit_=f"{BOONDOCK_BIN} session-off"),
    ]


# ----------------------------------------------------------------------
# Store
# ----------------------------------------------------------------------

# One stable lock file next to each store, held around a whole read-modify-write
# (never the JSON itself: os.replace swaps its inode). Re-entrant per thread, and
# serialises threads and processes alike (flock is per open file). Copies of this
# helper live in max_fans_state.py and decky/main.py; keep them in step.
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
def file_lock(path, *, strict=False):
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
    except OSError as e:
        if fd is not None:
            os.close(fd)
        if strict or e.errno != errno.EROFS:
            # Only a read-only filesystem is safe to write unlocked; EACCES and
            # the like mean another writer may hold a lock we cannot open.
            raise
        global _lock_fallback_warned
        if not _lock_fallback_warned:
            _lock_fallback_warned = True
            logger.warning(
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


def store_lock():
    """Held around every read-modify-write of profiles.json."""
    return file_lock(STORE_PATH)



def _read_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _atomic_write_text(path, text, *, mode=0o644, owner=None):
    """Replace `path` with `text` without trusting the directory.

    The directory can be writable by the deck user while this runs as root, so the
    temp file gets an unpredictable name and is created with O_EXCL (mkstemp); mode and
    owner are set on the open fd, never by path; a symlink at `path` is refused rather
    than replaced through. The temp file is removed on any failure.
    `owner` is (uid, gid) or None; it applies only when running as root.
    Copy of decky/main.py's helper (z13-profile is a single installed file).
    """
    d = os.path.dirname(path) or "."
    try:
        if stat.S_ISLNK(os.lstat(path).st_mode):
            raise OSError(errno.ELOOP, "refusing to write through a symlink", path)
    except FileNotFoundError:
        pass
    fd, tmp = tempfile.mkstemp(dir=d, prefix=f".{os.path.basename(path)}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fchmod(f.fileno(), mode)
            if owner is not None and os.geteuid() == 0:
                os.fchown(f.fileno(), *owner)
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Keep the files deck-owned when the Decky backend (root) writes them.
    owner = None
    if os.geteuid() == 0:
        try:
            st = os.stat(os.path.dirname(path))
            owner = (st.st_uid, st.st_gid)
        except OSError:
            pass
    _atomic_write_text(path, json.dumps(data, indent=2), owner=owner)


def _legacy_active():
    try:
        with open(LEGACY_CURRENT_PATH) as f:
            return LEGACY_NAMES.get(f.read().strip())
    except OSError:
        return None


def _migrate_boondock_hooks(p):
    """Stores seeded before the session split saved Boondock's hooks as plain
    `boondock on` / `boondock off`, which redo the power hardware after the engine
    set the profile's own. Rewrite exactly those two strings; an edited hook stays."""
    if p.get("name") != "Boondock" or not p.get("builtin"):
        return
    hooks = p.get("hooks")
    if not isinstance(hooks, dict):
        return
    for key, old, new in (("enter", "on", "session-on"), ("exit", "off", "session-off")):
        cmd = hooks.get(key)
        if isinstance(cmd, str):
            parts = cmd.split()
            if len(parts) == 2 and parts[0].endswith("boondock") and parts[1] == old:
                hooks[key] = f"{parts[0]} {new}"


def _migrate_builtin_fan_curve(p):
    """Add the cold-temperature anchor to an unedited shipped built-in curve.

    Exact matching protects user changes, including edited built-in curves and
    custom profiles that happen to use the same points.
    """
    if p.get("builtin") is not True:
        return False
    migration = {
        "Turbo": ("performance", LEGACY_TURBO_CURVE, TURBO_CURVE),
        "Balanced": ("balanced", LEGACY_BALANCED_CURVE, BALANCED_CURVE),
    }.get(p.get("name"))
    if migration is None:
        return False
    base, old_curve, new_curve = migration
    if p.get("base") != base or p.get("fan_curve") != old_curve:
        return False
    p["fan_curve"] = [point[:] for point in new_curve]
    return True


def _max_fans_curve_migration(store):
    if not isinstance(store, dict) or not isinstance(store.get("profiles"), list):
        return None
    active_name = store.get("active")
    active = next((p for p in store["profiles"]
                   if isinstance(p, dict) and p.get("name") == active_name), None)
    if active is None or active.get("builtin") is not True:
        return None
    migration = {
        "Turbo": ("performance", LEGACY_TURBO_CURVE, TURBO_CURVE),
        "Balanced": ("balanced", LEGACY_BALANCED_CURVE, BALANCED_CURVE),
    }.get(active_name)
    if migration is None:
        return None
    base, old_curve, new_curve = migration
    if active.get("base") != base or active.get("fan_curve") != new_curve:
        return None
    return old_curve, new_curve


def _migrate_enabled_max_fans_snapshot(store):
    """Update an enabled snapshot only when it is the active built-in's old curve.

    Profile-store lock is acquired before the Max Fans lock, matching apply's
    store-to-snapshot lock order. Re-read both files while locked so a profile
    edit or toggle racing startup is not overwritten.
    """
    migration = _max_fans_curve_migration(store)
    if migration is None:
        return
    old_curve, new_curve = migration
    old_snapshot = [[int(temp), round(int(pwm) * 255 / 100)]
                    for temp, pwm in old_curve]
    state = _read_json(MAX_FANS_PATH)
    if (
        not isinstance(state, dict)
        or state.get("enabled") is not True
        or state.get("saved_curve") != old_snapshot
    ):
        return

    with store_lock():
        # The active profile may have changed since the fast-path reads above.
        current_migration = _max_fans_curve_migration(_read_json(STORE_PATH))
        if current_migration is None:
            return
        old_curve, new_curve = current_migration
        old_snapshot = [[int(temp), round(int(pwm) * 255 / 100)]
                        for temp, pwm in old_curve]
        new_snapshot = [[int(temp), round(int(pwm) * 255 / 100)]
                        for temp, pwm in new_curve]
        with file_lock(MAX_FANS_PATH):
            state = _read_json(MAX_FANS_PATH)
            if (
                not isinstance(state, dict)
                or state.get("enabled") is not True
                or state.get("saved_curve") != old_snapshot
            ):
                return
            stamp = (f"{time.strftime('%Y%m%d-%H%M%S')}-"
                     f"{time.time_ns() // 1000 % 1_000_000:06d}-{os.getpid()}")
            backup = f"{MAX_FANS_PATH}.before-idle-curve-{stamp}"
            suffix = 0
            while os.path.exists(backup):
                suffix += 1
                backup = f"{MAX_FANS_PATH}.before-idle-curve-{stamp}-{suffix}"
            shutil.copy2(MAX_FANS_PATH, backup)
            state["saved_curve"] = new_snapshot
            _write_json(MAX_FANS_PATH, state)


def _loaded_profile_ok(p):
    """True when a profile read from disk is safe to hand to the rest of the app.

    The write-path rules (validate) plus the loose ends it leaves open on a
    hand-edited file: hooks must be {enter, exit} strings.
    """
    try:
        validate(p)
    except Exception as e:   # any shape surprise (KeyError, OverflowError...) drops the entry
        print(f"z13-profile: ignoring invalid profile entry: {type(e).__name__}: {e}", file=sys.stderr)
        return False
    hooks = p.get("hooks")
    if hooks is None:
        return True
    return isinstance(hooks, dict) and all(
        isinstance(hooks.get(k, ""), str) for k in ("enter", "exit"))


def _sanitize_store(store):
    """Check a parsed store's shape. Returns (store, changed), or (None, True)
    when nothing usable is left.

    Drops only the profile entries that are invalid (or repeat an earlier
    name), so one bad entry doesn't cost the user their other custom profiles;
    an active name that no longer exists falls back to Balanced, else the
    first profile. `changed` says the file differs from what was on disk.
    """
    if not isinstance(store, dict) or not isinstance(store.get("profiles"), list):
        return None, True
    good, seen = [], set()
    curve_migrated = False
    for p in store["profiles"]:
        if isinstance(p, dict):
            _sync_goal(p)   # a bad goal is dropped, not the whole profile
        if _loaded_profile_ok(p) and p["name"] not in seen:
            seen.add(p["name"])
            curve_migrated = _migrate_builtin_fan_curve(p) or curve_migrated
            good.append(p)
    if not good:
        return None, True
    changed = len(good) != len(store["profiles"]) or curve_migrated
    active = store.get("active")
    if not isinstance(active, str) or active not in seen:
        active = "Balanced" if "Balanced" in seen else good[0]["name"]
        changed = True
    if changed:
        store = dict(store, profiles=good, active=active)
    return store, changed


def _keep_aside(copy):
    """Keep the on-disk store for recovery under a .corrupt-<time> name."""
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() // 1000 % 1_000_000:06d}-{os.getpid()}"
    base = f"{STORE_PATH}.corrupt-{stamp}"
    dest, n = base, 0
    while os.path.exists(dest):      # never overwrite an existing backup
        n += 1
        dest = f"{base}-{n}"
    if copy:
        shutil.copy2(STORE_PATH, dest)
    else:
        os.replace(STORE_PATH, dest)


def _repair_or_seed(create):
    """Back up and rewrite a store that needs it. Caller holds the store lock."""
    raw = _read_json(STORE_PATH)
    store, changed = _sanitize_store(raw)
    if store is not None:
        if changed and create:
            _keep_aside(copy=True)
            _write_json(STORE_PATH, store)
        return store
    store = {"version": 1, "active": _legacy_active() or "Balanced",
             "profiles": default_profiles()}
    if create:
        if os.path.exists(STORE_PATH):
            # Unreadable or malformed: keep it for recovery instead of silently
            # replacing the user's custom profiles with the defaults.
            _keep_aside(copy=False)
        _write_json(STORE_PATH, store)
    return store


def load_store(create=True):
    """The store dict; creates the default one on first use.

    A store that parses but has the wrong shape is handled like a corrupt
    one: the original is kept as profiles.json.corrupt-<time>. If some
    profiles survive validation they are kept and the rest dropped; if none
    do, the defaults replace it.

    A read that needs no repair takes no lock. Anything that writes (repair or
    first-run seed) takes the store lock and re-reads under it, so a concurrent
    loader or writer that got there first is never overwritten from a stale
    snapshot and the backup is made once.
    """
    raw = _read_json(STORE_PATH)
    store, changed = _sanitize_store(raw)
    if store is None or (changed and create):
        if create:
            with store_lock():
                store = _repair_or_seed(create)
        elif store is None:
            store = {"version": 1, "active": _legacy_active() or "Balanced",
                     "profiles": default_profiles()}
    # Keys added after a store was seeded: built-ins take the shipped value.
    shipped = {p["name"]: p for p in default_profiles()}
    for p in store["profiles"]:
        if "max_fans" not in p:
            p["max_fans"] = bool(p.get("builtin") and shipped.get(p["name"], {}).get("max_fans"))
        _sync_goal(p)
        _migrate_boondock_hooks(p)
    if create:
        _migrate_enabled_max_fans_snapshot(store)
    return store


def save_store(store):
    for p in store.get("profiles", []):
        if isinstance(p, dict):
            _sync_goal(p)
    _write_json(STORE_PATH, store)


def list_profiles():
    return load_store()["profiles"]


def get_profile(name, store=None):
    store = store or load_store()
    for p in store["profiles"]:
        if p["name"] == name:
            return p
    return None


def active_name(create=True):
    return load_store(create=create).get("active")


def active_name_readonly():
    return load_store(create=False).get("active")


def sync_active_from_backend(state):
    """Mirror an externally changed z13ctl selection into profiles.json only.

    The daemon is authoritative for hardware state. This adapter updates the
    app-facing selected name without issuing another hardware write.
    """
    if not isinstance(state, dict):
        return None
    backend_name = state.get("profile")
    if not isinstance(backend_name, str) or not backend_name:
        return None
    try:
        from z13ctl_client import Z13CtlClient
        mapped_name = Z13CtlClient.mapped_profile_name
    except ImportError:
        return None
    with authority.transaction(), store_lock():
        store = load_store()
        before = json.dumps(store, sort_keys=True)
        selected = next((p for p in store["profiles"] if mapped_name(p) == backend_name), None)
        if selected is None and backend_name in BASES:
            # Upstream refuses to activate an empty custom profile. Such an app
            # profile is an alias for its firmware base; retain its app identity
            # rather than bouncing back to a built-in on every snapshot.
            current = get_profile(store.get("active"), store)
            saved = ((state.get("custom_profiles") or {}).get(mapped_name(current))
                     if current else None)
            if (current and current.get("base") == backend_name and isinstance(saved, dict)
                    and not any(current.get(k) is not None for k in ("tdp", "fan_curve", "undervolt"))
                    and not any(saved.get(k) is not None for k in ("tdp", "fan_curve", "undervolt"))):
                selected = current
            else:
                selected = next((p for p in store["profiles"]
                                 if p.get("builtin") and p.get("base") == backend_name), None)
        custom = (state.get("custom_profiles") or {}).get(backend_name)
        if selected is None and isinstance(custom, dict):
            # Profiles created through z13ctl are visible in both frontends too.
            name = backend_name
            if get_profile(name, store):
                name = f"Backend {name}"
            selected = {"name": name, "base": "balanced", "builtin": False,
                        "z13ctl_profile": backend_name, "hooks": {"enter": "", "exit": ""}}
            store["profiles"].append(selected)
        if selected is None:
            return None
        store["active"] = selected["name"]
        if isinstance(custom, dict):
            if state.get("platform_profile") in BASES:
                selected["base"] = state["platform_profile"]
            tdp = custom.get("tdp")
            selected["tdp"] = ({"pl1": tdp["pl1_spl"], "pl2": tdp["pl2_sppt"],
                                "pl3": tdp["fppt"], "force": tdp["pl1_spl"] > 75}
                               if tdp else None)
            uv = custom.get("undervolt")
            selected["undervolt"] = uv.get("cpu_co") if uv else None
            points = (custom.get("fan_curve") or {}).get("points") or []
            max_on = len(points) == 8 and all(p["pwm"] == 255 for p in points)
            _save_max_fans(enabled=max_on)
            # A Max Fans curve is temporary; retain the profile's restore curve.
            if not max_on:
                selected["fan_curve"] = [[p["temp"], round(p["pwm"] * 100 / 255)]
                                         for p in points] or None
        elif backend_name in BASES:
            _save_max_fans(enabled=False)
        if isinstance(state.get("battery_limit"), int):
            selected["battery_limit"] = state["battery_limit"]
        level = lighting_level(state)
        if isinstance(level, int) and 0 <= level <= 3:
            selected["brightness"] = ("off", "low", "medium", "high")[level]
        if json.dumps(store, sort_keys=True) != before:
            save_store(store)
            _mirror_legacy(selected["name"])
        return selected["name"]


def set_active(name):
    with store_lock():
        store = load_store()
        store["active"] = name
        save_store(store)
    _mirror_legacy(name)


def set_profile_lighting(name, device, setting, brightness=None):
    """Store one device's lighting intent in a profile; None clears its override."""
    if device not in LIGHTING_DEVICES:
        raise ValueError("bad lighting device")
    with store_lock():
        store = load_store()
        profile = get_profile(name, store)
        if profile is None:
            raise ValueError(f"unknown profile: {name}")
        lighting = profile.setdefault("lighting", {})
        if setting is None:
            lighting.pop(device, None)
            if not lighting:
                profile.pop("lighting", None)
        else:
            profile["lighting"] = {**lighting, device: dict(setting)}
            # z13ctl apply defaults to high brightness. Capture the current
            # UI choice when a profile first gains a mode so reapplying it
            # cannot silently change brightness later.
            if profile.get("brightness") is None:
                profile["brightness"] = brightness or "high"
        validate(profile)
        save_store(store)
    return get_profile(name)


def _mirror_legacy(name):
    """Mirror the active name for the legacy restore script / z13-* scripts."""
    legacy = LEGACY_NAMES_REV.get(name)
    if legacy:
        try:
            os.makedirs(os.path.dirname(LEGACY_CURRENT_PATH), exist_ok=True)
            with open(LEGACY_CURRENT_PATH, "w") as f:
                f.write(legacy + "\n")
        except OSError:
            pass


def upsert_profile(profile):
    """Add or replace a profile by name; returns the store."""
    validate(profile)
    with store_lock():
        store = load_store()
        for i, p in enumerate(store["profiles"]):
            if p["name"] == profile["name"]:
                profile["builtin"] = p.get("builtin", False)
                store["profiles"][i] = profile
                break
        else:
            profile.setdefault("builtin", False)
            store["profiles"].append(profile)
        save_store(store)
    return store


def delete_profile(name):
    with store_lock():
        store = load_store()
        p = get_profile(name, store)
        if p is None:
            raise KeyError(name)
        if p.get("builtin"):
            raise ValueError(f"{name} is built in and can't be deleted")
        store["profiles"] = [q for q in store["profiles"] if q["name"] != name]
        was_active = store.get("active") == name
        if was_active:
            store["active"] = "Balanced"
        save_store(store)
        if was_active:
            _mirror_legacy("Balanced")
    return store


def validate(p):
    if not isinstance(p, dict):
        raise ValueError("profile must be an object")
    name = p.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("profile needs a name")
    if p.get("base") not in BASES:
        raise ValueError(f"base must be one of {BASES}")
    tdp = p.get("tdp")
    if tdp is not None:
        if not isinstance(tdp, dict):
            raise ValueError("tdp must be null or {pl1, pl2, pl3, force}")
        for k in ("pl1", "pl2", "pl3"):
            v = tdp.get(k)
            if not isinstance(v, int) or not 5 <= v <= 93:
                raise ValueError(f"tdp.{k} must be 5..93 W")
        if tdp["pl1"] > 75 and not tdp.get("force"):
            raise ValueError("PL1 above 75 W needs force=true")
    validate_curve(p.get("fan_curve"))
    validate_goal(p.get("goal"))
    uv = p.get("undervolt")
    if uv is not None and (not isinstance(uv, int) or not -40 <= uv <= 0):
        raise ValueError("undervolt must be 0..-40")
    epp_value = p.get("epp")
    if epp_value is not None and epp_value not in EPP_CHOICES:
        raise ValueError(f"epp must be null or one of {EPP_CHOICES}")
    if p.get("brightness") not in (None, *BRIGHTNESS_LEVELS):
        raise ValueError("bad brightness level")
    lighting = p.get("lighting", {})
    if not isinstance(lighting, dict) or set(lighting) - set(LIGHTING_DEVICES):
        raise ValueError("lighting must map keyboard/lightbar to settings")
    for device, setting in lighting.items():
        if not isinstance(setting, dict):
            raise ValueError(f"lighting.{device} must be an object")
        if setting.get("mode") not in LIGHTING_MODES:
            raise ValueError(f"bad lighting.{device}.mode")
        if setting.get("color") not in LIGHTING_COLORS:
            raise ValueError(f"bad lighting.{device}.color")
    bl = p.get("battery_limit")
    if bl is not None and (not isinstance(bl, int) or not 40 <= bl <= 100):
        raise ValueError("battery_limit must be 40..100")


def fan_curve_floor_pct(temp):
    """BIOS safety floor from G-Helper: at least (temp-70)*1.2 %, i.e. 36% at 100 °C."""
    return max(0.0, (temp - 70) * 1.2)


def validate_curve(curve):
    """Same rules z13ctl enforces: 8 points, temps increasing, pct non-decreasing."""
    if curve is None:
        return
    if not isinstance(curve, (list, tuple)) or len(curve) != 8:
        raise ValueError("fan curve needs exactly 8 points")
    prev_t, prev_p = -1, -1
    for pt in curve:
        try:
            t, pct = int(pt[0]), int(pt[1])
            if len(pt) != 2:
                raise ValueError
        except (TypeError, ValueError, IndexError):
            raise ValueError(f"fan curve point {pt!r} is not [temp, pct]") from None
        if not (20 <= int(t) <= 110) or not (0 <= int(pct) <= 100):
            raise ValueError("fan curve point out of range")
        if int(t) < prev_t:
            raise ValueError("fan curve temps must be non-decreasing")
        if int(pct) < prev_p:
            raise ValueError("fan curve speeds must be non-decreasing")
        prev_t, prev_p = int(t), int(pct)


# ----------------------------------------------------------------------
# Apply engine
# ----------------------------------------------------------------------

def _runtime_dir():
    """Where the lightbar-reset stamp lives: $XDG_RUNTIME_DIR, or /tmp.

    Inside the Flatpak the sandbox's own runtime dir is private, so use the
    per-app directory Flatpak shares with the host (as single_instance does).
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base or not os.path.isdir(base):
        return "/tmp"
    app = os.environ.get("FLATPAK_ID")
    if app and os.path.isdir(os.path.join(base, "app", app)):
        return os.path.join(base, "app", app)
    return base


def lightbar_stamp_path():
    return os.path.join(_runtime_dir(), "z13-lightbar-reset.stamp")


def lightbar_daemon_stamp_path():
    return os.path.join(_runtime_dir(), "z13-lightbar-daemon-restart.stamp")


def lightbar_failure_kind(out):
    """Classify a failed lightbar write, or None if it isn't one we can recover.

    'wedged': the USB endpoint itself is stuck ("connection timed out" from
    z13ctl, or host.run's own timeout) -- needs a USB reset.
    'stale': the daemon is writing to a hidraw node that no longer exists
    ("no such device" / ENODEV), e.g. after a reset re-enumerated the device
    under new nodes -- needs only a daemon restart.
    """
    low = (out or "").lower()
    if "timed out" in low or "timeout" in low:
        return "wedged"
    if "no such device" in low or "enodev" in low:
        return "stale"
    return None


def lightbar_timed_out(out):
    """True when a failed lightbar write looks like the wedged endpoint."""
    return lightbar_failure_kind(out) == "wedged"


def _stamp_rate_limited(stamp, now, skipped_msg):
    try:
        age = now() - os.stat(stamp).st_mtime
        if 0 <= age < LIGHTBAR_RESET_MIN_GAP_S:
            return False, skipped_msg(int(age))
    except OSError:
        pass
    try:
        _atomic_write_text(stamp, f"{now():.0f}\n")
    except OSError:
        pass   # best effort: the unit/daemon itself serialises concurrent starts
    return True, None


def restart_lightbar_daemon(*, sleep=time.sleep):
    """Restart the z13ctl user daemon so it drops any cached hidraw path.

    On restart the daemon re-applies its own saved state (lighting, battery
    limit, custom fan curve), so nothing else needs to be re-pushed here.
    Returns (ok, message).
    """
    rc, out, _ = host.run(["systemctl", "--user", "restart", "--no-ask-password", Z13CTL_SERVICE], timeout=30)
    if rc != 0:
        return False, f"daemon restart failed ({out or f'rc={rc}'})"
    sleep(LIGHTBAR_DAEMON_RESTART_WAIT_S)
    return True, "daemon restarted"


def recover_lightbar(*, sleep=time.sleep, now=time.time):
    """Reset the wedged lightbar via the root oneshot, then restart the z13ctl
    daemon (it caches the hidraw path, which the reset invalidates). Returns
    (ok, message).

    Rate-limited to one attempt per LIGHTBAR_RESET_MIN_GAP_S through a stamp
    file (written before the attempt, so a concurrent caller backs off), then
    waits LIGHTBAR_RESET_SETTLE_S for the device to come back. Never loops:
    callers retry their one write once at most. The keyboard is a different
    USB device and is never touched. A daemon-restart failure is folded into
    the message (not into `ok`, since the USB reset itself did succeed) so a
    caller's retry -- which will then fail against the still-stale daemon --
    reports why.

    decky/main.py carries a copy of this (the Decky backend can't import src/).
    """
    ok, msg = _stamp_rate_limited(lightbar_stamp_path(), now,
                                   lambda age: f"lightbar reset skipped (last one {age} s ago)")
    if not ok:
        return False, msg
    # --no-ask-password: without the polkit rule, fail instead of popping a
    # password dialog from a background apply.
    rc, out, _ = host.run(["systemctl", "start", "--no-ask-password", LIGHTBAR_RESET_UNIT], timeout=30)
    if rc != 0:
        return False, f"lightbar reset failed ({out or f'rc={rc}'})"
    sleep(LIGHTBAR_RESET_SETTLE_S)
    daemon_ok, daemon_msg = restart_lightbar_daemon(sleep=sleep)
    if not daemon_ok:
        return True, f"lightbar reset, but {daemon_msg}"
    return True, "lightbar reset"


def recover_lightbar_stale(*, sleep=time.sleep, now=time.time):
    """Recover from a stale daemon handle (ENODEV) without a USB reset: the
    daemon is simply holding a dead hidraw path, so restarting it is enough.
    Rate-limited like recover_lightbar(), but via its own stamp so the two
    recoveries don't starve each other within the same 120 s window. Never
    loops; never touches the reset unit.

    decky/main.py carries a copy of this.
    """
    ok, msg = _stamp_rate_limited(lightbar_daemon_stamp_path(), now,
                                   lambda age: f"daemon restart skipped (last one {age} s ago)")
    if not ok:
        return False, msg
    return restart_lightbar_daemon(sleep=sleep)


def _max_fans_state():
    return _read_json(MAX_FANS_PATH) or {}


def _save_max_fans(**changes):
    with file_lock(MAX_FANS_PATH):
        state = _max_fans_state()
        state.update(changes)
        _write_json(MAX_FANS_PATH, state)


def curve_spec(curve):
    return ",".join(f"{int(t)}:{int(p)}%" for t, p in curve)


def apply_tdp(profile, *, z13ctl=Z13CTL):
    """The TDP step of apply(): [] when the profile has no TDP (the firmware's
    limits then stand), else one (step, ok, message). Also used by fan recovery."""
    tdp = profile.get("tdp")
    if not tdp:
        return []
    argv = [z13ctl, "tdp", "--set", str(tdp["pl1"]),
            "--pl2", str(tdp["pl2"]), "--pl3", str(tdp["pl3"])]
    if tdp.get("force"):
        argv.append("--force")
    rc, out, timed_out = authority.run(argv, timeout=Z13CTL_WRITE_TIMEOUT_S)
    ok = rc == 0
    message = (f"{tdp['pl1']}/{tdp['pl2']}/{tdp['pl3']} W" if ok else
               f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
               if timed_out else (out or f"rc={rc}"))
    return [("tdp", ok, message)]


def clear_z13ctl_default_custom_tdp(*, z13ctl=Z13CTL):
    """Clear a stale TDP from z13ctl's default custom profile, when safe.

    A bare ``fancurve --set`` edits and activates z13ctl's ``custom`` profile,
    including any TDP saved there by an earlier Z13-Helper profile. A Z13-Helper
    profile with a fan curve and no TDP must not inherit that old limit. The
    caller must first switch successfully to the firmware profile, which makes
    ``custom`` inactive. The CLI help is not enough: older daemons ignore the
    target field and apply the reset to the live profile, so require a working
    ``profile --list`` request and confirm the default custom profile is
    inactive and actually has a TDP before clearing it. Returns (ok, step); a
    missing step means no cleanup was needed or the CLI predates named profiles.
    """
    rc, help_text, _ = host.run([z13ctl, "tdp", "--help"], timeout=10)
    if rc == 0 and "--profile" not in help_text:
        return True, None
    if rc != 0:
        return False, ("tdp", False,
                       help_text or f"could not check z13ctl capabilities (rc={rc})")

    # Only the socket's JSON profile-list proves the daemon honours the target
    # field; the CLI firmware listing proves neither capability.
    try:
        client = authority.named_client()
    except (OSError, RuntimeError) as exc:
        return False, ("tdp", False, f"could not verify z13ctl daemon profile support ({exc})")
    if client is None:
        return False, ("tdp", False, "could not verify z13ctl daemon profile support")
    rc, listing, _ = host.run([z13ctl, "profile", "--list"], timeout=15)
    if rc != 0:
        return False, ("tdp", False,
                       listing or f"could not read z13ctl profile list (rc={rc})")
    lines = listing.splitlines()
    custom = None
    for line in lines:
        # `profile --list` uses a padded 20-character name column; requiring
        # its separator also keeps the `Custom profiles:` heading from matching.
        match = re.match(r"^[ \t]*(?P<active>\*)?[ \t]*custom[ \t]{2,}(?P<description>.*)$",
                         line, re.IGNORECASE)
        if match:
            custom = match
            break
    if custom is None:
        # A fresh profile list can contain no custom rows. Its recognized
        # headers prove the daemon replied, and there is no saved TDP to clear.
        return True, None
    if custom.group("active"):
        return False, ("tdp", False,
                       "z13ctl custom profile is still active; saved TDP was left unchanged")
    if not re.search(r"(?:^|,\s*)\d+W(?:,|$)", custom.group("description")):
        return True, None

    rc, out, timed_out = authority.run([z13ctl, "tdp", "--reset", "--profile", "custom"],
                                  timeout=Z13CTL_WRITE_TIMEOUT_S)
    ok = rc == 0
    message = ("cleared saved z13ctl custom TDP" if ok else
               f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
               if timed_out else (out or f"rc={rc}"))
    result = ("tdp", ok, message)
    return ok, result


def apply_undervolt(profile, *, z13ctl=Z13CTL):
    """The undervolt step of apply() (needs ryzen_smu; absent on stock SteamOS ->
    reported, not fatal): [] when the profile sets none. Also used by fan recovery."""
    uv = profile.get("undervolt")
    if uv is None:
        return []
    rc, out, timed_out = authority.run([z13ctl, "undervolt", "--set", str(uv)], timeout=Z13CTL_WRITE_TIMEOUT_S)
    if rc == 0:
        return [("undervolt", True, f"{uv}")]
    if timed_out:
        return [("undervolt", False,
                 f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)")]
    return [("undervolt", False, "unavailable (ryzen_smu not loaded)"
             if "ryzen_smu" in out else out)]


def epp_argv(value):
    """Host command that writes one EPP value to every cpufreq policy node.

    z13ctl has no EPP command, so this goes through host.run like the rest of the
    host seam. install.sh installs z13-helper-epp.conf to grant the users group
    write access to these policy nodes; no runtime elevation is needed.
    The policy directories are globbed on the host (a policy is not a CPU number, so
    nothing assumes policy{n} == cpu{n}). The value is checked against EPP_CHOICES
    and travels as an argv item (never spliced into the script). The script stops
    at the first write that fails and fails when no node matched (e.g. a kernel
    without amd-pstate), so that never reads as success. A failed write stops the
    loop, so policies can end up mixed (some written, some not), and the UI shows "mixed".
    """
    if value not in EPP_CHOICES:
        raise ValueError(f"epp must be one of {EPP_CHOICES}")
    script = (f'v=$1; n=0; for f in {EPP_SYSFS_GLOB}; do [ -e "$f" ] || continue; '
              'printf %s "$v" > "$f" || exit 1; n=$((n+1)); done; [ "$n" -gt 0 ]')
    return ["sh", "-c", script, "sh", value]


def write_epp(value):
    """(ok, message): write `value` to every cpufreq policy's energy_performance_preference.

    Callers must hold the state_authority transaction (all current callers do:
    set_epp via _push_field, apply_epp via the serialized apply paths).
    """
    rc, out, timed_out = host.run(epp_argv(value), timeout=Z13CTL_WRITE_TIMEOUT_S)
    if rc == 0:
        return True, value
    if timed_out:
        return False, f"EPP write timed out after {Z13CTL_WRITE_TIMEOUT_S} s"
    return False, out or f"rc={rc}"


def apply_epp(profile):
    """The CPU Boost step of apply(): [] when the profile sets no EPP (the firmware
    profile's choice stands), else one (step, ok, message)."""
    value = profile.get("epp")
    if value is None:
        return []
    ok, message = write_epp(value)
    return [("cpu boost", ok, message)]


def lighting_argv(device, setting, brightness, z13ctl=Z13CTL):
    """Build z13ctl's per-device lighting apply command from validated profile data."""
    if device not in LIGHTING_DEVICES:
        raise ValueError("bad lighting device")
    candidate = {"name": "lighting", "base": "balanced", "lighting": {device: setting}}
    validate(candidate)
    if brightness not in BRIGHTNESS_LEVELS:
        raise ValueError("bad brightness level")
    return [z13ctl, "--device", device, "apply", "--mode", setting["mode"],
            "--color", setting["color"], "--brightness", brightness]


@authority.serialized
def apply(profile, *, previous=None, z13ctl=Z13CTL,
          skip_hooks=False, login_battery_default=None):
    """Put the machine into the state `profile` describes.

    Order matters: the platform-profile write makes asus-wmi drop any custom
    fan curve and the firmware re-applies its own power limits, so it goes
    first; TDP next; then the fan curve pins the limits in place; then the
    rest. Each step is independent so one failure doesn't block the others.

    Returns a list of (step, ok, message). `previous` is the profile we are
    leaving (its exit hook runs first).
    """
    before_max_fans = bool(_max_fans_state().get("enabled"))
    results = _apply_steps(
        profile,
        previous=previous,
        z13ctl=z13ctl,
        skip_hooks=skip_hooks,
        login_battery_default=login_battery_default,
    )
    if timeline is not None:
        try:
            timeline.record_profile_apply(
                profile=profile,
                previous=previous,
                results=results,
                before_max_fans=before_max_fans,
                after_max_fans=bool(_max_fans_state().get("enabled")),
                defer=True,   # queued; never written inside this hardware transaction
            )
        except Exception as exc:  # noqa: BLE001 - timeline recording is best-effort
            logging.getLogger(__name__).warning(
                "timeline recording failed for profile apply: %s: %s", type(exc).__name__, str(exc)[:200])
    return results


def _apply_steps(profile, *, previous=None, z13ctl=Z13CTL,
                 skip_hooks=False, login_battery_default=None):
    results = []
    try:
        named_client = authority.named_client()
    except (OSError, RuntimeError) as exc:
        return [("profile", False, str(exc))]

    timed_out = False
    flag_pinned = False
    fan_ok = None   # None until the fan step has run, then whether it worked
    initial_state = _max_fans_state()
    initial_max_fans_on = bool(initial_state.get("enabled"))
    initial_suspended = initial_state.get("suspended")

    def finish(out):
        """Settle the shared Max Fans flag before apply() returns.

        A timed-out write leaves the flag as the user had it and sets a
        `suspended` marker: the write may have reached the daemon, so the
        hardware curve is unknown and the watchdog (which reads the marker)
        must not guess. Once the fan step itself has worked, the flag it set
        stands whatever failed later. Any earlier failure, or a failed fan
        step, restores the flag the user had before this attempt.
        """
        if timed_out:
            _save_max_fans(
                enabled=initial_max_fans_on, suspended="z13ctl write timed out"
            )
        elif not (flag_pinned or fan_ok) and (
            fan_ok is False or any(not ok for _, ok, _ in out)
        ):
            _save_max_fans(enabled=initial_max_fans_on, suspended=initial_suspended)
        elif (
            flag_pinned
            or initial_max_fans_on
            or _max_fans_state().get("suspended") is not None
        ):
            # Nothing to add when Max Fans was off and no stale marker exists.
            _save_max_fans(suspended=None)
        return out

    def step(name, argv, ok_msg=None, timeout=Z13CTL_WRITE_TIMEOUT_S):
        nonlocal timed_out
        rc, out, hit_timeout = authority.run(argv, timeout=timeout)
        ok = rc == 0
        timed_out = hit_timeout and os.path.basename(argv[0]) == os.path.basename(z13ctl)
        message = (ok_msg if ok and ok_msg else
                   f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
                   if timed_out
                   else (out or f"rc={rc}"))
        results.append((name, ok, message))
        return ok

    # 1. Leave the previous profile (e.g. `boondock off` restores the screen).
    if not skip_hooks and previous and previous.get("name") != profile["name"]:
        exit_cmd = (previous.get("hooks") or {}).get("exit") or ""
        if exit_cmd.strip():
            step("exit hook", ["bash", "-lc", exit_cmd], "hook ran",
                 timeout=HOOK_TIMEOUT_S)

    # Compute the desired state now, but suspend the shared Max Fans flag for
    # the whole apply. The watchdog must not re-pin a curve between the base
    # profile write and this apply's own fan write.
    curve = profile.get("fan_curve")
    tdp = profile.get("tdp")
    switching = previous is None or previous.get("name") != profile["name"]
    planned_max_fans_on = initial_max_fans_on
    released = False
    # A goal profile's runner (Phase 15c) is not built yet, so every goal is
    # applied as its fixed values; goal "max" is Max Fans.
    wants_max = goal_kind(profile) == "max"
    if switching and wants_max:
        planned_max_fans_on = True
    elif switching and previous and goal_kind(previous) == "max" and not wants_max \
            and initial_max_fans_on:
        planned_max_fans_on = False
        released = True
    if initial_max_fans_on:
        _save_max_fans(enabled=False)

    # 2. Base platform profile.
    if named_client is not None:
        backend_profile = dict(profile)
        if planned_max_fans_on:
            backend_profile["fan_curve"] = [[t, 100] for t in MAX_FAN_TEMPS]
        try:
            mapped_name = named_client.activate_app_profile(backend_profile)
        except TimeoutError:
            timed_out = True
            return finish(results + [(
                "profile", False,
                f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)",
            )])
        except (OSError, RuntimeError, ValueError) as exc:
            return finish(results + [("profile", False, str(exc))])
        base_ok = True
        results.append(("profile", True, mapped_name))
    else:
        base_ok = step("profile", [z13ctl, "profile", "--set", profile["base"]], profile["base"])
    if timed_out:
        # A timed-out write may still have reached the daemon. The Max Fans flag
        # represents confirmed enforcement, so keep it suspended rather than
        # letting the watchdog issue another curve write against uncertain state.
        return finish(results)
    if not base_ok:
        # The firmware profile did not change. Writing TDP or a curve now would
        # land them on the wrong base profile, so report and stop here.
        return finish(results)

    # 2b. CPU Boost (EPP), after the platform-profile write, which can reset it.
    results.extend(apply_epp(profile))

    # 3. TDP (None = whatever the firmware profile dictates, restored by the
    #    profile write above).
    if named_client is None:
        results.extend(apply_tdp(profile, z13ctl=z13ctl))
    timed_out = any(msg.startswith("z13ctl write timed out after ") for _, _, msg in results)
    if timed_out:
        return finish(results)

    # 4. Fan curve. Max Fans wins while it is ON: keep 100% pinned, but remember
    #    the profile's curve as the one to restore when it's switched OFF.
    #    A max_fans profile turns it ON when switched to and OFF when left
    #    (only on a real switch: restore/resume keeps whatever the user chose).
    curve_write_needed = bool(curve) or planned_max_fans_on
    cleanup_ok = True
    if named_client is not None:
        _save_max_fans(saved_curve=[[int(t), round(int(p) * 255 / 100)] for t, p in curve]
                       if curve else None, enabled=planned_max_fans_on)
        results.append(("fans", True, "z13ctl named profile"))
        fan_ok = True
    elif curve_write_needed and not tdp:
        # Editing a fan curve through z13ctl activates its default `custom`
        # profile. Clear only its inactive saved TDP first, so neither a
        # profile curve nor Max Fans (including curveless Max Fans profiles)
        # can reactivate a limit from an older app profile.
        if not base_ok:
            cleanup_ok = False
            results.append(("tdp", False,
                            "base profile switch failed; saved custom TDP was not changed"))
        else:
            cleanup_ok, cleanup_result = clear_z13ctl_default_custom_tdp(z13ctl=z13ctl)
            if cleanup_result:
                results.append(cleanup_result)
                if cleanup_result[2].startswith("z13ctl write timed out after "):
                    timed_out = True
                    return finish(results)
    if named_client is not None:
        pass
    elif curve_write_needed and not tdp and not cleanup_ok:
        # The base profile write releases a custom curve. Keep the shared flag
        # from asking the watchdog to re-enable Max Fans (which would write an
        # unverified custom profile). If the base write failed, preserve the
        # state that was already in force before this attempted transition.
        flag_pinned = True
        _save_max_fans(enabled=initial_max_fans_on if not base_ok else False)
        results.append(("fans", False,
                        "saved custom TDP could not be safely cleared; fan curve was not applied"))
    elif planned_max_fans_on:
        # What Max Fans OFF should go back to: this profile's curve, or none
        # (= firmware auto) -- never the previous profile's leftover curve.
        _save_max_fans(saved_curve=[[int(t), round(int(p) * 255 / 100)] for t, p in curve]
                        if curve else None)
        fan_ok = step("fans", [z13ctl, "fancurve", "--set", MAX_FANS_SPEC],
                      "Max Fans stays ON (100%)")
        if fan_ok:
            _save_max_fans(enabled=True)
    elif curve:
        fan_ok = step("fans", [z13ctl, "fancurve", "--set", curve_spec(curve)], "custom curve")
    elif released or tdp:
        # Leaving a Max Fans profile for one without a curve: hand the fans back
        # explicitly rather than trusting the profile write to drop the 100% curve.
        # A TDP without a curve needs it too: `tdp --set` activates z13ctl's
        # "custom" profile, which re-applies the last curve it stored (Max Fans,
        # say) -- a reset now, with "custom" active, is what clears it.
        fan_ok = step("fans", [z13ctl, "fancurve", "--reset"],
                      "Max Fans OFF, firmware auto" if released else "firmware auto")
    else:
        results.append(("fans", True, "firmware auto"))
        fan_ok = True
    if timed_out:
        return finish(results)

    # 5. Undervolt (needs ryzen_smu; absent on stock SteamOS -> reported, not fatal).
    if named_client is None:
        results.extend(apply_undervolt(profile, z13ctl=z13ctl))
    timed_out = any(msg.startswith("z13ctl write timed out after ") for _, _, msg in results)
    if timed_out:
        return finish(results)

    # 6. Lighting follows the repository's documented profile binding order.
    # Each device is independent so a
    #    stalled endpoint cannot block the other. A mode apply carries brightness
    #    because z13ctl otherwise defaults it to high.
    lighting = profile.get("lighting") or {}
    br = profile.get("brightness")
    # Apply one write per device in the established keyboard-then-lightbar order,
    # whether the profile requests a mode or only a brightness change.
    for dev in LIGHTING_DEVICES:
        setting = lighting.get(dev)
        if setting:
            level = br or "high"
            argv = lighting_argv(dev, setting, level, z13ctl)
            rc, out, timed_out = authority.run(argv, timeout=Z13CTL_WRITE_TIMEOUT_S)
            if timed_out:
                out = f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
            results.append((f"lighting ({dev})", rc == 0,
                            f"{setting['mode']} / {setting['color']} / {level}" if rc == 0
                            else (out or f"rc={rc}")))
            if timed_out:
                return finish(results)
        elif br:
            argv = [z13ctl, "--device", dev, "brightness", br]
            rc, out, hit_timeout = authority.run(argv, timeout=Z13CTL_WRITE_TIMEOUT_S)
            timed_out = hit_timeout
            if hit_timeout:
                out = f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
            if not hit_timeout and rc != 0 and dev == "lightbar":
                kind = lightbar_failure_kind(out)
                if kind == "wedged":
                    rec_ok, rec_msg = recover_lightbar()
                elif kind == "stale":
                    rec_ok, rec_msg = recover_lightbar_stale()
                else:
                    rec_ok = None
                if rec_ok is not None:
                    if rec_ok:
                        rc, out, hit_timeout = authority.run(argv, timeout=Z13CTL_WRITE_TIMEOUT_S)
                        timed_out = hit_timeout
                        if hit_timeout:
                            out = f"z13ctl write timed out after {Z13CTL_WRITE_TIMEOUT_S} s (kernel asus-wmi hang?)"
                        if rc == 0:
                            results.append((f"brightness ({dev})", True,
                                            f"{br} (lightbar recovered)"))
                            continue
                        if not hit_timeout:
                            out = f"{out or f'rc={rc}'} (still failing after a {rec_msg})"
                    else:
                        out = f"{out}; {rec_msg}"
            results.append((f"brightness ({dev})", rc == 0, br if rc == 0 else (out or f"rc={rc}")))
            if timed_out:
                return finish(results)

    # 7. Battery charge limit.
    # A login restore passes login_battery_default=80: a profile that sets no limit
    # (e.g. Boondock) then gets the 80% fallback, as the retired 80% unit used to give.
    bl = profile.get("battery_limit")
    note = ""
    if bl is None and login_battery_default is not None:
        bl, note = login_battery_default, " (fallback: profile sets no limit)"
    if bl is not None:
        step("battery limit", [z13ctl, "batterylimit", "--set", str(bl)], f"{bl}%{note}")
        if timed_out:
            return finish(results)

    # 8. Enter hook.
    if not skip_hooks:
        enter_cmd = (profile.get("hooks") or {}).get("enter") or ""
        if enter_cmd.strip():
            step("enter hook", ["bash", "-lc", enter_cmd], "hook ran",
                 timeout=HOOK_TIMEOUT_S)

    return finish(results)


@authority.serialized
def apply_by_name(name, **kw):
    store = load_store()
    profile = get_profile(name, store)
    if profile is None:
        raise KeyError(f"no profile named {name!r}")
    previous = get_profile(store.get("active"), store)
    results = apply(profile, previous=previous, **kw)
    if any(ok for s, ok, _ in results if s == "profile"):
        set_active(name)
    return results


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------

def _print_results(results):
    worst = 0
    for step, ok, msg in results:
        # Two spaces after the step name always: install_check.py splits on them.
        print(f"  {'ok ' if ok else 'ERR'}  {step:<22}  {msg}")
        worst = max(worst, 0 if ok else 1)
    return worst


def main(argv=None):
    ap = argparse.ArgumentParser(prog="z13-profile", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("list"); s.add_argument("--json", action="store_true")
    sub.add_parser("active")
    s = sub.add_parser("show"); s.add_argument("name")
    s = sub.add_parser("apply"); s.add_argument("name")
    s.add_argument("--no-hooks", action="store_true", help="skip enter/exit hooks")
    s = sub.add_parser("restore", help="re-apply the active profile (login / resume)")
    s.add_argument("--no-hooks", action="store_true")
    sub.add_parser("resume-owner", help="check whether z13ctl owns resume restore")
    sub.add_parser("watch", help="stream z13ctl state changes as JSON lines")
    args = ap.parse_args(argv)

    if args.cmd == "resume-owner":
        try:
            if _z13ctl_owns_resume_restore():
                print("z13ctl owns it")
                return 10
        except Exception:
            pass
        print("hook must restore")
        return 0

    if args.cmd == "watch":
        try:
            from z13ctl_client import Z13CtlClient, follow_state
        except ImportError as exc:
            print(f"z13-profile watch unavailable: {exc}", file=sys.stderr)
            return 1
        import signal
        stop = threading.Event()
        client = Z13CtlClient()
        signal.signal(signal.SIGTERM, lambda *_: (stop.set(), client.close()))
        signal.signal(signal.SIGINT, lambda *_: (stop.set(), client.close()))

        def emit(state, event):
            print(json.dumps({"event": event, "state": state}, separators=(",", ":")), flush=True)

        try:
            follow_state(client, emit, stop, read_state=lambda: authority.snapshot(client), on_error=lambda e: print(
                f"z13-profile watch reconnecting: {e}", file=sys.stderr, flush=True))
        except Exception as exc:
            print(f"z13-profile watch failed: {exc}", file=sys.stderr)
            return 1
        return 0

    if args.cmd == "list":
        store = load_store()
        if args.json:
            print(json.dumps(store))
        else:
            for p in store["profiles"]:
                mark = "*" if p["name"] == store.get("active") else " "
                print(f"{mark} {p['name']}  (base: {p['base']})")
        return 0
    if args.cmd == "active":
        print(active_name() or "")
        return 0
    if args.cmd == "show":
        p = get_profile(args.name)
        if p is None:
            print(f"no profile named {args.name!r}", file=sys.stderr)
            return 1
        print(json.dumps(p, indent=2))
        return 0
    if args.cmd in ("apply", "restore"):
        name = active_name() if args.cmd == "restore" else args.name
        if not name:
            print("no active profile", file=sys.stderr)
            return 1
        try:
            print(f"Applying profile: {name}")
            # On restore the hooks are intentionally skipped: a login shouldn't
            # re-run `boondock on` (its snapshot is from the previous session).
            results = apply_by_name(name, skip_hooks=args.no_hooks or args.cmd == "restore",
                                    login_battery_default=80 if args.cmd == "restore" else None)
        except KeyError as e:
            print(e, file=sys.stderr)
            return 1
        return _print_results(results)
    return 2


def _z13ctl_owns_resume_restore():
    """Return true only when file snapshots prove z13ctl can restore this profile.

    Deliberately file-only: resume must not query the daemon or read live hardware.
    Any malformed, missing, or incomplete state is an affirmative reason for the
    system resume hook to perform its existing full restore.
    """
    from z13ctl_client import Z13CtlClient

    raw_store = _read_json(STORE_PATH)
    if (not isinstance(raw_store, dict) or "active" not in raw_store
            or not isinstance(raw_store.get("profiles"), list)):
        return False
    active = raw_store.get("active")
    raw_profile = next((p for p in raw_store["profiles"]
                        if isinstance(p, dict) and p.get("name") == active), None)
    if not isinstance(raw_profile, dict):
        return False

    store = load_store(create=False)
    active = store.get("active")
    profile = get_profile(active, store) if isinstance(active, str) else None
    if not isinstance(profile, dict):
        return False

    max_fans = _read_json(MAX_FANS_PATH)
    if not isinstance(max_fans, dict) or type(max_fans.get("enabled")) is not bool:
        return False

    state_home = os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state")
    state = _read_json(os.path.join(state_home, "z13ctl", "state.json"))
    if not isinstance(state, dict):
        return False
    name = Z13CtlClient.mapped_profile_name(profile)
    if state.get("profile") != name:
        return False
    custom_profiles = state.get("custom_profiles")
    if not isinstance(custom_profiles, dict) or name not in custom_profiles:
        return False
    backend = custom_profiles[name]
    if not isinstance(backend, dict):
        return False

    app_curve = profile.get("fan_curve")
    if not isinstance(app_curve, list) or len(app_curve) != 8:
        return False
    curve = backend.get("fan_curve")
    if not isinstance(curve, dict) or type(curve.get("mode")) is not int or curve["mode"] != 1:
        return False
    points = curve.get("points")
    if not isinstance(points, list) or len(points) != 8:
        return False
    try:
        temps = [point["temp"] for point in points]
        pwms = [point["pwm"] for point in points]
        if len(set(temps)) != 8:
            return False
        if max_fans["enabled"]:
            if any(type(pwm) is not int or pwm != 255 for pwm in pwms):
                return False
        else:
            if any(not isinstance(point, (list, tuple)) or len(point) != 2 for point in app_curve):
                return False
            expected = {temp: round(pct * 255 / 100) for temp, pct in app_curve}
            if len(expected) != 8 or set(expected) != set(temps):
                return False
            if any(type(pwm) is not int or abs(pwm - expected[temp]) > 1
                   for temp, pwm in zip(temps, pwms)):
                return False
    except (KeyError, TypeError, ValueError):
        return False

    app_tdp = profile.get("tdp")
    backend_tdp = backend.get("tdp")
    if app_tdp is not None:
        if not isinstance(app_tdp, dict) or not isinstance(backend_tdp, dict):
            return False
        for source, target in (("pl1", "pl1_spl"), ("pl2", "pl2_sppt"), ("pl3", "fppt")):
            if source not in app_tdp or backend_tdp.get(target) != app_tdp[source]:
                return False
    elif backend_tdp is not None:
        return False

    if profile.get("epp") is not None:
        return False
    if "battery_limit" not in profile:
        return False
    battery_limit = profile["battery_limit"]
    if battery_limit is None:
        battery_limit = 80
    return state.get("battery_limit") == battery_limit


if __name__ == "__main__":
    sys.exit(main())

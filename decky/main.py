"""Z13-Helper — Decky Loader backend.

Runs as the deck user: Decky Loader only runs a backend as root for the flag "root",
and plugin.json says "_root" (checked live 2026-10-15). Every command goes through the
shared host seam (host.py), which works either way (it adds `runuser -u deck` only under
root), so the flag can change without code changes. Shells to the host z13ctl / boondock
CLIs — the same hardware backend the desktop PyQt app uses — so Game Mode and Desktop
Mode share one contract.
"""

import os
import re
import json
import time
import asyncio
import sys

import decky  # modern API (NOT decky_plugin)

# Shared with the desktop app: install.sh copies these from src/ into this plugin's
# py_modules/ as root-owned files (docs/adr/0001-decky-imports-root-owned-copies.md).
# The loader puts py_modules on sys.path too; adding it here keeps that explicit.
_PY_MODULES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "py_modules")
if os.path.isdir(_PY_MODULES) and _PY_MODULES not in sys.path:
    sys.path.insert(0, _PY_MODULES)
from fan_watchdog import FanTrace, FanWatchdog  # noqa: E402
import host  # noqa: E402
from sensor_reader import SensorReader  # noqa: E402

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
BOONDOCK = os.path.expanduser("~/.local/bin/boondock")
SHARED_CONFIG = os.path.expanduser("~/.config/z13-helper/config.json")
# Max Fans state shared with the desktop app (mirror of src/max_fans_state.py):
# {"enabled": bool, "saved_curve": [[temp, pwm], ...]}. Re-read, never cached,
# so a toggle in either frontend is seen by the other.
MAX_FANS_STATE = os.path.expanduser("~/.config/z13-helper/max-fans.json")

# Children get the deck env from host.run (deck's runtime dir and session bus, so HID
# commands use the z13ctl daemon socket; the host's LD_LIBRARY_PATH, not the loader's).

# Max Fans = 8 monotonic temp anchors, all at 100%.
MAX_FAN_TEMPS = (20, 30, 40, 50, 60, 70, 80, 90)   # from 20 C: fans idle below the first point
MAX_FANS_SPEC = ",".join(f"{t}:100%" for t in MAX_FAN_TEMPS)
# Fan watchdog poll cadence (its thresholds live in fan_watchdog.py).
ENFORCE_INTERVAL_S = 2
# Its trace, next to the desktop app's (Help -> Error log -> Fan trace shows both).
# Written as root, handed to deck so the app and deck can read and rotate it.
FAN_TRACE_DIR = "/home/deck/.local/state/z13-helper"
FAN_TRACE = os.path.join(FAN_TRACE_DIR, "fan-trace-gamemode.log")
DECK_IDS = (1000, 1000)
# The user's logging choice (mirror of src/install_record.py logging_enabled()):
# install.sh asks, the desktop app's Help -> Error log changes it. "logging": false
# = no fan trace file; missing = on. Read once, when the watchdog starts.
INSTALL_JSON = os.path.expanduser("~/.config/z13-helper/install.json")

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
# One z13ctl call per device: z13ctl's default --device all aborts on the first
# write error, so a wedged lightbar would starve the keyboard (ROADMAP §1, 2026-10-09).
LIGHTING_DEVICES = ("keyboard", "lightbar")
LIGHTING_TIMEOUT_S = 30
# Wedged-lightbar recovery: copy of src/profiles.py recover_lightbar() (this backend
# runs as root and can't import src/). Keep the two in step.
LIGHTBAR_RESET_UNIT = "z13-lightbar-reset.service"
LIGHTBAR_RESET_MIN_GAP_S = 120
LIGHTBAR_RESET_SETTLE_S = 3
# Same stamp the host-side z13-profile CLI uses (deck's runtime dir), so the
# rate limit holds across both.
LIGHTBAR_STAMP = "/run/user/1000/z13-lightbar-reset.stamp"
# The reset re-enumerates the lightbar under new hidraw nodes, but the z13ctl
# user daemon caches the old path and keeps writing to the dead node -- it
# has to be restarted too (copy of src/profiles.py; same reasoning there).
Z13CTL_SERVICE = "z13ctl.service"
LIGHTBAR_DAEMON_RESTART_WAIT_S = 2
LIGHTBAR_DAEMON_STAMP = "/run/user/1000/z13-lightbar-daemon-restart.stamp"


def _run(args, timeout=15, *, as_deck=False):
    """host.run, logged. Returns (rc, out)."""
    rc, out, _ = host.run(args, timeout=timeout, as_deck=as_deck)
    decky.logger.info("cmd %s -> rc=%s\n%s", " ".join(args), rc, out)
    return rc, out


def _state_path():
    return os.path.join(decky.DECKY_PLUGIN_SETTINGS_DIR, "state.json")


def _load_state():
    try:
        with open(_state_path()) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def _shared_config():
    """Read the desktop app's config.json (legacy Max Fans keys, for migration)."""
    try:
        with open(SHARED_CONFIG) as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


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
    legacy, old = _shared_config(), _load_state()
    state = {
        "enabled": bool(legacy.get("fans_max", old.get("max_fans", False))),
        "saved_curve": legacy.get("fan_curve_saved") or old.get("saved_curve"),
    }
    _save_max_fans(state)
    return state


def _save_max_fans(state):
    """Write the shared state atomically, owned by deck even if we run as root."""
    os.makedirs(os.path.dirname(MAX_FANS_STATE), exist_ok=True)
    tmp = f"{MAX_FANS_STATE}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    if os.geteuid() == 0:
        os.chown(tmp, 1000, 1000)
    os.replace(tmp, MAX_FANS_STATE)


def _update_max_fans(**changes):
    state = _load_max_fans()
    state.update(changes)
    _save_max_fans(state)


def _current_curve_pwm():
    """Parse `z13ctl fancurve --get` into a list of (temp, pwm) tuples."""
    _, out = _run([Z13CTL, "fancurve", "--get"])
    return [(int(t), int(pwm)) for t, pwm in re.findall(r"(\d+)\D+C:\s+(\d+)/255", out)]


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

def clamp_tdp(pl1, pl2, pl3):
    """Clamp to PL_MIN..PL_MAX and enforce sPPT >= SPL, fPPT >= sPPT.

    Mirrors the desktop window's slider rule (a lower rail pushes the higher ones
    up; a higher rail can't go below the lower one). Returns a profiles.json `tdp`
    dict. `force` follows the desktop app (pl1 > 75 W) because profiles.validate()
    and z13ctl both require it there; it is False for every SPL <= 75 W.
    """
    def one(v):
        return max(PL_MIN, min(PL_MAX, int(round(float(v)))))
    a = one(pl1)
    b = max(one(pl2), a)
    c = max(one(pl3), b)
    return {"pl1": a, "pl2": b, "pl3": c, "force": a > PL1_SAFE_MAX}


def clamp_battery(value):
    """Nearest BATTERY_STEP inside BATTERY_MIN..BATTERY_MAX."""
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


def update_active_profile(store, **fields):
    """Set `fields` on the active profile, in place. Returns its name.

    Only existing schema keys this panel edits are allowed, so the schema can't
    grow by accident. Raises KeyError if there is no active profile.
    """
    bad = set(fields) - {"tdp", "battery_limit"}
    if bad:
        raise ValueError(f"not editable from Decky: {sorted(bad)}")
    p = active_profile(store)
    if p is None:
        raise KeyError("no active profile in profiles.json")
    p.update(fields)
    return p["name"]


def write_store_atomic(store, path=None):
    """tmp + rename in the same dir; keep it deck-owned although we run as root."""
    path = path or PROFILES_STORE
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = f"{path}.{os.getpid()}.decky.tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(store, f, indent=2)   # same layout as src/profiles.py
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        if os.geteuid() == 0:
            try:
                st = os.stat(d)
                uid, gid = (st.st_uid, st.st_gid) if st.st_uid != 0 else (DECK_UID, DECK_GID)
            except OSError:
                uid, gid = DECK_UID, DECK_GID
            os.chown(tmp, uid, gid)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _set_active_field(**fields):
    """Read-modify-write the active profile. Returns (name, error-or-None)."""
    store = _ensure_store()
    if store is None:
        return None, "profiles.json missing (is z13-profile installed?)"
    try:
        name = update_active_profile(store, **fields)
        write_store_atomic(store)
    except (KeyError, ValueError, OSError) as e:
        return None, str(e)
    return name, None


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
    """APU / GPU temps (degC) and both fan RPMs; None where unreadable.

    hwmon nodes are found by `name` (acpitz / amdgpu / asus), never by index.
    Reads only hwmon; never the asus-armoury ppt_* files.
    """
    f1, f2 = _sensors.get_fan_speeds()
    return {"apu": _sensors.get_apu_temp(), "gpu": _sensors.get_gpu_temp(), "fan1": f1, "fan2": f2}


def lightbar_failure_kind(out):
    """Copy of profiles.lightbar_failure_kind."""
    low = (out or "").lower()
    if "timed out" in low or "timeout" in low:
        return "wedged"
    if "no such device" in low or "enodev" in low:
        return "stale"
    return None


def lightbar_timed_out(out):
    """Copy of profiles.lightbar_timed_out: the wedged endpoint's failure text."""
    return lightbar_failure_kind(out) == "wedged"


def _stamp_rate_limited(stamp, now, skipped_msg):
    """Copy of profiles._stamp_rate_limited, with the deck-ownership chown
    the desktop-app side doesn't need (we run as root)."""
    try:
        age = now() - os.stat(stamp).st_mtime
        if 0 <= age < LIGHTBAR_RESET_MIN_GAP_S:
            return False, skipped_msg(int(age))
    except OSError:
        pass
    try:
        tmp = f"{stamp}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            f.write(f"{now():.0f}\n")
        if os.geteuid() == 0:
            os.chown(tmp, DECK_UID, DECK_GID)   # the deck-side CLI rewrites it too
        os.replace(tmp, stamp)
    except OSError:
        pass
    return True, None


def restart_lightbar_daemon(*, sleep=time.sleep):
    """Copy of profiles.restart_lightbar_daemon(). Reaches the deck user's z13ctl
    daemon as deck (host.run's as_deck), even if this backend runs as root."""
    rc, out = _run(["systemctl", "--user", "restart", "--no-ask-password", Z13CTL_SERVICE],
                   timeout=30, as_deck=True)
    if rc != 0:
        return False, f"daemon restart failed ({out or f'rc={rc}'})"
    sleep(LIGHTBAR_DAEMON_RESTART_WAIT_S)
    return True, "daemon restarted"


def recover_lightbar(*, sleep=time.sleep, now=time.time, stamp=None):
    """Copy of profiles.recover_lightbar(). Returns (ok, message).

    Starts the root oneshot that re-authorizes the lightbar's USB device (we are
    root here, so no polkit involved), at most once per LIGHTBAR_RESET_MIN_GAP_S,
    waits for the device to come back, then restarts the z13ctl daemon (it
    caches the hidraw path, which the reset invalidates). Never loops; never
    touches the keyboard. A daemon-restart failure is folded into the message,
    not into `ok` -- the USB reset itself did succeed, so the caller still
    retries once, and that retry's own failure will report why.
    """
    stamp = stamp or LIGHTBAR_STAMP
    ok, msg = _stamp_rate_limited(stamp, now,
                                   lambda age: f"lightbar reset skipped (last one {age} s ago)")
    if not ok:
        return False, msg
    rc, out = _run(["systemctl", "start", "--no-ask-password", LIGHTBAR_RESET_UNIT], timeout=30)
    if rc != 0:
        return False, f"lightbar reset failed ({out or f'rc={rc}'})"
    sleep(LIGHTBAR_RESET_SETTLE_S)
    daemon_ok, daemon_msg = restart_lightbar_daemon(sleep=sleep)
    if not daemon_ok:
        return True, f"lightbar reset, but {daemon_msg}"
    return True, "lightbar reset"


def recover_lightbar_stale(*, sleep=time.sleep, now=time.time, stamp=None):
    """Copy of profiles.recover_lightbar_stale(). Recovers a stale daemon
    handle (ENODEV) without a USB reset: restart the daemon alone. Rate-limited
    like recover_lightbar(), via its own stamp. Never loops."""
    stamp = stamp or LIGHTBAR_DAEMON_STAMP
    ok, msg = _stamp_rate_limited(stamp, now,
                                   lambda age: f"daemon restart skipped (last one {age} s ago)")
    if not ok:
        return False, msg
    return restart_lightbar_daemon(sleep=sleep)


def set_brightness_per_device(level, recover=None, recover_stale=None):
    """One z13ctl call per lighting device. Returns {"ok", "failed", "message"}.

    ok is True when at least one device took the level; a failed device is
    reported, not fatal (a wedged lightbar must not block the keyboard). The
    keyboard goes first; a lightbar timeout gets one USB reset + daemon restart
    (`recover`) and one retry, a stale daemon handle (ENODEV) gets one daemon
    restart only (`recover_stale`) and one retry.
    """
    recover = recover or recover_lightbar
    recover_stale = recover_stale or recover_lightbar_stale
    if level not in BRIGHTNESS_LEVELS:
        return {"ok": False, "failed": list(LIGHTING_DEVICES), "message": f"bad level {level!r}"}
    failed = []
    notes = []
    for dev in LIGHTING_DEVICES:
        argv = [Z13CTL, "--device", dev, "brightness", level]
        rc, out = _run(argv, timeout=LIGHTING_TIMEOUT_S, as_deck=True)
        if rc != 0 and dev == "lightbar":
            kind = lightbar_failure_kind(out)
            if kind == "wedged":
                rec_ok, rec_msg = recover()
            elif kind == "stale":
                rec_ok, rec_msg = recover_stale()
            else:
                rec_ok = None
            if rec_ok is not None:
                if rec_ok:
                    rc, out = _run(argv, timeout=LIGHTING_TIMEOUT_S, as_deck=True)
                    if rc == 0:
                        notes.append("lightbar recovered")
                    else:
                        notes.append("lightbar still wedged after a reset" if kind == "wedged"
                                      else "lightbar still failing after a daemon restart")
                else:
                    notes.append(rec_msg)
        if rc != 0:
            failed.append(dev)
    ok = len(failed) < len(LIGHTING_DEVICES)
    if not failed:
        msg = f"Brightness {level}"
    elif ok:
        msg = f"Brightness {level} ({', '.join(failed)} not responding)"
    else:
        msg = "Brightness failed: no lighting device responded"
    if notes:
        msg += f" [{'; '.join(notes)}]"
    return {"ok": ok, "failed": failed, "message": msg}


def _save_plugin_state(**changes):
    """The plugin's private state.json (remembers the last brightness for the UI)."""
    state = _load_state()
    state.update(changes)
    try:
        os.makedirs(os.path.dirname(_state_path()), exist_ok=True)
        tmp = _state_path() + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, _state_path())
    except OSError as e:
        decky.logger.error("state.json write failed: %s", e)


def power_state():
    """What the TDP / battery / brightness controls should show on open."""
    p = active_profile(_ensure_store()) or {}
    tdp = p.get("tdp")
    pl = (tdp["pl1"], tdp["pl2"], tdp["pl3"]) if tdp else DEFAULT_TDP
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
        "pl_min": PL_MIN, "pl_max": PL_MAX, "pl1_safe_max": PL1_SAFE_MAX,
        "battery_limit": clamp_battery(bl) if bl is not None else BATTERY_MAX,
        "brightness": brightness if brightness in BRIGHTNESS_LEVELS else "",
    }


def apply_tdp(enabled, pl1, pl2, pl3):
    """Store the active profile's TDP, then push it. Returns a result dict.

    ON  -> `z13ctl tdp --set` as deck: the same targeted write the desktop Fans +
           Power window does (a full profile re-apply would rewrite the platform
           profile and the fan curve, i.e. a 5-10 s fan dip per slider release).
    OFF -> `z13-profile apply <active> --no-hooks`: the platform-profile rewrite is
           what hands the limits back to the firmware (desktop does the same).
    """
    tdp = clamp_tdp(pl1, pl2, pl3)
    name, err = _set_active_field(tdp=tdp if enabled else None)
    if err:
        return {"ok": False, "saved": False, "message": f"Not saved: {err}", "tdp": tdp}
    if enabled:
        rc, out = _run(tdp_argv(tdp), as_deck=True)
        ok = rc == 0
        msg = (f"{name}: {tdp['pl1']}/{tdp['pl2']}/{tdp['pl3']} W" if ok
               else f"{name}: saved, but z13ctl tdp failed ({out[-120:] or f'rc={rc}'})")
    else:
        rc, out = _run(Z13_PROFILE + ["apply", name, "--no-hooks"], timeout=120, as_deck=True)
        ok = rc == 0 and "ERR  profile" not in out
        msg = (f"{name}: power limits back to the firmware" if ok
               else f"{name}: saved, but re-apply failed ({out[-120:] or f'rc={rc}'})")
    return {"ok": ok, "saved": True, "message": msg, "tdp": tdp}


def apply_battery_limit(value):
    """Store the active profile's battery_limit, then `z13ctl batterylimit --set` as deck.
    The hardware write still happens if the store can't be written (reported)."""
    v = clamp_battery(value)
    name, err = _set_active_field(battery_limit=v)
    rc, out = _run([Z13CTL, "batterylimit", "--set", str(v)], as_deck=True)
    ok = rc == 0
    msg = f"Charge limit {v}%" if ok else f"Charge limit failed ({out[-120:] or f'rc={rc}'})"
    if err:
        msg += f" (not saved to profile: {err})"
    return {"ok": ok, "saved": err is None, "message": msg, "value": v, "profile": name or ""}


def max_fans_off_argv(state, store):
    """z13ctl argv for Max Fans OFF; same order as the desktop app's off()
    (src/dashboard_ui.py): the saved snapshot ([temp, pwm]), else the ACTIVE
    profile's curve ([temp, pct]), else firmware auto. saved_curve null means
    "the profile had no curve" (profiles.apply), so it must not fall back to a
    made-up custom curve."""
    saved = (state or {}).get("saved_curve")
    if saved:
        return [Z13CTL, "fancurve", "--set", ",".join(f"{int(t)}:{int(p)}" for t, p in saved)]
    curve = (active_profile(store) or {}).get("fan_curve")
    if curve:
        return [Z13CTL, "fancurve", "--set", ",".join(f"{int(t)}:{int(p)}%" for t, p in curve)]
    return [Z13CTL, "fancurve", "--reset"]


def set_max_fans_blocking(enabled):
    """Max Fans ON/OFF (blocking; run it in a thread). Returns True on success."""
    if enabled:
        # Snapshot the current custom curve (unless already maxed) so OFF restores it exactly.
        cur = _current_curve_pwm()
        if len(cur) == 8 and not all(pwm >= 255 for _, pwm in cur):
            _update_max_fans(saved_curve=cur)
        rc, _ = _run([Z13CTL, "fancurve", "--set", MAX_FANS_SPEC])
    else:
        rc, _ = _run(max_fans_off_argv(_load_max_fans(), _read_store()))
    ok = rc == 0
    if ok:
        _update_max_fans(enabled=enabled)
    return ok


class DeckyFans:
    """The fan commands the shared watchdog needs, as z13ctl calls from here.

    Each raises on a non-zero exit, so a failed profile rewrite stops the recovery
    before a bare curve write (the watchdog reports the error).
    """

    def _z13ctl(self, *args):
        rc, out = _run([Z13CTL, *args])
        if rc != 0:
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
        rc, out = _run(tdp_argv(tdp), as_deck=True)
        if rc != 0:
            return f"TDP not restored ({out[-120:] or f'rc={rc}'})"
        return None  # no readback: reading the PPT values can lock the EC

    def restore_undervolt(self):
        uv = (active_profile(_read_store()) or {}).get("undervolt")
        if uv is None:
            return None
        rc, out = _run([Z13CTL, "undervolt", "--set", str(uv)], as_deck=True)
        if rc != 0:
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
        if not os.path.isdir(d):
            os.makedirs(d)
            os.chown(d, *owner)
        return FanTrace.to_file(path, _sensors, "gamemode", owner=owner)
    except OSError as e:
        decky.logger.error("No fan trace: %s", e)
        return None


def make_fan_watchdog(sensors=None, fans=None, may_act=None, clock=time.monotonic, trace=None):
    return FanWatchdog(sensors or _sensors, fans or DeckyFans(),
                       max_fans_on=lambda: bool(_load_max_fans().get("enabled", False)),
                       may_act=may_act or _in_gamemode, clock=clock, trace=trace)


async def fan_watchdog_tick(watchdog):
    """One watchdog tick under the write lock; skipped while a write holds it.
    Returns the watchdog's message (None when it didn't act or was skipped)."""
    lock = _lock()
    if lock.locked():
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


def _lock():
    """One store write + hardware push at a time (slider releases can queue up)."""
    global _write_lock
    if _write_lock is None:
        _write_lock = asyncio.Lock()
    return _write_lock


class Plugin:
    # ---- Max Fans (mirrors the desktop app: 100% <-> custom aggressive curve) ----
    async def get_max_fans(self) -> bool:
        return bool(_load_max_fans().get("enabled", False))

    async def set_max_fans(self, enabled: bool) -> bool:
        async with _lock():
            return await asyncio.to_thread(set_max_fans_blocking, bool(enabled))

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

    async def set_app_profile(self, name: str) -> bool:
        # A full apply takes seconds (longer with a wedged lightbar): keep it off the
        # loader's event loop, and serialised with the other writes.
        async with _lock():
            rc, out = await asyncio.to_thread(
                lambda: _run(Z13_PROFILE + ["apply", name], 180, as_deck=True))
        decky.logger.info("profile apply %s -> rc=%s", name, rc)
        return rc == 0 and "ERR  profile" not in out

    # ---- Performance profile ----
    async def get_profile(self) -> str:
        return await asyncio.to_thread(_current_platform_profile)

    async def set_profile(self, profile: str) -> bool:
        async with _lock():
            rc, _ = await asyncio.to_thread(_run, [Z13CTL, "profile", "--set", profile])
        return rc == 0

    # ---- Power limits / battery / lighting / telemetry (Phase 11) ----
    # Blocking work runs in a thread so the fan watchdog keeps ticking meanwhile.
    async def get_power_state(self) -> dict:
        return await asyncio.to_thread(power_state)

    async def set_tdp(self, enabled: bool, pl1: int, pl2: int, pl3: int) -> dict:
        async with _lock():
            return await asyncio.to_thread(apply_tdp, bool(enabled), pl1, pl2, pl3)

    async def set_battery_limit(self, value: int) -> dict:
        async with _lock():
            return await asyncio.to_thread(apply_battery_limit, value)

    async def set_brightness(self, level: str) -> dict:
        async with _lock():
            res = await asyncio.to_thread(set_brightness_per_device, level)
        if res["ok"]:
            _save_plugin_state(brightness=level)
        return res

    async def get_telemetry(self) -> dict:
        return telemetry()   # a handful of sysfs reads; cheap enough inline

    # ---- Lifecycle ----
    async def _main(self):
        self.loop = asyncio.get_event_loop()
        self._enforce_task = self.loop.create_task(self._enforce_max_fans())
        self._desktop_task = self.loop.create_task(self._watch_desktop_app())
        decky.logger.info("z13-helper up. z13ctl=%s exists=%s max_fans=%s",
                          Z13CTL, os.path.exists(Z13CTL),
                          _load_max_fans().get("enabled"))

    async def _unload(self):
        for task in (getattr(self, "_enforce_task", None), getattr(self, "_desktop_task", None)):
            if task:
                task.cancel()
        decky.logger.info("z13-helper unloading")

    async def _uninstall(self):
        decky.logger.info("z13-helper uninstalled")

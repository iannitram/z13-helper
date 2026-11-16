"""Explicit platform support and read-only capability discovery (#22).

The app supports one device on one OS: Linux (SteamOS) on the ASUS ROG Flow Z13
GZ302EA (SPEC R04). ``detect()`` reports whether the machine is that device and
which controls the kernel exposes, from sysfs metadata only: it never reads a live
Armoury ``current_value`` (ROADMAP principle 5) and never writes. Frontends use it
to disable a control with a reason instead of offering one that cannot work.
Stdlib only; pass ``root`` to probe a test tree.
"""

import os
import re
import sys
import time

try:
    import pwd
except ImportError:  # not POSIX
    pwd = None

UNKNOWN = "unknown"

SUPPORTED_PRODUCT = "GZ302"
CAPABILITIES = ("platform_profile", "battery_limit", "keyboard_backlight",
                "fan_curve", "tdp", "undervolt")
REASONS = {
    "platform_profile": "no firmware platform profile (/sys/firmware/acpi/platform_profile)",
    "battery_limit": "the battery does not expose a charge limit",
    "keyboard_backlight": "no ASUS keyboard backlight found",
    "fan_curve": "the kernel exposes no custom fan curve (asus_custom_fan_curve)",
    "tdp": "no asus-armoury or asus-nb-wmi power-limit interface",
    "undervolt": "the ryzen_smu kernel module is not loaded (stock SteamOS)",
}


Z13CTL = "~/.local/bin/z13ctl"
VERSION_TIMEOUT_S = 3        # the probe runs on UI-thread re-probes: keep it short
_VERSION_OK_TTL_S = 3600     # a found version only changes on an update
_VERSION_FAIL_TTL_S = 60     # do not respawn a missing or hung binary every 30 s
_version_cache = {"at": 0.0, "ttl": 0.0, "value": None}


def reset_version_cache():
    _version_cache.update(at=0.0, ttl=0.0, value=None)


def backend_version(runner=None):
    """z13ctl's version from ``z13ctl --version`` (read-only, bounded), else UNKNOWN.

    ``runner(argv, timeout)`` returns ``(rc, out)``; the default is host.run. A
    failure (missing binary, hang, odd output) is UNKNOWN and is cached briefly
    so a re-probe never waits on it twice in a row. Never raises.
    """
    now = time.monotonic()
    if runner is None and _version_cache["value"] is not None \
            and now - _version_cache["at"] < _version_cache["ttl"]:
        return _version_cache["value"]
    value = UNKNOWN
    try:
        argv = [os.path.expanduser(Z13CTL), "--version"]
        if runner is None:
            import host
            res = host.run(argv, timeout=VERSION_TIMEOUT_S)
            rc, out = res.rc, res.out
        else:
            rc, out = runner(argv, VERSION_TIMEOUT_S)
        m = re.search(r"(\d+\.\d+\.\d+)", out or "") if rc == 0 else None
        if m:
            value = m.group(1)
    except Exception:  # noqa: BLE001 - a probe must never break detect()
        value = UNKNOWN
    if runner is None:
        _version_cache.update(at=now, value=value,
                              ttl=_VERSION_OK_TTL_S if value != UNKNOWN else _VERSION_FAIL_TTL_S)
    return value


def _p(root, path):
    return os.path.join(root, path.lstrip("/"))


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _hwmon_named(root, name):
    base = _p(root, "/sys/class/hwmon")
    try:
        entries = os.listdir(base)
    except OSError:
        return False
    return any(_read(os.path.join(base, e, "name")) == name for e in entries)


def _any_child(path, prefix=None, contains=None):
    try:
        names = os.listdir(path)
    except OSError:
        return False
    return any((prefix is None or n.startswith(prefix))
               and (contains is None or contains in n) for n in names)


def _uid_session(uid, root="/"):
    name, home = UNKNOWN, UNKNOWN
    if pwd is not None:
        try:
            pw = pwd.getpwuid(uid)
            name, home = pw.pw_name, pw.pw_dir
        except KeyError:
            pass
    if name == UNKNOWN and uid == 1000:       # the SteamOS deck session
        name, home = "deck", "/home/deck"
    runtime = f"/run/user/{uid}"
    return {"user": name, "uid": uid, "home": home, "runtime_dir": runtime,
            "bus": f"unix:path={runtime}/bus"}


def session(environ=None, euid=None, root="/"):
    """The desktop session user and runtime dir (read-only; never assumes ``deck``).

    A non-root process is its own session. Root (Decky/service) takes the user from
    SUDO_UID/PKEXEC_UID, else the lowest regular uid with a /run/user/<uid> dir,
    else uid 1000 (the SteamOS default, so the Z13 resolves to deck as before).
    """
    environ = os.environ if environ is None else environ
    euid = os.geteuid() if euid is None else euid
    if euid != 0:
        return _uid_session(euid, root)
    for key in ("SUDO_UID", "PKEXEC_UID"):
        if environ.get(key, "").isdigit() and int(environ[key]) != 0:
            return _uid_session(int(environ[key]), root)
    try:
        uids = sorted(int(n) for n in os.listdir(_p(root, "/run/user")) if n.isdigit() and int(n) >= 1000)
    except OSError:
        uids = []
    return _uid_session(uids[0] if uids else 1000, root)


def _inventory(root):
    """Facts readable without hardware access; anything not found is UNKNOWN or empty."""
    ps = _p(root, "/sys/class/power_supply")
    try:
        supplies = sorted(os.listdir(ps))
    except OSError:
        supplies = []
    kinds = {n: _read(os.path.join(ps, n, "type")) for n in supplies}
    battery = any(k == "Battery" or n.startswith("BAT") for n, k in kinds.items())
    mains = [n for n, k in kinds.items() if k == "Mains"]
    online = [n for n in mains if _read(os.path.join(ps, n, "online")) == "1"]
    power_source = (UNKNOWN if not supplies else
                    "ac" if online else "battery" if battery else UNKNOWN)
    choices = _read(_p(root, "/sys/firmware/acpi/platform_profile_choices"))
    hw = _p(root, "/sys/class/hwmon")
    sensors, fans = [], 0
    try:
        hwmons = sorted(os.listdir(hw))
    except OSError:
        hwmons = []
    for e in hwmons:
        nm = _read(os.path.join(hw, e, "name"))
        if nm:
            sensors.append(nm)
        try:
            fans += sum(1 for f in os.listdir(os.path.join(hw, e))
                        if f.startswith("fan") and f.endswith("_input"))
        except OSError:
            pass
    rails = {}
    base = _p(root, "/sys/class/firmware-attributes/asus-armoury/attributes")
    try:
        attrs = sorted(os.listdir(base))
    except OSError:
        attrs = []
    for a in attrs:
        d = os.path.join(base, a)
        # min/max/default are static metadata; current_value is deliberately not read.
        vals = {k: _read(os.path.join(d, f)) or UNKNOWN
                for k, f in (("min", "min_value"), ("max", "max_value"), ("default", "default_value"))}
        if any(v != UNKNOWN for v in vals.values()):
            rails[a] = vals
    return {
        "battery_present": battery,
        "power_source": power_source,
        "profile_choices": choices.split() if choices else [],
        "fan_count": fans if fans else UNKNOWN,
        "sensors": sensors,
        "rails": rails,
        "backend_version": UNKNOWN,   # filled by detect() from the z13ctl probe
    }


def detect(root="/", system=None, version_runner=None):
    """Return ``{"supported", "system", "product", "reason", "capabilities"}``.

    ``capabilities`` maps each name in CAPABILITIES to
    ``{"available": bool, "reason": str}`` (reason is "" when available). Controls
    are only available on the supported device; elsewhere every one is off with the
    device reason. Also: ``session`` (user, uid, runtime_dir), ``battery_present``,
    ``power_source``, ``profile_choices``, ``fan_count``, ``sensors``, ``rails``
    (name -> min/max/default), ``operations`` (available names), ``unsupported``
    (name -> reason) and ``backend_version`` (``z13ctl --version``, 3 s limit, cached). A value that cannot be read without
    hardware is the string "unknown".
    """
    system = system or sys.platform
    product = _read(_p(root, "/sys/class/dmi/id/product_name"))
    reason = ""
    if not system.startswith("linux"):
        reason = f"Z13-Helper runs on Linux (SteamOS) only, not {system}"
    elif SUPPORTED_PRODUCT not in product.upper():
        reason = ("Z13-Helper supports the ASUS ROG Flow Z13 GZ302EA only"
                  + (f" (this is {product})" if product else " (device not identified)"))
    supported = not reason

    found = {}
    if supported:
        ps = _p(root, "/sys/class/power_supply")
        found["platform_profile"] = os.path.exists(_p(root, "/sys/firmware/acpi/platform_profile"))
        found["battery_limit"] = any(
            os.path.exists(os.path.join(ps, n, "charge_control_end_threshold"))
            for n in (os.listdir(ps) if os.path.isdir(ps) else []))
        found["keyboard_backlight"] = _any_child(_p(root, "/sys/class/leds"), contains="kbd_backlight")
        found["fan_curve"] = _hwmon_named(root, "asus_custom_fan_curve")
        found["tdp"] = (os.path.isdir(_p(root, "/sys/class/firmware-attributes/asus-armoury/attributes"))
                        or os.path.isdir(_p(root, "/sys/devices/platform/asus-nb-wmi")))
        found["undervolt"] = (os.path.isdir(_p(root, "/sys/kernel/ryzen_smu_drv"))
                              or os.path.isdir(_p(root, "/sys/module/ryzen_smu")))
    caps = {}
    for name in CAPABILITIES:
        ok = bool(found.get(name))
        caps[name] = {"available": ok, "reason": "" if ok else (reason or REASONS[name])}
    inv = _inventory(root) if supported else {
        "battery_present": False, "power_source": UNKNOWN, "profile_choices": [],
        "fan_count": UNKNOWN, "sensors": [], "rails": {}, "backend_version": UNKNOWN}
    if supported and not inv["battery_present"]:
        caps["battery_limit"] = {"available": False, "reason": "no battery present"}
    if supported and (root == "/" or version_runner is not None):
        # Only the live machine (or an injected runner) is asked; a fake sysfs tree is not.
        inv["backend_version"] = backend_version(version_runner)
    inv["operations"] = [n for n in CAPABILITIES if caps[n]["available"]]
    inv["unsupported"] = {n: c["reason"] for n, c in caps.items() if not c["available"]}
    return {"supported": supported, "system": system, "product": product,
            "reason": reason, "capabilities": caps, "session": session(root=root), **inv}

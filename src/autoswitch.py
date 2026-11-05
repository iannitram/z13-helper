"""Charger-aware autoswitch intent, stdlib-only.

z13ctl's daemon performs the actual AC/battery switch. This module owns the
app-side intent: which app profile each power source maps to, and the manual
hold that pauses autoswitch without forgetting those choices. It also builds
the z13ctl argv; the caller resolves app profile names to daemon profile names.

Charger presence is not a power budget: nothing here chooses a TDP or infers
watts from USB power. It only returns the profile the user picked.
"""

SOURCES = ("ac", "battery")
HARDWARE_FIELDS = ("tdp", "fan_curve", "undervolt")


def default_config():
    return {"ac": None, "battery": None, "hold": False, "enabled": False}


def normalize(config, names):
    """Keep known app profiles and explicit enabled intent; hold pauses it."""
    known = set(names)
    out = default_config()
    if not isinstance(config, dict):
        return out
    for source in SOURCES:
        value = config.get(source)
        if isinstance(value, str) and value in known:
            out[source] = value
    out["hold"] = bool(config.get("hold"))
    out["enabled"] = bool(config.get("enabled"))
    return out


def decision(config, on_ac):
    """The app profile to apply for this power source, or None to stay put.

    None means autoswitch is off/held, that source has no target, or the stored
    value is not a profile name. The daemon is the writer; this is the pure
    decision the UI and tests share.
    """
    if not isinstance(on_ac, bool):
        return None
    if not isinstance(config, dict) or not config.get("enabled") or config.get("hold"):
        return None
    target = config.get("ac" if on_ac else "battery")
    return target if isinstance(target, str) and target else None


def has_hardware_overrides(profile):
    """True when z13ctl has performance settings worth a named profile.

    Lighting, brightness, battery limit and EPP are app-level fields; z13ctl's
    autoswitch only carries the daemon-owned performance profile.
    """
    return isinstance(profile, dict) and any(
        profile.get(field) is not None for field in HARDWARE_FIELDS
    )


def _target(targets, name):
    """The daemon target for an app profile name; anything but a str has none."""
    return targets.get(name) if isinstance(name, str) else None


def backend_argv(config, targets):
    """z13ctl autoswitch arguments (without the binary) for `config`.

    `targets` maps app profile names to daemon targets. An empty target clears
    that side. Disabled or held means `--off` with the targets retained;
    no targets at all clears the configuration.
    """
    if not isinstance(config, dict):
        config = {}
    if not isinstance(targets, dict):
        targets = {}
    ac = _target(targets, config.get("ac"))
    battery = _target(targets, config.get("battery"))
    if not ac and not battery:
        return ["autoswitch", "--clear"]
    argv = ["autoswitch", "--ac", ac or "", "--battery", battery or ""]
    off = not config.get("enabled") or config.get("hold")
    argv.append("--off" if off else "--on")
    return argv


def describe(config):
    """One line for the status bar."""
    if (
        not isinstance(config, dict)
        or not config.get("enabled")
        or not (config.get("ac") or config.get("battery"))
    ):
        return "Autoswitch off"
    ac = config.get("ac") or "leave unchanged"
    battery = config.get("battery") or "leave unchanged"
    held = " (held)" if config.get("hold") else ""
    return f"Autoswitch: {ac} on AC, {battery} on battery{held}"

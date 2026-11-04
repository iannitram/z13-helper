"""Explicit platform support and read-only capability discovery (#22).

The app supports one device on one OS: Linux (SteamOS) on the ASUS ROG Flow Z13
GZ302EA (SPEC R04). ``detect()`` reports whether the machine is that device and
which controls the kernel exposes, from sysfs metadata only: it never reads a live
Armoury ``current_value`` (ROADMAP principle 5) and never writes. Frontends use it
to disable a control with a reason instead of offering one that cannot work.
Stdlib only; pass ``root`` to probe a test tree.
"""

import os
import sys

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


def detect(root="/", system=None):
    """Return ``{"supported", "system", "product", "reason", "capabilities"}``.

    ``capabilities`` maps each name in CAPABILITIES to
    ``{"available": bool, "reason": str}`` (reason is "" when available). Controls
    are only available on the supported device; elsewhere every one is off with the
    device reason.
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
    return {"supported": supported, "system": system, "product": product,
            "reason": reason, "capabilities": caps}

"""Design tokens: the one place colours, sizes and radii are defined (#229, ADR 0002).

The values live in design_tokens.json next to this file so the Decky plugin can
bundle the same file (decky/src/tokens.ts). This module only loads and looks
them up; it has no Qt in it, so tests and tools can read the tokens anywhere.

    colours("dark")["accent"]       one theme's colours, by name
    resolve("system", "light")      which theme a setting means right now
    contrast("#fff", "#000")        WCAG contrast ratio, for the token tests
    css_variables("dark")           {"--z13-accent": "#...", ...} for the Decky panel
"""

import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "design_tokens.json")

MODES = ("dark", "light")
# What the user can pick; "system" follows the desktop's colour scheme.
CHOICES = ("system", "dark", "light")
DEFAULT_CHOICE = "system"
# The theme used when the desktop does not say which one it wants.
FALLBACK_MODE = "dark"


def load(path=PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


TOKENS = load()
FONT = TOKENS["font"]
SPACE = TOKENS["space"]
RADIUS = TOKENS["radius"]
SIZE = TOKENS["size"]


def colours(mode):
    """The colour table of one theme ("dark" or "light")."""
    return TOKENS["color"][mode if mode in MODES else FALLBACK_MODE]


def resolve(choice, system_mode=None):
    """Turn a theme setting into "dark" or "light".

    `choice` is the saved setting; anything unknown counts as "system".
    `system_mode` is what the desktop asks for, or None when it does not say.
    """
    if choice in MODES:
        return choice
    return system_mode if system_mode in MODES else FALLBACK_MODE


def _channel(value):
    value /= 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def luminance(colour):
    """WCAG relative luminance of a 6-digit hex colour."""
    h = colour.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)


def contrast(a, b):
    """WCAG contrast ratio between two hex colours (1.0 to 21.0)."""
    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def css_variables(mode="dark", prefix="--z13-"):
    """One theme as CSS custom properties, named like the tokens with dashes."""
    return {prefix + name.replace("_", "-"): value for name, value in colours(mode).items()}

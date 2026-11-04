"""Shared design tokens (stdlib only): palette, accents, radii, spacing, touch sizes.

`design_tokens.json` is the one source for the desktop app (`theme.py`) and the
Decky plugin (`decky/src/tokens.ts` imports the same file at build time). ADR 0002.
"""

import json
from pathlib import Path

TOKENS_PATH = Path(__file__).resolve().with_name("design_tokens.json")


def load(path=TOKENS_PATH):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


TOKENS = load()
TOUCH_MIN_PX = TOKENS["touch"]["touch_min_px"]

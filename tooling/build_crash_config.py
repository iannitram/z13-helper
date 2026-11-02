"""Bake private bug-drop build variables into an ignored packaging input.

Run before building either frontend. Never prints credentials and never reads
the private pipeline configuration directly; the build supplies its environment.
"""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parent.parent


def bake(output: Path, commit: str, environ=None):
    env = os.environ if environ is None else environ
    if not re.fullmatch(r"[0-9a-f]{7,64}", commit):
        raise ValueError("A source commit is required")
    base = env.get("CRAFT_BUG_DROP_BASE", "")
    token = env.get("CRAFT_BUG_DROP_TOKEN", "")
    if base or token:
        url = urlsplit(base)
        if not token or url.scheme != "https" or not url.netloc or url.username is not None or url.query or url.fragment:
            raise ValueError("A complete HTTPS bug-drop build configuration is required")
    version = json.loads((REPO / "decky" / "package.json").read_text())["version"]
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"base": base, "token": token, "version": version, "commit": commit}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "src" / "_crash_build.json")
    parser.add_argument("--commit", required=True, help="Source revision supplied by the build")
    args = parser.parse_args()
    try:
        bake(args.output, args.commit)
    except ValueError:
        parser.exit(1, "Invalid bug-drop build configuration\n")

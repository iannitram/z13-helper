#!/usr/bin/env bash
# Z13-Helper — Flatpak launcher.
# Inside the sandbox the runtime's python3 is on PATH; PyQt6 is installed
# under /app via pip (see manifest). We just point at our main.py.
exec python3 /app/share/z13-helper/main.py "$@"

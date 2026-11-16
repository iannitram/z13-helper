"""Phase 15e probe: tooling/z13_comfort_probe.py is run directly, so it must be executable.

Locked for the open review finding on #127: the probe was committed as mode 100644
while the other tooling scripts are 100755.
"""

import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_comfort_probe_is_executable():
    probe = REPO / "tooling" / "z13_comfort_probe.py"
    assert os.access(probe, os.X_OK), "tooling/z13_comfort_probe.py must have the executable bit (chmod +x)"

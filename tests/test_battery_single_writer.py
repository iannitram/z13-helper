"""Login must have one battery charge-limit writer: the profile restore.

z13-battery-default.service wrote a fixed 80% at login, unordered against
z13-restore-profile.service (which applies the selected profile's limit).
Whichever ran last won. The restore path already applies 80% itself when no
profile store exists (z13-restore-profile -> z13-balanced -> batterylimit 80),
so the fixed unit is retired. Unit files are checked as text.
"""

import re
from pathlib import Path

import pytest

import install_check as ic

REPO = Path(__file__).resolve().parent.parent
USER_UNITS = REPO / "packaging" / "host" / "systemd" / "user"
RETIRED = "z13-battery-default.service"


def unit_keys(path):
    """(section, key) -> value list for a systemd unit file, as plain text."""
    out, section = {}, None
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = re.fullmatch(r"\[(\w+)\]", line)
        if m:
            section = m.group(1)
            continue
        key, _, val = line.partition("=")
        out.setdefault((section, key.strip()), []).append(val.strip())
    return out


def test_retired_unit_is_not_shipped():
    assert not (USER_UNITS / RETIRED).exists()


def test_no_shipped_unit_references_retired_unit():
    for unit in USER_UNITS.glob("*.service"):
        assert RETIRED not in unit.read_text(), unit.name


def test_helper_unit_does_not_pull_in_retired_unit():
    keys = unit_keys(USER_UNITS / "z13-helper.service")
    for k in [("Unit", "After"), ("Unit", "Wants"), ("Unit", "Requires"), ("Unit", "BindsTo")]:
        for val in keys.get(k, []):
            assert RETIRED not in val, (k, val)


def test_restore_unit_is_the_login_writer():
    keys = unit_keys(USER_UNITS / "z13-restore-profile.service")
    assert keys[("Service", "ExecStart")] == ["%h/.local/bin/z13-restore-profile"]
    assert ("Install", "WantedBy") in keys


def test_manifest_and_verify_list_no_retired_unit():
    assert not any(RETIRED in row[0] or RETIRED in row[1] for row in ic.MANIFEST)
    assert RETIRED not in ic.USER_UNITS_ENABLED

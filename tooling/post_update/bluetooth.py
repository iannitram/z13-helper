"""Built-in post-update recipe 5 (issue #71): MT7925 Bluetooth workarounds.

Copies the four MT7925 workaround files from `packaging/host/` into their `/etc` locations
and registers them in the SteamOS atomic-update keep-list so they survive A/B system updates.
Only reads and writes through the injected HostIO; checks never write or run commands.
"""

import os

from .builtin import KEEP_DIR, HostIO, _refuse_unless_supported, supported_device
from .recipe import Recipe

KEEP_CONF = "/etc/atomic-update.conf.d/z13-bucket1.conf"
RESUME_SCRIPT = "/etc/systemd/system/mt7925-bt-resume.sh"

BT_FILES = (
    ("packaging/host/udev/50-mt7925-bt-no-autosuspend.rules", "/etc/udev/rules.d/50-mt7925-bt-no-autosuspend.rules"),
    ("packaging/host/systemd/system/bt-mt7925-cycle.service", "/etc/systemd/system/bt-mt7925-cycle.service"),
    ("packaging/host/systemd/system/mt7925-bt-resume.service", "/etc/systemd/system/mt7925-bt-resume.service"),
    ("packaging/host/systemd/system/mt7925-bt-resume.sh", "/etc/systemd/system/mt7925-bt-resume.sh"),
)


def _registered_paths(io: HostIO) -> set:
    registered = set()
    for name in sorted(io.listdir(KEEP_DIR)):
        if not name.endswith(".conf"):
            continue
        for line in (io.read(f"{KEEP_DIR}/{name}") or "").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                registered.add(line)
    return registered


def _run_or_raise(io: HostIO, argv: list) -> None:
    rc, out = io.run(argv)
    if rc != 0:
        raise RuntimeError(f"{' '.join(argv)} failed (rc {rc}): {out}")


def default_source_root() -> str:
    here = os.path.abspath(__file__)
    cand_a = os.path.dirname(os.path.dirname(here))
    cand_b = os.path.dirname(cand_a)
    for cand in (cand_a, cand_b):
        if all(os.path.isfile(os.path.join(cand, src)) for src, _ in BT_FILES):
            return cand
    return cand_b


def bluetooth_recipe(io: HostIO, src_root: str | None = None) -> Recipe:
    if src_root is None:
        src_root = default_source_root()

    def source(src: str) -> str:
        if src_root is not None:
            full = os.path.join(src_root, src)
            if io.exists(full) or not io.exists(src):
                return full
        return src

    def check() -> str:
        if not supported_device(io):
            return "na"
        for src, dest in BT_FILES:
            dest_text = io.read(dest)
            src_text = io.read(source(src))
            if dest_text is None or src_text is None or dest_text != src_text:
                return "needs"
        registered = _registered_paths(io)
        for _, dest in BT_FILES:
            if dest not in registered:
                return "needs"
        return "ok"

    def apply() -> None:
        _refuse_unless_supported(io)
        wrote_file = False
        wrote_script = False
        for src, dest in BT_FILES:
            src_text = io.read(source(src))
            if src_text is None:
                raise RuntimeError(f"cannot read source file {src}")
            if io.read(dest) != src_text:
                io.write(dest, src_text)
                wrote_file = True
                wrote_script = wrote_script or dest == RESUME_SCRIPT
        missing = [dest for _, dest in BT_FILES if dest not in _registered_paths(io)]
        if missing:
            text = io.read(KEEP_CONF) or ""
            if text and not text.endswith("\n"):
                text += "\n"
            io.write(KEEP_CONF, text + "".join(path + "\n" for path in missing))
        if wrote_script:
            _run_or_raise(io, ["chmod", "755", RESUME_SCRIPT])
        if wrote_file:
            _run_or_raise(io, ["systemctl", "daemon-reload"])
            _run_or_raise(io, ["systemctl", "enable", "bt-mt7925-cycle.service", "mt7925-bt-resume.service"])
            _run_or_raise(io, ["udevadm", "control", "--reload"])

    return Recipe(
        id="mt7925-bt",
        title="MT7925 Bluetooth workarounds",
        fixes="A2DP and HID drops, connect-time race after resume",
        check=check,
        apply=apply,
        sudo=True,
        default_on=True,
        replaces=tuple(dest for _, dest in BT_FILES),
    )

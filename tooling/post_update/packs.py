"""Optional packs 8 (dev headers and .pc files) and 9 (coredump size cap) (issue #72).

Both are off by default (`default_on=False`): the user ticks them. Host access goes only
through HostIO. Checks are `na` off a GZ302 on SteamOS and never write.
"""

import os
from posixpath import dirname

from .builtin import HostIO, _refuse_unless_supported, supported_device
from .recipe import Recipe

PACMAN_CACHE = "/var/cache/pacman/pkg"
COREDUMP_CONF = "/etc/systemd/coredump.conf.d/50-limit.conf"
COREDUMP_TEXT = "[Coredump]\nStorage=external\nMaxUse=500M\nExternalSizeMax=500M\n"
_PC_DIRS = ("usr/lib/pkgconfig/", "usr/share/pkgconfig/", "usr/lib64/pkgconfig/")


def missing_pc_files(io: HostIO) -> dict:
    """{package: [absolute .pc paths pacman owns but the disk lacks]} from `pacman -Ql`."""
    rc, out = io.run(["pacman", "-Ql"])
    if rc != 0:
        raise RuntimeError(f"pacman -Ql failed: {out}")
    found: dict = {}
    for line in out.splitlines():
        pkg, _, path = line.partition(" ")
        rel = path.lstrip("/")
        if path.endswith(".pc") and rel.startswith(_PC_DIRS) and not io.exists(path):
            found.setdefault(pkg, []).append(path)
    return found


def _cached_package(io: HostIO, pkg: str, version: str):
    prefix = f"{pkg}-{version}-"
    names = [n for n in io.listdir(PACMAN_CACHE) if n.startswith(prefix) and ".pkg.tar" in n
             and not n.endswith(".sig")]
    return os.path.join(PACMAN_CACHE, sorted(names)[-1]) if names else None


def _versions(io: HostIO) -> dict:
    rc, out = io.run(["pacman", "-Q"])
    if rc != 0:
        raise RuntimeError(f"pacman -Q failed: {out}")
    return dict(line.split(" ", 1) for line in out.splitlines() if " " in line)


def dev_headers_recipe(io: HostIO) -> Recipe:
    """Pack 8: extract only the missing .pc files from the cached packages (never --overwrite)."""

    def check() -> str:
        if not supported_device(io):
            return "na"
        return "needs" if missing_pc_files(io) else "ok"

    def apply() -> None:
        _refuse_unless_supported(io)
        versions = _versions(io)
        for pkg, paths in missing_pc_files(io).items():
            version = versions.get(pkg, "")
            cached = _cached_package(io, pkg, version)
            if cached is None:
                rc, out = io.run(["pacman", "-Sw", "--noconfirm", pkg])
                if rc != 0:
                    raise RuntimeError(f"pacman -Sw {pkg} failed: {out}")
                cached = _cached_package(io, pkg, version)
            if cached is None:
                raise RuntimeError(f"no cached package for {pkg} {version}")
            # -k keeps files that already exist; only the missing ones are written.
            rc, out = io.run(["bsdtar", "-xkf", cached, "-C", "/", *[p.lstrip("/") for p in paths]])
            if rc != 0:
                raise RuntimeError(f"bsdtar could not extract from {cached}: {out}")

    return Recipe(id="dev-headers", title="Dev headers and .pc files (optional)",
                  fixes="the update strips pkg-config files, so gcc and Tauri builds on the host break",
                  check=check, apply=apply, sudo=True, network=True, pacman=True)


def coredump_cap_recipe(io: HostIO) -> Recipe:
    """Pack 9: drop-in that caps crash dump size so they cannot fill the small rootfs."""

    def check() -> str:
        if not supported_device(io):
            return "na"
        return "ok" if io.read(COREDUMP_CONF) == COREDUMP_TEXT else "needs"

    def apply() -> None:
        _refuse_unless_supported(io)
        rc, out = io.run(["mkdir", "-p", dirname(COREDUMP_CONF)])
        if rc != 0:
            raise RuntimeError(f"mkdir failed: {out}")
        io.write(COREDUMP_CONF, COREDUMP_TEXT)

    return Recipe(id="coredump-cap", title="Coredump size cap (optional)",
                  fixes="crash dumps filling the small rootfs", check=check, apply=apply,
                  sudo=True, replaces=(COREDUMP_CONF,))


def pack_recipes(io: HostIO) -> list:
    return [dev_headers_recipe(io), coredump_cap_recipe(io)]

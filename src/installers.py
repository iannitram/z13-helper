#!/usr/bin/env python3
"""Installers behind the Updates panel's "Install / Update" button.

Runs ON THE HOST (the Flatpak feeds this file to the host python3 over
`flatpak-spawn --host python3 -u -`), stdlib only, prints one log line per
step so the dialog can stream it. Exit code 0 = installed.

What can be installed from here, and how:
- z13ctl   : GitHub release tarball -> sha256 verified against checksums.txt ->
             backup ~/.local/bin/z13ctl.bak-<old> -> install -> `sudo z13ctl setup`
             (udev rules + perms service) -> user units z13ctl.socket/.service with
             a drop-in that runs the daemon `--no-button` (our watcher owns the
             side button) -> enable --now -> sanity `z13ctl status`.
- decky    : the official Decky Loader installer script (needs sudo), then a
             health check for the stale-loader-holding-:1337 problem.
- steamos  : `sudo steamos-update` (official updater); a reboot finishes it.
Everything else in the panel is reference-only (asusctl/InputPlumber/hhd are
deliberately not installed here; the kernel comes with SteamOS; G-Helper is
Windows; the community repos are code to read, not packages).

CLI:  installers.py plan <id>      print what would happen
      installers.py install <id>   do it (latest upstream)
      installers.py repair <id>    z13ctl/decky only: make the installed version
                                   work again (perms, units, service) without
                                   upgrading; installs if missing. Used by
                                   install.sh --repair after a SteamOS update.
                                   Z13_INSTALL_DRY_RUN=1 logs decisions only.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.request

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".cache", "z13-helper", "updates")
Z13CTL = os.path.join(HOME, ".local", "bin", "z13ctl")
USER_UNITS = os.path.join(HOME, ".config", "systemd", "user")
API = "https://api.github.com/repos"
DECKY_INSTALLER = "https://github.com/SteamDeckHomebrew/decky-installer/releases/latest/download/install_release.sh"
DECKY_RELEASE_API = f"{API}/SteamDeckHomebrew/decky-installer/releases/latest"
DECKY_VERSION_FILE = os.path.join(HOME, "homebrew", "services", ".loader.version")
UDEV_RULES = "/etc/udev/rules.d/99-z13ctl.rules"
PERMS_UNIT = "/etc/systemd/system/z13ctl-perms.service"
# Root only ever executes files from here (root-owned dir, root-owned files),
# never from ~/.local/bin or the download cache, which the user can rewrite.
ROOT_DIR = "/etc/z13-helper"
DRY_RUN = os.environ.get("Z13_INSTALL_DRY_RUN") == "1"


def log(msg):
    print(msg, flush=True)


class Report:
    """What an install/repair run actually did, printed as one `RESULT: {json}`
    line at the end so the dialog can say so instead of guessing.
    changed  : steps that altered the machine (in order), even if a later step failed
    rollback : "not-attempted" | "restored-verified" | "restore-failed" | "not-rolled-back"
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.changed = []
        self.rollback = "not-attempted"
        self.daemon_after_rollback = None   # "running" | "not-running" once a restore happened

    def step(self, what):
        self.changed.append(what)

    def as_dict(self):
        d = {"changed": list(self.changed), "rollback": self.rollback}
        if self.daemon_after_rollback:
            d["daemon_after_rollback"] = self.daemon_after_rollback
        return d


REPORT = Report()


def run(argv, timeout=600, check=True, **kw):
    log("  $ " + " ".join(argv))
    p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, **kw)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    for line in out.splitlines()[-12:]:
        log("    " + line)
    if check and p.returncode != 0:
        raise RuntimeError(f"{argv[0]} exited {p.returncode}")
    return p.returncode, out


def fetch(url, dest=None, timeout=120):
    req = urllib.request.Request(url, headers={"User-Agent": "z13-helper-installer",
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
    if dest:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(data)
        return dest
    return data


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _root_copy(src, name, expected_sha256):
    """Copy `src` into the root-owned ROOT_DIR and return the copy's path.

    `expected_sha256` must come from a trusted source (the release metadata
    fetched over HTTPS), never from `src` itself, which the user can rewrite.
    The hash is taken of the root-owned copy; on a mismatch the copy is removed
    and this raises. Root then uses the copy, never `src`."""
    want = (expected_sha256 or "").strip().lower().removeprefix("sha256:")
    if not re.fullmatch(r"[0-9a-f]{64}", want):
        raise RuntimeError(f"no trusted sha256 for {name}; refusing to run it as root")
    dst = os.path.join(ROOT_DIR, name)
    run(["sudo", "-n", "install", "-d", "-o", "root", "-g", "root", "-m", "0755", ROOT_DIR])
    run(["sudo", "-n", "install", "-o", "root", "-g", "root", "-m", "0755", src, dst])
    got = sha256(dst)
    if got != want:
        run(["sudo", "-n", "rm", "-f", dst], check=False)
        raise RuntimeError(f"sha256 mismatch for {name}: got {got}, release says {want}; refusing")
    return dst


# Tarball path + the release's published sha256 for the last release verified in
# this process (set by _fetch_release); used to give root a checked copy.
_TRUST = {}


# ----------------------------------------------------------------------
# z13ctl
# ----------------------------------------------------------------------

def _z13ctl_installed_version(path=Z13CTL):
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=10).stdout
        m = re.search(r"(\d+\.\d+\.\d+)", out)
        return m.group(1) if m else None
    except (OSError, subprocess.SubprocessError):   # a hung binary counts as not runnable
        return None


def plan_z13ctl():
    return (
        "z13ctl (hardware backend)\n"
        "1. Download the latest release tarball + checksums.txt from github.com/dahui/z13ctl\n"
        "2. Verify the sha256, extract, check `z13ctl --version`\n"
        f"3. Back up {Z13CTL} to z13ctl.bak-<current version>\n"
        "4. Install the new binary to ~/.local/bin/z13ctl\n"
        f"5. Copy it to a root-owned {ROOT_DIR}/z13ctl (checked against the release's published hash; root extracts the binary from that copy), then sudo its `setup`\n"
        "   (udev rules + z13ctl-perms.service; passwordless sudo)\n"
        "6. Install the daemon user units (z13ctl.socket / z13ctl.service) with --no-button,\n"
        "   enable --now (named profiles, resume re-apply and AC/battery autoswitch need it)\n"
        "7. Restart the resident app so it sees the new backend\n"
        "Rollback: copy the .bak file back and `systemctl --user disable --now z13ctl.socket z13ctl.service`."
    )


def _fetch_release(tag=None):
    """Download + verify a z13ctl release (latest, or `tag`). Returns (version, extract dir)."""
    rel = json.loads(fetch(f"{API}/dahui/z13ctl/releases/" + (f"tags/{tag}" if tag else "latest")))
    tag = rel["tag_name"]
    ver = tag.lstrip("v")
    log(f"Release: {tag} ({rel.get('published_at', '')[:10]})")
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets", [])}
    tar_name = f"z13ctl_{ver}_linux_amd64.tar.gz"
    if tar_name not in assets or "checksums.txt" not in assets:
        raise RuntimeError(f"release has no {tar_name} / checksums.txt (assets: {', '.join(assets)})")
    tar_path = fetch(assets[tar_name], os.path.join(CACHE, tar_name))
    sums = fetch(assets["checksums.txt"]).decode()
    expected = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == tar_name:
            expected = parts[0]
    if not expected:
        raise RuntimeError("checksums.txt has no entry for the tarball")
    digest = next((a.get("digest") for a in rel.get("assets", []) if a["name"] == tar_name), None)
    if digest and digest.removeprefix("sha256:").lower() != expected.lower():
        raise RuntimeError(f"release digest {digest} disagrees with checksums.txt {expected}")
    actual = sha256(tar_path)
    if actual != expected:
        raise RuntimeError(f"sha256 mismatch: {actual} != {expected}")
    log(f"sha256 OK: {actual[:16]}…")

    tmp = tempfile.mkdtemp(prefix="z13ctl-")
    try:
        with tarfile.open(tar_path) as t:
            members = [m for m in t.getmembers() if not (m.name.startswith("/") or ".." in m.name)]
            if hasattr(tarfile, "data_filter"):
                t.extractall(tmp, members=members, filter="data")
            else:
                t.extractall(tmp, members=members)
        new_bin = os.path.join(tmp, "z13ctl")
        os.chmod(new_bin, 0o755)
        got = _z13ctl_installed_version(new_bin)
        if got != ver:
            raise RuntimeError(f"extracted binary reports {got}, expected {ver}")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)   # the caller only cleans up on success
        raise
    log(f"Extracted z13ctl {got}")
    _TRUST.clear()
    _TRUST.update(ver=ver, tar=tar_path, name=tar_name, sha=expected)
    return ver, tmp


def _root_z13ctl():
    """A z13ctl binary that only root can have written: the release tarball is
    copied to ROOT_DIR and checked against the release's published hash, then
    the binary is extracted from that root-owned tarball by root. If the
    release can't be verified, refuse rather than run an unchecked file."""
    ver = _TRUST.get("ver") or _z13ctl_installed_version()
    if not ver:
        raise RuntimeError("refusing the sudo step: z13ctl version unknown, cannot verify a release")
    if _TRUST.get("ver") != ver:
        try:
            _, tmp = _fetch_release(f"v{ver}")
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"refusing the sudo step: could not verify the v{ver} release ({e})") from e
    tar_root = _root_copy(_TRUST["tar"], _TRUST["name"], _TRUST["sha"])
    root_bin = os.path.join(ROOT_DIR, "z13ctl")
    run(["sudo", "-n", "rm", "-f", root_bin])
    run(["sudo", "-n", "tar", "-xzf", tar_root, "-C", ROOT_DIR, "--no-same-owner",
         "--no-same-permissions", "z13ctl"])
    run(["sudo", "-n", "chmod", "0755", root_bin])
    return root_bin


def _setup_perms(perms_only=False):
    """`sudo z13ctl setup`: udev rules + z13ctl-perms.service (z13ctl owns both files).
    perms_only re-grants the sysfs permissions without rewriting the rules."""
    root_bin = _root_z13ctl()
    run(["sudo", "-n", root_bin, "setup"] + (["--perms-only"] if perms_only else []), timeout=120)
    run(["sudo", "-n", "systemctl", "daemon-reload"], check=False)
    run(["sudo", "-n", "systemctl", "enable", "--now", "z13ctl-perms.service"], check=False)


DAEMON_OVERRIDE = ("# Written by Z13-Helper's installer. Our watcher owns the side button.\n"
                   "[Service]\nExecStart=\n"
                   f"ExecStart={Z13CTL} daemon --no-button\n")


def _install_daemon_units(src_units):
    """Daemon user units from the tarball, with --no-button (z13-button-watcher
    and the app own the side button) and an absolute ExecStart (user units
    don't have ~/.local/bin on PATH)."""
    os.makedirs(os.path.join(USER_UNITS, "z13ctl.service.d"), exist_ok=True)
    for unit in ("z13ctl.socket", "z13ctl.service"):
        shutil.copy2(os.path.join(src_units, unit), os.path.join(USER_UNITS, unit))
    with open(os.path.join(USER_UNITS, "z13ctl.service.d", "override.conf"), "w") as f:
        f.write(DAEMON_OVERRIDE)
    log("Installed user units + override (daemon --no-button)")


def _start_daemon(restart=True):
    run(["systemctl", "--user", "daemon-reload"], check=False)
    run(["systemctl", "--user", "enable", "--now", "z13ctl.socket"], check=False)
    run(["systemctl", "--user", "restart" if restart else "start", "z13ctl.service"], check=False)
    time.sleep(2)
    rc, _ = run(["systemctl", "--user", "is-active", "z13ctl.service"], check=False)
    log("Daemon: " + ("active" if rc == 0 else "NOT active — CLI falls back to direct hardware access"))
    run([Z13CTL, "status"], check=False, timeout=30)
    return rc


def _rollback_binary(bak):
    """Put the backed-up binary back and verify it by hash. Sets REPORT.rollback.
    Only the binary is restored; udev rules, units and services are not undone."""
    if not bak or not os.path.exists(bak):
        REPORT.rollback = "not-rolled-back"
        log("Rollback: no previous binary to restore — NOT rolled back")
        return
    try:
        shutil.copy2(bak, Z13CTL + ".new")
        os.replace(Z13CTL + ".new", Z13CTL)
        ok = sha256(Z13CTL) == sha256(bak)
    except OSError as e:
        log(f"Rollback: restoring {bak} failed ({e})")
        ok = False
    REPORT.rollback = "restored-verified" if ok else "restore-failed"
    log("Rollback: previous binary restored and verified (hash matches backup)" if ok
        else "Rollback: FAILED to restore the previous binary")
    if ok:
        # The daemon may still be running the new binary or be down: restart it on the old one.
        try:
            up = _start_daemon() == 0
        except Exception as e:  # noqa: BLE001
            log(f"Rollback: daemon restart raised ({e})")
            up = False
        REPORT.daemon_after_rollback = "running" if up else "not-running"
        log("Rollback: daemon running again" if up else "Rollback: daemon NOT running")


def install_z13ctl():
    old = _z13ctl_installed_version()
    log(f"Installed z13ctl: {old or 'none'}")
    rel = json.loads(fetch(f"{API}/dahui/z13ctl/releases/latest"))
    ver = rel["tag_name"].lstrip("v")
    log(f"Latest release: {rel['tag_name']} ({rel.get('published_at', '')[:10]})")
    if old == ver:
        log("Already up to date.")
        return 0
    if DRY_RUN:
        log(f"(dry run) would install z13ctl {ver}, copy it to root-owned {ROOT_DIR}, run `sudo z13ctl setup` from there, install daemon units")
        return 0
    ver, tmp = _fetch_release()

    bak = None
    if old and os.path.exists(Z13CTL):
        bak = f"{Z13CTL}.bak-{old}"
        shutil.copy2(Z13CTL, bak)
        log(f"Backup: {bak}")
    os.makedirs(os.path.dirname(Z13CTL), exist_ok=True)
    shutil.copy2(os.path.join(tmp, "z13ctl"), Z13CTL + ".new")
    os.replace(Z13CTL + ".new", Z13CTL)
    REPORT.step(f"z13ctl binary replaced ({old or 'none'} -> {ver})")
    log(f"Installed {Z13CTL} ({ver})")

    try:
        # Permissions: udev rules + perms service (the 1.4 perms unit also covers PPT).
        _setup_perms()
        REPORT.step("udev rules + perms service (sudo z13ctl setup)")
        _install_daemon_units(os.path.join(tmp, "contrib", "systemd", "user"))
        REPORT.step("daemon user units + override")
        if _start_daemon() != 0:
            REPORT.step("daemon enable/start attempted")
            raise RuntimeError("z13ctl daemon is not active after install")
        REPORT.step("daemon started")
    except BaseException:
        _rollback_binary(bak)
        shutil.rmtree(tmp, ignore_errors=True)
        raise

    # Let the resident app pick up the new backend (it re-spawns z13ctl per call,
    # but its version probe and the Updates row are cached).
    run(["systemctl", "--user", "try-restart", "z13-helper.service"], check=False, timeout=60)
    shutil.rmtree(tmp, ignore_errors=True)
    log(f"Done: z13ctl {old or '?'} -> {ver}")
    return 0


def _unit_enabled(unit, user=False):
    argv = ["systemctl"] + (["--user"] if user else []) + ["is-enabled", unit]
    p = subprocess.run(argv, capture_output=True, text=True, timeout=30)
    return p.stdout.strip() in ("enabled", "static", "alias")


def repair_z13ctl():
    """Make the *installed* z13ctl work again without upgrading it (a version
    pin is deliberate here). Missing binary -> full install of the latest."""
    ver = _z13ctl_installed_version()
    if not ver:
        log("z13ctl missing or not runnable -> installing the latest release")
        return install_z13ctl()
    log(f"Installed z13ctl: {ver} (kept; `install.sh` without --repair upgrades)")

    # 1. Permissions. Rules + perms unit present -> only re-grant; else full setup.
    rules_ok = os.path.exists(UDEV_RULES) and os.path.exists(PERMS_UNIT) and _unit_enabled("z13ctl-perms.service")
    if DRY_RUN:
        log("(dry run) would run `sudo z13ctl setup" + (" --perms-only`" if rules_ok else "` (rules or perms unit missing)"))
        log(f"(dry run) the binary is first copied to root-owned {ROOT_DIR} and hash-checked; root runs only that copy")
    else:
        _setup_perms(perms_only=rules_ok)

    # 2. Daemon user units + our --no-button override.
    override = os.path.join(USER_UNITS, "z13ctl.service.d", "override.conf")
    units_ok = all(os.path.exists(os.path.join(USER_UNITS, u)) for u in ("z13ctl.socket", "z13ctl.service"))
    try:
        override_ok = open(override).read() == DAEMON_OVERRIDE
    except OSError:
        override_ok = False
    changed = False
    if not units_ok:
        log("Daemon units missing -> fetching them from the v%s release" % ver)
        if not DRY_RUN:
            _, tmp = _fetch_release(f"v{ver}")
            _install_daemon_units(os.path.join(tmp, "contrib", "systemd", "user"))
            shutil.rmtree(tmp, ignore_errors=True)
            changed = True
    elif not override_ok:
        log("Daemon override missing/different -> rewriting (--no-button)")
        if not DRY_RUN:
            os.makedirs(os.path.dirname(override), exist_ok=True)
            with open(override, "w") as f:
                f.write(DAEMON_OVERRIDE)
            changed = True
    else:
        log("Daemon units + --no-button override: OK")

    # 3. Socket enabled, daemon running.
    sock_ok = _unit_enabled("z13ctl.socket", user=True)
    active = subprocess.run(["systemctl", "--user", "is-active", "z13ctl.service"],
                            capture_output=True, text=True, timeout=30).stdout.strip() == "active"
    if changed or not sock_ok or not active:
        if DRY_RUN:
            log("(dry run) would enable z13ctl.socket and (re)start z13ctl.service")
        else:
            if _start_daemon(restart=changed) != 0:
                log("FAILED: z13ctl daemon is not active after repair")
                return 1
    else:
        log("Daemon: active")
    return 0


# ----------------------------------------------------------------------
# Decky Loader
# ----------------------------------------------------------------------

def plan_decky():
    return (
        "Decky Loader\n"
        f"1. Download the official installer script ({DECKY_INSTALLER})\n"
        f"2. Copy it to a root-owned {ROOT_DIR} (checked against the release's published digest) and run that copy with sudo (it stops plugin_loader, fetches the latest PluginLoader, restarts)\n"
        "3. Health check: if a stale loader still holds port 1337, stop/kill/start the unit\n"
        "Plugins (including z13-helper) are kept. Alternative: Decky's own Settings → Update in the QAM."
    )


def _decky_health():
    rc, out = run(["sudo", "-n", "journalctl", "-u", "plugin_loader", "--no-pager", "-S", "-2min"], check=False)
    if "address already in use" in out:
        log("Stale loader detected (port 1337 busy) — cleaning up")
        run(["sudo", "-n", "systemctl", "stop", "plugin_loader.service"], check=False)
        time.sleep(2)
        run(["sudo", "-n", "systemctl", "kill", "plugin_loader.service"], check=False)
        time.sleep(2)
        run(["sudo", "-n", "systemctl", "reset-failed", "plugin_loader.service"], check=False)
        run(["sudo", "-n", "systemctl", "start", "plugin_loader.service"], check=False)
        time.sleep(8)
    rc, out = run(["systemctl", "is-active", "plugin_loader.service"], check=False)
    log("plugin_loader: " + out.strip())
    return rc


def _decky_installed_version():
    try:
        with open(DECKY_VERSION_FILE) as f:
            return f.read().strip().lstrip("v") or None
    except OSError:
        return None


def install_decky():
    if DRY_RUN:
        log(f"(dry run) would run the official Decky installer ({DECKY_INSTALLER})")
        return 0
    # The expected hash is the release asset's published digest (HTTPS API),
    # not anything read back from the downloaded file. No digest -> refuse.
    rel = json.loads(fetch(DECKY_RELEASE_API))
    asset = next((a for a in rel.get("assets", []) if a.get("name") == "install_release.sh"), None)
    digest = (asset or {}).get("digest")
    if not asset or not digest:
        log("FAILED: the Decky installer release has no published sha256 digest; not running it as root")
        return 1
    script = fetch(asset["browser_download_url"], os.path.join(CACHE, "decky_install_release.sh"))
    log("Downloaded installer script")
    root_script = _root_copy(script, "decky_install_release.sh", digest)
    REPORT.step("Decky Loader installer started (may be partially applied)")
    run(["sudo", "-n", "bash", root_script], timeout=900)
    time.sleep(8)
    healthy = _decky_health() == 0
    log("Decky Loader now: " + (_decky_installed_version() or "?"))
    if not healthy:
        log("FAILED: plugin_loader.service is not active after the install")
        return 1
    return 0


def repair_decky():
    """Reinstall Decky Loader only when it's missing, not running, or behind the
    latest release (a SteamOS update usually drops /etc/.../plugin_loader.service,
    and an old loader can break with a new Steam client)."""
    have = _decky_installed_version()
    unit = os.path.exists("/etc/systemd/system/plugin_loader.service")
    active = subprocess.run(["systemctl", "is-active", "plugin_loader.service"],
                            capture_output=True, text=True, timeout=30).stdout.strip() == "active"
    try:
        latest = json.loads(fetch(f"{API}/SteamDeckHomebrew/decky-loader/releases/latest"))["tag_name"].lstrip("v")
    except Exception as e:  # noqa: BLE001
        latest = None
        log(f"Could not read the latest Decky release ({e}); judging by the local state only")
    log(f"Decky Loader: installed {have or 'none'}, latest {latest or '?'}, "
        f"unit {'present' if unit else 'MISSING'}, {'active' if active else 'NOT active'}")
    if have and unit and active and (latest is None or have == latest):
        log("Decky Loader: OK")
        return 0
    return install_decky()


# ----------------------------------------------------------------------
# SteamOS
# ----------------------------------------------------------------------

def plan_steamos():
    return ("SteamOS\n1. sudo steamos-update  (the official updater; can take a while)\n"
            "2. Reboot to finish. Our /etc customisations are on the atomic-update keep list.")


def install_steamos():
    rc, out = run(["steamos-update", "check"], check=False, timeout=120)
    low = out.lower()
    # steamos-atomupd: exit 0 = update available, 7 = none. The text is a cross-check only.
    if rc == 7:
        if re.search(r"(?<!no )\bupdates?\s+(?:is\s+)?available\b", low):
            log("SteamOS check contradicts itself (exit 7, text says update available); the updater was not run.")
            return 1
        log("No SteamOS update available.")
        return 0
    if rc != 0:
        log(f"SteamOS update check failed (exit {rc}); the updater was not run.")
        return 1
    if "no update available" in low:
        log("SteamOS check contradicts itself (exit 0, text says no update); the updater was not run.")
        return 1
    REPORT.step("steamos-update started (may be partially applied)")
    run(["sudo", "-n", "steamos-update"], timeout=3600)
    log("SteamOS update staged. Reboot to finish.")
    return 0


INSTALLERS = {
    "z13ctl": (plan_z13ctl, install_z13ctl),
    "decky": (plan_decky, install_decky),
    "steamos": (plan_steamos, install_steamos),
}
REPAIRS = {"z13ctl": repair_z13ctl, "decky": repair_decky}


def main(argv):
    if (len(argv) != 2 or argv[0] not in ("plan", "install", "repair") or argv[1] not in INSTALLERS
            or (argv[0] == "repair" and argv[1] not in REPAIRS)):
        print(f"usage: installers.py plan|install {{{'|'.join(INSTALLERS)}}}\n"
              f"       installers.py repair {{{'|'.join(REPAIRS)}}}", file=sys.stderr)
        return 2
    plan_fn, install_fn = INSTALLERS[argv[1]]
    if argv[0] == "plan":
        print(plan_fn())
        return 0
    if argv[0] == "repair":
        install_fn = REPAIRS[argv[1]]
    log(f"== {'Repairing' if argv[0] == 'repair' else 'Installing'} {argv[1]} ==")
    REPORT.reset()
    try:
        rc = install_fn()
    except Exception as e:  # noqa: BLE001
        log(f"FAILED: {e}")
        rc = 1
    log("RESULT: " + json.dumps(REPORT.as_dict()))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""What install.sh puts on the host, and the report that proves it works.

Runs ON THE HOST from the repo checkout, stdlib only. One manifest, two users:
install.sh reads it (`manifest`) to know what to copy where, and `verify`
checks the machine against it plus the live behaviour (ROADMAP §12 as code).

CLI:  install_check.py manifest           TSV: repo-path  dest  mode  root(0/1)
      install_check.py keep-list          the /etc atomic-update keep-list entries we need
      install_check.py flatpak-diff       repo files the installed Flatpak lacks; exit 0 same,
                                          1 differs, 2 not installed
      install_check.py verify [--no-restore]
            green/yellow/red report; exit 1 on any red. Without --no-restore it
            runs `z13-profile restore` (the same thing login and resume do) and
            judges brightness, fans and the profile from what that did.

Deliberately NOT in the manifest:
- /etc/udev/rules.d/99-z13ctl.rules and /etc/systemd/system/z13ctl-perms.service:
  `sudo z13ctl setup` writes those (installers.py); the packaging/host copies are
  reference only, refreshed from the live files.
- packaging/host/xdg autostart entry: z13-helper.service is the resident path;
  both would race for the single-instance lock.
- packaging/host/asusd: asusd isn't installed (it fights z13ctl).
- tooling/z13_fanlog.sh: an ad-hoc logger, run from the repo when needed.
"""

import os
import re
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "src"))

HOME = os.path.expanduser("~")
BIN = os.path.join(HOME, ".local", "bin")
LIB = os.path.join(HOME, ".local", "lib", "z13-helper")
UNITS = os.path.join(HOME, ".config", "systemd", "user")
ETC_UNITS = "/etc/systemd/system"
APP_ID = "io.github.iannitram.Z13Helper"
KEEP_LIST_FILE = "/etc/atomic-update.conf.d/z13-bucket1.conf"
# polkit 126 keeps rules.d root:polkitd 0750: deck can't even stat files in it.
POLKIT_RULE = "/etc/polkit-1/rules.d/50-z13-lightbar-reset.rules"
LIGHTBAR_RESET_UNIT = "z13-lightbar-reset.service"
DECKY_PLUGIN = os.path.join(HOME, "homebrew", "plugins", "z13-helper")
DECKY_LOGS = os.path.join(HOME, "homebrew", "logs", "z13-helper")
DECKY_FILES = ["plugin.json", "package.json", "main.py", "dist/index.js",
               "py_modules/fan_watchdog.py", "py_modules/sensor_reader.py", "py_modules/host.py",
               "py_modules/power_range.py"]


def decky_source(f):
    """Repo copy of an installed plugin file: py_modules/X is src/X, the rest decky/."""
    if f.startswith("py_modules/"):
        return os.path.join(REPO, "src", f[len("py_modules/"):])
    return os.path.join(REPO, "decky", f)

# (repo path, installed path, mode, needs root)
MANIFEST = [
    ("src/profiles.py", f"{BIN}/z13-profile", "755", False),
    # z13-profile's host seam (profiles.py puts this folder on sys.path).
    ("src/host.py", f"{LIB}/host.py", "644", False),
    ("tooling/profiles/z13-max", f"{BIN}/z13-max", "755", False),
    ("tooling/profiles/z13-balanced", f"{BIN}/z13-balanced", "755", False),
    ("tooling/profiles/z13-cool", f"{BIN}/z13-cool", "755", False),
    ("tooling/profiles/z13-restore-profile", f"{BIN}/z13-restore-profile", "755", False),
    ("tooling/boondock", f"{BIN}/boondock", "755", False),
    ("tooling/z13-button-watcher", f"{BIN}/z13-button-watcher", "755", False),
    ("tooling/z13_cdp.py", f"{BIN}/z13_cdp.py", "644", False),
    ("tooling/diag/z13-memory-check", f"{BIN}/z13-memory-check", "755", False),
    ("tooling/diag/z13-storage-check", f"{BIN}/z13-storage-check", "755", False),
    ("tooling/diag/z13-whereami", f"{BIN}/z13-whereami", "755", False),
    ("packaging/host/systemd/user/z13-battery-default.service", f"{UNITS}/z13-battery-default.service", "644", False),
    ("packaging/host/systemd/user/z13-button-watcher.service", f"{UNITS}/z13-button-watcher.service", "644", False),
    ("packaging/host/systemd/user/z13-helper.service", f"{UNITS}/z13-helper.service", "644", False),
    ("packaging/host/systemd/user/z13-restore-profile.service", f"{UNITS}/z13-restore-profile.service", "644", False),
    ("packaging/host/systemd/system/z13-fan-resume.sh", f"{ETC_UNITS}/z13-fan-resume.sh", "755", True),
    ("packaging/host/systemd/system/z13-fan-resume.service", f"{ETC_UNITS}/z13-fan-resume.service", "644", True),
    ("packaging/host/systemd/system/z13-lightbar-reset.sh", f"{ETC_UNITS}/z13-lightbar-reset.sh", "755", True),
    ("packaging/host/systemd/system/z13-lightbar-reset.service", f"{ETC_UNITS}/z13-lightbar-reset.service", "644", True),
    ("packaging/host/polkit/50-z13-lightbar-reset.rules", POLKIT_RULE, "644", True),
]
USER_UNITS_ENABLED = ["z13-battery-default.service", "z13-button-watcher.service",
                      "z13-helper.service", "z13-restore-profile.service", "z13ctl.socket"]
# Ours (resume hook, lightbar reset + its polkit rule) + z13ctl setup's two
# files. They survive an atomic update only while listed here.
KEEP_LIST = [
    f"{ETC_UNITS}/z13-fan-resume.sh",
    f"{ETC_UNITS}/z13-fan-resume.service",
    f"{ETC_UNITS}/z13-lightbar-reset.sh",
    f"{ETC_UNITS}/z13-lightbar-reset.service",
    POLKIT_RULE,
    f"{ETC_UNITS}/z13ctl-perms.service",
    "/etc/udev/rules.d/99-z13ctl.rules",
]

GREEN, YELLOW, RED, SKIP = "ok", "warn", "FAIL", "skip"
_COL = {GREEN: "\033[32m", YELLOW: "\033[33m", RED: "\033[31m", SKIP: "\033[90m"}


def sh(argv, timeout=60):
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


def same(a, b):
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def same_root(src, dest):
    """Like same(), for a root-owned file deck may not be able to read.

    Returns True/False, or None when it can't tell (no permission and no
    passwordless sudo). Never writes anything.
    """
    try:
        os.stat(dest)
        if os.access(dest, os.R_OK):
            return same(src, dest)
    except FileNotFoundError:
        return False
    except PermissionError:
        pass
    # sudo -n fails with 1 like `cmp` "differs" does, so ask sudo on its own first.
    if sh(["sudo", "-n", "true"], timeout=10)[0] != 0:
        return None
    rc, _ = sh(["sudo", "-n", "cmp", "-s", src, dest], timeout=10)
    return {0: True, 1: False}.get(rc)


class Report:
    def __init__(self):
        self.rows = []
        self.section = ""

    def head(self, title):
        self.section = title
        self.rows.append((None, title, ""))

    def add(self, status, name, detail=""):
        self.rows.append((status, name, detail))

    def print(self):
        color = sys.stdout.isatty()
        for status, name, detail in self.rows:
            if status is None:
                print(f"\n{name}")
                continue
            tag = f"{status:>4}"
            if color:
                tag = f"{_COL[status]}{tag}\033[0m"
            print(f"  [{tag}] {name:<34} {detail}")
        reds = sum(1 for s, _, _ in self.rows if s == RED)
        warns = sum(1 for s, _, _ in self.rows if s == YELLOW)
        oks = sum(1 for s, _, _ in self.rows if s == GREEN)
        print(f"\n{oks} ok, {warns} warning(s), {reds} failure(s)")
        return 1 if reds else 0


# ----------------------------------------------------------------------
# checks
# ----------------------------------------------------------------------

def _active(unit, user=True):
    return sh(["systemctl"] + (["--user"] if user else []) + ["is-active", unit])[1]


def _enabled(unit, user=True):
    return sh(["systemctl"] + (["--user"] if user else []) + ["is-enabled", unit])[1]


def desktop_session():
    return _active("plasma-workspace.target") == "active"


def check_system(r):
    r.head("System")
    osr = {}
    try:
        for line in open("/etc/os-release"):
            k, _, v = line.strip().partition("=")
            osr[k] = v.strip('"')
    except OSError:
        pass
    r.add(GREEN, "SteamOS", f"{osr.get('VERSION_ID', '?')} (build {osr.get('BUILD_ID', '?')})")
    r.add(GREEN, "kernel", os.uname().release)
    r.add(GREEN, "session", "Desktop (Plasma)" if desktop_session() else "Game Mode / no Plasma")
    groups = sh(["id", "-nG"])[1].split()
    r.add(GREEN if "users" in groups else RED, "deck in group `users`",
          "yes" if "users" in groups else "no: z13ctl's udev grants won't apply (sudo usermod -aG users deck, re-login)")
    ip = _enabled("inputplumber.service", user=False)
    r.add(GREEN if ip == "masked" else RED, "InputPlumber masked",
          ip if ip == "masked" else f"{ip}: the phantom controller returns; re-mask with "
          "`sudo systemctl mask --now inputplumber.service` (never unmask it)")


def check_z13ctl(r):
    r.head("z13ctl (the only hardware writer)")
    z = os.path.join(BIN, "z13ctl")
    rc, out = sh([z, "--version"], timeout=10)
    m = re.search(r"(\d+\.\d+\.\d+)", out)
    r.add(GREEN if rc == 0 and m else RED, "binary", m.group(1) if m else f"not runnable: {out[:80]}")
    rc, out = sh([z, "status"], timeout=30)
    r.add(GREEN if rc == 0 else RED, "`z13ctl status`", "rc 0" if rc == 0 else out.splitlines()[-1][:90] if out else f"rc {rc}")
    # z13ctl writes the platform-profile class nodes (udev grants them to `users`),
    # not the root-only legacy /sys/firmware/acpi/platform_profile.
    import glob
    pp = glob.glob("/sys/class/platform-profile/platform-profile-*/profile")
    for paths, what in ((pp, "platform profile writable"),
                        (["/sys/class/power_supply/BAT0/charge_control_end_threshold"], "battery limit writable")):
        ok = bool(paths) and all(os.access(p, os.W_OK) for p in paths)
        r.add(GREEN if ok else RED, what, "yes" if ok else "no: run `sudo z13ctl setup --perms-only`")
    perms = _enabled("z13ctl-perms.service", user=False)
    r.add(GREEN if perms == "enabled" else RED, "z13ctl-perms.service", perms)
    sock = _active("z13ctl.socket")
    r.add(GREEN if sock == "active" else RED, "daemon socket", sock)
    svc = _active("z13ctl.service")
    r.add(GREEN if svc == "active" else YELLOW, "daemon", svc if svc == "active" else f"{svc} (socket-activated on first use)")
    try:
        ovr = open(os.path.join(UNITS, "z13ctl.service.d", "override.conf")).read()
    except OSError:
        ovr = ""
    r.add(GREEN if "--no-button" in ovr else RED, "daemon --no-button override",
          "present" if "--no-button" in ovr else "missing: the daemon would grab the side button")


def check_files(r):
    r.head("Host files (installed == repo)")
    for rel, dest, _mode, root in MANIFEST:
        src = os.path.join(REPO, rel)
        if root:
            st = same_root(src, dest)
            if st is None:
                r.add(YELLOW, os.path.basename(dest), f"can't read {dest} without sudo; not checked")
            elif st:
                r.add(GREEN, os.path.basename(dest), dest)
            else:
                r.add(RED, os.path.basename(dest), f"missing or differs from {rel}")
            continue
        if not os.path.exists(dest):
            r.add(RED, os.path.basename(dest), f"missing ({dest})")
        elif not same(src, dest):
            r.add(RED, os.path.basename(dest), f"differs from {rel}")
        else:
            r.add(GREEN, os.path.basename(dest), dest.replace(HOME, "~"))


def check_units(r):
    r.head("Services")
    for u in USER_UNITS_ENABLED:
        st = _enabled(u)
        r.add(GREEN if st == "enabled" else RED, f"{u} enabled", st)
    st = _active("z13-button-watcher.service")
    r.add(GREEN if st == "active" else RED, "button watcher running", st)
    st = _enabled("z13-fan-resume.service", user=False)
    r.add(GREEN if st == "enabled" else RED, "resume hook enabled", st)
    try:
        listed = {l.strip() for l in open(KEEP_LIST_FILE) if l.strip() and not l.startswith("#")}
    except OSError:
        listed = set()
    missing = [p for p in KEEP_LIST if p not in listed]
    r.add(GREEN if not missing else RED, "atomic-update keep-list",
          f"all {len(KEEP_LIST)} entries" if not missing
          else "missing: " + ", ".join(os.path.basename(m) for m in missing))
    # Lightbar reset: read-only check. Never started from here (it resets a USB device).
    rc, out = sh(["systemctl", "cat", LIGHTBAR_RESET_UNIT], timeout=10)
    r.add(GREEN if rc == 0 else RED, "lightbar reset unit",
          "installed (started on demand only)" if rc == 0
          else f"`systemctl cat {LIGHTBAR_RESET_UNIT}` failed: {out.splitlines()[-1][:70] if out else rc}")


def flatpak_location():
    rc, out = sh(["flatpak", "info", "--show-location", APP_ID])
    return out.strip() if rc == 0 else None


def flatpak_diff(loc):
    """Repo files that differ from the installed Flatpak tree (empty = current)."""
    tree = os.path.join(loc, "files", "share", "z13-helper")
    pairs = [(os.path.join(REPO, "main.py"), os.path.join(tree, "main.py")),
             (os.path.join(REPO, "flatpak", "launch.sh"), os.path.join(loc, "files", "bin", "z13-helper-flatpak"))]
    for top in ("src", "assets"):
        base = os.path.join(REPO, top)
        for d, dirs, files in os.walk(base):
            dirs[:] = [x for x in dirs if x != "__pycache__"]
            for f in files:
                if f.endswith(".pyc"):
                    continue
                p = os.path.join(d, f)
                pairs.append((p, os.path.join(tree, os.path.relpath(p, REPO))))
    return [os.path.relpath(a, REPO) for a, b in pairs if not same(a, b)]


CRASH_RECENT_S = 7 * 24 * 3600


def check_crash_reports(r, now=None):
    """Crash reports the app saved (src/app_log.py). Recent ones are a warning,
    not a failure: the app keeps running after an uncaught error."""
    import app_log
    now = now or time.time()
    reports = app_log.list_crash_reports()
    recent = [x for x in reports if now - x[1] < CRASH_RECENT_S]
    if not recent:
        r.add(GREEN, "app crash reports (7 days)", "none" + (f" ({len(reports)} older)" if reports else ""))
        return
    path, _mtime, summary = recent[0]
    r.add(YELLOW, "app crash reports (7 days)",
          f"{len(recent)}; newest: {summary[:60]} ({path.replace(HOME, '~')}; Help → Error log)")


def check_app(r):
    r.head("Desktop app (Flatpak)")
    loc = flatpak_location()
    if not loc:
        r.add(RED, APP_ID, "not installed (bash flatpak/build.sh)")
        return
    r.add(GREEN, APP_ID, "installed")
    d = flatpak_diff(loc)
    r.add(GREEN if not d else RED, "installed build == repo",
          "yes" if not d else f"{len(d)} file(s) differ, e.g. {d[0]} (rebuild)")
    check_crash_reports(r)
    check_old_name(r)
    if not desktop_session():
        r.add(SKIP, "running instances", "not in a Desktop session (the app runs only under Plasma)")
        return
    n_app = len(sh(["pgrep", "-f", "[/]app/share/z13-helper/main[.]py"])[1].split())
    r.add(GREEN if n_app == 1 else RED, "exactly one app instance", str(n_app))
    # One reader = a sandbox flatpak-spawn + the host python3; count the host side.
    n_rd = len(sh(["pgrep", "-f", "^python3 .*--emit z13-side-button-reade[r]"])[1].split())
    r.add(GREEN if n_rd == 1 else RED, "exactly one side-button reader", str(n_rd))


# Left over from before the rename to Z13-Helper (2026-10-15); install.sh removes them.
OLD_NAME_LEFTOVERS = [
    ("old Flatpak io.github.z13.Controller", lambda: sh(["flatpak", "info", "io.github.z13.Controller"])[0] == 0),
    ("old unit z13-controller.service", lambda: os.path.exists(os.path.join(UNITS, "z13-controller.service"))),
    ("old Decky plugin z13-decky", lambda: os.path.exists(os.path.join(HOME, "homebrew", "plugins", "z13-decky"))),
    ("old config folder (not a symlink)", lambda: os.path.isdir(os.path.join(HOME, ".config", "z13-controller"))
     and not os.path.islink(os.path.join(HOME, ".config", "z13-controller"))),
]


def check_old_name(r):
    left = [name for name, present in OLD_NAME_LEFTOVERS if present()]
    if left:
        r.add(YELLOW, "renamed-app leftovers", "; ".join(left) + " (./install.sh --repair removes them)")


def check_decky(r):
    r.head("Decky (Game Mode)")
    ver = "?"
    try:
        ver = open(os.path.join(HOME, "homebrew", "services", ".loader.version")).read().strip()
    except OSError:
        pass
    st = _active("plugin_loader.service", user=False)
    r.add(GREEN if st == "active" else RED, "plugin_loader", f"{st}, Decky {ver}")
    out = sh(["ss", "-Hltn", "sport = :1337"])[1]
    n = len([l for l in out.splitlines() if l.strip()])
    r.add(GREEN if n == 1 else RED, "one loader on :1337", str(n) if n != 1 else "yes")
    bad = [f for f in DECKY_FILES
           if not same(decky_source(f), os.path.join(DECKY_PLUGIN, f))]
    r.add(GREEN if not bad else RED, "z13-helper == repo build", "yes" if not bad else "differs: " + ", ".join(bad))
    try:
        newest = max((os.path.join(DECKY_LOGS, f) for f in os.listdir(DECKY_LOGS)), key=os.path.getmtime)
        up = "z13-helper up" in open(newest, errors="replace").read()
        r.add(GREEN if up else RED, "backend log says up", os.path.basename(newest) if up else
              f"no 'z13-helper up' in {os.path.basename(newest)}")
    except (OSError, ValueError):
        r.add(RED, "backend log says up", f"no logs in {DECKY_LOGS}")


def check_restore(r):
    """Run what login/resume run, and grade each step it reports."""
    r.head("Profile restore (`z13-profile restore`, as at login/resume)")
    rc, out = sh([os.path.join(BIN, "z13-profile"), "restore"], timeout=180)
    steps = 0
    for line in out.splitlines():
        m = re.match(r"\s*(ok|ERR)\s+(.+?)\s{2,}(.*)$", line)
        if not m:
            continue
        steps += 1
        ok, step, msg = m.group(1) == "ok", m.group(2).strip(), m.group(3).strip()
        if ok:
            r.add(GREEN, step, msg[:90])
        elif step == "brightness (lightbar)":
            r.add(YELLOW, step, "lightbar wedged or stale (" + msg[-60:] + "); manual recovery: "
                  f"systemctl start {LIGHTBAR_RESET_UNIT}, then systemctl --user restart z13ctl.service")
        elif step == "undervolt":
            r.add(YELLOW, step, msg[:90])
        else:
            r.add(RED, step, msg[:90])
    if not steps:
        r.add(RED, "z13-profile restore", f"rc {rc}: {out.splitlines()[-1][:90] if out else 'no output'}")


def check_fans(r, settle):
    r.head("Fans")
    import max_fans_state
    import profiles
    from sensor_reader import SensorReader
    if settle:
        # Every curve change stops the fans ~5-10 s while the firmware re-ramps.
        time.sleep(settle)
    s = SensorReader()
    max_on = max_fans_state.is_enabled()
    store = profiles.load_store(create=False)   # read-only: never seed the store from here
    prof = profiles.get_profile(store.get("active"), store) or {}
    want_custom = max_on or bool(prof.get("fan_curve"))
    enabled = s.custom_fan_curve_enabled()
    want = "custom (Max Fans 100%)" if max_on else "custom curve" if want_custom else "firmware auto"
    r.add(GREEN if enabled == want_custom else RED, "curve mode",
          f"{'custom' if enabled else 'auto'}; profile {prof.get('name', '?')} wants {want}")
    first = s.get_fan_speeds()
    if s.get_apu_temp() is None or None in first:
        r.add(RED, "sensors", "can't read APU temp / fan RPM")
        return
    # ~22 s window: at full speed a healthy tach can sit on one 100-RPM step for
    # 8 s or more (seen 2026-10-11), so a short window cries wolf.
    samples = []
    for i in range(12):
        samples.append(s.get_fan_speeds())
        if i < 11:
            time.sleep(2)
    temp = s.get_apu_temp()
    f1, f2 = samples[-1]
    if temp is None or f1 is None or f2 is None:
        r.add(RED, "sensors", "can't read APU temp / fan RPM")
        return
    expected = s.expected_fan_pwm(temp - 3) if enabled else None
    detail = f"APU {temp:.0f}°C, fans {f1}/{f2} RPM" + (f", curve asks {expected * 100 // 255}%" if expected is not None else "")
    stalled = enabled and expected is not None and expected >= 128 and f1 == 0 and f2 == 0
    r.add(RED if stalled else GREEN, "fans spinning as asked",
          detail + (" — STALLED (watchdog/app recovers with profile rewrite + curve)" if stalled else ""))
    # Stale tach: at >= 50% a healthy fan's reading jitters every tick (at low
    # auto speeds it legitimately sits on one 100-RPM step), same rule as the watchdog.
    for i in ((0, 1) if expected is not None and expected >= 128 else ()):
        vals = {smp[i] for smp in samples}
        other = {smp[1 - i] for smp in samples}
        if len(vals) == 1 and samples[0][i] > 0 and len(other) > 1:
            r.add(YELLOW, f"fan {i + 1} tach", f"read {samples[0][i]} for 22 s while the other moved; "
                  "possible stale tach (stopped fan); listen to it")


def verify(no_restore):
    r = Report()
    check_system(r)
    check_z13ctl(r)
    check_files(r)
    check_units(r)
    check_app(r)
    check_decky(r)
    if not no_restore:
        check_restore(r)
    check_fans(r, settle=0 if no_restore else 12)
    return r.print()


def main(argv):
    if argv[:1] == ["manifest"]:
        for rel, dest, mode, root in MANIFEST:
            print(f"{rel}\t{dest}\t{mode}\t{int(root)}")
        return 0
    if argv[:1] == ["keep-list"]:
        print("\n".join(KEEP_LIST))
        return 0
    if argv[:1] == ["flatpak-diff"]:
        loc = flatpak_location()
        if not loc:
            print("not installed")
            return 2
        d = flatpak_diff(loc)
        print("\n".join(d))
        return 1 if d else 0
    if argv[:1] == ["verify"]:
        return verify("--no-restore" in argv[1:])
    print(__doc__.split("\n\n")[1], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

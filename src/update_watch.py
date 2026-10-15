"""Upstream update watch: tells the user when something we depend on moved.

Checks the projects listed in docs/ROADMAP.md §11 (z13ctl, Decky Loader,
asusctl, InputPlumber, G-Helper, the kernel, SteamOS, and a few Z13 community
repos) against what is installed where we can read it. Results are cached in
~/.config/z13-helper/updates.json with ETags so a daily check costs a
handful of conditional GitHub requests (unauthenticated: 60/h is plenty).

The Flatpak sandbox has no network at runtime, so HTTP goes through the host's
curl via `flatpak-spawn --host`. Rows with an "installer" can be installed from
the dialog (installers.py, runs on the host, asks first). Stdlib only.
"""

import json
import os
import platform
import re
import subprocess
import time

CACHE_PATH = os.path.expanduser("~/.config/z13-helper/updates.json")
CHECK_INTERVAL_S = 24 * 3600
API = "https://api.github.com/repos"


def _in_flatpak():
    return os.path.exists("/.flatpak-info") or "FLATPAK_ID" in os.environ


def host_run(argv, timeout=30):
    cmd = list(argv)
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host"] + cmd
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or ""), (p.stderr or "")
    except (subprocess.TimeoutExpired, OSError) as e:
        return 1, "", str(e)


def host_read(path):
    rc, out, _ = host_run(["cat", os.path.expanduser(path)], timeout=10)
    return out.strip() if rc == 0 else None


# ----------------------------------------------------------------------
# Installed-version probes
# ----------------------------------------------------------------------

def _z13ctl_version():
    rc, out, _ = host_run([os.path.expanduser("~/.local/bin/z13ctl"), "--version"], timeout=10)
    m = re.search(r"(\d+\.\d+\.\d+)", out) if rc == 0 else None
    return m.group(1) if m else None


def _decky_version():
    v = host_read("~/homebrew/services/.loader.version")
    return v.lstrip("v") if v else None


def _kernel_version():
    rel = platform.release()  # same kernel inside the sandbox
    m = re.match(r"(\d+\.\d+(?:\.\d+)?)", rel)
    return m.group(1) if m else rel


def _steamos_version():
    text = host_read("/etc/os-release") or ""
    m = re.search(r'^VERSION_ID="?([^"\n]+)', text, re.M)
    return m.group(1) if m else None


# Order = display order. "installed" is a callable or None (watch-only).
SOURCES = [
    {"id": "z13ctl", "label": "z13ctl (hardware backend)", "repo": "dahui/z13ctl",
     "kind": "release", "installed": _z13ctl_version, "installer": "z13ctl",
     "note": "Installable here: tarball, sha256-verified, daemon runs --no-button. Backup kept as z13ctl.bak-<ver>."},
    {"id": "decky", "label": "Decky Loader", "repo": "SteamDeckHomebrew/decky-loader",
     "kind": "release", "installed": _decky_version, "installer": "decky",
     "note": "Installable here via the official Decky installer script (or Decky's own Settings → Update)."},
    {"id": "kernel", "label": "Linux kernel (mainline — reference only)", "kind": "kernel", "installed": _kernel_version,
     "note": "Not installable: SteamOS ships its own kernel (updates arrive via the SteamOS row). Watch for 7.0+: "
             "it fixes the Z13 keyboard-backlight init (-75) and adds the GZ302 power-limit table."},
    {"id": "steamos", "label": "SteamOS", "kind": "steamos", "installed": _steamos_version, "installer": "steamos",
     "note": "Installable here (sudo steamos-update + reboot). A kernel bump to 7.x changes how brightness and TDP should be done."},
    {"id": "asusctl", "label": "asusctl (reference, not installed)", "repo": "OpenGamingCollective/asusctl",
     "kind": "release", "installed": None,
     "note": "Don't run asusd next to z13ctl (GZ302 boot-hang race, gitlab #718)."},
    {"id": "inputplumber", "label": "InputPlumber (masked here)", "repo": "ShadowBlip/InputPlumber",
     "kind": "release", "installed": None,
     "note": "A phantom-controller fix would let us unmask it."},
    {"id": "g-helper", "label": "G-Helper (UI reference)", "repo": "seerge/g-helper",
     "kind": "release", "installed": None, "note": "Layout we mirror."},
    {"id": "hhd", "label": "Handheld Daemon", "repo": "hhd-dev/hhd",
     "kind": "release", "installed": None, "note": "Z13 support on stock SteamOS kernels?"},
    {"id": "z13ctl-plus", "label": "z13ctl-plus (community)", "repo": "LostVector/z13ctl-plus",
     "kind": "commit", "installed": None, "note": "AC/battery automation, EPP ideas."},
    {"id": "z13-tablet-switch", "label": "z13-tablet-switch (community)", "repo": "m4rw3r/z13-tablet-switch",
     "kind": "commit", "installed": None, "note": "Folio attach/detach → tablet mode."},
    {"id": "lightctl", "label": "lightctl (community)", "repo": "Xcli0126/lightctl",
     "kind": "commit", "installed": None, "note": "Independent rear-light control."},
]


# ----------------------------------------------------------------------
# HTTP via host curl (conditional requests)
# ----------------------------------------------------------------------

def _get(url, etag=None):
    """Returns (status, etag, body_text). status 0 on transport failure."""
    argv = ["curl", "-s", "-m", "25", "-i", "-H", "Accept: application/vnd.github+json",
            "-H", "User-Agent: z13-helper-update-watch"]
    if etag:
        argv += ["-H", f"If-None-Match: {etag}"]
    argv.append(url)
    rc, out, err = host_run(argv, timeout=40)
    if rc != 0 or not out:
        return 0, etag, err
    # curl -i may emit several header blocks (redirects); take the last one.
    head, _, body = out.rpartition("\r\n\r\n")
    if not head:
        head, _, body = out.rpartition("\n\n")
    status = 0
    new_etag = etag
    for line in head.splitlines():
        m = re.match(r"HTTP/\S+\s+(\d{3})", line)
        if m:
            status = int(m.group(1))
        m = re.match(r"etag:\s*(.+)", line, re.I)
        if m:
            new_etag = m.group(1).strip()
    return status, new_etag, body


def _fetch_release(repo, cached):
    status, etag, body = _get(f"{API}/{repo}/releases/latest", cached.get("etag"))
    if status == 304:
        return dict(cached, error=None)
    if status != 200:
        return dict(cached, error=f"HTTP {status}" if status else (body or "network error")[:120])
    d = json.loads(body)
    return {"latest": (d.get("tag_name") or "").lstrip("v"), "published": (d.get("published_at") or "")[:10],
            "url": d.get("html_url"), "notes": (d.get("body") or "").strip()[:600], "etag": etag, "error": None}


def _fetch_commit(repo, cached):
    status, etag, body = _get(f"{API}/{repo}/commits?per_page=1", cached.get("etag"))
    if status == 304:
        return dict(cached, error=None)
    if status != 200:
        return dict(cached, error=f"HTTP {status}" if status else (body or "network error")[:120])
    arr = json.loads(body)
    if not arr:
        return dict(cached, error="no commits")
    c = arr[0]
    msg = (c.get("commit", {}).get("message") or "").splitlines()[0][:120]
    return {"latest": c.get("sha", "")[:7], "published": (c.get("commit", {}).get("committer", {}).get("date") or "")[:10],
            "url": c.get("html_url"), "notes": msg, "etag": etag, "error": None}


def _fetch_kernel(cached):
    status, etag, body = _get("https://www.kernel.org/releases.json", cached.get("etag"))
    if status == 304:
        return dict(cached, error=None)
    if status != 200:
        return dict(cached, error=f"HTTP {status}" if status else (body or "network error")[:120])
    d = json.loads(body)
    stable = d.get("latest_stable", {}).get("version", "")
    date = ""
    for r in d.get("releases", []):
        if r.get("version") == stable:
            date = (r.get("released", {}) or {}).get("isodate", "")
    return {"latest": stable, "published": date, "url": "https://www.kernel.org/",
            "notes": "latest stable", "etag": etag, "error": None}


def _fetch_steamos(cached):
    rc, out, err = host_run(["steamos-update", "check"], timeout=90)
    text = (out + err).strip()
    available = "no update available" not in text.lower() and rc == 0
    return {"latest": "update available" if available else (_steamos_version() or ""),
            "published": "", "url": None, "notes": text[:200], "etag": None, "error": None}


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def load_cache():
    try:
        with open(CACHE_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    tmp = f"{CACHE_PATH}.{os.getpid()}.tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f, indent=1)
    os.replace(tmp, CACHE_PATH)


def _vtuple(v):
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums) if nums else None


def is_newer(latest, installed):
    """True if `latest` is a newer version than `installed` (tuple compare, else !=)."""
    if not latest:
        return False
    if not installed:
        return True
    a, b = _vtuple(latest), _vtuple(installed)
    if a and b:
        return a > b
    return latest != installed


def check(force=False):
    """Refresh the cache if it is stale (or `force`). Returns the item list."""
    cache = load_cache()
    items = cache.get("items", {})
    stale = force or (time.time() - cache.get("checked_at", 0)) > CHECK_INTERVAL_S
    for src in SOURCES:
        cur = items.get(src["id"], {})
        if stale:
            if src["kind"] == "release":
                cur = _fetch_release(src["repo"], cur)
            elif src["kind"] == "commit":
                cur = _fetch_commit(src["repo"], cur)
            elif src["kind"] == "kernel":
                cur = _fetch_kernel(cur)
            elif src["kind"] == "steamos":
                cur = _fetch_steamos(cur)
        if src.get("installed"):
            try:
                cur["installed"] = src["installed"]()
            except Exception:  # noqa: BLE001
                cur["installed"] = None
        items[src["id"]] = cur
    if stale:
        cache["checked_at"] = time.time()
    cache["items"] = items
    _save_cache(cache)
    return summary(cache)


def summary(cache=None):
    """Rows for the UI: one dict per source, with `new` computed."""
    cache = cache or load_cache()
    items = cache.get("items", {})
    rows = []
    for src in SOURCES:
        it = items.get(src["id"], {})
        latest, installed, seen = it.get("latest"), it.get("installed"), it.get("seen")
        if src.get("installed"):
            new = is_newer(latest, installed) and latest != seen
        else:
            new = bool(latest) and latest != seen
        rows.append({"id": src["id"], "label": src["label"], "repo": src.get("repo"),
                     "kind": src["kind"], "note": src.get("note", ""), "installer": src.get("installer"),
                     "installed": installed, "latest": latest, "published": it.get("published"),
                     "url": it.get("url"), "notes": it.get("notes"), "error": it.get("error"),
                     "new": new})
    return rows


def new_items(cache=None):
    return [r for r in summary(cache) if r["new"]]


def mark_seen(ids=None):
    """Acknowledge current `latest` values so they stop counting as new."""
    cache = load_cache()
    for src in SOURCES:
        if ids and src["id"] not in ids:
            continue
        it = cache.setdefault("items", {}).setdefault(src["id"], {})
        if it.get("latest"):
            it["seen"] = it["latest"]
    _save_cache(cache)


def last_checked():
    return load_cache().get("checked_at", 0)


if __name__ == "__main__":
    import sys
    rows = check(force="--force" in sys.argv)
    for r in rows:
        flag = "NEW" if r["new"] else "   "
        print(f"{flag} {r['label']:<38} installed={r['installed'] or '-':<12} "
              f"latest={r['latest'] or '-':<10} {r['published'] or ''}  {r['error'] or ''}")

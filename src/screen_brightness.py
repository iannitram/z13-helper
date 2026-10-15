"""Built-in screen (display backlight) brightness.

Two backends, tried in order:

  kde    PowerDevil's org.kde.ScreenBrightness D-Bus API (Plasma 6.2+). The
         preferred path: PowerDevil owns the backlight in Desktop Mode, so
         going through it keeps the Plasma slider, the Fn keys and its
         dim-on-idle in step with us. A raw sysfs write behind its back would
         be undone the next time it restores or dims. Needs
         --talk-name=org.kde.ScreenBrightness in the Flatpak manifest.
  sysfs  /sys/class/backlight/<dev>/brightness, written on the host (the
         sandbox mounts /sys read-only; SteamOS makes the node group-writable
         by deck). Fallback for when PowerDevil isn't running.

Brightness is exposed as a percentage, floored at MIN_PERCENT so the slider
can't black the panel out.

Game Mode has Steam's own brightness slider; this is for Desktop Mode only.
"""

import logging
import os
import subprocess
import threading

from hardware_controller import _in_flatpak

logger = logging.getLogger(__name__)

MIN_PERCENT = 1

KDE_SERVICE = "org.kde.ScreenBrightness"
KDE_PATH = "/org/kde/ScreenBrightness"
KDE_IFACE = "org.kde.ScreenBrightness"
KDE_DISPLAY_IFACE = "org.kde.ScreenBrightness.Display"
# SetBrightness flags: 1 = SuppressIndicator (no OSD popup on every slider step).
KDE_SUPPRESS_OSD = 1

BACKLIGHT_ROOT = "/sys/class/backlight"


def to_percent(raw: int, maximum: int) -> int:
    if maximum <= 0:
        return 0
    return max(0, min(100, round(raw * 100 / maximum)))


def to_raw(percent: int, maximum: int) -> int:
    percent = max(MIN_PERCENT, min(100, int(percent)))
    return max(1, round(percent * maximum / 100))


def find_backlight(root: str = BACKLIGHT_ROOT) -> str | None:
    """The internal panel's backlight dir (amdgpu_bl* on the Z13), or None."""
    try:
        names = sorted(os.listdir(root))
    except OSError:
        return None
    # Prefer firmware/raw panel drivers over generic ones (acpi_video* is often a dud).
    names.sort(key=lambda n: (n.startswith("acpi_video"), n))
    for n in names:
        if os.path.exists(os.path.join(root, n, "max_brightness")):
            return os.path.join(root, n)
    return None


def _read_int(path: str) -> int | None:
    try:
        with open(path) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


class _KdeBackend:
    name = "kde"

    def __init__(self, bus):
        self._bus = bus
        self._path = None       # the chosen display's object path
        self._max = 0           # ...and its MaxBrightness, cached for set()

    def _iface(self, path, iface):
        from PyQt6.QtDBus import QDBusInterface
        return QDBusInterface(KDE_SERVICE, path, iface, self._bus)

    def _props(self, path, iface):
        # QDBusInterface.property() returns None under PyQt6; ask Properties directly.
        from PyQt6.QtDBus import QDBusMessage
        r = self._iface(path, "org.freedesktop.DBus.Properties").call("GetAll", iface)
        if r.type() != QDBusMessage.MessageType.ReplyMessage or not r.arguments():
            return None
        return r.arguments()[0]

    def _display(self):
        """(path, props) of the internal panel, or None."""
        if self._path:
            props = self._props(self._path, KDE_DISPLAY_IFACE)
            if props:
                return self._path, props
            self._path = None
        root = self._props(KDE_PATH, KDE_IFACE) or {}
        found = []
        for n in root.get("DisplaysDBusNames") or []:
            path = f"{KDE_PATH}/{n}"
            props = self._props(path, KDE_DISPLAY_IFACE)
            if props:
                found.append((path, props))
        # The built-in screen, else the first display PowerDevil knows.
        found.sort(key=lambda d: not d[1].get("IsInternal"))
        if not found:
            return None
        self._path = found[0][0]
        return found[0]

    def _remember(self, d):
        if d is not None:
            self._max = int(d[1].get("MaxBrightness") or 0)
        return d

    def available(self) -> bool:
        return self._remember(self._display()) is not None

    def get(self) -> int | None:
        d = self._remember(self._display())
        if d is None:
            return None
        raw, mx = d[1].get("Brightness"), d[1].get("MaxBrightness")
        if raw is None or not mx:
            return None
        return to_percent(int(raw), int(mx))

    def set(self, percent: int):
        from PyQt6.QtDBus import QDBus
        from PyQt6.QtCore import QVariant, QMetaType
        if not (self._path and self._max) and self._remember(self._display()) is None:
            raise RuntimeError("PowerDevil screen brightness service went away")
        flags = QVariant(KDE_SUPPRESS_OSD)
        flags.convert(QMetaType(QMetaType.Type.UInt.value))   # signature is (iu)
        # NoBlock: called on every slider step from the GUI thread.
        self._iface(self._path, KDE_DISPLAY_IFACE).call(
            QDBus.CallMode.NoBlock, "SetBrightness", to_raw(percent, self._max), flags)


class _SysfsBackend:
    name = "sysfs"

    def __init__(self, path: str, runner=None):
        self.path = path
        self._runner = runner or _host_write
        self._max = _read_int(os.path.join(path, "max_brightness")) or 0
        # Host writes are subprocesses: run them off the GUI thread, always
        # writing only the latest requested value (slider drags fire fast).
        self._want = None
        self._cond = threading.Condition()
        threading.Thread(target=self._pump, name="backlight-writer", daemon=True).start()

    def available(self) -> bool:
        return self._max > 0

    def get(self) -> int | None:
        raw = _read_int(os.path.join(self.path, "brightness"))
        return None if raw is None else to_percent(raw, self._max)

    def set(self, percent: int):
        with self._cond:
            self._want = to_raw(percent, self._max)
            self._cond.notify()

    def _pump(self):
        while True:
            with self._cond:
                while self._want is None:
                    self._cond.wait()
                raw, self._want = self._want, None
            ok, err = self._runner(os.path.join(self.path, "brightness"), raw)
            if not ok:
                logger.error("Screen brightness write failed: %s", err)


def _host_write(path: str, value: int):
    cmd = ["tee", path]
    if _in_flatpak():
        cmd = ["flatpak-spawn", "--host"] + cmd
    try:
        p = subprocess.run(cmd, input=f"{value}\n", capture_output=True, text=True, timeout=10)
        return p.returncode == 0, (p.stderr or "").strip()
    except (subprocess.TimeoutExpired, OSError) as e:
        return False, str(e)


class ScreenBrightness:
    """Picks a backend at construction. `backend` is None if neither works."""

    def __init__(self, bus=None, backlight_root: str = BACKLIGHT_ROOT, runner=None):
        self.backend = None
        if bus is not None:
            try:
                kde = _KdeBackend(bus)
                if kde.available():
                    self.backend = kde
            except Exception as e:
                logger.info("PowerDevil screen brightness unavailable: %s", e)
        if self.backend is None:
            path = find_backlight(backlight_root)
            if path:
                sysfs = _SysfsBackend(path, runner)
                if sysfs.available():
                    self.backend = sysfs
        logger.info("Screen brightness backend: %s", self.backend.name if self.backend else "none")

    def available(self) -> bool:
        return self.backend is not None

    def get(self) -> int | None:
        try:
            return self.backend.get() if self.backend else None
        except Exception as e:
            logger.debug("Screen brightness read failed: %s", e)
            return None

    def set(self, percent: int):
        if self.backend is None:
            raise RuntimeError("No screen brightness control available")
        self.backend.set(max(MIN_PERCENT, min(100, int(percent))))

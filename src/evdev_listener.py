import glob
import os
import struct
import sys
import threading
import logging

logger = logging.getLogger(__name__)

# linux/input.h: struct input_event { struct timeval time; __u16 type; __u16 code; __s32 value; }
# On x86-64 Linux: timeval = (long sec, long usec) = 8+8 bytes → total 24 bytes.
_EVENT_FORMAT = "llHHi"
_EVENT_SIZE = struct.calcsize(_EVENT_FORMAT)
EV_KEY = 0x01


def find_side_button_device() -> tuple[str | None, int]:
    """Finds the best available device and key code for the Z13 side button.

    InputPlumber (when running) grabs the raw 'Asus WMI hotkeys' device exclusively
    and remaps KeyProg3 → BTN_MODE (316) on a virtual Xbox gamepad it creates under
    /sys/devices/virtual/input/.  We prefer that virtual device when present.

    Falls back to the raw 'Asus WMI hotkeys' device (KeyProg3 = 202) if InputPlumber
    is not running, though that device requires the input group + udev rule.

    Returns (device_path, key_code) or (None, 0) if nothing is found.
    """
    BTN_MODE   = 316   # Xbox guide/QuickAccess via InputPlumber virtual pad
    # KEY_PROG3's real evdev keycode is 202 (0xca) — verified live on the Z13:
    # pressing the side button emits `EV_KEY code=202` on the 'Asus WMI hotkeys'
    # device. The old notes/wiki said "579", which is NOT KEY_PROG3 and is why the
    # listener never fired. (579/0x243 was a mis-recorded value.)
    KEY_PROG3  = 202   # Raw WMI hotkey: Armoury Crate side button

    # 1. Look for InputPlumber's virtual Xbox pad in /sys/devices/virtual/input/
    virtual_root = "/sys/devices/virtual/input"
    try:
        for entry in sorted(os.listdir(virtual_root)):
            name_file = os.path.join(virtual_root, entry, "name")
            try:
                with open(name_file) as f:
                    if "x-box" in f.read().lower():
                        # Find the eventN child
                        for child in os.listdir(os.path.join(virtual_root, entry)):
                            if child.startswith("event"):
                                path = f"/dev/input/{child}"
                                if os.access(path, os.R_OK):
                                    logger.info(f"Using InputPlumber virtual Xbox device: {path}")
                                    return path, BTN_MODE
            except OSError:
                continue
    except OSError:
        pass

    # 2. Fall back to raw Asus WMI hotkeys device
    for name_path in glob.glob("/sys/class/input/event*/device/name"):
        try:
            with open(name_path) as f:
                if "wmi hotkeys" in f.read().lower():
                    event = os.path.basename(os.path.dirname(os.path.dirname(name_path)))
                    path = f"/dev/input/{event}"
                    if os.access(path, os.R_OK):
                        logger.info(f"Using raw WMI hotkeys device: {path}")
                        return path, KEY_PROG3
        except OSError:
            continue

    return None, 0


class EvdevListener(threading.Thread):
    """Listens for hardware button events by reading raw input events from /dev/input/.

    Uses Python's built-in struct module to decode linux input_event structs —
    no external evdev package required (which needs kernel headers to compile).
    """

    def __init__(self, device_path: str, callback):
        super().__init__()
        self.device_path = device_path
        self.callback = callback
        self._running = True
        self.daemon = True
        self._fd = None

    def run(self):
        logger.info(f"Starting EvdevListener on {self.device_path}")
        try:
            with open(self.device_path, "rb") as f:
                self._fd = f
                while self._running:
                    data = f.read(_EVENT_SIZE)
                    if len(data) < _EVENT_SIZE:
                        break
                    _, _, ev_type, code, value = struct.unpack(_EVENT_FORMAT, data)
                    if ev_type == EV_KEY and value == 1:  # key press (not release/repeat)
                        self.callback(code)
        except PermissionError:
            logger.error(
                f"Permission denied on {self.device_path}. "
                "Add user to 'input' group or check udev rules."
            )
        except Exception as e:
            logger.error(f"EvdevListener error on {self.device_path}: {e}")
        finally:
            self._fd = None

    def stop(self):
        self._running = False
        if self._fd:
            try:
                self._fd.close()
            except OSError:
                pass


def run_emit_loop() -> int:
    """Machine-readable reader: print ``PRESS`` to stdout on each side-button press.

    This is the entrypoint the Flatpak app drives via
    ``flatpak-spawn --host python3 - --emit`` (feeding this file's own source on
    stdin). It has to run on the *host* rather than inside the sandbox because
    Flatpak drops our supplementary groups ('users'/'input'), so an in-sandbox
    open() of /dev/input/event* returns EACCES — while on the host the deck user
    is in the 'users' group that 99-z13ctl.rules grants read access to.

    Protocol (line-based, stdout):
        READY <device> <code>   → emitted once on stderr for diagnostics
        NODEV                    → emitted on stderr if no device was found (exit 2)
        PRESS                    → one line per side-button press
    """
    device, code = find_side_button_device()
    if not device:
        print("NODEV", file=sys.stderr, flush=True)
        return 2
    print(f"READY {device} {code}", file=sys.stderr, flush=True)

    def on_key(event_code):
        # Only the side button — the raw WMI/virtual-pad device also carries
        # other hotkeys/gamepad buttons; filter to the code we resolved.
        if event_code == code:
            print("PRESS", flush=True)

    listener = EvdevListener(device, on_key)
    listener.daemon = False
    listener.start()
    try:
        listener.join()
    except KeyboardInterrupt:
        listener.stop()
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    if "--emit" in sys.argv:
        sys.exit(run_emit_loop())

    device, code = find_side_button_device()
    if device:
        print(f"Listening on {device} for key code {code} (Ctrl+C to stop)...")

        def on_key(event_code):
            marker = "  <-- SIDE BUTTON" if event_code == code else ""
            print(f"Key pressed: code={event_code} (0x{event_code:04x}){marker}")

        listener = EvdevListener(device, on_key)
        listener.daemon = False
        listener.start()
        try:
            listener.join()
        except KeyboardInterrupt:
            listener.stop()
    else:
        print("No accessible side-button device found.")
        print("Available input devices:")
        for name_path in sorted(glob.glob("/sys/class/input/event*/device/name")):
            try:
                event = os.path.basename(os.path.dirname(os.path.dirname(name_path)))
                with open(name_path) as f:
                    print(f"  /dev/input/{event}: {f.read().strip()}")
            except OSError:
                pass

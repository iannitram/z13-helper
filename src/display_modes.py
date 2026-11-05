"""Desktop refresh-rate choice for the built-in panel, through kscreen-doctor (#28).

Only modes the panel advertises are offered: the list comes from kscreen-doctor's own
output for the panel at its current resolution. A change is read back; if the panel
did not end up in the requested mode, the previous mode is restored. `previous` keeps
the mode before the last successful change so the UI can offer "Back".

Desktop Mode only. Game Mode refresh is gamescope's (docs/game-mode-display.md).
Commands go through host.run (kscreen-doctor runs on the host, in the user's session).
"""

import collections
import re

import host

PANEL = "eDP-1"
TIMEOUT_S = 15

Mode = collections.namedtuple("Mode", "id w h hz current preferred")

_ANSI = re.compile(r"\x1b\[[0-9;]*m")
_MODE = re.compile(r"(\d+):(\d+)x(\d+)@([\d.]+)([*!]*)")


def parse_modes(text, output=PANEL):
    """Modes of `output` from `kscreen-doctor -o` text; [] when the output or its list is missing."""
    block = None
    for line in _ANSI.sub("", text or "").splitlines():
        if line.startswith("Output:"):
            parts = line.split()
            block = len(parts) > 2 and parts[2] == output
            continue
        if block and line.strip().startswith("Modes:"):
            return [Mode(int(i), int(w), int(h), float(hz), "*" in flags, "!" in flags)
                    for i, w, h, hz, flags in _MODE.findall(line)]
    return []


def current_mode(modes):
    return next((m for m in modes if m.current), None)


def refresh_choices(modes):
    """[(whole Hz, Mode)] at the current resolution, one per distinct rate, lowest first."""
    cur = current_mode(modes)
    if cur is None:
        return []
    best = {}
    for m in modes:
        if (m.w, m.h) != (cur.w, cur.h):
            continue
        hz = int(round(m.hz))
        # Two modes can round to one rate (59.88 / 60.00): keep the closer to whole.
        if hz not in best or abs(m.hz - hz) < abs(best[hz].hz - hz):
            best[hz] = m
    return sorted(best.items())


def read_modes(output=PANEL):
    res = host.run(["kscreen-doctor", "-o"], timeout=TIMEOUT_S)
    return parse_modes(res.out, output) if res.rc == 0 else []


def _set(mode_id, output):
    return host.run(["kscreen-doctor", f"output.{output}.mode.{mode_id}"], timeout=TIMEOUT_S)


class RefreshControl:
    def __init__(self, output=PANEL):
        self.output = output
        self.previous = None       # Mode before the last successful change

    def choices(self):
        return refresh_choices(read_modes(self.output))

    def set_refresh(self, hz):
        """Switch to `hz` (a rate from choices()). Returns (ok, message); never raises.

        Refuses a rate the panel does not advertise. A change that does not read back
        is rolled back to the mode we started from.
        """
        modes = read_modes(self.output)
        before = current_mode(modes)
        target = dict(refresh_choices(modes)).get(hz)
        if before is None or target is None:
            return False, f"{hz} Hz is not a mode this panel advertises"
        if target.id == before.id:
            return True, f"Already at {hz} Hz"
        res = _set(target.id, self.output)
        now = current_mode(read_modes(self.output))
        if res.rc == 0 and now is not None and now.id == target.id:
            self.previous = before
            return True, f"Refresh {hz} Hz"
        # Rejected or not reflected: put the old mode back.
        _set(before.id, self.output)
        restored = current_mode(read_modes(self.output))
        tail = "restored the previous mode" if restored and restored.id == before.id \
            else "could not confirm the previous mode"
        return False, f"{hz} Hz was not accepted ({tail})"

    def back(self):
        """Return to the mode before the last change. (ok, message)."""
        if self.previous is None:
            return False, "No previous mode to return to"
        modes = read_modes(self.output)
        prev = next((m for m in modes if (m.w, m.h, round(m.hz, 2)) ==
                     (self.previous.w, self.previous.h, round(self.previous.hz, 2))), None)
        if prev is None:
            return False, "The previous mode is no longer advertised"
        res = _set(prev.id, self.output)
        now = current_mode(read_modes(self.output))
        if res.rc == 0 and now is not None and now.id == prev.id:
            self.previous = None
            return True, f"Back to {int(round(prev.hz))} Hz"
        return False, "Could not return to the previous mode"

"""display_modes: parse kscreen-doctor, offer only advertised rates, read back and roll back (#28)."""

import pytest

import display_modes as dm
import host

SAMPLE = (
    "Output: 1 eDP-1 22dc4f73-0c3d-4d07-94fd-91508c1da7ff\n"
    "\tenabled\n\tconnected\n"
    "\tModes:  1:2560x1600@180.00!  2:2560x1600@60.00  3:1920x1200@59.88  4:1920x1200@179.89*"
    "  5:1280x720@59.85  6:1280x720@179.72 \n"
    "\tScale: 1.25\n"
    "Output: 2 HDMI-A-1 aaaa\n\tModes:  1:1920x1080@60.00*!  2:1920x1080@144.00\n"
)


def sample(current):
    """SAMPLE with the `*` on mode id `current` of eDP-1."""
    line = ("\tModes: " + "  ".join(f"{i}:{w}x{h}@{hz}{'*' if i == current else ''}"
                                    for i, w, h, hz in ((1, 2560, 1600, "180.00"), (2, 2560, 1600, "60.00"),
                                                        (3, 1920, 1200, "59.88"), (4, 1920, 1200, "179.89"))) + "\n")
    return "Output: 1 eDP-1 uuid\n" + line


def test_parse_picks_the_panel_block_only():
    modes = dm.parse_modes(SAMPLE)
    assert [m.id for m in modes] == [1, 2, 3, 4, 5, 6]
    assert dm.current_mode(modes).id == 4 and modes[0].preferred
    assert dm.parse_modes(SAMPLE, "HDMI-A-1")[1].hz == 144.0
    assert dm.parse_modes(SAMPLE, "DP-9") == [] and dm.parse_modes("") == []


def test_ansi_colour_codes_are_ignored():
    assert len(dm.parse_modes("\x1b[01;32mOutput: 1 eDP-1 u\x1b[0;0m\n\tModes:  1:1x2@3.00*\n")) == 1


def test_choices_are_whole_rates_at_current_resolution():
    assert [(hz, m.id) for hz, m in dm.refresh_choices(dm.parse_modes(SAMPLE))] == [(60, 3), (180, 4)]


def test_no_current_mode_means_no_choices():
    assert dm.refresh_choices(dm.parse_modes("Output: 1 eDP-1 u\n\tModes:  1:1x2@3.00\n")) == []


class Screen:
    """Scripted kscreen-doctor: `-o` reports the current mode; a set command moves it
    only when `accept` is true, except that putting back `home` always works."""

    def __init__(self, start=4, accept=True):
        self.cur, self.home, self.accept = start, start, accept
        self.argvs = []

    def run(self, argv, timeout=30, *, as_deck=False, input=None):
        self.argvs.append(list(argv))
        if argv[:2] == ["kscreen-doctor", "-o"]:
            return host.Result(0, sample(self.cur), False)
        mid = int(argv[1].rsplit(".", 1)[1])
        if self.accept or mid == self.home:
            self.cur = mid
        return host.Result(0 if self.accept else 1, "", False)

    def sets(self):
        return [a[1] for a in self.argvs if a[1] != "-o"]


@pytest.fixture
def screen(monkeypatch):
    s = Screen()
    monkeypatch.setattr(host, "run", s.run)
    return s


def test_set_refresh_switches_and_remembers_previous(screen):
    ctl = dm.RefreshControl()
    assert ctl.set_refresh(60) == (True, "Refresh 60 Hz")
    assert screen.cur == 3 and ctl.previous.id == 4
    assert screen.sets() == ["output.eDP-1.mode.3"]
    assert ctl.back() == (True, "Back to 180 Hz") and screen.cur == 4 and ctl.previous is None


def test_unadvertised_rate_is_refused_without_writing(screen):
    assert dm.RefreshControl().set_refresh(144)[0] is False
    assert screen.sets() == []


def test_same_rate_is_a_noop(screen):
    assert dm.RefreshControl().set_refresh(180) == (True, "Already at 180 Hz")
    assert screen.sets() == []


def test_rejected_mode_rolls_back(screen):
    screen.accept = False
    ctl = dm.RefreshControl()
    ok, msg = ctl.set_refresh(60)
    assert not ok and "restored the previous mode" in msg
    assert screen.sets() == ["output.eDP-1.mode.3", "output.eDP-1.mode.4"]
    assert screen.cur == 4 and ctl.previous is None


def test_back_without_a_change(screen):
    assert dm.RefreshControl().back() == (False, "No previous mode to return to")

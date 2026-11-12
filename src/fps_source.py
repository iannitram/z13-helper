"""Read the current game's frame rate in Game Mode, when a safe source exists.

Phase 15d probe (#126) found no dependable SteamOS 3.9.5 source that can be
read by this plugin without configuring a separate telemetry producer. gamescope
was not running during the probe, and its stats output requires a path supplied
when gamescope starts. MangoHud logging is opt-in and writes historical samples;
it does not provide a live read contract for this process. Steam's client does
not expose a frame-rate reading through a supported interface used here.

Until a source is demonstrated, Hold FPS is a no-go. Keep this API fail-closed:
missing or unsupported telemetry is unknown, not zero FPS.
"""


def read():
    """Return current game FPS, or ``None`` when no dependable source is available.

    This deliberately performs no I/O, process launch, privilege escalation, or
    hardware access. The current probe did not establish a source whose read
    latency and semantics could be guaranteed within the 50 ms contract.
    """
    return None

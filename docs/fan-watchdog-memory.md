# Fan watchdog memory investigation (#30)

## Evidence and conclusion

The issue's supplied historical evidence reports one backend identity at 43–44 MiB
for 17 h 14 min, with a separate regular 4 h 18 min overlap. Those windows cannot
be added together to establish a 24-hour run. They do not reproduce or explain the
reported 43 GB resident-memory spike. No completed current-identity sampler log
is available in this worktree. **Conclusion: inconclusive; #30 remains open.**
PR #107 supplied `tooling/rss_watch.py`; tooling and simulated-time tests are not
an actual soak. A valid device run must end with `COMPLETE`, span at least
86,400 seconds, and keep the same PID/start-time identity throughout.

## Source audit (2026-11-04)

| Area | Finding |
|---|---|
| `decky/main.py` watchdog loop | One watchdog instance, one awaited `to_thread` tick every 2 s; no per-tick background task or retained sample list. A held write lock skips the tick. No cumulative retention found. |
| Desktop-app check | One awaited check every 30 s, fixed counters and one restart per miss streak; no growing retry list. |
| Watchdog recovery (`src/fan_watchdog.py`) | Current-tick event list is replaced every tick. Per-fan arrays have two entries, fault set has at most two entries; recovery results are local. Attempts and retry gaps bound repeated hardware recovery. No retained growing result collection found. |
| Trace history | Previously bounded only by the last 120 s of monotonic time (normally about 61 Game Mode entries). Now also capped at 256 entries, including repeated timestamps or burst calls. This is preventive hardening, not an identified cause of the report. |
| Trace output | Synchronous rotating handler, 2 MiB per file plus three backups; no in-memory log queue. `to_file` removes/closes old handlers for the same logger name. Event replay copies only the bounded deque. No cumulative retention found. |
| Commands and retries | Commands go through `host.run`; timeouts bound command duration (hardware writes 20 s, full profile apply 180 s). Lighting recovery retries are finite and rate limited; file-lock waits have a deadline. No unbounded watchdog retry/task accumulation found. |
| Output capture | `host.run` previously used `subprocess.run(capture_output=True)` with no byte cap, so a noisy child could transiently consume memory before its timeout. It now merges stdout/stderr through a reader thread that keeps at most `MAX_OUTPUT_BYTES` (1 MiB) and discards (but still drains) the rest; tests cover the cap. This is preventive hardening, not evidence for the historical report. The RSS guard still does not count child-process RSS. Concurrent frontend requests can also queue behind the write lock; the periodic watchdog itself does not enqueue ticks. |

## Containment

**To-do 3 (cap or self-restart) is only partly done.** The cap is `os._exit` at
500 MiB, **off by default**: it only exits when `Z13_RSS_GUARD_EXIT=1` is set, until the device check below is done; otherwise a trip logs once. Whether Decky Loader respawns the plugin afterwards is **unverified**
and needs a device check; until then a trip may leave the Game Mode fan watchdog
and backend API down until the plugin is reloaded by hand. No restart or recovery
path is implemented here.

`BackendMemoryGuard` in `decky/main.py` reads current `VmRSS` from
`/proc/self/status` immediately at startup and then every 5 s on one daemon
thread. It works independently of the asyncio event loop, hardware write lock
and thread-pool workers. At **500 MiB or above**, it emits a numeric diagnostic
to stderr and, only when `Z13_RSS_GUARD_EXIT=1`, calls `os._exit(1)` for this plugin's backend process, **assuming** Decky Loader runs each plugin backend as a separate process. That isolation is unverified: no Decky version or source has been checked, and if a backend ever ran in-process the exit would take the loader down with it.
The guard holds no sample history. Unload signals and joins the monitor.
Read failures warn once per failure streak and retry; they never trigger an exit
based on a missing or malformed reading.

This is a sampled fail-stop threshold, not a kernel-enforced allocation cap:
a rapid allocation may overshoot it, a failed monitor cannot enforce it, and
child-process RSS is not counted. A trip removes the plugin's Game Mode fan
watchdog and API until the plugin is reloaded. It does not restart the shared
Decky Loader, rewrite firmware settings or clear persisted intent. An in-flight
host command may finish after the backend exits. Automatic loader recovery is
not assumed and must be observed on the device; operator plugin reload is the
fallback. The threshold is deliberately well above the supplied 43–44 MiB baseline.

Regression tests cover the threshold, current rather than peak/virtual memory,
read failures/retry, exit even if stderr fails, independent monitor lifecycle,
plugin startup/unload wiring, and trace entry/time bounds. They use synthetic
RSS and never exit a real backend or allocate hundreds of MiB.

The remaining evidence is a completed 24-hour run and its measured interpretation
(flat, growing or inconclusive), plus device confirmation of containment/reload.
No found-and-fixed leak or attribution
of the 43 GB report is claimed.

Device check still needed: trip the guard on a test build (lower the limit), then record whether Decky respawns the backend, and confirm each plugin backend is a separate process (cite the Decky Loader version/source).

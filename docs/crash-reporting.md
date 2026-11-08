# Automatic crash reports (#82)

The desktop app and Decky plugin install `src/crash_report.py` at startup.
Python main-thread exceptions (including PyQt slots), worker-thread exceptions,
and unhandled Decky asyncio errors are handed to one bounded background worker.
Existing exception handlers still run. Failed Decky background tasks are reported
when they finish, rather than waiting for task garbage collection.
The worker exits when idle. While processing a crash it keeps the interpreter
alive to avoid losing a top-level exception during shutdown; the upload remains
in the background and is bounded by its timeout. Clean shutdown spools accepted
crashes and stops further uploads without waiting in the UI thread.

Help → Error log has **Send crash reports automatically**, default on. The shared
`install.json` preference is `send_crash_reports`; it is independent of local
disk logging. Both frontends check the saved preference before each upload.
An upload already in progress may finish after the setting is disabled.

Reports contain `report.json` and the last 500 lines of the app's own log, scrubbed
before either queuing or upload. The queue is `crash-upload/` under the app's state
directory, shared by both frontends, with a persistent random installation id.
It retains at most ten zips, dropping the oldest arrivals. A cross-process lock
serializes queue changes and limits **upload attempts** to five per UTC day;
failed and timed-out requests count because they may have reached the server.
An unsuccessful upload stays queued and retries on the next start with the same
filename. Each start makes at most one attempt per queued report.

Reports are uploaded into the drop's `z13-helper/` folder (created by the drop on the
first upload); queued retries go to the same folder.

Fatal signals write through faulthandler and are reported on the next start;
Python cannot safely create or upload a zip inside a fatal signal handler.
Desktop local logging retains its existing fatal report. Decky and desktop runs
with local logging disabled use separate fatal files in the upload state folder.
Automatic reporting can therefore write crash state even when local logging is off.

## Packaging

The private build supplies `CRAFT_BUG_DROP_BASE` and `CRAFT_BUG_DROP_TOKEN` in its
environment. Before building or packaging either frontend, run:

```sh
python3 tooling/build_crash_config.py --commit <source-commit>
```

This writes ignored `src/_crash_build.json`, containing the drop configuration,
source revision and package version. No private configuration is committed.
The Flatpak manifest copies it with the Python sources and permits networking;
the Decky installer copies it into root-owned `py_modules/`, alongside the report,
scrubber and queue modules. Without the generated file, Decky installs an empty
default and both frontends emit one log line and do no reporting. Runtime
environment variables cannot configure or override a packaged drop.

The transport follows craft-bugreport-web's `src/core/upload.js`: ZIP `PUT` to
`<base>/public.php/dav/files/<encoded-token>/<filename>`, HTTP 201 means accepted.
Requests have a ten-second socket timeout, reject redirects, and use
`If-None-Match: *` to prevent overwriting an existing report after an ambiguous
timeout. An HTTP 412 for that filename acknowledges an already-present report.
Network failures are logged without exception details or private URLs.

## Verification

Tests use scratch paths, fake configuration and mocked HTTP; they do not upload
anything. `tests/test_crash_wiring.py` covers hooks, de-duplication, privacy,
preferences, shared limits, offline restart, fatal recovery and the transport
contract. `decky/tests/test_crash_entry.py` covers plugin startup and unload.

A real configured-build crash upload and the intake filename on
craft-bugreport-web#12 remain device/network checks. They must be performed
separately; the offline tests do not establish successful delivery to the drop.

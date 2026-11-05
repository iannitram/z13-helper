import collections
import faulthandler
import io
import json
import logging
import os
import platform
import queue
import re
import secrets
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from crash_queue import CrashQueue
from crash_scrub import scrub

APP_ID = "z13-helper"
LOG_LINES = 500
MAX_LOG_LINE_CHARS = 2048
MAX_FATAL_BYTES = 256 * 1024
MAX_FATAL_READ_BYTES = 1024 * 1024
MESSAGE_CAP = 4096
REPORT_CAP_BYTES = 512 * 1024
SETTING = "send_crash_reports"
BUILD_CONFIG = Path(__file__).with_name("_crash_build.json")
UPLOAD_TIMEOUT = 10


def reporting_enabled(config_path: str) -> bool:
    """Shared desktop/Decky preference; malformed settings fail closed."""
    try:
        config = json.loads(Path(config_path).read_text())
        return isinstance(config, dict) and config.get(SETTING, True) is True
    except FileNotFoundError:
        return True
    except (OSError, ValueError):
        return False


def set_reporting(config_path: str, enabled: bool) -> None:
    """Save the toggle atomically, preserving unrelated shared preferences."""
    path = Path(config_path)
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        config = {}
    if not isinstance(config, dict):
        raise TypeError("Settings must be a JSON object")
    config[SETTING] = bool(enabled)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        tmp.write_text(json.dumps(config, indent=4))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


class _LogTail(logging.Handler):
    def __init__(self, max_line_len: int = MAX_LOG_LINE_CHARS):
        super().__init__()
        self.lines = collections.deque(maxlen=LOG_LINES)
        self.max_line_len = max_line_len

    def emit(self, record):
        line = self.format(record)
        if self.max_line_len and len(line.encode("utf-8")) > self.max_line_len:
            line = _cap_bytes(line, self.max_line_len) + " ... [cut]"
        self.lines.append(line)


def _cap_bytes(text: str, limit: int) -> str:
    """Limit text to limit UTF-8 bytes without splitting a multi-byte character."""
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore")


def read_fatal(fatal: Path | str, max_bytes: int = MAX_FATAL_BYTES) -> str:
    """Read a fatal crash log, capping its size in UTF-8 bytes and marking truncation."""
    p = Path(fatal)
    try:
        if p.exists() and p.is_file():
            size = p.stat().st_size
            with p.open("rb") as f:
                data = f.read(max_bytes)
                text = data.decode("utf-8", errors="ignore")
                if size > max_bytes or f.read(1):
                    return text + "\n... [cut] ...\n"
                return text
    except (OSError, ValueError):
        pass
    text = str(fatal)
    if len(text.encode("utf-8")) > max_bytes:
        return _cap_bytes(text, max_bytes) + "\n... [cut] ...\n"
    return text


class FatalError(RuntimeError):
    """A fatal signal recovered from faulthandler on the following start."""


class CrashReporter:
    """One bounded background worker. Hooks only enqueue; all I/O runs here.

    Failed uploads stay queued until the next start. A shared file lock serializes
    desktop and Decky queue operations, including the daily upload reservations.
    """

    def __init__(self, build, config_path, directory, logger, *, log_text=None, upload=None,
                 fatal_active=lambda: True, fatal_name="fatal-desktop.log"):
        self.build, self.config_path = build, config_path
        self.directory, self.log = Path(directory), logger
        self.log_text = log_text
        self.upload = upload or upload_report
        self.events = queue.Queue(maxsize=10)
        self.capture_lock = threading.Lock()
        self.stopped = threading.Event()
        self.seen = object()
        self.tail = _LogTail()
        logger.addHandler(self.tail)
        self.previous_hooks = (sys.excepthook, threading.excepthook)
        self.loop = None
        self.fatal_active = fatal_active
        self.fatal_name = fatal_name
        self.fatal_file = None
        self.fatal_lock = threading.Lock()
        self.offline = False
        self.prepared_fatal = False
        self.running = False
        self.worker = None

    def _start_worker(self):
        # Called under capture_lock. Exit when idle, but keep Python alive long
        # enough to persist an uncaught top-level exception before interpreter exit.
        if not self.running:
            self.running = True
            self.worker = threading.Thread(target=self._work, name="crash-reports", daemon=False)
            self.worker.start()

    def capture(self, exc, tb=None, *, fatal_text=None):
        if self.stopped.is_set() or isinstance(exc, (KeyboardInterrupt, SystemExit)):
            return
        # Mark the exception itself: do not retain tracebacks in a global seen set.
        try:
            with self.capture_lock:
                if self.stopped.is_set():
                    return
                if getattr(exc, "_z13_crash_reporter", None) is self.seen:
                    return
                exc._z13_crash_reporter = self.seen
                event = (exc, tb or exc.__traceback__, fatal_text)
                try:
                    self.events.put_nowait(event)
                except queue.Full:
                    # Keep the newest crashes during a storm, just like the disk queue.
                    try:
                        self.events.get_nowait()
                        self.events.task_done()
                    except queue.Empty:
                        pass
                    self.events.put_nowait(event)
                self._start_worker()
        except Exception:  # noqa: BLE001 -- reporting must never break an exception hook
            # A broken exception object or a crash storm must not break the hook.
            return

    def install_hooks(self, loop=None):
        def main_hook(kind, exc, tb):
            self.capture(exc, tb)
            self.previous_hooks[0](kind, exc, tb)

        def thread_hook(args):
            self.capture(args.exc_value, args.exc_traceback)
            self.previous_hooks[1](args)

        self.hooks = (main_hook, thread_hook)
        sys.excepthook, threading.excepthook = self.hooks
        if loop is not None:
            self.loop, self.previous_loop_hook = loop, loop.get_exception_handler()

            def loop_hook(loop, context):
                exc = context.get("exception") or RuntimeError(context.get("message", "Async error"))
                self.capture(exc, exc.__traceback__)
                if self.previous_loop_hook:
                    self.previous_loop_hook(loop, context)
                else:
                    loop.default_exception_handler(context)

            self.loop_hook = loop_hook
            loop.set_exception_handler(loop_hook)
        with self.capture_lock:
            self._start_worker()

    def close(self):
        """Restore hooks on Decky reload; never wait for an upload during exit."""
        self.stopped.set()
        if sys.excepthook is self.hooks[0]:
            sys.excepthook = self.previous_hooks[0]
        if threading.excepthook is self.hooks[1]:
            threading.excepthook = self.previous_hooks[1]
        if self.loop and self.loop.get_exception_handler() is self.loop_hook:
            self.loop.set_exception_handler(self.previous_loop_hook)
        self.log.removeHandler(self.tail)
        with self.fatal_lock:
            if self.fatal_file:
                faulthandler.disable()
                self.fatal_file.close()
                self.fatal_file = None

    def activate_fatal(self):
        """app_log's fallback when local disk logging is disabled."""
        with self.fatal_lock:
            if self.fatal_file and not self.stopped.is_set():
                faulthandler.enable(file=self.fatal_file, all_threads=True)
                return True
        return False

    def task_done(self, task):
        """Report failed Decky tasks immediately even while the plugin retains them."""
        if not task.cancelled() and (exc := task.exception()) is not None:
            self.capture(exc, exc.__traceback__)
            if self.loop:
                self.loop.call_exception_handler({
                    "message": "Unhandled Decky background task exception",
                    "exception": exc, "task": task,
                })

    def _prepare_fatal(self, pending):
        path = self.directory / self.fatal_name
        if path.exists() and path.stat().st_size:
            name, data = self._report(FatalError("Fatal Python error (previous run)"), None, path)
            pending.add(name, data)
        # This target covers Decky and desktop runs with local logging disabled.
        # app_log retains its own fatal target while local disk logging is on.
        with self.fatal_lock:
            if self.stopped.is_set():
                return
            self.fatal_file = path.open("w")
        if self.fatal_active():
            self.activate_fatal()

    def _work(self):
        event = None  # first pass retries the disk queue from a previous run
        while True:
            try:
                if reporting_enabled(self.config_path):
                    self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                    # Linux frontends share this lock across processes.
                    import fcntl
                    with (self.directory / "queue.lock").open("w") as lock:
                        fcntl.flock(lock, fcntl.LOCK_EX)
                        pending = CrashQueue(str(self.directory))
                        if not self.prepared_fatal:
                            self._prepare_fatal(pending)
                            self.prepared_fatal = True
                        if event is not None:
                            name, data = self._report(*event)
                            pending.add(name, data)
                        if not self.offline:
                            for name in pending.pending():
                                if self.stopped.is_set() or not reporting_enabled(self.config_path):
                                    break
                                if not pending.can_send():
                                    break
                                # Count attempts too: timeouts may already have reached the drop.
                                pending.mark_sent()
                                try:
                                    self.upload(self.build, name, pending.read(name))
                                except Exception:  # noqa: BLE001 -- do not echo private transport errors
                                    self.offline = True
                                    self.log.info("Crash report queued; retrying on next start")
                                    break
                                pending.remove(name)
            except Exception:  # noqa: BLE001 -- disk/metadata failures cannot crash the app
                # Never log the exception: transport errors can contain drop credentials.
                self.log.warning("Crash reporting unavailable; continuing without upload")
            finally:
                if event is not None:
                    self.events.task_done()
            with self.capture_lock:
                try:
                    event = self.events.get_nowait()
                except queue.Empty:
                    self.running = False
                    return

    def _report(self, exc, tb, fatal_text):
        import getpass
        import pwd
        import socket
        import traceback

        install_file = self.directory / "install-id"
        if not install_file.exists():
            install_file.write_text(secrets.token_hex(16))
        now = datetime.now(UTC)
        report = build_report(
            exc, version=self.build["version"], commit=self.build["commit"],
            os_name=platform.freedesktop_os_release().get("PRETTY_NAME", sys.platform),
            kernel=platform.release(), python=platform.python_version(), now=now,
            install_id=install_file.read_text().strip(),
        )
        tb_text = (read_fatal(fatal_text, max_bytes=MAX_FATAL_READ_BYTES) if fatal_text else
                   "".join(traceback.format_exception(type(exc), exc, tb)))
        # A uid with no passwd entry and no USER/LOGNAME makes this raise; the report
        # must still go out, just without that identifier to scrub.
        try:
            login = getpass.getuser()
        except Exception:  # noqa: BLE001 -- a failed lookup only loses a scrub target
            login = None
        try:
            home = str(Path.home())
        except Exception:  # noqa: BLE001 -- a failed lookup only loses a scrub target
            home = ""
        try:
            host = socket.gethostname()
        except Exception:  # noqa: BLE001
            host = None
        identities = {"home": home, "hostname": host, "user": login}
        try:
            account = pwd.getpwuid(os.getuid())
        except Exception:  # noqa: BLE001
            account = None

        def clean(text):
            # Build credentials are never report contents, even if an error echoed them.
            for key in ("base", "token"):
                text = text.replace(self.build[key], "<redacted>")
            text = scrub(text, **identities)
            if account:
                text = scrub(text, home=account.pw_dir, user=account.pw_name)
                text = scrub(text, home="", user=account.pw_gecos.split(",")[0] or None)
            return text

        # Redact before the size caps: a cut landing inside an identifier would
        # otherwise leave a partial one that clean() no longer matches.
        tb_text = clean(tb_text)
        if len(tb_text.encode("utf-8")) > MAX_FATAL_BYTES:
            tb_text = _cap_bytes(tb_text, MAX_FATAL_BYTES) + "\n... [cut] ...\n"
        report["traceback"] = tb_text
        report = {key: clean(value) for key, value in report.items()}
        if len(report["message"].encode("utf-8")) > MESSAGE_CAP:
            marker = " ... [cut]"
            report["message"] = _cap_bytes(report["message"], MESSAGE_CAP - len(marker)) + marker
        logs = self.log_text() if self.log_text else "\n".join(list(self.tail.lines))
        return (drop_filename(sys.platform, now, secrets.token_hex(4)),
                build_zip(report, clean(logs)))


def upload_report(build, name, data):
    """Nextcloud File Drop contract shared with craft-bugreport-web.

    No immediate retry: a timeout may have reached the server already. A queued
    retry on next start uses the same filename and refuses to overwrite it.
    """
    url = (build["base"].rstrip("/") + "/public.php/dav/files/" +
           urllib.parse.quote(build["token"], safe="") + "/" + urllib.parse.quote(name, safe=""))
    request = urllib.request.Request(url, data=data, method="PUT", headers={
        "Content-Type": "application/zip", "If-None-Match": "*",
    })
    # Reject redirects so neither the upload nor its token can reach another host.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=UPLOAD_TIMEOUT) as response:
            if response.status != 201:
                raise OSError("Bug drop did not accept the report")
    except urllib.error.HTTPError as error:
        # The same unpredictable filename already exists after an ambiguous
        # timeout. Do not overwrite it or let it block every subsequent report.
        if error.code != 412:
            raise


def install(*, config_path=None, state_dir=None, logger=None, log_text=None, loop=None,
            previous_fatal=None, fatal_active=lambda: True, fatal_name="fatal-desktop.log"):
    """Install hooks from packaged config only; never consult runtime drop env vars.

    PyQt routes slot exceptions to sys.excepthook. Fatal signals cannot upload
    safely inside a signal handler; the existing faulthandler report is queued
    on the next start instead.
    """
    log = logger or logging.getLogger()
    try:
        build = json.loads(BUILD_CONFIG.read_text())
        if not all(isinstance(build.get(k), str) and build[k]
                   for k in ("base", "token", "version", "commit")):
            raise ValueError("Incomplete build config")
    except (OSError, ValueError, AttributeError):
        log.info("Automatic crash reporting unavailable: no build-time drop configuration")
        return None
    reporter = CrashReporter(
        build, config_path or os.path.expanduser("~/.config/z13-helper/install.json"),
        state_dir or os.path.expanduser("~/.local/state/z13-helper/crash-upload"), log,
        log_text=log_text, fatal_active=fatal_active, fatal_name=fatal_name,
    )
    reporter.install_hooks(loop)
    if previous_fatal:
        reporter.capture(FatalError("Fatal Python error (previous run)"),
                         fatal_text=previous_fatal)
    return reporter


def drop_filename(platform: str, now: datetime, rand_hex: str) -> str:
    """Generate crash report filename in the format: z13-helper-<platform>-<stamp>-<rand_hex>.zip

    Args:
        platform: The platform identifier
        now: The timestamp (should be timezone-aware)
        rand_hex: 8 lowercase hex characters

    Returns:
        The formatted filename

    Raises:
        ValueError: If inputs are invalid
    """
    if not re.fullmatch(r"[a-f0-9]{8}", rand_hex):
        raise ValueError("rand_hex must be exactly 8 lowercase hex characters")

    # Clean platform name to keep only ASCII letters and digits
    cleaned_platform = re.sub(r"[^a-zA-Z0-9]", "", platform)
    if not cleaned_platform:
        raise ValueError(
            "platform must contain at least one ASCII letter or digit after cleaning"
        )

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    # Convert to UTC if needed
    utc_time = now.utctimetuple()
    stamp = f"{utc_time.tm_year}{utc_time.tm_mon:02}{utc_time.tm_mday:02}T{utc_time.tm_hour:02}{utc_time.tm_min:02}{utc_time.tm_sec:02}Z"

    return f"{APP_ID}-{cleaned_platform}-{stamp}-{rand_hex}.zip"


def log_tail(text: str, n: int = LOG_LINES, max_line_len: int = MAX_LOG_LINE_CHARS) -> str:
    """Get the last n lines of text, each ending with a newline.

    Args:
        text: The input text
        n: Number of lines to return (default: LOG_LINES)
        max_line_len: Maximum UTF-8 bytes per line (default: MAX_LOG_LINE_CHARS)

    Returns:
        The last n lines, each ending with \n
    """
    if not text or n <= 0:
        return ""

    lines = text.splitlines()
    out = []
    for line in lines[-n:]:
        if max_line_len and len(line.encode("utf-8")) > max_line_len:
            line = _cap_bytes(line, max_line_len) + " ... [cut]"
        out.append(line + "\n")
    return "".join(out)


def build_report(
    exc: BaseException,
    *,
    version: str,
    commit: str,
    os_name: str,
    kernel: str,
    python: str,
    now: datetime,
    install_id: str,
) -> dict:
    """Build a crash report dictionary.

    Args:
        exc: The exception that occurred
        version: App version
        commit: Git commit hash
        os_name: OS name
        kernel: Kernel version
        python: Python version
        now: Timestamp
        install_id: Installation ID

    Returns:
        Dictionary containing crash report data
    """
    import traceback

    # Convert timestamp to UTC ISO format
    utc_time = now.utctimetuple()
    timestamp = f"{utc_time.tm_year}-{utc_time.tm_mon:02}-{utc_time.tm_mday:02}T{utc_time.tm_hour:02}:{utc_time.tm_min:02}:{utc_time.tm_sec:02}Z"

    return {
        "app": APP_ID,
        "version": version,
        "commit": commit,
        "os": os_name,
        "kernel": kernel,
        "python": python,
        "exception_type": type(exc).__name__,
        "message": str(exc),
        "traceback": "".join(traceback.format_exception(exc)),
        "timestamp": timestamp,
        "install_id": install_id,
    }


def build_zip(report: dict, log_text: str) -> bytes:
    """Build a zip file containing report.json and app.log.

    The uncompressed entries stay under REPORT_CAP_BYTES: the oldest log lines
    are dropped first (one line kept), then the traceback is cut.

    Args:
        report: The crash report dictionary
        log_text: The log text

    Returns:
        Bytes of the zip file
    """
    lines = [line + "\n" for line in log_tail(log_text).splitlines()]
    line_sizes = [len(line.encode("utf-8")) for line in lines]
    log_size = sum(line_sizes)
    report_json = json.dumps(report, indent=2, sort_keys=True)
    while len(lines) > 1 and len(report_json.encode("utf-8")) + log_size > REPORT_CAP_BYTES:
        log_size -= line_sizes.pop(0)
        lines.pop(0)
    while len(report_json.encode("utf-8")) + log_size > REPORT_CAP_BYTES:
        tb = report.get("traceback") or ""
        cut = _cap_bytes(tb, len(tb.encode("utf-8")) * 3 // 4)
        if cut == tb:
            break
        report["traceback"] = cut
        report_json = json.dumps(report, indent=2, sort_keys=True)
    # Create zip in memory
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        zip_file.writestr("report.json", report_json)
        zip_file.writestr("app.log", "".join(lines))

    zip_buffer.seek(0)
    return zip_buffer.read()

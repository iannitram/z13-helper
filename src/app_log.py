"""Crash and error log: one place where everything the app logs, every uncaught
exception and every hard crash ends up, on disk and in memory for the
Help -> Error log window.

Files (all under ~/.local/state/z13-helper/, next to install.sh's logs; the
Flatpak has write access to exactly that folder):

    app.log, app.log.1..3      everything the app logs (INFO and up), rotated at 1 MB
    fan-trace-desktop.log      the fan watchdog's readings and decisions (fan_watchdog.FanTrace),
    fan-trace-gamemode.log     from this app and from the Decky plugin; rotated at 2 MB
    crashes/crash-<stamp>.txt  one report per uncaught exception: where, traceback,
                               app/OS/kernel versions, the last log lines
    crashes/fatal-<stamp>.txt  a hard crash (segfault/abort, e.g. inside Qt) from a
                               previous run, recovered from fatal.log on the next start
    fatal.log                  faulthandler's target for the current run; empty
                               unless the process dies on a fatal signal

What is caught: sys.excepthook (main thread; with it set, PyQt6 also routes
exceptions raised in slots here instead of aborting), threading.excepthook
(worker threads), faulthandler (fatal signals), and Qt's own warnings via
install_qt_handler(). An uncaught exception is reported, not fatal: the
resident app keeps running.

Stdlib only; nothing here imports Qt at module level, so the host side and the
tests can use it. Crash reports never run subprocesses (the crash path must not
hang).
"""

import collections
import faulthandler
import logging
import logging.handlers
import os
import platform
import sys
import threading
import time
import traceback

LOG_DIR = os.path.expanduser("~/.local/state/z13-helper")
LOG_NAME = "app.log"
CRASH_SUBDIR = "crashes"
FATAL_NAME = "fatal.log"
MAX_LOG_BYTES = 1 << 20
LOG_BACKUPS = 3
MAX_CRASH_REPORTS = 20
RING_SIZE = 1000
REPORT_TAIL_LINES = 200
FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"

_state = {"app_version": "?", "ring": None, "fatal_file": None, "crash_listeners": [],
          "previous_fatal": None}


def log_path():
    return os.path.join(LOG_DIR, LOG_NAME)


def fan_trace_path(side):
    """The fan watchdog's trace: side "desktop" (this app) or "gamemode" (the
    Decky plugin, which writes it as root and hands it to deck)."""
    return os.path.join(LOG_DIR, f"fan-trace-{side}.log")


def crash_dir():
    return os.path.join(LOG_DIR, CRASH_SUBDIR)


def fatal_path():
    return os.path.join(LOG_DIR, FATAL_NAME)


class RingHandler(logging.Handler):
    """Keeps the last RING_SIZE formatted records for the log window and tells
    listeners about each new one. Listeners run on the logging thread: a Qt
    listener must hand off through a signal (queued across threads)."""

    def __init__(self, size=RING_SIZE):
        super().__init__(logging.DEBUG)
        self.records = collections.deque(maxlen=size)   # (levelno, created, text)
        self._listeners = []
        self.counts = collections.Counter()              # levelname -> count this run

    def add_listener(self, fn):
        self._listeners.append(fn)

    def emit(self, record):
        try:
            text = self.format(record)
        except Exception:  # noqa: BLE001
            self.handleError(record)
            return
        item = (record.levelno, record.created, text)
        self.records.append(item)
        self.counts[record.levelname] += 1
        for fn in list(self._listeners):
            try:
                fn(item)
            except Exception:  # noqa: BLE001  (a broken listener must not break logging)
                pass

    def lines(self, min_level=logging.DEBUG):
        return [t for lv, _c, t in list(self.records) if lv >= min_level]


def ring():
    """The in-memory handler set up by setup(), or None."""
    return _state["ring"]


def set_version(v):
    """setup() runs before the UI module (which owns APP_VERSION) is imported."""
    _state["app_version"] = v
    logging.getLogger("z13").info("App version %s", v)


def previous_fatal():
    """Path of the hard-crash report recovered at startup, or None."""
    return _state["previous_fatal"]


def on_crash(fn):
    """Call fn(report_path, summary) after each crash report is written.
    Runs on the crashing thread; Qt listeners must hand off via a signal."""
    _state["crash_listeners"].append(fn)


# ----------------------------------------------------------------------
# setup
# ----------------------------------------------------------------------

def setup(app_version="?", level=logging.INFO, stderr=True):
    """Install the handlers and hooks. Safe to call once per process; returns
    the RingHandler. Never raises: a read-only disk still gets the in-memory
    log and the stderr copy."""
    _state["app_version"] = app_version
    root = logging.getLogger()
    root.setLevel(level)
    fmt = logging.Formatter(FORMAT)

    rh = RingHandler()
    rh.setFormatter(fmt)
    root.addHandler(rh)
    _state["ring"] = rh

    if stderr:
        # The journal (z13-helper.service) keeps warnings and up.
        sh = logging.StreamHandler()
        sh.setLevel(logging.WARNING)
        sh.setFormatter(fmt)
        root.addHandler(sh)

    disk_ok = True
    try:
        os.makedirs(crash_dir(), exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(log_path(), maxBytes=MAX_LOG_BYTES,
                                                  backupCount=LOG_BACKUPS, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:
        disk_ok = False
        logging.getLogger(__name__).warning("log file unavailable (%s); logging to memory only", e)

    previous = _recover_previous_fatal() if disk_ok else None
    _state["previous_fatal"] = previous
    if disk_ok:
        try:
            # Opened for the life of the process: faulthandler writes to the fd
            # from a signal handler, so it can't open anything itself.
            _state["fatal_file"] = open(fatal_path(), "w", encoding="utf-8")
            faulthandler.enable(file=_state["fatal_file"], all_threads=True)
        except OSError:
            pass

    sys.excepthook = _excepthook
    threading.excepthook = _thread_excepthook

    log = logging.getLogger("z13")
    log.info("Z13-Helper%s starting (pid %d, Python %s%s)",
             "" if app_version == "?" else f" {app_version}", os.getpid(),
             platform.python_version(), ", Flatpak" if os.environ.get("FLATPAK_ID") else "")
    if previous:
        log.error("The previous run crashed hard (fatal signal); report saved: %s", previous)
    return rh


def shutdown():
    """Clean exit: an empty fatal.log means 'no hard crash'; remove it."""
    f = _state.get("fatal_file")
    if f:
        try:
            faulthandler.disable()
            f.close()
            if os.path.getsize(fatal_path()) == 0:
                os.remove(fatal_path())
        except OSError:
            pass
        _state["fatal_file"] = None


def _recover_previous_fatal():
    """fatal.log with content = the last run died on a fatal signal. Move it into
    crashes/ with a header and return the new path."""
    p = fatal_path()
    try:
        if os.path.getsize(p) == 0:
            return None
        with open(p, encoding="utf-8", errors="replace") as f:
            body = f.read()
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(os.path.getmtime(p)))
    except OSError:
        return None
    out = os.path.join(crash_dir(), f"fatal-{stamp}.txt")
    text = (f"Z13-Helper hard crash (fatal signal), recovered on the next start\n"
            f"{_environment()}\n--- faulthandler output ---\n{body}")
    if _write_atomic(out, text):
        try:
            os.remove(p)
        except OSError:
            pass
        _prune_crashes()
        return out
    return None


# ----------------------------------------------------------------------
# hooks + reports
# ----------------------------------------------------------------------

def _excepthook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    report_exception(exc_type, exc, tb, where="main thread")


def _thread_excepthook(args):
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread else "unknown thread"
    report_exception(args.exc_type, args.exc_value, args.exc_traceback, where=f"thread {name}")


def report_exception(exc_type, exc, tb, where="?"):
    """Log an uncaught exception, write a crash report, notify listeners.
    Returns the report path (None if it couldn't be written)."""
    summary = "".join(traceback.format_exception_only(exc_type, exc)).strip()
    logging.getLogger("z13").critical("Uncaught exception in %s: %s", where, summary,
                                      exc_info=(exc_type, exc, tb))
    path = write_crash_report(exc_type, exc, tb, where)
    for fn in list(_state["crash_listeners"]):
        try:
            fn(path, summary)
        except Exception:  # noqa: BLE001
            pass
    return path


def write_crash_report(exc_type, exc, tb, where="?"):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(crash_dir(), f"crash-{stamp}-{os.getpid()}-{threading.get_ident() % 10000}.txt")
    tail = (ring().lines()[-REPORT_TAIL_LINES:] if ring() else [])
    text = (f"Z13-Helper crash report\n"
            f"Time:   {time.strftime('%Y-%m-%d %H:%M:%S %z')}\n"
            f"Where:  {where}\n"
            f"{_environment()}\n"
            f"--- traceback ---\n"
            f"{''.join(traceback.format_exception(exc_type, exc, tb))}\n"
            f"--- last {len(tail)} log lines ---\n" + "\n".join(tail) + "\n")
    if not _write_atomic(path, text):
        return None
    _prune_crashes()
    return path


def _environment():
    osr = {}
    try:
        with open("/run/host/os-release" if os.path.exists("/run/host/os-release") else "/etc/os-release") as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                osr[k] = v.strip('"')
    except OSError:
        pass
    return (f"App:    Z13-Helper {_state['app_version']}"
            f"{' (Flatpak)' if os.environ.get('FLATPAK_ID') else ''}\n"
            f"OS:     {osr.get('PRETTY_NAME', '?')} (build {osr.get('BUILD_ID', '?')})\n"
            f"Kernel: {platform.release()}\n"
            f"Python: {platform.python_version()}")


def _write_atomic(path, text):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _prune_crashes():
    reports = list_crash_reports()
    for p, _m, _s in reports[MAX_CRASH_REPORTS:]:
        try:
            os.remove(p)
        except OSError:
            pass


# ----------------------------------------------------------------------
# reading (the log window, install_check)
# ----------------------------------------------------------------------

def list_crash_reports():
    """[(path, mtime, one-line summary)], newest first."""
    out = []
    try:
        names = os.listdir(crash_dir())
    except OSError:
        return out
    for n in names:
        if not n.endswith(".txt") or not n.startswith(("crash-", "fatal-")):
            continue
        p = os.path.join(crash_dir(), n)
        try:
            out.append((p, os.path.getmtime(p), _summary(p)))
        except OSError:
            continue
    out.sort(key=lambda r: r[1], reverse=True)
    return out


def _summary(path):
    """The exception line of a crash report, or the first faulthandler line."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return "?"
    if os.path.basename(path).startswith("fatal-"):
        for ln in lines:
            if ln.startswith("Fatal Python error"):
                return ln.strip()
        return "hard crash (fatal signal)"
    # The exception line is the last non-blank line of the traceback section.
    last = "?"
    in_tb = False
    for ln in lines:
        if ln == "--- traceback ---":
            in_tb = True
        elif ln.startswith("--- last "):
            break
        elif in_tb and ln.strip():
            last = ln.strip()
    return last


def read_text(path, max_bytes=512 * 1024):
    """A file's text (the tail, if it's large)."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
        text = data.decode("utf-8", errors="replace")
        return text if size <= max_bytes else "…(earlier lines cut)…\n" + text.split("\n", 1)[-1]
    except OSError as e:
        return f"(can't read {path}: {e})"


def clear_crash_reports():
    n = 0
    for p, _m, _s in list_crash_reports():
        try:
            os.remove(p)
            n += 1
        except OSError:
            pass
    return n


# ----------------------------------------------------------------------
# Qt
# ----------------------------------------------------------------------

def install_qt_handler():
    """Route Qt's own qDebug/qWarning/qCritical/qFatal into logging."""
    from PyQt6.QtCore import QtMsgType, qInstallMessageHandler

    log = logging.getLogger("qt")
    levels = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
              QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
              QtMsgType.QtFatalMsg: logging.CRITICAL}

    def handler(mode, ctx, msg):
        where = f" ({ctx.file}:{ctx.line})" if ctx is not None and ctx.file else ""
        log.log(levels.get(mode, logging.WARNING), "%s%s", msg, where)

    qInstallMessageHandler(handler)

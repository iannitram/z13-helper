import io
import json
import re
import zipfile
from datetime import datetime

APP_ID = "z13-helper"
LOG_LINES = 500


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


def log_tail(text: str, n: int = LOG_LINES) -> str:
    """Get the last n lines of text, each ending with a newline.

    Args:
        text: The input text
        n: Number of lines to return (default: LOG_LINES)

    Returns:
        The last n lines, each ending with \n
    """
    if not text:
        return ""

    lines = text.splitlines(keepends=True)
    return "".join(lines[-n:])


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

    Args:
        report: The crash report dictionary
        log_text: The log text

    Returns:
        Bytes of the zip file
    """
    # Create zip in memory
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        # Add report.json
        report_json = json.dumps(report, indent=2, sort_keys=True)
        zip_file.writestr("report.json", report_json)

        # Add app.log with tail
        log_tail_content = log_tail(log_text)
        zip_file.writestr("app.log", log_tail_content)

    # Return the zip bytes
    zip_buffer.seek(0)
    return zip_buffer.read()

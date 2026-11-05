"""Recipe 7: "Has upstream fixed it?" probes (#71).

Pure functions that evaluate sample command outputs (dmesg text, uname -r, /proc/modules,
timed WMI write/read-back) and report whether a workaround can be retired. Read-only and
informative: no command execution, no sysfs writes, no system changes.
"""

import re
from dataclasses import dataclass

from .recipe import Recipe


@dataclass(frozen=True)
class ProbeResult:
    probe_id: str
    fixed: bool
    finding: str
    detail: str


_MES_RE = re.compile(r"MES\s+fw\s+version:\s*0x([0-9a-fA-F]+)")
_BT_RE = re.compile(r"(?:BT_RAM_CODE_MT7925|MT7925)[^\n]*build:\s*(\d{8})")
_KERNEL_RE = re.compile(r"^\s*(\d+)\.(\d+)(?:\.(\d+))?")
_SMU_RE = re.compile(r"^\s*(ryzen_smu(?:_drv)?)\b", re.MULTILINE)

MES_FLOOR = 0x7F
BT_BUILD_FLOOR = 20250305
KERNEL_FLOOR = (7, 3, 0)
WMI_HANG_MS = 5000


def probe_mes_firmware(raw_text: "str | None") -> ProbeResult:
    if raw_text is None or not isinstance(raw_text, str) or not raw_text.strip():
        return ProbeResult("mes_fw", False, "unreadable", "MES firmware version not found in log")
    m = _MES_RE.search(raw_text)
    if not m:
        return ProbeResult("mes_fw", False, "unreadable", "MES firmware version not found in log")
    fw_ver = int(m.group(1), 16)
    if fw_ver >= MES_FLOOR:
        return ProbeResult("mes_fw", True, "fixed",
                           f"MES FW 0x{fw_ver:02x} >= 0x7f: cwsr_enable=0 can be retired")
    return ProbeResult("mes_fw", False, "needed",
                       f"MES FW 0x{fw_ver:02x} < 0x7f: cwsr_enable=0 workaround required")


def probe_mt7925_bt(raw_text: "str | None") -> ProbeResult:
    if raw_text is None or not isinstance(raw_text, str) or not raw_text.strip():
        return ProbeResult("mt7925_bt", False, "unreadable", "MT7925 BT firmware build date not found in log")
    m = _BT_RE.search(raw_text)
    if not m:
        return ProbeResult("mt7925_bt", False, "unreadable", "MT7925 BT firmware build date not found in log")
    build_date = int(m.group(1))
    if build_date > BT_BUILD_FLOOR:
        return ProbeResult("mt7925_bt", True, "fixed",
                           f"MT7925 BT FW build {build_date} > 20250305: BT workarounds can be retired")
    return ProbeResult("mt7925_bt", False, "needed",
                       f"MT7925 BT FW build {build_date} <= 20250305: BT workarounds required")


def probe_kernel_version(raw_text: "str | None") -> ProbeResult:
    if raw_text is None or not isinstance(raw_text, str) or not raw_text.strip():
        return ProbeResult("kernel_version", False, "unreadable", "Kernel release string is empty or invalid")
    m = _KERNEL_RE.match(raw_text)
    if not m:
        return ProbeResult("kernel_version", False, "unreadable",
                           f"Invalid kernel release string: {raw_text.strip()!r}")
    parsed = (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))
    if parsed >= KERNEL_FLOOR:
        return ProbeResult("kernel_version", True, "fixed",
                           f"Kernel {parsed[0]}.{parsed[1]}.{parsed[2]} >= 7.3.0: dcdebugmask=0x20e10 can be retired")
    return ProbeResult("kernel_version", False, "needed",
                       f"Kernel {parsed[0]}.{parsed[1]}.{parsed[2]} < 7.3.0: dcdebugmask workaround required")


def probe_ryzen_smu(raw_text: "str | None") -> ProbeResult:
    if raw_text is None or not isinstance(raw_text, str) or not raw_text.strip():
        return ProbeResult("ryzen_smu", False, "unreadable", "/proc/modules text is unreadable or empty")
    m = _SMU_RE.search(raw_text)
    if m:
        return ProbeResult("ryzen_smu", True, "fixed",
                           f"ryzen_smu loaded ({m.group(1)}): APU undervolt support enabled")
    return ProbeResult("ryzen_smu", False, "needed",
                       "ryzen_smu absent: APU undervolt remains disabled on stock SteamOS")


def probe_wmi_write_hang(elapsed_ms: "int | float", write_rc: int, read_val: "str | None",
                         expected_w: int = 25) -> ProbeResult:
    if not isinstance(elapsed_ms, (int, float)) or isinstance(elapsed_ms, bool) or elapsed_ms < 0:
        return ProbeResult("wmi_hang", False, "unreadable", "Invalid elapsed_ms timing value")
    if elapsed_ms >= WMI_HANG_MS:
        return ProbeResult("wmi_hang", False, "needed",
                           f"asus-nb-wmi write hang detected: took {elapsed_ms:.0f} ms (>= 5000 ms threshold)")
    if write_rc != 0:
        return ProbeResult("wmi_hang", False, "needed",
                           f"asus-nb-wmi write failed with rc={write_rc} (EIO or error)")
    if read_val is None or not read_val.strip().isdigit():
        return ProbeResult("wmi_hang", False, "unreadable",
                           f"asus-nb-wmi read-back value invalid: {read_val!r}")
    read_w = int(read_val.strip())
    if read_w != expected_w:
        return ProbeResult("wmi_hang", False, "needed",
                           f"asus-nb-wmi read-back mismatch: expected {expected_w} W, read {read_w} W")
    return ProbeResult("wmi_hang", True, "fixed",
                       f"asus-nb-wmi write responsive: {elapsed_ms:.0f} ms (< 5000 ms) with verified {read_w} W read-back")


def evaluate_upstream_probes(mes_text, bt_text, uname_r, modules_text, wmi_timing) -> "list[ProbeResult]":
    """All five probes in order, on the sample outputs."""
    elapsed_ms, write_rc, read_val, expected_w = wmi_timing
    return [
        probe_mes_firmware(mes_text),
        probe_mt7925_bt(bt_text),
        probe_kernel_version(uname_r),
        probe_ryzen_smu(modules_text),
        probe_wmi_write_hang(elapsed_ms, write_rc, read_val, expected_w),
    ]


def upstream_probes_recipe(io=None) -> Recipe:
    return Recipe(
        id="upstream-probes",
        title="Has upstream fixed it? (info only)",
        fixes="knowing when a workaround can be retired",
        check=lambda: "info",
        apply=lambda: None,
    )

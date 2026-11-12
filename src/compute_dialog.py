"""File -> Memory and compute: unified-memory breakdown and compute runtime status (#26).

Showing the report allocates nothing. The allocation smoke test runs only when the
button is pressed, within the budget compute_health.SMOKE_BUDGET_TEXT states.
"""

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout

import compute_health

GIB = 1024 ** 3


def _gib(v):
    return "unavailable" if v is None else f"{v / GIB:.1f} GiB"


def _yn(v):
    return "unavailable" if v is None else ("yes" if v else "no")


def format_report(r):
    """The report dict (compute_health.health_report) as plain text."""
    m, rt = r["memory"], r["runtime"]
    p = m["pressure_avg10"]
    lines = [
        "Memory (one physical pool; the lines below are not additive)",
        f"  Physical RAM (OS + firmware reservation): {_gib(m['physical_total'])}",
        f"  Seen by Linux (MemTotal): {_gib(m['ram_total'])}, available {_gib(m['ram_available'])}, "
        f"in use {_gib(m['ram_used'])}",
        f"  Firmware-reserved VRAM: {_gib(m['vram_reserved'])}, in use {_gib(m['vram_used'])}",
        f"  GTT (shared, inside Linux RAM): up to {_gib(m['gtt_limit'])}, in use {_gib(m['gtt_used'])}",
        "  Memory pressure (10 s): " + ("unavailable" if p is None else f"{p:.1f} %"),
    ]
    lines += [f"  {n}" for n in m["notes"]]
    lines += ["", "Compute",
              "  GPU: " + (", ".join(f"{a['gfx']}" + (f" ({a['name']})" if a["name"] else "")
                                     for a in r["gpu_agents"]) or "no GPU agent from the kernel (KFD)"),
              "  gfx1151 (Strix Halo): " + _yn(r["gfx1151"]) + f", kernel {r['kernel']}"]
    if not rt["ok"]:
        lines.append("  Runtime discovery failed (host command did not answer).")
    else:
        lines += [
            f"  /dev/kfd access: {_yn(rt['kfd_access'])}, render node access: {_yn(rt['render_access'])}",
            "  ROCm/HIP: " + (", ".join(rt["hip_libs"]) + (f" (ROCm {rt['rocm_version']})"
                                                           if rt["rocm_version"] else "")
                              if rt["hip_libs"] else "not found"),
            f"  rocminfo: {rt['rocminfo'] or 'not found'}",
            "  Vulkan: " + (f"{len(rt['vulkan_icds'])} AMD driver file(s)" if rt["vulkan_icds"]
                             else "no AMD driver file found") + f", vulkaninfo: {rt['vulkaninfo'] or 'not found'}",
            "  Finding a library is not proof every component works; run the test below.",
        ]
    vk = r.get("vulkan")
    if vk:
        if vk.get("ok"):
            lines += [f"  Vulkan {vk['instance_version'] or '?'}: {d['name']} [{d['driver']}, {d['api_version']}]"
                      for d in vk["devices"]]
        else:
            lines.append("  Vulkan devices: vulkaninfo not found or listed no device.")
    if r.get("compat"):
        lines += ["", "Compatibility (a hint, not a verdict)"]
        lines += [f"  [{c['status']}] {c['label']}: {c['detail']}" for c in r["compat"]]
        lines += [f"  {n}" for n in r.get("compat_notes", [])]
    return "\n".join(lines)


class _Worker(QThread):
    done = pyqtSignal(object)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.done.emit(self._fn())
        except Exception as e:   # never let a probe kill the app
            self.done.emit({"error": str(e)})


class ComputeDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Memory and compute — Z13-Helper")
        self.resize(720, 460)
        v = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        v.addWidget(self.text)
        self.budget = QLabel(compute_health.SMOKE_BUDGET_TEXT)
        self.budget.setObjectName("dim")
        self.budget.setWordWrap(True)
        v.addWidget(self.budget)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        self.runtime = QComboBox()
        for key, label in (("hip", "HIP (ROCm)"), ("vulkan", "Vulkan")):
            self.runtime.addItem(label, key)
        self.btn_smoke = QPushButton("Run allocation test")
        self.btn_smoke.clicked.connect(self._smoke)
        row.addWidget(self.btn_refresh)
        row.addWidget(self.runtime)
        row.addWidget(self.btn_smoke)
        row.addStretch()
        v.addLayout(row)
        self._report_text = ""
        self._worker = None

    def refresh(self):
        self._run(compute_health.health_report, self._show_report, "Reading…")

    def _smoke(self):
        runtime = self.runtime.currentData()
        self._run(lambda: compute_health.smoke_test(runtime=runtime), self._show_smoke,
                  f"Running the {self.runtime.currentText()} allocation test…")

    def _run(self, fn, slot, busy):
        if self._worker is not None and self._worker.isRunning():
            return
        self.btn_refresh.setEnabled(False)
        self.btn_smoke.setEnabled(False)
        self.runtime.setEnabled(False)
        self.text.setPlainText(busy)
        self._worker = _Worker(fn, self)
        self._worker.done.connect(slot)
        self._worker.start()

    def _finish(self):
        self.btn_refresh.setEnabled(True)
        self.btn_smoke.setEnabled(True)
        self.runtime.setEnabled(True)

    def _show_report(self, r):
        self._finish()
        self._report_text = format_report(r) if "memory" in r else f"Could not read: {r.get('error')}"
        self.text.setPlainText(self._report_text)

    def _show_smoke(self, r):
        self._finish()
        verdict = "PASSED" if r.get("ok") else "FAILED"
        self.text.setPlainText(f"{self._report_text}\n\n{r.get('runtime', 'hip').upper()} allocation test {verdict}: "
                               f"{r.get('detail') or r.get('error')}")

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

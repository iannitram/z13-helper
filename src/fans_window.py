"""Fans + Power window — G-Helper's `Fans` form, adapted to the Z13 on Linux.

Top row: the profile combo with New / Rename / Remove; everything below
belongs to the profile picked there. Left column, one card each: Platform
Profile, CPU Boost and Power Limits ("Apply Power Limits" then SPL/sPPT/fPPT);
only when ryzen_smu exists, a CPU / Advanced switch adds the Advanced page
(Curve Optimizer). Right: the "BIOS Fan Curves" card with "Apply Custom Fan
Curve", Max Fans, the draggable 8-point chart, Factory Defaults and Clamp to
Grid; under it the "Last Hour" card (temperatures and fan speeds,
history_chart.py).

Everything edits the profile selected in the combo; edits to the active
profile are pushed to the hardware immediately (ProfileControl).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSlider,
    QPushButton, QInputDialog, QMessageBox, QStackedWidget,
)

import epp
import power_range
import profiles
from theme import ModeTile, Panel, CheckBox, ComboBox, heading, tip, STANDARD, CUSTOM, ACCENT_FOR_BASE, SIZE, SPACE
from fan_curve_widget import FanCurveWidget, DEFAULT_CURVE
from history_chart import HistoryChartWidget
from profile_control import ProfileControl

PL_MIN, PL_MAX = 5, 93           # IsZ1325 cap in G-Helper: the outer envelope; the live per-rail range
                                 # is picked by power_range.detect_ranges (sysfs metadata only). z13ctl --force above 75
PL1_SAFE_MAX = 75
# Slider positions when the profile has no TDP override. Deliberately NOT read live:
# `z13ctl tdp --get` can go through the asus-armoury ppt_* files, which may
# hard-lock the EC (ROADMAP principle 5); the Decky panel uses the same values.
DEFAULT_TDP = (70, 86, 86)
UV_MIN, UV_MAX = -40, 0
BASE_LABELS = [("quiet", "Quiet (Silent)"), ("balanced", "Balanced"), ("performance", "Performance (Turbo)")]


class _SliderRow(QWidget):
    """Caption left · bold value right · slider underneath."""

    def __init__(self, caption, lo, hi, suffix, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        h = QHBoxLayout()
        self.caption = QLabel(caption)
        self.value = QLabel("")
        self.value.setObjectName("value")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.caption)
        h.addStretch(1)
        h.addWidget(self.value)
        v.addLayout(h)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(lo, hi)
        self.slider.setTickInterval(5)
        v.addWidget(self.slider)
        self.suffix = suffix
        self.slider.valueChanged.connect(lambda val: self.value.setText(f"{val}{self.suffix}"))
        self.value.setText(f"{self.slider.value()}{suffix}")

    def set_range(self, lo, hi):
        self.slider.blockSignals(True)
        self.slider.setRange(lo, hi)
        self.slider.blockSignals(False)

    def set(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(val))
        self.slider.blockSignals(False)
        self.value.setText(f"{int(val)}{self.suffix}")


class FansWindow(QWidget):
    def __init__(self, control: ProfileControl, controller, sensors, parent=None, history=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("root")
        self.setWindowTitle("Fans and Power — ROG Flow Z13")
        self.control = control
        self.controller = controller
        self.sensors = sensors
        self._history = history
        self._uv_available = controller.is_undervolt_available()
        self._selected = control.active_name()
        self._loading = False
        # Per-rail kernel range from sysfs metadata (no live PPT reads, no z13ctl).
        self._ranges = power_range.detect_ranges()
        self._support = None          # platform_support.detect() result (#22), set by the dashboard
        self.resize(SIZE["fans_window_w"], SIZE["fans_window_h"])
        self._build()
        self.control.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACE["md"], SPACE["md"], SPACE["md"], SPACE["md"])
        outer.setSpacing(SPACE["md"])

        # ---- top: which profile both columns below belong to ----
        bar = QHBoxLayout()
        bar.setSpacing(SPACE["sm"])
        bar.addWidget(heading("Profile"))
        self.combo = ComboBox()
        self.combo.setMinimumWidth(200)
        self.combo.currentTextChanged.connect(self._on_combo)
        bar.addWidget(self.combo)
        self.btn_add = QPushButton("New")
        self.btn_add.setToolTip("Add a custom profile (copies the selected one)")
        self.btn_add.clicked.connect(self._add)
        self.btn_rename = QPushButton("Rename")
        self.btn_rename.clicked.connect(self._rename)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove)
        for b in (self.btn_add, self.btn_rename, self.btn_remove):
            bar.addWidget(b)
        bar.addStretch(1)
        outer.addLayout(bar)

        root = QHBoxLayout()
        root.setSpacing(SPACE["md"])
        outer.addLayout(root, 1)

        # ---- left column: one card per setting group ----
        left = QWidget()
        left.setFixedWidth(SIZE["fans_side_w"])
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(SPACE["md"])

        # CPU / Advanced switch: only shown when there is an Advanced page to switch to
        # (G-Helper: Advanced only with a Ryzen SMU driver).
        self._nav_row = QWidget()
        nav = QHBoxLayout(self._nav_row)
        nav.setContentsMargins(0, 0, 0, 0)
        nav.setSpacing(SPACE["sm"])
        self.btn_cpu = ModeTile("CPU", STANDARD, height=SIZE["tile_small_h"])
        self.btn_adv = ModeTile("Advanced", STANDARD, height=SIZE["tile_small_h"])
        self.btn_cpu.clicked.connect(lambda: self._nav(0))
        self.btn_adv.clicked.connect(lambda: self._nav(1))
        nav.addWidget(self.btn_cpu)
        nav.addWidget(self.btn_adv)
        lv.addWidget(self._nav_row)
        self._nav_row.setVisible(self._uv_available)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_cpu_page())
        self.pages.addWidget(self._build_adv_page())
        lv.addWidget(self.pages, 1)
        root.addWidget(left)

        # ---- right region: the fan curve card, then the last hour ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(SPACE["md"])

        curves = Panel("BIOS Fan Curves")

        # What applies to this profile, above the chart it switches on.
        toggles = QHBoxLayout()
        toggles.setSpacing(SPACE["lg"])
        self.apply_curve = CheckBox("Apply Custom Fan Curve")
        self.apply_curve.toggled.connect(self._on_apply_curve)
        toggles.addWidget(self.apply_curve)
        self.max_fans_cb = CheckBox("Max Fans")
        self.max_fans_cb.setToolTip("Switching to this profile turns Max Fans ON (100%);\n"
                                    "switching away turns it OFF again.")
        self.max_fans_cb.toggled.connect(self._on_max_fans_cb)
        toggles.addWidget(self.max_fans_cb)
        toggles.addStretch(1)
        curves.body.addLayout(toggles)

        self.chart = FanCurveWidget(title="RPM / °C · both fans share one curve")
        self.chart.curveChanged.connect(self._on_curve_dragged)
        curves.body.addWidget(self.chart, 1)

        bottom = QHBoxLayout()
        self.btn_defaults = QPushButton("Factory Defaults")
        self.btn_defaults.clicked.connect(self._factory_defaults)
        bottom.addWidget(self.btn_defaults)
        self.result = QLabel("")
        self.result.setObjectName("dim")
        bottom.addWidget(self.result, 1)
        self.clamp = CheckBox("Clamp to Grid")       # an editing aid, not a profile setting
        self.clamp.toggled.connect(lambda on: self.chart.setClampToGrid(on))
        bottom.addWidget(self.clamp)
        curves.body.addLayout(bottom)
        rv.addWidget(curves, 1)

        if self._history is not None:
            # The last hour, kept in memory only (telemetry_history.py).
            hour = Panel("Last Hour")
            self.history_chart = HistoryChartWidget(self._history)
            hour.body.addWidget(self.history_chart)
            rv.addWidget(hour)
        root.addWidget(right, 1)
        self._nav(0)

    def _build_cpu_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE["md"])

        platform = Panel("Platform Profile")
        self.base_combo = ComboBox()
        for key, label in BASE_LABELS:
            self.base_combo.addItem(label, key)
        self.base_combo.currentIndexChanged.connect(self._on_base)
        platform.body.addWidget(self.base_combo)
        platform.body.addWidget(tip("The firmware mode underneath (asus-wmi platform_profile). "
                                    "G-Helper's \"Windows Power Mode\" slot."))
        v.addWidget(platform)

        boost = Panel("CPU Boost")
        self.epp_combo = ComboBox()
        epp.fill_combo(self.epp_combo, self.controller.get_epp_choices())
        self.epp_combo.currentIndexChanged.connect(self._on_epp)
        boost.body.addWidget(self.epp_combo)
        self.epp_now = boost.right          # "Now: ..." sits in the card's head row
        boost.body.addWidget(tip("amd-pstate energy_performance_preference on every CPU. Unchanged "
                                 "leaves it as the platform profile sets it."))
        v.addWidget(boost)

        power = Panel("Power Limits")
        # The switch comes first: it is what makes the three sliders count.
        self.apply_power = CheckBox("Apply Power Limits")
        self.apply_power.toggled.connect(self._on_apply_power)
        power.body.addWidget(self.apply_power)
        self.spl = _SliderRow("SPL (CPU sustained)", PL_MIN, PL_MAX, " W")
        self.sppt = _SliderRow("sPPT (CPU long boost)", PL_MIN, PL_MAX, " W")
        self.fppt = _SliderRow("fPPT (CPU short boost)", PL_MIN, PL_MAX, " W")
        for row, k in ((self.spl, "pl1"), (self.sppt, "pl2"), (self.fppt, "pl3")):
            row.set_range(*self._ranges[k])
            power.body.addWidget(row)
            row.slider.valueChanged.connect(self._on_tdp_moved)
            row.slider.sliderReleased.connect(self._on_tdp_released)
        self.power_note = QLabel("")
        self.power_note.setObjectName("dim")
        self.power_note.setWordWrap(True)
        self.power_note.setVisible(False)
        power.body.addWidget(self.power_note)
        power.body.addWidget(tip("sPPT ≥ SPL, fPPT ≥ sPPT. Above 75 W sustained, z13ctl runs the fans "
                                 "at full speed (--force)."))
        v.addWidget(power)
        v.addStretch(1)
        return page

    def _build_adv_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(SPACE["md"])
        under = Panel("Undervolting")
        self.uv = _SliderRow("CPU (Curve Optimizer)", UV_MIN, UV_MAX, "")
        self.uv.slider.sliderReleased.connect(self._on_uv_released)
        under.body.addWidget(self.uv)
        self.auto_uv = CheckBox("Auto Apply")
        self.auto_uv.toggled.connect(self._on_auto_uv)
        under.body.addWidget(self.auto_uv)
        if not self._uv_available:
            under.body.addWidget(tip("Unavailable: the ryzen_smu kernel module is not loaded (stock SteamOS)."))
            self.uv.setEnabled(False)
            self.auto_uv.setEnabled(False)
        v.addWidget(under)
        v.addStretch(1)
        return page

    def _nav(self, idx):
        self.pages.setCurrentIndex(idx)
        self.btn_cpu.setChecked(idx == 0)
        self.btn_adv.setChecked(idx == 1)

    def apply_capabilities(self, support):
        """Enable only the controls this machine can run (#22). Each control needs one
        capability; a disabled control's tooltip says why. Called again on every refresh,
        because refresh() enables the power rows by the profile's own values."""
        self._support = support
        supported = support["supported"]
        caps = support["capabilities"]

        def gate(widget, *names, tooltip=True):
            missing = [n for n in names if not caps[n]["available"]]
            ok = supported and not missing
            widget.setEnabled(ok)
            if tooltip:
                widget.setToolTip("" if ok else (caps[missing[0]]["reason"] if missing
                                                 else support["reason"]))
            return ok

        gate(self.base_combo, "platform_profile")
        gate(self.epp_combo, "platform_profile")
        tdp_ok = gate(self.apply_power, "tdp")
        for row in (self.spl, self.sppt, self.fppt):
            row.setEnabled(tdp_ok and self.apply_power.isChecked())
        gate(self.apply_curve, "fan_curve")
        gate(self.chart, "fan_curve", tooltip=False)
        gate(self.max_fans_cb, "fan_curve", tooltip=False)
        gate(self.uv, "undervolt", tooltip=False)
        gate(self.auto_uv, "undervolt")

    # ------------------------------------------------------------------
    # Refresh from store
    # ------------------------------------------------------------------

    def select(self, name):
        self._selected = name
        self.refresh()

    def refresh(self):
        self._loading = True
        try:
            names = self.control.names()
            if self._selected not in names:
                self._selected = self.control.active_name()
            self.combo.blockSignals(True)
            self.combo.clear()
            self.combo.addItems(names)
            self.combo.setCurrentText(self._selected)
            self.combo.blockSignals(False)
            p = self.control.get(self._selected)
            if not p:
                return
            custom = not p.get("builtin")
            self.btn_rename.setVisible(custom)
            self.btn_remove.setVisible(custom)

            idx = self.base_combo.findData(p["base"])
            self.base_combo.blockSignals(True)
            self.base_combo.setCurrentIndex(max(0, idx))
            self.base_combo.blockSignals(False)

            epp.show_profile_epp(self.epp_combo, p)
            self.epp_now.setText(epp.now_text(self.controller.get_epp()))

            tdp = p.get("tdp")
            self.apply_power.setChecked(bool(tdp))
            note = ""
            if tdp:
                # Show each rail clamped into the kernel's range; the stored values stay as
                # they are until the user moves a slider (then the shown values are saved).
                st = power_range.tdp_status(tdp, self._ranges)
                a = st["accepted"]
                self.spl.set(a["pl1"]); self.sppt.set(a["pl2"]); self.fppt.set(a["pl3"])
                note = st["note"]
            else:
                # Keep enable-time slider positions, but make clear these are
                # placeholders rather than power limits applied to this profile.
                self.spl.set(DEFAULT_TDP[0]); self.sppt.set(DEFAULT_TDP[1]); self.fppt.set(DEFAULT_TDP[2])
                for row in (self.spl, self.sppt, self.fppt):
                    row.value.setText("Not set")
            self.power_note.setText(note)
            self.power_note.setVisible(bool(note))
            for row in (self.spl, self.sppt, self.fppt):
                row.setEnabled(bool(tdp))
            if self._support is not None:
                self.apply_capabilities(self._support)   # the profile's values never override the hardware gate

            self.max_fans_cb.setChecked(bool(p.get("max_fans")))
            curve = p.get("fan_curve")
            self.apply_curve.setChecked(bool(curve))
            self.chart.setPoints(curve or DEFAULT_CURVE)
            self.chart.setApplied(bool(curve))
            self.chart.setAccent(ACCENT_FOR_BASE.get(p["base"], STANDARD) if p.get("builtin") else CUSTOM)

            uv = p.get("undervolt")
            self.auto_uv.setChecked(uv is not None)
            self.uv.set(uv if uv is not None else 0)
            active = self._selected == self.control.active_name()
            self.setWindowTitle(f"Fans and Power — {self._selected}" + ("" if active else " (not active)"))
        finally:
            self._loading = False

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _profile(self):
        return self.control.get(self._selected)

    def _on_combo(self, name):
        if self._loading or not name:
            return
        self._selected = name
        self.control.switch(name)     # G-Helper: picking a mode in the Fans window applies it
        self.refresh()

    def _on_base(self, _idx):
        if self._loading:
            return
        p = self._profile()
        p["base"] = self.base_combo.currentData()
        self.control.save(p, "base")

    def _on_epp(self, _idx):
        if self._loading:
            return
        self.control.save(epp.profile_with_epp(self._profile(), self.epp_combo), "epp")

    def _on_tdp_moved(self, _val):
        if self._loading:
            return
        # Constraints: sPPT >= SPL, fPPT >= sPPT (G-Helper enforces slow >= total)
        s, m, f = self.spl.slider.value(), self.sppt.slider.value(), self.fppt.slider.value()
        if m < s:
            self.sppt.set(s)
        if f < max(s, m):
            self.fppt.set(max(s, m))

    def _on_tdp_released(self):
        if self._loading or not self.apply_power.isChecked():
            return
        self._save_tdp()

    def _save_tdp(self):
        p = self._profile()
        pl1, pl2, pl3 = self.spl.slider.value(), self.sppt.slider.value(), self.fppt.slider.value()
        p["tdp"] = {"pl1": pl1, "pl2": pl2, "pl3": pl3, "force": pl1 > PL1_SAFE_MAX}
        try:
            self.control.save(p, "tdp")
        except ValueError as e:
            self.result.setText(str(e))

    def _on_apply_power(self, on):
        if self._loading:
            return
        for row in (self.spl, self.sppt, self.fppt):
            row.setEnabled(on)
        if on:
            for row in (self.spl, self.sppt, self.fppt):
                row.value.setText(f"{row.slider.value()}{row.suffix}")
            self._save_tdp()
        else:
            for row in (self.spl, self.sppt, self.fppt):
                row.value.setText("Not set")
            p = self._profile()
            p["tdp"] = None
            self.control.save(p, "tdp")

    def _on_curve_dragged(self, points):
        if self._loading:
            return
        if not self.apply_curve.isChecked():
            self.apply_curve.setChecked(True)   # dragging implies you want it applied
            return                              # (toggle handler saves)
        self._save_curve(points)

    def _save_curve(self, points):
        p = self._profile()
        try:
            profiles.validate_curve(points)
        except ValueError as e:
            self.result.setText(str(e))
            return
        p["fan_curve"] = points
        self.control.save(p, "fan_curve")
        self.chart.setApplied(True)
        self.result.setText("")

    def _on_apply_curve(self, on):
        if self._loading:
            return
        if on:
            self._save_curve(self.chart.points())
        else:
            p = self._profile()
            p["fan_curve"] = None
            self.control.save(p, "fan_curve")
            self.chart.setApplied(False)

    def _on_max_fans_cb(self, on):
        if self._loading:
            return
        p = self._profile()
        p["max_fans"] = bool(on)
        if on:
            p["goal"] = {"kind": "max"}      # the checkbox is the Max goal
        elif (p.get("goal") or {}).get("kind") == "max":
            p.pop("goal")
        self.control.save(p)    # takes effect on the next switch to/from this profile

    def _on_uv_released(self):
        if self._loading or not self.auto_uv.isChecked():
            return
        p = self._profile()
        p["undervolt"] = self.uv.slider.value()
        self.control.save(p, "undervolt")

    def _on_auto_uv(self, on):
        if self._loading:
            return
        p = self._profile()
        p["undervolt"] = self.uv.slider.value() if on else None
        self.control.save(p, "undervolt")

    def _add(self):
        new, ok = QInputDialog.getText(
            self, "New profile", f"Name (copies {self._selected!r}):")
        if not ok or not new.strip():
            return
        name = self.control.add_custom(self._selected, new)
        if name is None:
            return
        self._selected = name
        self.control.switch(name)
        self.refresh()

    def _rename(self):
        new, ok = QInputDialog.getText(self, "Rename profile", "Name:", text=self._selected)
        if ok and self.control.rename(self._selected, new):
            self._selected = new.strip()
            self.refresh()

    def _remove(self):
        if QMessageBox.question(self, "Remove profile", f"Remove {self._selected!r}?") != QMessageBox.StandardButton.Yes:
            return
        self.control.remove(self._selected)
        self._selected = self.control.active_name()
        self.refresh()

    def _factory_defaults(self):
        self.control.factory_defaults(self._selected)
        self.result.setText("Factory defaults restored")
        self.refresh()

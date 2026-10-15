"""Fans + Power window — G-Helper's `Fans` form, adapted to the Z13 on Linux.

Left column: CPU page (platform profile · power limits SPL/sPPT/fPPT ·
"Apply Power Limits") and, only when ryzen_smu exists, an Advanced page
(Curve Optimizer). Right: "BIOS Fan Curves" with the profile combo and
＋ / Rename / Remove, Clamp to Grid, the draggable 8-point chart, Factory
Defaults and "Apply Custom Fan Curve".

Everything edits the profile selected in the combo; edits to the active
profile are pushed to the hardware immediately (ProfileControl).
"""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QLabel, QComboBox, QSlider, QCheckBox,
    QPushButton, QInputDialog, QMessageBox, QFrame, QStackedWidget, QSizePolicy,
)

import profiles
from theme import ModeTile, Panel, tip, STANDARD, CUSTOM, ACCENT_FOR_BASE, TEXT_DIM
from fan_curve_widget import FanCurveWidget, DEFAULT_CURVE
from profile_control import ProfileControl

PL_MIN, PL_MAX = 5, 93           # IsZ1325 cap in G-Helper; z13ctl --force above 75
PL1_SAFE_MAX = 75
# Shown when the profile has no TDP override. Deliberately NOT read live:
# `z13ctl tdp --get` can go through the asus-armoury ppt_* files, which may
# hard-lock the EC (ROADMAP principle 5); the Decky panel uses the same values.
DEFAULT_TDP = (70, 86, 86)
UV_MIN, UV_MAX = -40, 0
BASE_LABELS = [("quiet", "Quiet (Silent)"), ("balanced", "Balanced"), ("performance", "Performance (Turbo)")]


class _SliderRow(QWidget):
    """Caption left · bold value right · slider underneath (G-Helper panelTotal etc.)."""

    def __init__(self, caption, lo, hi, suffix, parent=None):
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 2, 0, 2)
        v.setSpacing(2)
        h = QHBoxLayout()
        self.caption = QLabel(caption)
        self.value = QLabel("")
        self.value.setObjectName("title")
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

    def set(self, val):
        self.slider.blockSignals(True)
        self.slider.setValue(int(val))
        self.slider.blockSignals(False)
        self.value.setText(f"{int(val)}{self.suffix}")


class FansWindow(QWidget):
    def __init__(self, control: ProfileControl, controller, sensors, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setObjectName("root")
        self.setWindowTitle("Fans and Power — ROG Flow Z13")
        self.control = control
        self.controller = controller
        self.sensors = sensors
        self._uv_available = controller.is_undervolt_available()
        self._selected = control.active_name()
        self._loading = False
        self.resize(675, 560)
        self._build()
        self.control.changed.connect(self.refresh)
        self.refresh()

    # ------------------------------------------------------------------
    # Build
    # ------------------------------------------------------------------

    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)

        # ---- left column (265 px) ----
        left = QWidget()
        left.setFixedWidth(265)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(8)

        nav = QHBoxLayout()
        self.btn_cpu = ModeTile("CPU", STANDARD, secondary=True, height=29)
        self.btn_cpu.radius = 2
        self.btn_adv = ModeTile("Advanced", STANDARD, secondary=True, height=29)
        self.btn_adv.radius = 2
        self.btn_cpu.clicked.connect(lambda: self._nav(0))
        self.btn_adv.clicked.connect(lambda: self._nav(1))
        nav.addWidget(self.btn_cpu)
        nav.addWidget(self.btn_adv)
        lv.addLayout(nav)
        self.btn_adv.setVisible(self._uv_available)   # G-Helper: Advanced only with a Ryzen SMU driver

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_cpu_page())
        self.pages.addWidget(self._build_adv_page())
        lv.addWidget(self.pages, 1)
        root.addWidget(left)

        # ---- right region ----
        right = QWidget()
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)

        head = QHBoxLayout()
        t = QLabel("BIOS Fan Curves")
        t.setObjectName("title")
        head.addWidget(t)
        head.addStretch(1)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(150)
        self.combo.currentTextChanged.connect(self._on_combo)
        head.addWidget(self.combo)
        self.btn_add = QPushButton("＋")
        self.btn_add.setFixedWidth(30)
        self.btn_add.setToolTip("Add a custom profile (copies the selected one)")
        self.btn_add.clicked.connect(self._add)
        self.btn_rename = QPushButton("Rename")
        self.btn_rename.clicked.connect(self._rename)
        self.btn_remove = QPushButton("Remove")
        self.btn_remove.clicked.connect(self._remove)
        for b in (self.btn_add, self.btn_rename, self.btn_remove):
            head.addWidget(b)
        rv.addLayout(head)

        self.clamp = QCheckBox("Clamp to Grid")
        self.clamp.toggled.connect(lambda on: self.chart.setClampToGrid(on))
        rv.addWidget(self.clamp)

        self.chart = FanCurveWidget(title="CPU Fan Profile, RPM/°C  (both fans share one curve on the Z13)")
        self.chart.curveChanged.connect(self._on_curve_dragged)
        rv.addWidget(self.chart, 1)

        bottom = QHBoxLayout()
        self.btn_defaults = QPushButton("Factory Defaults")
        self.btn_defaults.clicked.connect(self._factory_defaults)
        bottom.addWidget(self.btn_defaults)
        self.result = QLabel("")
        self.result.setObjectName("dim")
        bottom.addWidget(self.result, 1)
        self.max_fans_cb = QCheckBox("Max Fans")
        self.max_fans_cb.setToolTip("Switching to this profile turns Max Fans ON (100%);\n"
                                    "switching away turns it OFF again.")
        self.max_fans_cb.toggled.connect(self._on_max_fans_cb)
        bottom.addWidget(self.max_fans_cb)
        self.apply_curve = QCheckBox("Apply Custom Fan Curve")
        self.apply_curve.toggled.connect(self._on_apply_curve)
        bottom.addWidget(self.apply_curve)
        rv.addLayout(bottom)
        root.addWidget(right, 1)
        self._nav(0)

    def _build_cpu_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(6)

        t = QLabel("Platform Profile")
        t.setObjectName("title")
        v.addWidget(t)
        self.base_combo = QComboBox()
        for key, label in BASE_LABELS:
            self.base_combo.addItem(label, key)
        self.base_combo.currentIndexChanged.connect(self._on_base)
        v.addWidget(self.base_combo)
        v.addWidget(tip("The firmware mode underneath (asus-wmi platform_profile). "
                        "G-Helper's \"Windows Power Mode\" slot."))

        t2 = QLabel("Power Limits")
        t2.setObjectName("title")
        v.addWidget(t2)
        self.spl = _SliderRow("SPL (CPU sustained)", PL_MIN, PL_MAX, "W")
        self.sppt = _SliderRow("sPPT (CPU long boost)", PL_MIN, PL_MAX, "W")
        self.fppt = _SliderRow("fPPT (CPU short boost)", PL_MIN, PL_MAX, "W")
        for row in (self.spl, self.sppt, self.fppt):
            v.addWidget(row)
            row.slider.valueChanged.connect(self._on_tdp_moved)
            row.slider.sliderReleased.connect(self._on_tdp_released)
        v.addWidget(tip("sPPT ≥ SPL, fPPT ≥ sPPT. Above 75 W sustained, z13ctl runs the fans "
                        "at full speed (--force)."))
        self.apply_power = QCheckBox("Apply Power Limits")
        self.apply_power.toggled.connect(self._on_apply_power)
        v.addWidget(self.apply_power)
        v.addStretch(1)
        return page

    def _build_adv_page(self):
        page = QWidget()
        v = QVBoxLayout(page)
        v.setContentsMargins(0, 0, 0, 0)
        t = QLabel("Undervolting")
        t.setObjectName("title")
        v.addWidget(t)
        self.uv = _SliderRow("CPU (Curve Optimizer)", UV_MIN, UV_MAX, "")
        self.uv.slider.sliderReleased.connect(self._on_uv_released)
        v.addWidget(self.uv)
        self.auto_uv = QCheckBox("Auto Apply")
        self.auto_uv.toggled.connect(self._on_auto_uv)
        v.addWidget(self.auto_uv)
        if not self._uv_available:
            v.addWidget(tip("Unavailable: the ryzen_smu kernel module is not loaded (stock SteamOS)."))
            self.uv.setEnabled(False)
            self.auto_uv.setEnabled(False)
        v.addStretch(1)
        return page

    def _nav(self, idx):
        self.pages.setCurrentIndex(idx)
        self.btn_cpu.setChecked(idx == 0)
        self.btn_adv.setChecked(idx == 1)

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

            tdp = p.get("tdp")
            self.apply_power.setChecked(bool(tdp))
            if tdp:
                self.spl.set(tdp["pl1"]); self.sppt.set(tdp["pl2"]); self.fppt.set(tdp["pl3"])
            else:
                self.spl.set(DEFAULT_TDP[0]); self.sppt.set(DEFAULT_TDP[1]); self.fppt.set(DEFAULT_TDP[2])
            for row in (self.spl, self.sppt, self.fppt):
                row.setEnabled(bool(tdp))

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
            self._save_tdp()
        else:
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
        self.control.save(p)     # takes effect on the next switch to/from this profile

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
        name = self.control.add_custom(self._selected)
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

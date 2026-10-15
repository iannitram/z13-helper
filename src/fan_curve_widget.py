"""Draggable 8-point fan curve chart, modelled on G-Helper's Fans window.

Semantics copied from seerge/g-helper `app/Fans.cs`:
- X axis 20…110 °C, Y axis 0…100 % (labels shown as RPM by default; click the
  Y axis to toggle RPM/%). 8 points, drawn as a line with round markers.
- Dragging clamps to the axes, applies the BIOS safety floor
  `y >= (x - 70) * 1.2`, optionally clamps point i to its 10 °C grid column
  (30+10i … 39+10i), and pushes neighbouring points so the curve stays
  monotonic in both axes. Shift-drag moves every point's Y together.
  Arrow keys nudge the focused point (Shift = ×5).
- A floating "65°C, 3600 RPM" label follows the dragged point.
- The series is drawn grey when the curve isn't applied.

Pure QPainter; no QtCharts dependency.
"""

from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PyQt6.QtWidgets import QWidget, QSizePolicy

TEMP_MIN, TEMP_MAX = 20, 110
PCT_MIN, PCT_MAX = 0, 100
POINTS = 8
MARKER_R = 5          # G-Helper MarkerSize 10 → radius 5 px
HIT_R = 12

DEFAULT_CURVE = [[40, 20], [50, 35], [60, 55], [70, 75], [75, 90], [80, 100], [85, 100], [90, 100]]


def floor_pct(temp):
    """BIOS safety floor from G-Helper: at least (temp-70)*1.2 %, i.e. 36% at 100 °C."""
    return max(0.0, (temp - 70) * 1.2)


class FanCurveWidget(QWidget):
    curveChanged = pyqtSignal(list)     # [[temp, pct] x 8], emitted on mouse release / key nudge
    rpmLabelsToggled = pyqtSignal(bool)

    def __init__(self, parent=None, title="CPU Fan Profile, RPM/°C"):
        super().__init__(parent)
        self._points = [list(p) for p in DEFAULT_CURVE]
        self._title = title
        self._applied = True
        self._rpm_labels = True
        self._clamp_grid = False
        self._max_rpm = 8900          # observed at 100% on this GZ302EA
        self._accent = QColor("#3AAEEF")
        self._drag = None             # index being dragged
        self._focus = None            # index for arrow-key nudges
        self._drag_all_y0 = None      # shift-drag: starting pcts
        self._drag_y0 = None
        self._live = None             # (temp, rpm) marker, optional
        self.setMinimumSize(380, 190)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        # Colours (dark theme defaults; set via setPalette for light)
        self.col_bg = QColor("#232323")
        self.col_grid = QColor("#464646")
        self.col_text = QColor("#B0B0B0")
        self.col_inactive = QColor("#7A7A7A")

    # ---------------- public API ----------------

    def points(self):
        return [[int(round(t)), int(round(p))] for t, p in self._points]

    def setPoints(self, points):
        if len(points) != POINTS:
            return
        self._points = [[float(t), float(p)] for t, p in points]
        self.update()

    def setApplied(self, applied: bool):
        self._applied = bool(applied)
        self.update()

    def setAccent(self, color):
        self._accent = QColor(color)
        self.update()

    def setClampToGrid(self, on: bool):
        self._clamp_grid = bool(on)

    def setRpmLabels(self, on: bool):
        self._rpm_labels = bool(on)
        self.update()

    def setMaxRpm(self, rpm: int):
        self._max_rpm = max(1000, int(rpm))
        self.update()

    def setLive(self, temp, rpm):
        """Optional live marker (temp °C, rpm). Pass None to hide."""
        self._live = (temp, rpm) if temp is not None else None
        self.update()

    def setColors(self, bg, grid, text):
        self.col_bg, self.col_grid, self.col_text = QColor(bg), QColor(grid), QColor(text)
        self.update()

    # ---------------- geometry ----------------

    def _plot_rect(self) -> QRectF:
        # Left margin fits "8800" / "100%", bottom fits the °C labels, top fits the title.
        return QRectF(44, 22, max(10, self.width() - 56), max(10, self.height() - 44))

    def _to_px(self, temp, pct) -> QPointF:
        r = self._plot_rect()
        x = r.left() + (temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN) * r.width()
        y = r.bottom() - (pct - PCT_MIN) / (PCT_MAX - PCT_MIN) * r.height()
        return QPointF(x, y)

    def _from_px(self, pos: QPointF):
        r = self._plot_rect()
        temp = TEMP_MIN + (pos.x() - r.left()) / r.width() * (TEMP_MAX - TEMP_MIN)
        pct = PCT_MIN + (r.bottom() - pos.y()) / r.height() * (PCT_MAX - PCT_MIN)
        return temp, pct

    def _rpm_for(self, pct):
        return int(round(pct / 100.0 * self._max_rpm / 100.0)) * 100

    def _y_label(self, pct):
        if not self._rpm_labels:
            return f"{int(pct)}%"
        return "OFF" if pct <= 0 else str(self._rpm_for(pct))

    # ---------------- constraints (G-Helper ChartCPU_MouseMove / AdjustAllLevels) ----------------

    def _constrain(self, i, temp, pct):
        temp = min(TEMP_MAX, max(TEMP_MIN, temp))
        if self._clamp_grid:
            lo, hi = 30 + 10 * i, 39 + 10 * i
            temp = min(hi, max(lo, temp))
        pct = min(PCT_MAX, max(PCT_MIN, pct))
        pct = max(pct, floor_pct(temp))
        self._points[i] = [temp, pct]
        # Push later points up/right and earlier points down/left so both axes stay monotonic.
        for j in range(i + 1, POINTS):
            self._points[j][0] = max(self._points[j][0], temp)
            self._points[j][1] = max(self._points[j][1], pct)
        for j in range(i - 1, -1, -1):
            self._points[j][0] = min(self._points[j][0], temp)
            self._points[j][1] = min(self._points[j][1], pct)

    def _adjust_all(self, delta_pct):
        for i, (t, p0) in enumerate(zip((pt[0] for pt in self._points), self._drag_all_y0)):
            pct = min(PCT_MAX, max(PCT_MIN, p0 + delta_pct))
            self._points[i][1] = max(pct, floor_pct(t))

    # ---------------- events ----------------

    def _hit(self, pos: QPointF):
        best, best_d = None, HIT_R
        for i, (t, p) in enumerate(self._points):
            d = (self._to_px(t, p) - pos).manhattanLength()
            if d < best_d:
                best, best_d = i, d
        return best

    def mousePressEvent(self, ev):
        pos = ev.position()
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        r = self._plot_rect()
        if pos.x() < r.left() and r.top() <= pos.y() <= r.bottom():
            self._rpm_labels = not self._rpm_labels   # Y-axis click toggles RPM/%
            self.rpmLabelsToggled.emit(self._rpm_labels)
            self.update()
            return
        i = self._hit(pos)
        if i is None:
            return
        self._drag = self._focus = i
        if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._drag_all_y0 = [p for _, p in self._points]
            self._drag_y0 = self._from_px(pos)[1]
        else:
            self._drag_all_y0 = None
        self.setFocus()
        self.update()

    def mouseMoveEvent(self, ev):
        if self._drag is None:
            i = self._hit(ev.position())
            self.setCursor(Qt.CursorShape.PointingHandCursor if i is not None else Qt.CursorShape.ArrowCursor)
            return
        temp, pct = self._from_px(ev.position())
        if self._drag_all_y0 is not None:
            self._adjust_all(pct - self._drag_y0)
        else:
            self._constrain(self._drag, temp, pct)
        self.update()

    def mouseReleaseEvent(self, ev):
        if self._drag is None:
            return
        self._drag = None
        self._drag_all_y0 = None
        self._points = [[round(t), round(p)] for t, p in self._points]
        self.update()
        self.curveChanged.emit(self.points())

    def keyPressEvent(self, ev):
        if self._focus is None:
            return super().keyPressEvent(ev)
        step = 5 if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1
        t, p = self._points[self._focus]
        k = ev.key()
        if k == Qt.Key.Key_Left:
            t -= step
        elif k == Qt.Key.Key_Right:
            t += step
        elif k == Qt.Key.Key_Up:
            p += step
        elif k == Qt.Key.Key_Down:
            p -= step
        elif k == Qt.Key.Key_Tab:
            self._focus = (self._focus + 1) % POINTS
            self.update()
            return
        else:
            return super().keyPressEvent(ev)
        self._constrain(self._focus, t, p)
        self._points = [[round(a), round(b)] for a, b in self._points]
        self.update()
        self.curveChanged.emit(self.points())

    # ---------------- painting ----------------

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self._plot_rect()
        p.fillRect(self.rect(), self.col_bg)

        font = QFont(self.font())
        font.setPointSizeF(7.5)
        p.setFont(font)

        # Title (top-left, like the chart title in G-Helper)
        p.setPen(self.col_text)
        p.drawText(QRectF(r.left(), 2, r.width(), 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._title)

        # Grid + axis labels: every 10 °C, every 10 %
        grid_pen = QPen(self.col_grid, 1)
        p.setPen(grid_pen)
        for t in range(TEMP_MIN, TEMP_MAX + 1, 10):
            x = self._to_px(t, 0).x()
            p.setPen(grid_pen)
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
            p.setPen(self.col_text)
            p.drawText(QRectF(x - 16, r.bottom() + 2, 32, 16), Qt.AlignmentFlag.AlignCenter, f"{t}")
        for pct in range(PCT_MIN, PCT_MAX + 1, 10):
            y = self._to_px(TEMP_MIN, pct).y()
            p.setPen(grid_pen)
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(self.col_text)
            p.drawText(QRectF(0, y - 8, r.left() - 4, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._y_label(pct))

        # Live marker (optional): thin vertical line at the current temperature
        if self._live is not None:
            lt, lrpm = self._live
            if lt is not None and TEMP_MIN <= lt <= TEMP_MAX:
                x = self._to_px(lt, 0).x()
                live_pen = QPen(QColor(self._accent), 1, Qt.PenStyle.DashLine)
                p.setPen(live_pen)
                p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))

        # Series
        color = self._accent if self._applied else self.col_inactive
        pen = QPen(color, 2)
        p.setPen(pen)
        pts = [self._to_px(t, pc) for t, pc in self._points]
        for a, b in zip(pts, pts[1:]):
            p.drawLine(a, b)
        p.setBrush(QBrush(color))
        for i, pt in enumerate(pts):
            rad = MARKER_R + (2 if i in (self._drag, self._focus) else 0)
            p.setPen(QPen(self.col_bg, 1.5))
            p.drawEllipse(pt, rad, rad)

        # Floating label while dragging / focused
        idx = self._drag if self._drag is not None else None
        if idx is not None:
            t, pc = self._points[idx]
            label = f"{int(round(t))}°C, {self._rpm_for(pc)} RPM" if self._rpm_labels else f"{int(round(t))}°C, {int(round(pc))}%"
            pt = pts[idx]
            box = QRectF(pt.x() + 10, pt.y() - 22, 110, 18)
            if box.right() > self.width():
                box.moveRight(pt.x() - 10)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(box, 3, 3)
            p.setPen(QColor("#F0F0F0"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, label)
        p.end()

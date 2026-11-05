"""Small line chart of the last hour of temperatures and both fan speeds (#31).

Same approach as fan_curve_widget: plain QPainter, no QtCharts. Temperatures use the
left axis, fan RPM the right one. A missing reading breaks the line instead of
drawing zero.
"""

import time

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from telemetry_history import TelemetryHistory

TEMP_LO, TEMP_HI = 20, 110
RPM_HI = 9000
# field -> (legend text, colour, axis)
LINES = {
    "cpu": ("CPU °C", "#E8704A", "temp"),
    "gpu": ("GPU °C", "#C9B037", "temp"),
    "fan1": ("Fan 1 RPM", "#3AAEEF", "rpm"),
    "fan2": ("Fan 2 RPM", "#58C28A", "rpm"),
}


def line_segments(points, to_xy):
    """Split [(x, value_or_None)] into runs of QPointF, breaking at gaps."""
    runs, run = [], []
    for x, v in points:
        if v is None:
            if run:
                runs.append(run)
            run = []
        else:
            run.append(to_xy(x, v))
    if run:
        runs.append(run)
    return runs


class HistoryChartWidget(QWidget):
    def __init__(self, history: TelemetryHistory, parent=None):
        super().__init__(parent)
        self._history = history
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.col_bg, self.col_grid, self.col_text = QColor("#232323"), QColor("#464646"), QColor("#B0B0B0")

    def setColors(self, bg, grid, text):
        self.col_bg, self.col_grid, self.col_text = QColor(bg), QColor(grid), QColor(text)
        self.update()

    def _plot_rect(self):
        return QRectF(40, 18, max(10, self.width() - 90), max(10, self.height() - 34))

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), self.col_bg)
        r = self._plot_rect()
        font = QFont(self.font())
        font.setPointSizeF(8)
        p.setFont(font)
        window = self._history.window_s
        for frac in (0, 0.5, 1):
            y = r.bottom() - frac * r.height()
            p.setPen(QPen(self.col_grid, 1))
            p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
            p.setPen(self.col_text)
            p.drawText(QRectF(0, y - 8, 36, 16), Qt.AlignmentFlag.AlignRight,
                       f"{TEMP_LO + frac * (TEMP_HI - TEMP_LO):.0f}")
            p.drawText(QRectF(r.right() + 4, y - 8, 46, 16), Qt.AlignmentFlag.AlignLeft,
                       f"{frac * RPM_HI:.0f}")
        span = max(60.0, min(window, self._history.span_s()))
        p.setPen(self.col_text)
        p.drawText(QRectF(r.left(), r.bottom() + 2, 80, 14), Qt.AlignmentFlag.AlignLeft,
                   f"-{span / 60:.0f} min")
        p.drawText(QRectF(r.right() - 40, r.bottom() + 2, 40, 14), Qt.AlignmentFlag.AlignRight, "now")

        def to_xy(field_axis):
            def f(ago, v):
                x = r.right() - ago / span * r.width()
                lo, hi = (TEMP_LO, TEMP_HI) if field_axis == "temp" else (0, RPM_HI)
                y = r.bottom() - (min(max(v, lo), hi) - lo) / (hi - lo) * r.height()
                return QPointF(x, y)
            return f

        lx = r.left() + 6
        for field, (text, colour, axis) in LINES.items():
            pen = QPen(QColor(colour), 1.6)
            p.setPen(pen)
            for run in line_segments([(a, v) for a, v in self._history.series(field) if a <= span],
                                     to_xy(axis)):
                if len(run) == 1:
                    p.drawPoint(run[0])
                    continue
                path = QPainterPath(run[0])
                for pt in run[1:]:
                    path.lineTo(pt)
                p.drawPath(path)
            p.drawText(QPointF(lx, 12), text)
            lx += p.fontMetrics().horizontalAdvance(text) + 14
        p.end()

"""Small line charts of the last hour: temperatures and both fan speeds (#31).

Same approach as fan_curve_widget: plain QPainter, no QtCharts. Two measures
with different units get two plots side by side on one time axis each, never
two Y scales on one plot: temperature (°C) on the left, fan speed (RPM) on the
right. A missing reading breaks the line instead of drawing zero.

Colours come from the design tokens (theme.py) at paint time. Each line keeps
its own colour in both themes; the legend text is ordinary ink, with a swatch
of the line's colour beside it. The latest values are the status strip of the
main window, so the lines carry no number labels.
"""

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QFont, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QSizePolicy, QWidget

from telemetry_history import TelemetryHistory
from theme import FONT, RADIUS, qcolor

TEMP_LO, TEMP_HI = 20, 110
RPM_HI = 9000
# field -> (legend text, colour token)
LINES = {
    "cpu": ("CPU", "chart_cpu"),
    "gpu": ("GPU", "chart_gpu"),
    "fan1": ("Fan 1", "chart_fan1"),
    "fan2": ("Fan 2", "chart_fan2"),
}
# One plot per unit: (title, fields, low, high)
PLOTS = (
    ("°C", ("cpu", "gpu"), TEMP_LO, TEMP_HI),
    ("RPM", ("fan1", "fan2"), 0, RPM_HI),
)


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
        self.setMinimumHeight(132)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def _plot_rects(self):
        """The two plot areas, left to right."""
        half = self.width() / 2
        top, bottom, left, right = 28, 20, 40, 14
        return [QRectF(i * half + left, top, max(10, half - left - right), max(10, self.height() - top - bottom))
                for i in range(len(PLOTS))]

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        ink, grid = qcolor("text_muted"), qcolor("chart_grid")
        p.setPen(QPen(qcolor("border"), 1))
        p.setBrush(qcolor("inset"))
        p.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), RADIUS["md"], RADIUS["md"])
        font = QFont(self.font())
        font.setPointSizeF(FONT["caption_pt"])
        p.setFont(font)
        fm = p.fontMetrics()
        span = max(60.0, min(self._history.window_s, self._history.span_s()))

        for (title, fields, lo, hi), r in zip(PLOTS, self._plot_rects()):
            # Title, then the legend: a swatch of the line's colour and its name in ink
            p.setPen(ink)
            p.drawText(QRectF(r.left(), 6, r.width(), 18),
                       Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, title)
            x = r.right()
            for field in reversed(fields):
                text, token = LINES[field]
                x -= fm.horizontalAdvance(text)
                p.setPen(ink)
                p.drawText(QRectF(x, 6, r.right() - x, 18),
                           Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
                x -= 14
                p.setPen(QPen(qcolor(token), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawLine(QPointF(x, 15), QPointF(x + 9, 15))
                x -= 10
            # Grid and Y labels
            for frac in (0, 0.5, 1):
                y = r.bottom() - frac * r.height()
                p.setPen(QPen(grid, 1))
                p.drawLine(QPointF(r.left(), y), QPointF(r.right(), y))
                p.setPen(ink)
                p.drawText(QRectF(r.left() - 40, y - 8, 34, 16),
                           Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                           f"{lo + frac * (hi - lo):.0f}")
            p.setPen(ink)
            p.drawText(QRectF(r.left(), r.bottom() + 3, 80, 14), Qt.AlignmentFlag.AlignLeft,
                       f"-{span / 60:.0f} min")
            p.drawText(QRectF(r.right() - 40, r.bottom() + 3, 40, 14), Qt.AlignmentFlag.AlignRight, "now")

            def to_xy(ago, v, r=r, lo=lo, hi=hi):
                return QPointF(r.right() - ago / span * r.width(),
                               r.bottom() - (min(max(v, lo), hi) - lo) / (hi - lo) * r.height())

            for field in fields:
                p.setPen(QPen(qcolor(LINES[field][1]), 2, Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
                p.setBrush(Qt.BrushStyle.NoBrush)
                for run in line_segments([(a, v) for a, v in self._history.series(field) if a <= span],
                                         to_xy):
                    if len(run) == 1:
                        p.drawPoint(run[0])
                        continue
                    path = QPainterPath(run[0])
                    for pt in run[1:]:
                        path.lineTo(pt)
                    p.drawPath(path)
        p.end()

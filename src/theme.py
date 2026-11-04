"""Look and feel lifted from G-Helper (seerge/g-helper, UI/RForm.cs + UI/RButton.cs).

Dark theme values are G-Helper's; accents are the fixed mode colours it uses
in both themes. `ModeTile` reproduces RButton: a flat rounded button whose
active state is a 2 px accent border plus a faint top tint.
"""

from PyQt6.QtCore import Qt, QRectF, QEvent
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient, QFont, QFontMetrics, QPainterPath
from PyQt6.QtWidgets import QPushButton, QLabel, QFrame, QVBoxLayout, QHBoxLayout, QWidget

from design_tokens import TOKENS

_T = TOKENS
_C, _A, _TOUCH = _T["colors"], _T["accents"], _T["touch"]
_R, _S, _F = _T["radii"], _T["spacing"], _T["font_pt"]
TOUCH_MIN = _TOUCH["touch_min_px"]

# Dark theme (RForm.cs), values in design_tokens.json
FORM = _C["form"]
BUTTON_MAIN = _C["button_main"]
BUTTON_SECOND = _C["button_second"]
TEXT = _C["text"]
TEXT_DIM = _C["text_dim"]
BORDER = _C["border"]
CHART_BG = _C["chart_bg"]
CHART_GRID = _C["chart_grid"]
DISABLED = _C["disabled"]

# Accents (fixed in both themes)
ECO = _A["quiet"]        # Silent (green)
STANDARD = _A["balanced"]   # Balanced (blue) — also the slider accent
TURBO = _A["performance"]      # Turbo (red)
CUSTOM = _A["custom"]     # Fans + Power / custom (orange)
GRAY = _A["gray"]

ACCENT_FOR_BASE = {"quiet": ECO, "balanced": STANDARD, "performance": TURBO}

TILE_PRIMARY = _TOUCH["tile_primary_px"]
TILE_SECONDARY = _TOUCH["tile_secondary_px"]
_HANDLE = _TOUCH["slider_handle_px"]
_GROOVE = _TOUCH["slider_groove_px"]
_CHECK = _TOUCH["checkbox_indicator_px"]

STYLESHEET = f"""
QMainWindow, QWidget#root, QDialog {{ background: {FORM}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; font-size: {_F["body"]}pt; }}
QLabel#title {{ font-weight: bold; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#tip {{ color: {TEXT_DIM}; font-size: {_F["small"]}pt; }}
QComboBox, QLineEdit, QSpinBox {{
    background: {BUTTON_MAIN}; border: 1px solid {BORDER}; border-radius: {_R["control"]}px;
    padding: 4px {_S["md"]}px; min-height: {TOUCH_MIN - 10}px; color: {TEXT};
}}
QComboBox::drop-down {{ border: none; width: 32px; }}
QComboBox QAbstractItemView {{ background: {BUTTON_MAIN}; selection-background-color: {STANDARD}; color: {TEXT}; }}
QComboBox QAbstractItemView::item {{ min-height: {TOUCH_MIN}px; }}
QPushButton {{
    background: {BUTTON_SECOND}; border: 1px solid {BORDER}; border-radius: {_R["control"]}px;
    padding: 4px {_S["lg"]}px; color: {TEXT}; min-height: {TOUCH_MIN - 10}px;
}}
QPushButton:hover {{ background: {BUTTON_MAIN}; }}
QPushButton:disabled {{ color: {DISABLED}; }}
QPushButton#pill, QPushButton#pillOrange {{ border-radius: {_R["control"]}px; font-size: {_F["pill"]}pt; font-weight: bold; padding: 2px {_S["md"]}px; min-height: {TOUCH_MIN - 6}px; }}
QPushButton#tile {{ padding: 0px; }}
QPushButton#pill:checked {{ background: {STANDARD}; border-color: {STANDARD}; color: white; }}
QPushButton#pillOrange:checked {{ background: {CUSTOM}; border-color: {CUSTOM}; color: white; }}
QCheckBox {{ spacing: {_S["md"]}px; min-height: {TOUCH_MIN}px; }}
QCheckBox::indicator {{ width: {_CHECK}px; height: {_CHECK}px; border: 1px solid {BORDER}; border-radius: {_R["control"]}px; background: {BUTTON_MAIN}; }}
QCheckBox::indicator:checked {{ background: {STANDARD}; border-color: {STANDARD}; }}
QSlider:horizontal {{ min-height: {TOUCH_MIN}px; }}
QSlider::groove:horizontal {{ height: {_GROOVE}px; background: {CHART_GRID}; border-radius: {_GROOVE // 2}px; }}
QSlider::sub-page:horizontal {{ background: {STANDARD}; border-radius: {_GROOVE // 2}px; }}
QSlider::handle:horizontal {{
    width: {_HANDLE - 6}px; height: {_HANDLE - 6}px; margin: -{(_HANDLE - _GROOVE) // 2}px 0; border-radius: {_HANDLE // 2}px;
    background: white; border: 3px solid {STANDARD};
}}
QStatusBar {{ background: {FORM}; color: {TEXT_DIM}; font-size: {_F["small"]}pt; }}
QPushButton#errorBadge {{ color: {TURBO}; font-weight: bold; font-size: {_F["small"]}pt; border: none; padding: 0 {_S["md"]}px; min-height: {TOUCH_MIN}px; background: transparent; }}
QPushButton#errorBadge:hover {{ text-decoration: underline; }}
QMenuBar {{ background: {FORM}; }}
QMenuBar::item {{ padding: 12px {_S["lg"]}px; }}
QMenu::item {{ padding: 12px 24px; min-height: {TOUCH_MIN - 24}px; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {BUTTON_MAIN}; }}
QMenu {{ background: {BUTTON_SECOND}; border: 1px solid {BORDER}; }}
QTableWidget {{ background: {CHART_BG}; gridline-color: {CHART_GRID}; border: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {BUTTON_MAIN}; border: none; padding: 4px; }}
QScrollBar:vertical {{ background: {FORM}; width: {TOUCH_MIN}px; }}
QScrollBar::handle:vertical {{ background: {BUTTON_MAIN}; border-radius: 8px; min-height: {TOUCH_MIN}px; }}
QScrollBar:horizontal {{ background: {FORM}; height: {TOUCH_MIN}px; }}
QScrollBar::handle:horizontal {{ background: {BUTTON_MAIN}; border-radius: 8px; min-width: {TOUCH_MIN}px; }}
QToolTip {{ background: {BUTTON_MAIN}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


class ModeTile(QPushButton):
    """G-Helper's RButton: flat, rounded; active = accent border + top tint.

    `accent` is fixed per button (Silent green, Balanced blue, Turbo red,
    Fans + Power orange) but can be changed at runtime (the Fans + Power tile
    takes the custom mode's base colour).
    """

    def __init__(self, text, accent=STANDARD, secondary=False, height=TILE_PRIMARY, icon=None, parent=None):
        super().__init__(text, parent)
        self.accent = QColor(accent)
        self.secondary = secondary
        self.icon_name = icon
        self.radius = _R["tile"]
        self.setCheckable(True)
        # The stylesheet's generic QPushButton min-height/padding is applied at
        # polish time and overrode setFixedHeight (tiles collapsed to ~28 px), so
        # the fixed height is re-applied after every polish.
        self.setObjectName("tile")
        self._h = height
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def event(self, ev):
        handled = super().event(ev)
        if ev.type() == QEvent.Type.Polish:
            self.setFixedHeight(self._h)
        return handled

    def setAccent(self, color):
        self.accent = QColor(color)
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        bg = QColor(BUTTON_SECOND if self.secondary else BUTTON_MAIN)
        if self.underMouse() and self.isEnabled():
            bg = bg.lighter(115)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(r, self.radius, self.radius)
        if self.isChecked():
            # Faint top-to-bottom tint over the top 20 % (RButton alpha 32 -> 0)
            g = QLinearGradient(0, r.top(), 0, r.top() + r.height() * 0.2)
            tint = QColor(self.accent)
            tint.setAlpha(32)
            g.setColorAt(0, tint)
            tint2 = QColor(self.accent)
            tint2.setAlpha(0)
            g.setColorAt(1, tint2)
            p.setBrush(g)
            p.drawRoundedRect(r, self.radius, self.radius)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(self.accent, 2))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
        text_color = QColor(TEXT if self.isEnabled() else DISABLED)
        p.setPen(text_color)
        f = QFont(self.font())
        p.setFont(f)
        if self.icon_name:
            p.drawText(self.rect().adjusted(3, 3, -3, -23),
                       Qt.AlignmentFlag.AlignCenter, self.text())
            self._draw_tile_icon(p, text_color)
        else:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()

    def _draw_tile_icon(self, painter, text_color):
        """Draw a small theme-safe line icon centred along the tile's bottom edge."""
        cx = self.width() / 2
        cy = self.height() - 12
        color = QColor(self.accent if self.isEnabled() else text_color)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if self.icon_name == "leaf":
            path.moveTo(cx - 7, cy + 4)
            path.cubicTo(cx - 9, cy - 4, cx + 1, cy - 10, cx + 7, cy - 7)
            path.cubicTo(cx + 8, cy - 1, cx + 2, cy + 7, cx - 7, cy + 4)
            painter.drawPath(path)
            painter.drawLine(int(cx - 5), int(cy + 3), int(cx + 5), int(cy - 6))
        elif self.icon_name == "balance":
            painter.drawLine(int(cx), int(cy - 8), int(cx), int(cy + 6))
            painter.drawLine(int(cx - 7), int(cy - 5), int(cx + 7), int(cy - 5))
            painter.drawLine(int(cx - 5), int(cy - 5), int(cx - 8), int(cy + 1))
            painter.drawLine(int(cx + 5), int(cy - 5), int(cx + 8), int(cy + 1))
            painter.drawLine(int(cx - 4), int(cy + 6), int(cx + 4), int(cy + 6))
            painter.drawLine(int(cx - 8), int(cy + 1), int(cx - 3), int(cy + 1))
            painter.drawLine(int(cx + 3), int(cy + 1), int(cx + 8), int(cy + 1))
        elif self.icon_name == "bolt":
            path.moveTo(cx + 2, cy - 8)
            path.lineTo(cx - 5, cy + 1)
            path.lineTo(cx - 1, cy + 1)
            path.lineTo(cx - 3, cy + 8)
            path.lineTo(cx + 5, cy - 2)
            path.lineTo(cx + 1, cy - 2)
            path.closeSubpath()
            painter.drawPath(path)
        elif self.icon_name == "fan":
            painter.drawEllipse(int(cx - 1.4), int(cy - 1.4), 3, 3)
            for angle in (0, 120, 240):
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(angle)
                path.moveTo(0, -2)
                path.cubicTo(1, -4, 7, -8, 6, -3)
                path.cubicTo(5, -1, 2, 0, 0, 1)
                painter.drawPath(path)
                path = QPainterPath()
                painter.restore()
        painter.restore()


def pill(text, orange=False, checkable=True):
    b = QPushButton(text)
    b.setObjectName("pillOrange" if orange else "pill")
    b.setCheckable(checkable)
    b.setMinimumHeight(TOUCH_MIN)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


class Panel(QFrame):
    """One stacked section: title row (title · right-aligned grey sensor/extra)
    then whatever the caller adds to `body`.
    """

    def __init__(self, title, parent=None):
        super().__init__(parent)
        self.setContentsMargins(0, 0, 0, 0)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(10, 10, 10, 6)
        outer.setSpacing(6)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.title = QLabel(title)
        self.title.setObjectName("title")
        head.addWidget(self.title)
        head.addStretch(1)
        self.right = QLabel("")
        self.right.setObjectName("dim")
        self.right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        head.addWidget(self.right)
        self.head = head
        outer.addLayout(head)
        self.body = QVBoxLayout()
        self.body.setSpacing(6)
        outer.addLayout(self.body)

    def add_head_widget(self, w):
        """Insert a widget (pill button, combo) between the title and the sensor label."""
        self.head.insertWidget(self.head.count() - 1, w)


def tip(text):
    l = QLabel(text)
    l.setObjectName("tip")
    l.setWordWrap(True)
    return l

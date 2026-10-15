"""Look and feel lifted from G-Helper (seerge/g-helper, UI/RForm.cs + UI/RButton.cs).

Dark theme values are G-Helper's; accents are the fixed mode colours it uses
in both themes. `ModeTile` reproduces RButton: a flat rounded button whose
active state is a 2 px accent border plus a faint top tint.
"""

from PyQt6.QtCore import Qt, QRectF, QEvent
from PyQt6.QtGui import QColor, QPainter, QPen, QLinearGradient, QFont, QFontMetrics
from PyQt6.QtWidgets import QPushButton, QLabel, QFrame, QVBoxLayout, QHBoxLayout, QWidget

# Dark theme (RForm.cs)
FORM = "#1C1C1C"
BUTTON_MAIN = "#2E2E2E"
BUTTON_SECOND = "#242424"
TEXT = "#F0F0F0"
TEXT_DIM = "#A8A8A8"
BORDER = "#373737"
CHART_BG = "#232323"
CHART_GRID = "#464646"

# Accents (fixed in both themes)
ECO = "#06B48A"        # Silent (green)
STANDARD = "#3AAEEF"   # Balanced (blue) — also the slider accent
TURBO = "#FF2020"      # Turbo (red)
CUSTOM = "#FF8000"     # Fans + Power / custom (orange)
GRAY = "#A8A8A8"

ACCENT_FOR_BASE = {"quiet": ECO, "balanced": STANDARD, "performance": TURBO}

STYLESHEET = f"""
QMainWindow, QWidget#root, QDialog {{ background: {FORM}; color: {TEXT}; }}
QWidget {{ color: {TEXT}; font-size: 9pt; }}
QLabel#title {{ font-weight: bold; }}
QLabel#dim {{ color: {TEXT_DIM}; }}
QLabel#tip {{ color: {TEXT_DIM}; font-size: 8pt; }}
QComboBox, QLineEdit, QSpinBox {{
    background: {BUTTON_MAIN}; border: 1px solid {BORDER}; border-radius: 3px;
    padding: 3px 8px; min-height: 20px; color: {TEXT};
}}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background: {BUTTON_MAIN}; selection-background-color: {STANDARD}; color: {TEXT}; }}
QPushButton {{
    background: {BUTTON_SECOND}; border: 1px solid {BORDER}; border-radius: 3px;
    padding: 4px 10px; color: {TEXT}; min-height: 20px;
}}
QPushButton:hover {{ background: {BUTTON_MAIN}; }}
QPushButton:disabled {{ color: #6a6a6a; }}
QPushButton#pill, QPushButton#pillOrange {{ border-radius: 2px; font-size: 7.5pt; font-weight: bold; padding: 2px 8px; min-height: 16px; }}
QPushButton#tile {{ padding: 0px; }}
QPushButton#pill:checked {{ background: {STANDARD}; border-color: {STANDARD}; color: white; }}
QPushButton#pillOrange:checked {{ background: {CUSTOM}; border-color: {CUSTOM}; color: white; }}
QCheckBox {{ spacing: 6px; }}
QCheckBox::indicator {{ width: 14px; height: 14px; border: 1px solid {BORDER}; border-radius: 2px; background: {BUTTON_MAIN}; }}
QCheckBox::indicator:checked {{ background: {STANDARD}; border-color: {STANDARD}; }}
QSlider::groove:horizontal {{ height: 4px; background: {CHART_GRID}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {STANDARD}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    width: 16px; height: 16px; margin: -6px 0; border-radius: 8px;
    background: white; border: 3px solid {STANDARD};
}}
QStatusBar {{ background: {FORM}; color: {TEXT_DIM}; font-size: 8pt; }}
QPushButton#errorBadge {{ color: {TURBO}; font-weight: bold; font-size: 8pt; border: none; padding: 0 6px; min-height: 0; background: transparent; }}
QPushButton#errorBadge:hover {{ text-decoration: underline; }}
QMenuBar {{ background: {FORM}; }}
QMenuBar::item:selected, QMenu::item:selected {{ background: {BUTTON_MAIN}; }}
QMenu {{ background: {BUTTON_SECOND}; border: 1px solid {BORDER}; }}
QTableWidget {{ background: {CHART_BG}; gridline-color: {CHART_GRID}; border: 1px solid {BORDER}; }}
QHeaderView::section {{ background: {BUTTON_MAIN}; border: none; padding: 4px; }}
QScrollBar:vertical {{ background: {FORM}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {BUTTON_MAIN}; border-radius: 4px; min-height: 20px; }}
QToolTip {{ background: {BUTTON_MAIN}; color: {TEXT}; border: 1px solid {BORDER}; }}
"""


class ModeTile(QPushButton):
    """G-Helper's RButton: flat, rounded; active = accent border + top tint.

    `accent` is fixed per button (Silent green, Balanced blue, Turbo red,
    Fans + Power orange) but can be changed at runtime (the Fans + Power tile
    takes the custom mode's base colour).
    """

    def __init__(self, text, accent=STANDARD, secondary=False, height=60, parent=None):
        super().__init__(text, parent)
        self.accent = QColor(accent)
        self.secondary = secondary
        self.radius = 5
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
        p.setPen(QColor(TEXT if self.isEnabled() else "#6a6a6a"))
        f = QFont(self.font())
        p.setFont(f)
        p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()


def pill(text, orange=False, checkable=True):
    b = QPushButton(text)
    b.setObjectName("pillOrange" if orange else "pill")
    b.setCheckable(checkable)
    b.setFixedHeight(20)
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

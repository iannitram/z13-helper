"""Look and feel of the desktop app (#229).

Every colour, size and radius comes from design_tokens.json (design_tokens.py);
this module turns the tokens into Qt: one application stylesheet, a QPalette,
and the few widgets that paint themselves (ModeTile, CheckBox) or give the
windows their structure (Panel, StatTile).

Two themes, dark and light. `apply(app, choice)` sets one up; the choice is
"system" (follow the desktop's colour scheme, the default), "dark" or "light".
Widgets that paint themselves ask `qcolor("name")` at paint time, so changing
the theme only needs a repaint; anything that stores a colour (table items)
registers with `on_change`.

An accent is passed around as a token name ("quiet", "balanced",
"performance", "custom"), never as a colour, so it is right in both themes.
"""

from PyQt6.QtCore import Qt, QRectF, QEvent, QSize, QPointF
from PyQt6.QtGui import QColor, QPainter, QPen, QFont, QPainterPath, QPalette, QGuiApplication
from PyQt6.QtWidgets import (QPushButton, QLabel, QTableWidget, QFrame, QVBoxLayout, QHBoxLayout, QCheckBox,
                             QComboBox,
                             QSizePolicy)

import design_tokens as tokens

SIZE, SPACE, RADIUS, FONT = tokens.SIZE, tokens.SPACE, tokens.RADIUS, tokens.FONT

# Accent token names. The old names stay: the tiles are still G-Helper's
# Silent / Balanced / Turbo, and Fans + Power / custom modes are orange.
ECO = "quiet"
STANDARD = "balanced"
TURBO = "performance"
CUSTOM = "custom"
ACCENT_FOR_BASE = {"quiet": ECO, "balanced": STANDARD, "performance": TURBO}

_state = {"choice": tokens.DEFAULT_CHOICE, "mode": tokens.FALLBACK_MODE, "app": None, "hooked": False}
_listeners = []


# ----------------------------------------------------------------------
# Which theme, and its colours
# ----------------------------------------------------------------------

def mode():
    """The theme in use: "dark" or "light"."""
    return _state["mode"]


def choice():
    """The setting: "system", "dark" or "light"."""
    return _state["choice"]


def colour(name):
    """A token's colour in the current theme, as "#RRGGBB"."""
    return tokens.colours(_state["mode"])[name]


def qcolor(name, alpha=None):
    """A token's colour in the current theme as a QColor; `alpha` is 0 to 255."""
    c = QColor(colour(name))
    if alpha is not None:
        c.setAlpha(alpha)
    return c


def system_mode():
    """What the desktop asks for: "dark", "light", or None when it does not say."""
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        if scheme == Qt.ColorScheme.Light:
            return "light"
        if scheme == Qt.ColorScheme.Dark:
            return "dark"
    except Exception:
        pass
    return None


def on_change(callback):
    """Call `callback(mode)` after every theme change (for stored colours)."""
    _listeners.append(callback)


def apply(app, choice=tokens.DEFAULT_CHOICE):
    """Theme the whole application. Safe to call again with another choice."""
    _state["app"] = app
    _state["choice"] = choice if choice in tokens.CHOICES else tokens.DEFAULT_CHOICE
    if app is None:
        return
    if not _state["hooked"]:
        _state["hooked"] = True
        try:
            # One widget style everywhere, so the stylesheet lands on the same
            # base on the device, in the Flatpak and in offscreen screenshots.
            app.setStyle("Fusion")
            QGuiApplication.styleHints().colorSchemeChanged.connect(lambda _scheme: _refresh())
        except Exception:
            pass
    _refresh()


def _refresh():
    app = _state["app"]
    if app is None:
        return
    _state["mode"] = tokens.resolve(_state["choice"], system_mode())
    app.setPalette(_palette())
    app.setStyleSheet(stylesheet())
    for widget in app.allWidgets():
        if isinstance(widget, QTableWidget):
            _retone_table(widget)
        widget.update()
    for callback in list(_listeners):
        try:
            callback(_state["mode"])
        except RuntimeError:        # its widget is gone
            _listeners.remove(callback)


def set_tone(widget, tone):
    """Colour a label by meaning: "danger", "warn", "ok", "accent", or "" for the normal ink."""
    if widget.property("tone") == (tone or ""):
        return
    widget.setProperty("tone", tone or "")
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


_TONE_ROLE = Qt.ItemDataRole.UserRole + 71


def tone_item(item, tone):
    """Colour a table cell by meaning; it follows the theme when that changes."""
    item.setData(_TONE_ROLE, tone or "")
    if tone:
        item.setForeground(qcolor(tone))


def _retone_table(table):
    for row in range(table.rowCount()):
        for col in range(table.columnCount()):
            item = table.item(row, col)
            tone = item.data(_TONE_ROLE) if item is not None else ""
            if tone:
                item.setForeground(qcolor(tone))


def _palette():
    c = tokens.colours(_state["mode"])
    pal = QPalette()
    role = QPalette.ColorRole
    for r, name in ((role.Window, "window"), (role.WindowText, "text"), (role.Base, "inset"),
                    (role.AlternateBase, "card"), (role.Text, "text"), (role.Button, "control"),
                    (role.ButtonText, "text"), (role.BrightText, "on_accent"),
                    (role.Highlight, "accent"), (role.HighlightedText, "on_accent"),
                    (role.ToolTipBase, "tooltip"), (role.ToolTipText, "on_tooltip"),
                    (role.PlaceholderText, "text_faint"), (role.Link, "accent"),
                    (role.LinkVisited, "accent"), (role.Light, "control_hover"),
                    (role.Midlight, "border"), (role.Mid, "border_strong"),
                    (role.Dark, "border"), (role.Shadow, "window")):
        pal.setColor(r, QColor(c[name]))
    for r in (role.WindowText, role.Text, role.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, r, QColor(c["text_faint"]))
    return pal


def stylesheet(for_mode=None):
    """The application stylesheet for one theme (default: the current one)."""
    c = tokens.colours(for_mode or _state["mode"])
    f, r, s = FONT, RADIUS, SIZE
    pad = (s["control_h"] - 22 - 2) // 2          # min-height 22 + padding + 1 px borders = control_h
    half = s["slider_handle"] // 2
    pull = (s["slider_handle"] - s["slider_groove"]) // 2
    return f"""
QMainWindow, QDialog, QWidget#root {{ background: {c['window']}; }}
QWidget {{ color: {c['text']}; font-size: {f['body_pt']}pt; }}
QLabel {{ background: transparent; }}

QFrame#card {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: {r['lg']}px; }}
QFrame#row, QFrame#stat {{ background: transparent; border: none; }}
QFrame#divider {{ background: {c['border']}; border: none; min-height: 1px; max-height: 1px; }}
QFrame#vdivider {{ background: {c['border']}; border: none; min-width: 1px; max-width: 1px; }}

QLabel#title {{ font-weight: 600; }}
QLabel#heading {{ font-size: {f['heading_pt']}pt; font-weight: 600; }}
QLabel#value {{ font-weight: 600; }}
QLabel#stat {{ font-size: {f['stat_pt']}pt; font-weight: 600; }}
QLabel#caption {{ color: {c['text_muted']}; font-size: {f['caption_pt']}pt; font-weight: 600; }}
QLabel#dim {{ color: {c['text_muted']}; }}
QLabel#tip, QLabel#note {{ color: {c['text_muted']}; font-size: {f['caption_pt']}pt; }}
QLabel:disabled {{ color: {c['text_faint']}; }}
QLabel[tone="danger"], QLabel#dim[tone="danger"], QLabel#tip[tone="danger"], QLabel#note[tone="danger"] {{ color: {c['danger']}; font-weight: 600; }}
QLabel[tone="warn"], QLabel#dim[tone="warn"], QLabel#tip[tone="warn"], QLabel#note[tone="warn"] {{ color: {c['warn']}; }}
QLabel[tone="ok"], QLabel#dim[tone="ok"], QLabel#tip[tone="ok"], QLabel#note[tone="ok"] {{ color: {c['ok']}; }}
QLabel[tone="accent"], QLabel#dim[tone="accent"], QLabel#tip[tone="accent"], QLabel#note[tone="accent"] {{ color: {c['accent']}; }}
QLabel[link="true"] {{ text-decoration: underline; }}

QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {c['control']}; border: 1px solid {c['border']}; border-radius: {r['md']}px;
    padding: {pad}px 10px; min-height: 22px;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']};
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {c['border_strong']}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {c['accent']}; }}
QComboBox:disabled, QLineEdit:disabled, QSpinBox:disabled {{ color: {c['text_faint']}; }}
QComboBox {{ padding-right: 26px; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox::down-arrow {{ image: none; }}
QComboBox QAbstractItemView {{
    background: {c['card']}; border: 1px solid {c['border']}; padding: 4px; outline: 0;
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']};
}}

QPushButton {{
    background: {c['control']}; border: 1px solid {c['border']}; border-radius: {r['md']}px;
    padding: {pad}px 14px; min-height: 22px;
}}
QPushButton:hover {{ background: {c['control_hover']}; border-color: {c['border_strong']}; }}
QPushButton:pressed {{ background: {c['control_pressed']}; }}
QPushButton:focus {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['text_faint']}; }}
QPushButton:default, QPushButton#primary {{
    background: {c['accent']}; border-color: {c['accent']}; color: {c['on_accent']}; font-weight: 600;
}}
QPushButton:default:hover, QPushButton#primary:hover {{ background: {c['accent_hover']}; border-color: {c['accent_hover']}; }}
QPushButton#pill, QPushButton#pillOrange {{ font-size: {f['caption_pt']}pt; font-weight: 600; padding: 0px 12px; min-height: 0px; }}
QPushButton#pill:checked {{ background: {c['accent']}; border-color: {c['accent']}; color: {c['on_accent']}; }}
QPushButton#pillOrange:checked {{ background: {c['custom']}; border-color: {c['custom']}; color: {c['on_accent']}; }}
QPushButton#tile {{ padding: 0px; border: none; background: transparent; }}
QPushButton#errorBadge {{
    color: {c['danger']}; font-weight: 600; font-size: {f['caption_pt']}pt; border: none;
    padding: 0px 8px; min-height: 0px; background: transparent;
}}
QPushButton#errorBadge:hover {{ text-decoration: underline; }}

QSlider {{ min-height: {s['slider_handle'] + 4}px; }}
QSlider::groove:horizontal {{ height: {s['slider_groove']}px; background: {c['border']}; border-radius: {s['slider_groove'] // 2}px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: {s['slider_groove'] // 2}px; }}
QSlider::handle:horizontal {{
    width: {s['slider_handle'] - 4}px; height: {s['slider_handle'] - 4}px; margin: -{pull}px 0px;
    border-radius: {half}px; background: {c['accent']}; border: 2px solid {c['card']};
}}
QSlider::handle:horizontal:hover {{ background: {c['accent_hover']}; }}
QSlider::sub-page:horizontal:disabled, QSlider::handle:horizontal:disabled {{ background: {c['text_faint']}; }}

QMenuBar {{ background: {c['window']}; border-bottom: 1px solid {c['border']}; padding: 2px 4px; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; border-radius: {r['sm']}px; }}
QMenuBar::item:selected {{ background: {c['control_hover']}; }}
QMenu {{ background: {c['card']}; border: 1px solid {c['border']}; padding: 4px; }}
QMenu::item {{ padding: 6px 22px 6px 26px; border-radius: {r['sm']}px; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['on_accent']}; }}
QMenu::item:disabled {{ color: {c['text_faint']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; left: 8px; }}
QStatusBar {{ background: {c['window']}; color: {c['text_muted']}; font-size: {f['caption_pt']}pt; }}
QStatusBar::item {{ border: none; }}

QTabWidget::pane {{ background: {c['card']}; border: 1px solid {c['border']}; border-radius: {r['md']}px; top: -1px; }}
QTabBar::tab {{
    background: transparent; color: {c['text_muted']}; padding: 7px 14px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {c['text']}; }}
QTabBar::tab:selected {{ color: {c['text']}; border-bottom: 2px solid {c['accent']}; }}
QTableView, QTreeView, QListView, QPlainTextEdit, QTextEdit {{
    background: {c['inset']}; alternate-background-color: {c['card']}; border: 1px solid {c['border']};
    border-radius: {r['md']}px; gridline-color: {c['chart_grid']};
    selection-background-color: {c['accent']}; selection-color: {c['on_accent']};
}}
QHeaderView {{ background: transparent; }}
QHeaderView::section {{
    background: {c['control']}; color: {c['text_muted']}; border: none;
    border-bottom: 1px solid {c['border']}; padding: 6px 8px; font-weight: 600;
}}
QTableCornerButton::section {{ background: {c['control']}; border: none; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['border_strong']}; border-radius: 4px; min-height: 28px; }}
QScrollBar::handle:horizontal {{ background: {c['border_strong']}; border-radius: 4px; min-width: 28px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0px; height: 0px; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QProgressBar {{ background: {c['control']}; border: 1px solid {c['border']}; border-radius: {r['sm']}px; text-align: center; }}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: {r['sm']}px; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: {r['md']}px; margin-top: 16px; padding: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {c['text_muted']}; }}
QToolTip {{ background: {c['tooltip']}; color: {c['on_tooltip']}; border: 1px solid {c['border_strong']}; padding: 4px 6px; }}
"""


# ----------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------

class ModeTile(QPushButton):
    """A large toggle tile: icon over label; the active one wears its accent.

    `accent` is a token name, fixed per tile (Silent "quiet", Balanced
    "balanced", Turbo "performance", Fans + Power "custom") but changeable at
    runtime: the Fans + Power tile takes the custom mode's base accent.
    `secondary` tiles are outlined instead of filled: they open something
    rather than being one of the choices.
    """

    def __init__(self, text, accent=STANDARD, secondary=False, height=None, icon=None, parent=None):
        super().__init__(text, parent)
        self.accent = accent
        self.secondary = secondary
        self.icon_name = icon
        self.radius = RADIUS["md"]
        self.setCheckable(True)
        # The stylesheet's generic QPushButton min-height/padding is applied at
        # polish time and would override setFixedHeight, so the fixed height is
        # re-applied after every polish.
        self.setObjectName("tile")
        self._h = height or SIZE["tile_h"]
        self.setFixedHeight(self._h)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def event(self, ev):
        handled = super().event(ev)
        if ev.type() == QEvent.Type.Polish:
            self.setFixedHeight(self._h)
        return handled

    def setAccent(self, accent):
        self.accent = accent
        self.update()

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        enabled, on = self.isEnabled(), self.isChecked()
        if self.isDown():
            fill = "control_pressed"
        elif self.underMouse() and enabled:
            fill = "control_hover"
        else:
            fill = "card" if self.secondary else "control"
        p.setPen(QPen(qcolor("border_strong" if self.secondary and not on else "border"), 1))
        p.setBrush(qcolor(fill))
        p.drawRoundedRect(r, self.radius, self.radius)
        if on:
            # The active tile: a wash of its accent and a 2 px accent outline.
            p.setBrush(qcolor(self.accent, 40 if mode() == "dark" else 30))
            p.setPen(QPen(qcolor(self.accent), 2))
            p.drawRoundedRect(r.adjusted(0.5, 0.5, -0.5, -0.5), self.radius, self.radius)
        ink = qcolor("text" if enabled else "text_faint")
        font = QFont(self.font())
        if on:
            font.setWeight(QFont.Weight.DemiBold)
        p.setFont(font)
        p.setPen(ink)
        if self.icon_name:
            mid = self.height() / 2
            self._draw_tile_icon(p, qcolor(self.accent) if enabled else ink, self.width() / 2, mid - 9)
            p.setPen(ink)
            p.drawText(QRectF(2, mid + 3, self.width() - 4, mid - 6),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, self.text())
        else:
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()

    def _draw_tile_icon(self, painter, color, cx, cy):
        """Draw a small line icon centred on (cx, cy)."""
        painter.save()
        painter.setPen(QPen(color, 1.7, Qt.PenStyle.SolidLine,
                            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath()
        if self.icon_name == "leaf":
            path.moveTo(cx - 7, cy + 4)
            path.cubicTo(cx - 9, cy - 4, cx + 1, cy - 10, cx + 7, cy - 7)
            path.cubicTo(cx + 8, cy - 1, cx + 2, cy + 7, cx - 7, cy + 4)
            painter.drawPath(path)
            painter.drawLine(QPointF(cx - 5, cy + 3), QPointF(cx + 5, cy - 6))
        elif self.icon_name == "balance":
            for x1, y1, x2, y2 in ((0, -8, 0, 6), (-7, -5, 7, -5), (-5, -5, -8, 1), (5, -5, 8, 1),
                                   (-4, 6, 4, 6), (-8, 1, -3, 1), (3, 1, 8, 1)):
                painter.drawLine(QPointF(cx + x1, cy + y1), QPointF(cx + x2, cy + y2))
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
            painter.drawEllipse(QPointF(cx, cy), 1.5, 1.5)
            for angle in (0, 120, 240):
                painter.save()
                painter.translate(cx, cy)
                painter.rotate(angle)
                blade = QPainterPath()
                blade.moveTo(0, -2)
                blade.cubicTo(1, -4, 7, -8, 6, -3)
                blade.cubicTo(5, -1, 2, 0, 0, 1)
                painter.drawPath(blade)
                painter.restore()
        painter.restore()


class CheckBox(QCheckBox):
    """A check box drawn from the tokens: a clear tick in both themes, no image files."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self):
        fm = self.fontMetrics()
        return QSize(SIZE["check"] + SPACE["sm"] + fm.horizontalAdvance(self.text()) + 4,
                     max(SIZE["check"], fm.height()) + SPACE["sm"])

    def minimumSizeHint(self):
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _ev):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = SIZE["check"]
        box = QRectF(0.5, (self.height() - side) / 2 + 0.5, side - 1, side - 1)
        enabled = self.isEnabled()
        if not enabled:
            p.setOpacity(0.5)
        if self.isChecked():
            p.setPen(QPen(qcolor("accent"), 1))
            p.setBrush(qcolor("accent"))
            p.drawRoundedRect(box, RADIUS["sm"], RADIUS["sm"])
            tick = QPainterPath()
            tick.moveTo(box.left() + side * 0.24, box.top() + side * 0.50)
            tick.lineTo(box.left() + side * 0.42, box.top() + side * 0.68)
            tick.lineTo(box.left() + side * 0.74, box.top() + side * 0.30)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(qcolor("on_accent"), 2, Qt.PenStyle.SolidLine,
                          Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(tick)
        else:
            hover = self.underMouse() and enabled
            p.setPen(QPen(qcolor("accent" if hover else "border_strong"), 1.5))
            p.setBrush(qcolor("control"))
            p.drawRoundedRect(box, RADIUS["sm"], RADIUS["sm"])
        p.setOpacity(1.0)
        p.setPen(qcolor("text" if enabled else "text_faint"))
        left = side + SPACE["sm"]
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight,
                                             max(0, self.width() - left))
        p.drawText(QRectF(left, 0, self.width() - left, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        p.end()


class ComboBox(QComboBox):
    """A combo box whose chevron is drawn in the theme's ink (a stylesheet arrow would need an image file)."""

    def paintEvent(self, ev):
        super().paintEvent(ev)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(QPen(qcolor("text_muted" if self.isEnabled() else "text_faint"), 1.6,
                      Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        cx, cy = self.width() - 15.0, self.height() / 2.0 + 0.5
        path = QPainterPath(QPointF(cx - 4, cy - 2))
        path.lineTo(QPointF(cx, cy + 2))
        path.lineTo(QPointF(cx + 4, cy - 2))
        p.drawPath(path)
        p.end()


def pill(text, orange=False, checkable=True):
    """A small toggle button; checked fills with the accent (orange: the custom accent)."""
    b = QPushButton(text)
    b.setObjectName("pillOrange" if orange else "pill")
    b.setCheckable(checkable)
    b.setFixedHeight(SIZE["control_h"])
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return b


class _Note(QLabel):
    """A label that takes no room while it has nothing to say."""

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setVisible(bool(text))

    def setText(self, text):
        super().setText(text)
        self.setVisible(bool(text))


class Panel(QFrame):
    """One section of a window.

    A card: a head row (`title`, any head widgets, the grey `right` note, the
    bold `value`) and then whatever the caller adds to `body`.

    `inline=True` makes a one-line setting for a list of settings instead:
    title, `body` and `value` share the row, and the `right` note goes on its
    own line underneath when it has text. Inline panels have no frame of their
    own; put them in a `card()` with `divider()`s between them.
    """

    def __init__(self, title, parent=None, inline=False):
        super().__init__(parent)
        self.setObjectName("row" if inline else "card")
        outer = QVBoxLayout(self)
        m = SPACE["md"]
        v = SPACE["sm"] - 2 if inline else m - 2
        outer.setContentsMargins(m + 2, v, m + 2, v)
        outer.setSpacing(SPACE["xs"] if inline else SPACE["sm"])
        head = QHBoxLayout()
        head.setSpacing(SPACE["sm"])
        self.title = QLabel(title)
        self.title.setObjectName("title")
        head.addWidget(self.title)
        self.value = QLabel("") if inline else _Note()
        self.value.setObjectName("value")
        self.value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.head = head
        self.body = QVBoxLayout()
        self.body.setSpacing(SPACE["sm"])
        if inline:
            self.title.setMinimumWidth(SIZE["row_label_w"])
            self.value.setMinimumWidth(SIZE["row_value_w"])
            self.right = _Note()
            self.right.setObjectName("note")
            self.right.setWordWrap(True)
            head.addLayout(self.body, 1)
            head.addWidget(self.value)
            outer.addLayout(head)
            outer.addWidget(self.right)
        else:
            head.addStretch(1)
            self.right = _Note()
            self.right.setObjectName("dim")
            self.right.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            head.addWidget(self.right)
            head.addWidget(self.value)
            outer.addLayout(head)
            outer.addLayout(self.body)

    def add_head_widget(self, w):
        """Put a widget (pill button, combo) in the head row, before the note and the value."""
        self.head.insertWidget(self.head.indexOf(self.right), w)


class StatTile(QFrame):
    """One live reading: small caption, large value, small note underneath."""

    def __init__(self, caption, parent=None):
        super().__init__(parent)
        self.setObjectName("stat")
        col = QVBoxLayout(self)
        col.setContentsMargins(SPACE["md"], SPACE["sm"] - 2, SPACE["md"], SPACE["sm"] - 2)
        col.setSpacing(0)
        self.caption = QLabel(caption)
        self.caption.setObjectName("caption")
        self.value = QLabel("--")
        self.value.setObjectName("stat")
        self.note = QLabel("")
        self.note.setObjectName("tip")
        for w in (self.caption, self.value, self.note):
            col.addWidget(w)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def set(self, value, note=""):
        self.value.setText(value)
        self.note.setText(note)


def card(*widgets, horizontal=False):
    """A card frame holding `widgets` edge to edge (inline Panels, StatTiles, dividers)."""
    frame = QFrame()
    frame.setObjectName("card")
    lay = QHBoxLayout(frame) if horizontal else QVBoxLayout(frame)
    lay.setContentsMargins(1, SPACE["xs"], 1, SPACE["xs"])
    lay.setSpacing(0)
    for w in widgets:
        lay.addWidget(w)
    return frame


def divider(vertical=False):
    """A one-pixel line between the parts of a card."""
    f = QFrame()
    f.setObjectName("vdivider" if vertical else "divider")
    return f


def heading(text):
    """A window-level heading."""
    l = QLabel(text)
    l.setObjectName("heading")
    return l


def tip(text):
    l = QLabel(text)
    l.setObjectName("tip")
    l.setWordWrap(True)
    return l

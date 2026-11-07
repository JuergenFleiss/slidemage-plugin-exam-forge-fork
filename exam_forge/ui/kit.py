# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge's additions to the host's kit: accent tones, topic dots, small widgets, a dialog in the host's look, and
the shared wording of confirmations and check results."""
from math import ceil

from PySide6.QtCore import QEvent, QObject, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


def tone_colors(t):
    """Categorical accents plus the plugin's semantic tone names; red is load-only."""
    return {**t.accents, "red": t.accents["pink"], "amber": t.warning, "error": t.error}


def color(host, name):
    """A tone's current colour, for HTML that cannot carry a property."""
    return tone_colors(host.theme())[name]


def tone_sheet(t):
    """Colour tagged widgets and their text using the current host theme."""
    return "\n".join(f'*[accent="{name}"], *[accent="{name}"] QLabel {{ color: {value}; }}'
                     for name, value in tone_colors(t).items())


def mix(first, second, share):
    """`share` of colour `first` over `second`, for the painted tick's hover mark only."""
    a, b = QColor(first), QColor(second)
    return QColor(*(round(x * share + y * (1 - share)) for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))).name()


def window_sheet(t):
    """The window's stylesheet from the host's theme tokens `t`: the tones, then the window's own surfaces. Only text
    and line colours are picked here; every surface is the theme's."""
    return tone_sheet(t) + f"""
QFrame#efHeader {{ background: {t.surface}; border: none; border-bottom: 1px solid {t.line}; }}
QFrame#efFooter {{ border: none; border-top: 1px solid {t.line}; }}
QFrame#efAnswer {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: {t.radius_md}px; }}
QFrame#efAnswer[correct="true"] {{ background: {t.ok_bg}; border-color: {t.ok}; }}
QFrame#efAnswer QPlainTextEdit {{
    font-size: {t.font_control}px; background: transparent; color: {t.text}; border: 1px solid transparent;
    border-radius: {t.radius_sm}px; padding: 1px {t.space_xs}px; }}
QFrame#efAnswer QPlainTextEdit#efWhy {{ font-size: {t.font_body}px; color: {t.text_2}; }}
QFrame#efAnswer QPlainTextEdit:hover {{ border-color: {t.line}; }}
QFrame#efAnswer QPlainTextEdit:focus {{ background: {t.surface}; border-color: {t.focus}; }}
QFrame#efAnswer QPlainTextEdit:disabled {{ border-color: transparent; }}
QFrame#efCard {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: {t.radius_md}px; }}
QFrame#efCard[selected="true"] {{ border-color: {t.text_3}; }}
QFrame#efThumb {{ background: {t.surface}; border: 1px solid {t.line}; border-radius: {t.radius_sm + 1}px; }}
QLabel#efThumbNumber {{ font-family: "{t.font_mono}"; font-size: {t.font_micro}px; color: {t.text_2}; }}
"""


def restyle(widget, name, value):
    """Set a property the stylesheet reads, and apply the stylesheet again to the widget and the labels in it."""
    widget.setProperty(name, value)
    for part in (widget, *widget.findChildren(QLabel)):
        part.style().unpolish(part)
        part.style().polish(part)
    return widget


def tone(widget, name=""):
    """Tag a widget with a named theme tone, or clear it with ""; a topic colour is a tone too."""
    return restyle(widget, "accent", name)


def passive(widget):
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return widget


class Mark(QLabel):
    """A small painted mark in its tone's colour: a topic's "dot", the "square" of All slides, or the dashed "ring" of
    No topic; "" draws nothing."""

    def __init__(self):
        super().__init__()
        self.shape = ""
        self.setFixedSize(14, 14)

    def paintEvent(self, event):
        if not self.shape:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = self.palette().color(QPalette.ColorRole.WindowText)
        box = QRectF(3, 3, 8, 8)
        if self.shape == "dot":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(box)
            return
        pen = QPen(colour, 1.5)
        if self.shape == "ring":
            pen.setStyle(Qt.PenStyle.DotLine)
        painter.setPen(pen)
        box = box.adjusted(0.75, 0.75, -0.75, -0.75)
        painter.drawRoundedRect(box, 2, 2) if self.shape == "square" else painter.drawEllipse(box)


def dot(topic=None, widget=None, shape=""):
    """A topic's coloured dot, or none without a topic; `shape` "square" or "ring" marks All slides or No topic. Pass
    `widget` to change an existing dot."""
    widget = widget or passive(Mark())
    widget.shape = "dot" if topic else shape
    widget.update()
    return tone(widget, topic.color if topic else "slate" if shape else "")


class Logo(QWidget):
    """Exam Forge's mark: a rounded square in the theme's inverse colour with three lines of text."""

    def __init__(self, host):
        super().__init__()
        self.host = host
        self.setFixedSize(30, 30)

    def paintEvent(self, event):
        t = self.host.theme()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(t.inverse))
        painter.drawRoundedRect(QRectF(0, 0, 30, 30), 8, 8)
        pen = QPen(QColor(t.on_inverse), 2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        for y, end in ((11, 21), (15, 17), (19, 19)):
            painter.drawLine(9, y, end, y)


class GrowingEdit(QPlainTextEdit):
    """A text field as high as its text, from `lines` up to `most` lines; beyond that it scrolls."""

    def __init__(self, text="", lines=1, most=12):
        super().__init__(text)
        self.lines, self.most = lines, most
        self.setTabChangesFocus(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self.fit)
        self.fit()

    def fit(self, *_):
        needed = self.document().documentLayout().documentSize().height()
        shown = min(max(needed, self.lines), self.most)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded if needed > self.most
                                        else Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        margins = self.contentsMargins()    # the stylesheet's border and padding, once polished
        self.setFixedHeight(ceil(shown * self.fontMetrics().lineSpacing() + 2 * self.document().documentMargin()
                                 + margins.top() + margins.bottom()))

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self.fit()

    def showEvent(self, event):
        super().showEvent(event)
        self.fit()


def named(widget, name):
    """`widget` under the object name window_sheet styles it by."""
    widget.setObjectName(name)
    return widget


def field(widget):
    """A text field in the design's frame."""
    return named(widget, "efField")


def section(host, text):
    """The host caption treatment over a group of rows."""
    return passive(host.Label(text, "caption"))


class Faint(QObject):
    """Keeps `button` faint until the pointer is over `row` or the button has the keyboard focus."""

    def __init__(self, row, button):
        super().__init__(row)
        self.row, self.button = row, button
        for widget in (row, button):
            widget.installEventFilter(self)
        button.set_faint(True)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.FocusIn):
            self.button.set_faint(False)
        elif event.type() in (QEvent.Type.Leave, QEvent.Type.FocusOut):
            self.button.set_faint(not (self.row.underMouse() or self.button.hasFocus()))
        return False


def slide_title(ref):
    """A slide's title to show: without LaTeX markup."""
    return ref.title_text or ref.title or ref.id_slug


def label(host, text="", *, role="body", wrap=False, clip=False):
    widget = host.Label(text, role)
    widget.setWordWrap(wrap)
    if clip:  # A long title is cut off by the column instead of widening it.
        widget.setMinimumWidth(0)
        widget.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
    return widget


def line(host, *parts):
    box = QHBoxLayout()
    box.setSpacing(host.theme().space_sm)
    for part in parts:
        box.addWidget(part)
    box.addStretch()
    return box


class Flow(QLayout):
    """Widgets in a line that wraps when it runs out of width, each at its full size: chips are never squeezed."""

    def __init__(self, spacing):
        super().__init__()
        self.items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, move=True)

    def sizeHint(self):
        """All on one line; heightForWidth says how high it is when it wraps."""
        hints = [item.sizeHint() for item in self.items]
        return QSize(sum(h.width() for h in hints) + self.spacing() * max(len(hints) - 1, 0),
                     max((h.height() for h in hints), default=0))

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def arrange(self, rect, move):
        """Place the items, or only measure them; returns the height they need."""
        x, y, line = rect.x(), rect.y(), 0
        for item in self.items:
            hint = item.sizeHint()
            if x > rect.x() and x + hint.width() > rect.right() + 1:
                x, y, line = rect.x(), y + line + self.spacing(), 0
            if move:
                item.setGeometry(QRect(x, y, hint.width(), hint.height()))
            x += hint.width() + self.spacing()
            line = max(line, hint.height())
        return y + line - rect.y()


def flow(host, *parts):
    box = Flow(host.theme().space_sm)
    for part in parts:
        box.addWidget(part)
    return box


class Box(QWidget):
    """A container that shows what is behind it: the stylesheet paints the page colour on a plain QWidget only."""


def row(host, lead=(), body=()):
    """A list row: leading widgets, then a column of lines."""
    widget = Box()
    outer = QHBoxLayout(widget)
    t = host.theme()
    outer.setContentsMargins(t.space_sm, t.space_xs, t.space_sm, t.space_xs)
    for part in lead:
        outer.addWidget(part)
    column = QVBoxLayout()
    column.setSpacing(t.space_2xs)
    for part in body:
        column.addLayout(part) if isinstance(part, QHBoxLayout) else column.addWidget(part)
    outer.addLayout(column, 1)
    return widget


def clear(layout):
    while layout.count():
        child = layout.takeAt(0)
        if child.widget():
            child.widget().hide()       # deleted later: not shown in the meantime
            child.widget().deleteLater()
        elif child.layout():
            clear(child.layout())


def scroll_area(widget):
    """A frameless vertical scroll area around `widget` that shows the card behind it."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QScrollArea.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # The stylesheet would paint the page colour on its viewport.
    scroll.setStyleSheet("QScrollArea, QScrollArea > QWidget { background: transparent; border: none; }")
    scroll.setWidget(widget)
    return scroll


class Fit(QObject):
    """Row heights follow the list's width, so wrapped text is never clipped."""

    def __init__(self, listw):
        super().__init__(listw)
        self.listw, self.width = listw, -1
        listw.viewport().installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Resize and obj.width() != self.width:
            self.refit()
        return False

    def refit(self):
        self.width = self.listw.viewport().width()
        width = self.width - 2 * self.listw.spacing()
        for index in range(self.listw.count()):
            entry = self.listw.item(index)
            widget = self.listw.itemWidget(entry)
            if widget is not None:
                layout = widget.layout()
                height = layout.totalHeightForWidth(width) if layout.hasHeightForWidth() else 0
                entry.setSizeHint(QSize(width, max(height, widget.sizeHint().height())))


class Switch(QWidget):
    """Two-option switch. A value outside the options shows as unselected but is kept."""
    changed = Signal()

    def __init__(self, host, options, clearable=False):
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.clearable, self._value = clearable, "" if clearable else options[0][1]
        self.control = host.SegmentedControl([(value, label) for label, value in options])
        self.buttons = dict(zip((value for _, value in options), self.control.buttons()))
        self.control.changed.connect(self.pick)
        row.addWidget(self.control)
        row.addStretch()
        self.sync()

    def sync(self):
        if self._value in self.buttons:
            self.control.set_current(self._value)
        else:
            self.control.clear_current()

    def value(self):
        return self._value

    def set_value(self, value):
        value = value or ""
        changed = value != self._value
        self._value = value
        self.sync()
        if changed:
            self.changed.emit()

    def pick(self, value):
        self.set_value("" if self.clearable and value == self._value else value)
        self.sync()


class Fold(QWidget):
    def __init__(self, title):
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        self.header = QToolButton()
        self.header.setText(title)
        self.header.setCheckable(True)
        self.header.setAutoRaise(True)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setArrowType(Qt.ArrowType.RightArrow)
        self.header.toggled.connect(self.set_open)
        self.content = Box()
        self.content.hide()
        self.box = QVBoxLayout(self.content)
        self.box.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.header)
        column.addWidget(self.content)

    def set_open(self, on):
        self.header.setChecked(on)
        self.content.setVisible(on)
        self.header.setArrowType(Qt.ArrowType.DownArrow if on else Qt.ArrowType.RightArrow)


def confirm(parent, title, text, *, warning=False):
    """Ask Yes or No, No by default; a warning is for a step that is risky or cannot be undone."""
    ask = QMessageBox.warning if warning else QMessageBox.question
    yes, no = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No
    return ask(parent, title, text, yes | no, no) == yes


def source_details(check):
    """One line for each of a question's sources that did not pass: "slide (view, language): status message"."""
    return [f"{s.source.slide_id} ({s.source.view}, {s.source.language}): {s.status} {s.message}".rstrip()
            for s in check.sources if s.status != "unchanged"]


def deck_language(host, deck):
    """The deck's language, or English when the host cannot tell; it only hints which language text is in."""
    try:
        return host.deck_language(deck)
    except Exception:  # noqa: BLE001 - any failure falls back
        return "en"


class Sheet(QObject):
    """A dialog in the host's look. The host makes the sheet, so it cannot be subclassed: this object owns one and
    answers for it; `body` is the layout to fill, and add_button adds to its footer."""

    DialogCode = QDialog.DialogCode

    def __init__(self, host, title, parent=None):
        self.sheet = host.ModalSheet(title, parent)
        super().__init__(self.sheet)
        font = self.sheet.font()
        font.setFamily(host.theme().font_sans)
        self.sheet.setFont(font)
        page = host.Surface()
        self.body = QVBoxLayout(page)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.sheet.add_tab(title, page)
        self.sheet.installEventFilter(self)

    def __getattr__(self, name):
        if name == "sheet":
            raise AttributeError(name)
        return getattr(self.sheet, name)

    def findChild(self, *args):
        return self.sheet.findChild(*args)

    def findChildren(self, *args):
        return self.sheet.findChildren(*args)

    def may_close(self):
        """False keeps the sheet open when the user closes it."""
        return True

    def eventFilter(self, obj, event):
        if obj is self.sheet and event.type() == QEvent.Type.Close and not self.may_close():
            event.ignore()
            return True
        return False

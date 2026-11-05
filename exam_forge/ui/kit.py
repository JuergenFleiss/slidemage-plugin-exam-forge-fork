# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge's additions to the host's kit: accent tones, topic dots, small widgets, a dialog in the host's look, and
the shared wording of confirmations and check results."""
from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

# Text colours, (light, dark). Violet: a slide changed. Amber: an AI draft. Error: a problem. Then one per topic colour
# (model.PALETTE), kept clear of both accents. Only text colour is explicit; surfaces inherit the theme.
TONES = {"violet": ("#7040ac", "#c4b5fd"), "amber": ("#8a5000", "#fbbf24"), "error": ("#a8231a", "#fca5a5"),
         "blue": ("#1d4ed8", "#60a5fa"), "green": ("#15803d", "#4ade80"), "red": ("#b91c1c", "#f87171"),
         "teal": ("#0f766e", "#2dd4bf"), "pink": ("#be185d", "#f472b6"), "slate": ("#475569", "#94a3b8")}


def color(host, name):
    """A tone's colour in the host's current theme, for HTML that cannot carry a property."""
    return TONES[name][host.theme().dark]


def tone_sheet(dark):
    """The window's stylesheet: each tone colours a tagged widget's text and the text inside it, such as a chip's.
    It outranks the host theme's application stylesheet, and the window's dialogs inherit it."""
    return "\n".join(f'*[accent="{name}"], *[accent="{name}"] QLabel {{ color: {colors[dark]}; }}'
                     for name, colors in TONES.items())


def tone(widget, name=""):
    """Tag a widget with a tone (see TONES), or clear it with ""; a topic colour is a tone too."""
    widget.setProperty("accent", name)
    for part in (widget, *widget.findChildren(QLabel)):
        part.style().unpolish(part)
        part.style().polish(part)
    return widget


def passive(widget):
    widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    return widget


def dot(topic=None, widget=None):
    """A topic's coloured dot, or none without a topic; pass `widget` to change an existing dot."""
    widget = widget or passive(QLabel())
    widget.setFixedWidth(14)
    widget.setText("●" if topic else "")
    return tone(widget, topic.color if topic else "")


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


def line(*parts):
    box = QHBoxLayout()
    box.setSpacing(6)
    for part in parts:
        box.addWidget(part)
    box.addStretch()
    return box


class Box(QWidget):
    """A container that shows what is behind it: the stylesheet paints the page colour on a plain QWidget only."""


def row(lead=(), body=()):
    """A list row: leading widgets, then a column of lines."""
    widget = Box()
    outer = QHBoxLayout(widget)
    outer.setContentsMargins(6, 4, 6, 4)
    for part in lead:
        outer.addWidget(part)
    column = QVBoxLayout()
    column.setSpacing(2)
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

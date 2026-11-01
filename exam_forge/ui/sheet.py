# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A dialog in the host's look. The host makes the sheet, so it cannot be subclassed: this object owns one and answers
for it; `body` is the layout to fill, and add_button adds to its footer."""
from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QDialog, QVBoxLayout


class Sheet(QObject):
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

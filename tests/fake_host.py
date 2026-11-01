# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small fake for the Host services used by Exam Forge; no Qt or SlideMage UI."""
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from slidemage.plugin_api import Deck, SlideRef, SlideSource


@dataclass
class FakeDeck:
    name: str
    path: Path
    text: str = ""
    groups: tuple[str, ...] = ()


@dataclass
class Command:
    title: str
    run: Callable[[Deck], None]
    enabled: Callable[[Deck | None], bool]


class FakeAiChat:
    def available(self):
        return False

    def request(self, request, done):
        raise AssertionError("No AI chat route configured")


class FakeHost:
    def __init__(self, root=None):
        self.commands: dict[str, Command] = {}
        self.menus: dict[str, list[str]] = {}
        self.messages: list[tuple[str, bool]] = []
        self.chat = FakeAiChat()
        self._active = True
        self.root = Path(root) if root is not None else Path.cwd()
        self.output_folders: dict[tuple[Path, str], Path] = {}
        self.slides: dict[tuple[Path, str, str], list[SlideRef]] = {}
        self.sources: dict[SlideRef, SlideSource] = {}
        self.opened_slides: list[SlideRef] = []

    def finish_activation(self):
        self._active = False

    def add_command(self, id, title, run, enabled=lambda deck: True):
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        if id in self.commands:
            raise ValueError(f"command {id!r} is registered twice")
        self.commands[id] = Command(title, run, enabled)

    def add_menu_entry(self, menu, command_id):
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        if menu != "deck" or command_id not in self.commands:
            raise ValueError(f"unknown menu {menu!r} or command {command_id!r}")
        self.menus.setdefault(menu, []).append(command_id)

    def ai_chat(self):
        return self.chat

    def show_message(self, text, error=False):
        self.messages.append((text, error))

    def run_command(self, command_id, deck):
        command = self.commands[command_id]
        if not command.enabled(deck):
            raise RuntimeError(f"{command_id} is not enabled")
        command.run(deck)

    def project_root(self) -> Path:
        return self.root

    def deck_output_folder(self, deck: Deck, view: str = "instructor") -> Path:
        if view not in ("instructor", "during-course", "after-course"):
            raise ValueError(f"unknown view: {view}")
        return self.output_folders.get((deck.path, view), self.root / "pdf")

    def deck_slides(self, deck: Deck, view: str, language: str) -> list[SlideRef]:
        if view not in ("instructor", "during-course", "after-course") or language not in ("de", "en"):
            raise ValueError("unknown view or language")
        return list(self.slides.get((deck.path, view, language), []))

    def slide_source(self, ref: SlideRef) -> SlideSource:
        return self.sources[ref]

    def open_slide(self, ref: SlideRef) -> None:
        self.opened_slides.append(ref)

    def settings(self):
        if not hasattr(self, "_settings"):
            self._settings = FakeSettings()
        return self._settings

    def add_settings_tab(self, title, build):
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        self.settings_tabs = getattr(self, "settings_tabs", {})
        self.settings_tabs[title] = build

    def add_sidebar_box(self, key, title, build, size="compact", when=lambda deck: True):
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        if not hasattr(self, "boxes"):
            self.boxes = {}
        self.boxes[key] = (title, build, size, when)

    def refresh_sidebar_box(self, key):
        self.refreshed = getattr(self, "refreshed", []) + [key]

    def focus(self):
        from slidemage.plugin_api import Focus
        return getattr(self, "current_focus", Focus(None, None, "slide"))

    def deck_language(self, deck):
        return "en"

    def run_in_background(self, work, done):
        try:
            result = work()
        except BaseException as exc:  # noqa: BLE001 - deliver errors like the real host worker
            done(None, exc)
        else:
            done(result, None)

    def Page(self):
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        page = QWidget()
        page.body = QVBoxLayout(page)
        return page

    def Field(self, label, widget):
        from PySide6.QtWidgets import QLabel
        page = self.Page()
        page.body.addWidget(QLabel(label))
        page.body.addWidget(widget)
        return page

    def Button(self, text, kind="secondary", *, compact=False):
        from PySide6.QtWidgets import QPushButton
        return QPushButton(text)

    def Chip(self, text, tone="neutral"):
        from PySide6.QtWidgets import QHBoxLayout, QLabel, QWidget

        class Chip(QWidget):
            """The host Chip's API: a widget with set_text and set_tone, not a QLabel."""

            def __init__(self):
                super().__init__()
                self.tone = tone
                self.label = QLabel(text, self)
                QHBoxLayout(self).addWidget(self.label)

            def text(self):
                return self.label.text()

            def set_text(self, value):
                self.label.setText(value)

            def set_tone(self, value):
                self.tone = value

        return Chip()


class FakeSettings:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        pass

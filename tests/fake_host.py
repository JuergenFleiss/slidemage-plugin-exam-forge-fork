# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small fake for the Host services used by Exam Forge, and helpers that fill it and the window.

Its widgets are the host's real kit, so tests see what users get."""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from threading import get_ident
from uuid import uuid4

from PySide6.QtCore import Qt
from slidemage.plugin_api import Deck, Focus, SlideRef, SlideSource

KEY = Qt.ItemDataRole.UserRole


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


class FakeSettings:
    def __init__(self):
        self.data, self.saves = {}, 0

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value

    def save(self):
        self.saves += 1


class FakeHost:
    def __init__(self, root=None):
        self.commands: dict[str, Command] = {}
        self.menus: dict[str, list[str]] = {}
        self.messages: list[tuple[str, bool]] = []
        self.toasts: list[str] = []
        self.chat = FakeAiChat()
        self._active = True
        self.root = Path(root) if root is not None else Path.cwd()
        self.output_folders: dict[tuple[Path, str], Path] = {}
        self.slides: dict[tuple[Path, str, str], list[SlideRef]] = {}
        self.sources: dict[SlideRef, SlideSource] = {}
        self.opened_slides: list[SlideRef] = []
        self.settings_tabs, self.boxes = {}, {}
        self.current_focus = Focus(None, None, "slide")
        self._settings = FakeSettings()
        self.dark, self.theme_listeners = False, []

    def finish_activation(self):
        self._active = False

    def _registering(self):
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")

    def add_command(self, id, title, run, enabled=lambda deck: True):
        self._registering()
        if id in self.commands:
            raise ValueError(f"command {id!r} is registered twice")
        self.commands[id] = Command(title, run, enabled)

    def add_menu_entry(self, menu, command_id):
        self._registering()
        if menu != "deck" or command_id not in self.commands:
            raise ValueError(f"unknown menu {menu!r} or command {command_id!r}")
        self.menus.setdefault(menu, []).append(command_id)

    def add_settings_tab(self, title, build):
        self._registering()
        self.settings_tabs[title] = build

    def add_sidebar_box(self, key, title, build, size="compact", when=lambda deck: True):
        self._registering()
        self.boxes[key] = (title, build, size, when)

    def refresh_sidebar_box(self, key):
        pass

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
        return self._settings

    def focus(self):
        return self.current_focus

    def deck_language(self, deck):
        return "en"

    def project_languages(self):
        return ["en", "de"]

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

    def Chip(self, text, tone="neutral", *, icon=None, elide=False, pressable=False):
        return _kit().Chip(text, tone, icon, pressable=pressable, elide=elide)

    def caption(self, text, tone=""):
        return _kit().caption(text, tone)

    def Surface(self):
        return _kit().Surface()

    def IconLabel(self, icon, size):
        return _kit().IconLabel(icon, size)

    def IconButton(self, icon, tooltip):
        return _kit().IconButton(icon, tooltip)

    def Window(self, title):
        return _kit().ToolWindow(title)

    def Card(self, raised=False):
        return _kit().Card(raised=raised)

    def SegmentedControl(self, items, current=None):
        return _kit().SegmentedControl(items, current=current, roving=True)

    def Badge(self, text, tone="neutral"):
        return _kit().Badge(text, tone)

    def RowButton(self, title, sub="", *, icon=None, chip=("", ""), selectable=False):
        return _kit().RowButton(icon, title, sub, chip=chip, selectable=selectable, elide_title=True)

    def PlainList(self):
        return _kit().PlainList()

    def Label(self, text, role="body"):
        return _kit().text_label(text, role)

    def ModalSheet(self, title, parent=None):
        return _kit().ModalSheet(title, parent)

    def toast(self, parent, text):
        self.toasts.append(text)

    def theme(self):
        from slidemage.plugins.host import _theme_tokens
        return _theme_tokens(self.dark)

    def on_theme_changed(self, callback):
        self.theme_listeners.append(callback)
        return lambda: self.theme_listeners.remove(callback)

    def set_dark(self, dark):
        """Switch the theme, as the user does in SlideMage."""
        self.dark = dark
        for callback in list(self.theme_listeners):
            callback(self.theme())


def _kit():
    from slidemage.ui.kit import widgets
    return widgets


class GuiHost(FakeHost):
    """Queues work, runs it on a real worker thread on drain(), and calls done on the UI thread."""

    def __init__(self, root):
        super().__init__(root)
        self.pending, self.ui_thread, self.read_threads = [], get_ident(), []

    def run_in_background(self, work, done):
        assert get_ident() == self.ui_thread
        self.pending.append((work, done))

    def drain(self):
        from PySide6.QtWidgets import QApplication
        while self.pending:
            work, done = self.pending.pop(0)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(work)
                error = future.exception()
            assert get_ident() == self.ui_thread
            done(None if error else future.result(), error)
        QApplication.processEvents()

    def deck_slides(self, deck, view, language):
        assert get_ident() != self.ui_thread
        self.read_threads.append(get_ident())
        self.last_deck_text = deck.text
        return super().deck_slides(deck, view, language)

    def show_message(self, text, error=False):
        assert get_ident() == self.ui_thread
        super().show_message(text, error)


class RecordingChat:
    """An available AI route that records requests; the test answers through `done`."""

    def __init__(self, host):
        self.host, self.requests, self.cancelled = host, [], 0

    def available(self):
        return True

    def request(self, request, done):
        assert get_ident() == self.host.ui_thread
        self.requests.append(request)
        self.done = done

        def cancel():
            self.cancelled += 1
        return cancel


def enable_ai(host):
    """Give the host an AI route and tick AI support in the settings; returns the chat and the settings page."""
    host.chat = RecordingChat(host)
    page = host.settings_tabs["Exam Forge"]()
    page.ai_checkbox.setChecked(True)
    return host.chat, page


def activate(host):
    """Activate Exam Forge on the host and return its window controller."""
    import exam_forge
    exam_forge.activate(host)
    host.finish_activation()
    return host.commands["exam-forge.open"].run.__self__


def make_slides(host, deck, ids=("a", "b", "c", "d"), view="instructor"):
    refs = [SlideRef(id=i, id_slug=f"{i}-EN-Title", path=host.root / f"{i}.tex", language="en", language_read="en",
                     title=f"Title {i}", version="1-0-0", position=n) for n, i in enumerate(ids, 1)]
    host.slides[(deck.path, view, "en")] = refs
    for ref in refs:
        host.sources[ref] = SlideSource(text=f"Text of {ref.id}")
    return {ref.id: ref for ref in refs}


def source(host, deck):
    """The deck's only slide: s1, "Addition"."""
    ref = replace(make_slides(host, deck, ("s1",))["s1"], title="Addition")
    host.slides[(deck.path, "instructor", "en")] = [ref]
    host.sources[ref] = SlideSource(text="Two plus two = four")
    return ref


def add(bank, item_id, stem, links=(), status="draft", **fields):
    from exam_forge.model import Item, Option
    options = (Option(uuid4().hex, "Right", True), Option(uuid4().hex, "Wrong"))
    item = bank.create(Item(item_id, 1, stem, options, sources=tuple(links), **fields))
    for target in ("reviewed", "retired")[:("draft", "reviewed", "retired").index(status)]:
        item = bank.set_status(item_id, item.revision, target)
    return item


def fill(window, link=True):
    """Write and save a two-answer question in the open window, linked to slide s1."""
    from exam_forge.sources import link_slide
    window.questions.new_question()
    window.editor.stem.setPlainText("What is two plus two?")
    first, second = window.editor.rows
    first.text.setPlainText("4")
    first.tick.click()
    first.why.setPlainText("Two pairs.")
    second.text.setPlainText("3")
    window.editor.objective.setText("Add two numbers")
    if link:
        window.editor.links = (link_slide(window.host, source(window.host, window.deck), "instructor"),)
    assert window.editor.save()
    return window.editor.current


def revision(bank, item_id, number):
    """An earlier revision of a question, as its file holds it."""
    return bank._revisions(item_id)[number - 1]


def keys(listw):
    return [listw.item(i).data(KEY) for i in range(listw.count()) if listw.item(i).data(KEY) is not None]


def entry(listw, key):
    return next(listw.item(i) for i in range(listw.count()) if listw.item(i).data(KEY) == key)


def texts(widget):
    from PySide6.QtWidgets import QLabel
    return " | ".join(label.text() for label in widget.findChildren(QLabel))


def row_text(listw, key):
    return texts(listw.itemWidget(entry(listw, key)))


def pick(listw, key):
    listw.setCurrentItem(entry(listw, key))


def current_key(listw):
    return listw.currentItem().data(KEY) if listw.currentItem() else None

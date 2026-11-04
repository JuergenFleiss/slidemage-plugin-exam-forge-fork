# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""One reachable top-level window per deck; sidebar widgets have shorter lives."""
from pathlib import Path

from PySide6.QtWidgets import QLabel

from ..bank import Bank, BankError
from ..folder import remembered_folder
from ..sources import DeckSnapshot, fresh_deck, link_slide
from .first_use import change_bank, choose_bank
from .window import BankWindow

SIDEBAR_KEY = "bank"


class Controller:
    def __init__(self, host):
        self.host = host
        self.windows = {}
        self.counts = {}
        self.loading = set()

    @staticmethod
    def key(deck):
        return Path(deck.path).resolve()

    def publish(self, deck, items):
        self.counts[self.key(deck)] = (len(items), sum(i.status == "draft" for i in items),
                                       sum(i.status == "reviewed" for i in items),
                                       sum(i.status == "retired" for i in items),
                                       sum(i.source_changed for i in items))
        self.host.refresh_sidebar_box(SIDEBAR_KEY)

    def refresh_ai_support(self):
        for window in self.windows.values():
            window.refresh_ai_support()

    def open_deck(self):
        """The deck open in SlideMage, or None. Settings gets no deck, and a sidebar box is built only when shown."""
        path = self.host.focus().deck
        if path is None:
            return None
        window = self.windows.get(Path(path).resolve())
        return window.deck if window is not None else fresh_deck(DeckSnapshot(Path(path).stem, Path(path), "", ()))

    def bank_folder(self, deck):
        window = self.windows.get(self.key(deck))
        return window.bank.folder if window is not None else remembered_folder(self.host, deck)

    def answer_rules_changed(self, deck):
        window = self.windows.get(self.key(deck)) if deck is not None else None
        if window is not None:
            window.answer_rules_changed()

    def change_folder(self, deck, parent=None):
        """Point this deck at another bank folder; True when it changed."""
        key = self.key(deck)
        window = self.windows.get(key)
        if window is not None and window.busy:
            self.host.show_message("Wait for the current operation to finish.", error=True)
            return False
        try:
            changed = change_bank(self.host, deck, self.bank_folder(deck), parent,
                                  window.may_leave if window is not None else lambda: True)
        except (OSError, BankError, ValueError) as exc:
            self.host.show_message(str(exc), error=True)
            return False
        if changed is None:
            return False
        bank, message = changed
        if window is not None:
            window.switch_bank(bank)
        else:
            self.counts.pop(key, None)
            self.host.refresh_sidebar_box(SIDEBAR_KEY)
        self.host.show_message(message)
        return True

    def open(self, deck):
        key = self.key(deck)
        window = self.windows.get(key)
        if window is None:
            try:
                bank = choose_bank(self.host, deck)
                if bank is None:
                    return None
                window = BankWindow(self.host, deck, bank, lambda items: self.publish(deck, items))
                self.windows[key] = window
            except (OSError, BankError, ValueError) as exc:
                self.host.show_message(str(exc), error=True)
                return None
        else:
            if not window.bank.folder.is_dir() or not (window.bank.folder / "bank.json").is_file():
                try:
                    if window.busy:
                        return None
                    bank = choose_bank(self.host, deck)
                    if bank is None or not window.may_leave():
                        return None
                    window.switch_bank(bank)
                except (OSError, BankError, ValueError) as exc:
                    self.host.show_message(str(exc), error=True)
                    return None
            window.deck = deck
            # Reopening also rechecks the bank without replacing unsaved editor content.
            window.check_sources()
        window.show()
        window.raise_()
        window.activateWindow()
        return window

    def sidebar(self, deck):
        page = self.host.Page()
        counts = self.counts.get(self.key(deck))
        if counts is None:
            folder = remembered_folder(self.host, deck)
            label = "Choose a private bank folder to begin." if folder is None else "Loading bank counts…"
            key = self.key(deck)
            if folder is not None and key not in self.loading:
                self.loading.add(key)

                def work():
                    return Bank(folder).load()

                def done(result, error):
                    self.loading.discard(key)
                    if error:
                        # Cache the error to avoid repeated rebuilds starting the same failed read.
                        self.counts[key] = str(error)
                        self.host.refresh_sidebar_box(SIDEBAR_KEY)
                    else:
                        items, errors = result
                        if errors:
                            self.host.show_message("\n".join(map(str, errors)), error=True)
                        self.publish(deck, items)
                self.host.run_in_background(work, done)
        elif isinstance(counts, str):
            label = counts
        else:
            total, draft, reviewed, retired, changed = counts
            label = f"{total} questions · {draft} draft · {reviewed} reviewed · {retired} retired\n{changed} Source changed"
        text = QLabel(label)
        text.setWordWrap(True)
        page.body.addWidget(text)
        forge = self.host.Button("Open Exam Forge", compact=True)
        forge.clicked.connect(lambda: self.open(deck))
        page.body.addWidget(forge)
        write = self.host.Button("Write a question for this slide…", compact=True)
        write.clicked.connect(lambda: self.write_for_focus(deck))
        page.body.addWidget(write)
        return page

    def write_for_focus(self, deck):
        focus = self.host.focus()  # Read on click, never capture focus while building the box.
        if focus.slide is None:
            self.host.show_message("No slide is in focus. Open a slide first.")
            return
        if focus.deck != deck.path:
            self.host.show_message("The focused slide belongs to a different deck. Open its Exam Forge box first.")
            return
        window = self.open(deck)
        if window is None:
            return
        path = focus.slide.resolve()

        def resolved(link, error):
            if error:
                window.error(error)
                return

            window.when_idle(lambda: window.start_question(link))

        def prepared(snapshot, error):
            if error:
                window.error(error)
                return
            language = self.host.deck_language(snapshot)

            def work():
                refs = self.host.deck_slides(snapshot, "instructor", language)
                matches = [ref for ref in refs if ref.path.resolve() == path]
                if len({(ref.id, ref.version) for ref in matches}) != 1:
                    raise ValueError("The focused file could not be resolved to one slide in this deck.")
                return link_slide(self.host, matches[0], "instructor")
            self.host.run_in_background(work, resolved)

        self.host.run_in_background(lambda: fresh_deck(deck), prepared)

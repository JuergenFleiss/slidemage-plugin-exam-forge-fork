# SPDX-FileCopyrightText: 2026 Stefan Palan, Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""A small fake for the Host services used by Exam Forge; no Qt or SlideMage UI."""
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from slidemage.plugin_api import Deck, ServiceInfo, SlideRef, SlideSource


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


class FakeHost:
    def __init__(self, root=None):
        self.commands: dict[str, Command] = {}
        self.menus: dict[str, list[str]] = {}
        self.messages: list[tuple[str, bool]] = []
        self.services: dict[tuple[str, int], tuple[object, str]] = {}
        self._active = True
        self.root = Path(root) if root is not None else Path.cwd()
        self.output_folders: dict[tuple[Path, str], Path] = {}
        self.slides: dict[tuple[Path, str, str], list[SlideRef]] = {}
        self.sources: dict[SlideRef, SlideSource] = {}
        self.opened_slides: list[SlideRef] = []
        self.opened_files: list[Path] = []

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

    def add_service(self, name, version, implementation, label=""):
        if not self._active:
            raise RuntimeError("registrations are only allowed in activate()")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ValueError(f"service version {version!r} must be an integer from 1")
        if (name, version) in self.services:
            raise ValueError(f"service {name!r} version {version} is already registered")
        self.services[(name, version)] = (implementation, label)

    def offer(self, name, version, implementation, label=""):
        """Stand in for another plugin's service, so a test can give the plugin something to find."""
        self.services[(name, version)] = (implementation, label)

    def service(self, name, version=1):
        """The implementation itself where the real host gives a proxy: a ServiceError raised by it passes as it is."""
        if self._active:
            raise RuntimeError("service() is for after activate() has returned")
        found = self.services.get((name, version))
        return None if found is None else found[0]

    def service_providers(self, name):
        if self._active:
            raise RuntimeError("service_providers() is for after activate() has returned")
        return [ServiceInfo(name, version, "fake", label) for (n, version), (_, label) in sorted(self.services.items())
                if n == name]

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

    def open_file(self, path: Path) -> None:
        path = Path(path)
        path = path if path.is_absolute() else self.root / path
        if not path.exists():
            raise FileNotFoundError(path)
        self.opened_files.append(path)

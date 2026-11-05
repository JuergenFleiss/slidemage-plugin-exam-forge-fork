# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The window end to end: revisions, source checks, images, Moodle export and the bank folder."""
from dataclasses import replace
from pathlib import Path
import shutil
from xml.etree import ElementTree as ET

from fake_host import GuiHost, activate, fill, revision, source
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QLabel, QMessageBox, QPushButton
from slidemage.plugin_api import Focus, SlideSource

from exam_forge.bank import Bank
from exam_forge.model import Item
from exam_forge.ui.editor import png_bytes


def png(color="red"):
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return png_bytes(image)


def test_editing_a_reviewed_question_creates_a_draft_revision_with_its_image(env, monkeypatch, tmp_path):
    host, _, bank, _, window = env
    item = fill(window)
    window.editor.approve_button.click()
    assert window.busy
    host.drain()
    reviewed = window.editor.current
    assert reviewed.status == "reviewed"
    image_path = tmp_path / "figure.png"
    image_path.write_bytes(png())
    monkeypatch.setattr("exam_forge.ui.editor.QFileDialog.getOpenFileName", lambda *a: (str(image_path), ""))
    window.editor.attach_image()
    assert "describe every image" in window.editor.status_line.text()
    window.editor.image_rows[0].alt.setText("A red square")
    window.editor.save_button.click()
    edited = window.editor.current
    assert edited.status == "draft" and edited.revision == reviewed.revision + 1
    assert bank.read_media(edited.media[0]) == image_path.read_bytes()
    assert revision(bank, item.id, reviewed.revision) == reviewed and not reviewed.media


def test_conflict_keeps_every_field(env):
    host, _, bank, _, window = env
    item = fill(window)
    window.editor.stem.setPlainText("My unsaved text")
    window.editor.rows[1].why.setPlainText("Typed rationale")
    window.editor.difficulty.set_value("hard")
    bank.save(replace(item, stem="Their edit"))
    assert not window.editor.save() and "stale revision" in host.messages[-1][0]
    assert window.editor.stem.toPlainText() == "My unsaved text" and window.editor.rows[1].why.toPlainText() == "Typed rationale"
    assert window.editor.difficulty.value() == "hard" and bank.get(item.id).stem == "Their edit"


def test_source_check_on_open_check_and_before_approval(env, monkeypatch):
    host, deck, bank, controller, window = env
    item = fill(window)
    ref = source(host, deck)
    host.sources[ref] = SlideSource(text="Changed addition")
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: notices.append(a[2]) or QMessageBox.StandardButton.No)
    window.editor.approve_button.click()
    host.drain()
    assert bank.get(item.id).status == "draft" and bank.get(item.id).source_changed
    assert window.editor.source_banner.isVisible()
    assert "What is two plus two?" in notices[0] and "Changed addition" not in notices[0]  # No raw TeX.
    sidebar = controller.sidebar(deck)
    assert "1 Source changed" in " ".join(l.text() for l in sidebar.findChildren(QLabel))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Yes)
    window.editor.approve_button.click()
    host.drain()
    assert window.editor.current.status == "reviewed" and window.editor.current.source_changed
    host.sources[ref] = SlideSource(text="Two plus two = four")
    window.check_button.click()
    host.drain()
    assert not bank.get(item.id).source_changed
    assert host.read_threads and all(t != host.ui_thread for t in host.read_threads)
    host.sources[ref] = SlideSource(text="Changed again")
    controller.open(deck)
    host.drain()
    assert bank.get(item.id).source_changed


def test_background_check_reads_the_fresh_deck_and_keeps_unsaved_fields(env):
    host, deck, _, _, window = env
    fill(window)
    deck.path.write_text("fresh deck contents")
    window.editor.stem.setPlainText("Still typing")
    window.check_sources()
    host.drain()
    assert host.last_deck_text == "fresh deck contents"
    assert window.editor.stem.toPlainText() == "Still typing" and window.editor.dirty()


def test_a_failed_check_or_an_edit_during_it_cannot_approve(env):
    host, deck, bank, _, window = env
    item = fill(window)
    window.editor.change_status("reviewed")
    bank.save(replace(item, stem="Edited elsewhere during check"))
    host.drain()
    assert bank.get(item.id).status == "draft" and "changed during the check" in host.messages[-1][0]
    deck.path.unlink()
    window.check_sources()
    host.drain()
    assert not window.busy and host.messages[-1][1] and window.editor.stem.toPlainText() == item.stem
    assert window.notice.text() == "The source check did not finish."


def test_moodle_export_has_bank_media_and_leaves_out_exam_draft_and_retired(env, tmp_path, monkeypatch):
    host, _, bank, _, window = env
    item = fill(window)
    media = replace(bank.store_media("plot.png", b"picture bytes"), alt="A plot")
    window.editor.attachments = (media,)
    assert window.editor.save()
    window.editor.approve_button.click()
    host.drain()
    bank.create(Item("exam", 1, "PRIVATE EXAM", item.options, use="exam"))
    bank.set_status("exam", 1, "reviewed")
    bank.create(Item("draft", 1, "DRAFT", item.options))
    bank.create(Item("retired", 1, "RETIRED", item.options))
    bank.set_status("retired", 1, "reviewed")
    bank.set_status("retired", 2, "retired")
    window.reload()
    path = tmp_path / "export.xml"
    monkeypatch.setattr("exam_forge.ui.export_menu.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    window.export_menu.export()
    host.drain()
    xml = path.read_bytes()
    root = ET.fromstring(xml)
    assert len(root) == 1 and root[0].find("questiontext/file").attrib["name"] == media.name
    assert b"PRIVATE" not in xml and b"DRAFT" not in xml and b"RETIRED" not in xml
    (bank.folder / "media" / media.name).write_bytes(b"changed")  # A hash failure keeps the old export.
    window.export_menu.export()
    host.drain()
    assert path.read_bytes() == xml and "hash mismatch" in host.messages[-1][0]


def test_export_rechecks_the_bank_and_the_slides(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    item = fill(window)
    window.editor.approve_button.click()
    host.drain()
    path = tmp_path / "out.xml"
    monkeypatch.setattr("exam_forge.ui.export_menu.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    host.sources[source(host, deck)] = SlideSource(text="Changed after list loaded")
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: notices.append(a[2]) or QMessageBox.StandardButton.No)
    window.export_menu.export()
    host.drain()
    assert notices and item.stem in notices[0] and bank.get(item.id).source_changed and not path.exists()
    bank.save(replace(bank.get(item.id), stem="Changed elsewhere", status="draft", use="exam"))
    window.export_menu.export()
    host.drain()
    assert not path.exists() and "No reviewed" in host.messages[-1][0]


# -- the bank folder ----------------------------------------------------------------------------------------


def test_a_missing_bank_asks_for_its_new_folder_and_keeps_the_editor(env, tmp_path, monkeypatch):
    host, deck, bank, controller, window = env
    fill(window)
    window.editor.stem.setPlainText("Unsaved")
    (bank.folder / "bank.json").unlink()
    monkeypatch.setattr("exam_forge.ui.controller.QFileDialog.getExistingDirectory", lambda *a: "")
    assert controller.open(deck) is None and window.editor.stem.toPlainText() == "Unsaved"
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    existing = Bank(relocated, create=True)
    host = GuiHost(tmp_path / "other")
    host.root.mkdir()
    host.settings().set("bank_folders", {str(deck.path.resolve()): str(tmp_path / "missing")})
    monkeypatch.setattr("exam_forge.ui.controller.QFileDialog.getExistingDirectory", lambda *a: str(relocated))
    other = activate(host).open(deck)
    host.drain()
    assert other.bank.id == existing.id
    assert host.settings().get("bank_folders")[str(deck.path.resolve())] == str(relocated)
    other.shell.hide()
    other.shell.deleteLater()


def change_folder(host, deck, monkeypatch, folder):
    monkeypatch.setattr("exam_forge.ui.controller.QFileDialog.getExistingDirectory", lambda *a: str(folder))
    host.current_focus = Focus(deck.path, None, "slide")
    page = host.settings_tabs["Exam Forge"]()
    next(b for b in page.findChildren(QPushButton) if b.text() == "Change folder…").click()
    host.drain()


def test_changing_folder_copies_only_the_bank_and_leaves_the_old_one(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    item = fill(window)
    media = replace(bank.store_media("plot.png", png()), alt="A plot")
    window.editor.attachments = (media,)
    window.editor.save()
    (bank.folder / "unrelated.txt").write_text("not the bank's")
    (bank.folder / "1.manifest.json").write_text("{}")
    before = sorted(p.name for p in bank.folder.iterdir())
    target = tmp_path / "moved"
    target.mkdir()
    change_folder(host, deck, monkeypatch, target)
    assert window.bank.folder == target and window.bank.read_media(window.bank.get(item.id).media[0]) == png()
    assert (target / "1.manifest.json").exists() and not (target / "unrelated.txt").exists()
    assert sorted(p.name for p in bank.folder.iterdir()) == before
    assert host.settings().get("bank_folders")[str(deck.path.resolve())] == str(target)


def test_a_folder_with_a_bank_is_used_only_after_asking(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    other = tmp_path / "other"
    other.mkdir()
    existing = Bank(other, create=True)
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr("exam_forge.ui.kit.QMessageBox.question", lambda *a: answers.pop(0))
    change_folder(host, deck, monkeypatch, other)
    assert window.bank.folder == bank.folder
    change_folder(host, deck, monkeypatch, other)
    assert window.bank.id == existing.id


def test_a_refused_unsaved_question_or_a_failed_copy_keeps_the_folder(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    fill(window)
    target = tmp_path / "moved"
    target.mkdir()
    window.editor.stem.setPlainText("Edited")
    monkeypatch.setattr("exam_forge.ui.editor.QMessageBox.question", lambda *a: QMessageBox.StandardButton.Cancel)
    change_folder(host, deck, monkeypatch, target)
    assert window.bank.folder == bank.folder and window.editor.stem.toPlainText() == "Edited"
    window.editor.save()
    real_copy = shutil.copy2

    def failing(source, copy):
        if Path(source).name == "bank.json":
            raise OSError("disk full")
        return real_copy(source, copy)

    monkeypatch.setattr("exam_forge.folder.shutil.copy2", failing)
    change_folder(host, deck, monkeypatch, target)
    assert host.messages[-1] == ("disk full", True) and not any(target.iterdir())
    assert window.bank.folder == bank.folder

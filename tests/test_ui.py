# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real offscreen Qt widgets; workers execute on a separate thread, done on the UI thread."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import shutil
from threading import get_ident
from xml.etree import ElementTree as ET

import pytest
from fake_host import FakeDeck, FakeHost
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtCore import QMimeData, Qt, QUrl
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton
from slidemage.plugin_api import Focus, SlideRef, SlideSource

import exam_forge
from exam_forge.bank import Bank
from exam_forge.folder import open_chosen_folder
from exam_forge.model import Item, Origin
from exam_forge.sources import link_slide
from exam_forge.ui.editor import png_bytes


class GuiHost(FakeHost):
    def __init__(self, root):
        super().__init__(root)
        self.pending = []
        self.ui_thread = get_ident()
        self.read_threads = []

    def run_in_background(self, work, done):
        assert get_ident() == self.ui_thread
        self.pending.append((work, done))

    def drain(self):
        while self.pending:
            work, done = self.pending.pop(0)
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(work)
                error = future.exception()
                result = None if error else future.result()
            assert get_ident() == self.ui_thread
            done(result, error)
        QApplication.processEvents()

    def deck_slides(self, deck, view, language):
        self.read_threads.append(get_ident())
        assert get_ident() != self.ui_thread
        return super().deck_slides(deck, view, language)

    def refresh_sidebar_box(self, key):
        assert get_ident() == self.ui_thread
        super().refresh_sidebar_box(key)

    def show_message(self, text, error=False):
        assert get_ident() == self.ui_thread
        super().show_message(text, error)


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def env(app, tmp_path):
    host = GuiHost(tmp_path / "project")
    host.root.mkdir()
    deck = FakeDeck("course", host.root / "course.tex")
    deck.path.write_text("old deck")
    bank_folder = tmp_path / "bank"
    bank_folder.mkdir()
    bank = open_chosen_folder(host, deck, bank_folder)
    exam_forge.activate(host)
    host.finish_activation()
    controller = host.commands["exam-forge.open"].run.__self__
    window = controller.open(deck)
    assert window.busy
    host.drain()
    yield host, deck, bank, controller, window
    host.drain()
    for window in controller.windows.values():
        window.hide()
        window.deleteLater()
    QApplication.processEvents()


def fill(window, link=True):
    window.new_question()
    window.stem.setPlainText("What is two plus two?")
    first, second = window.rows
    first.text.setPlainText("4")
    first.tick.click()
    first.why.setPlainText("Two pairs.")
    second.text.setPlainText("3")
    window.current = replace(window.current, topic="Arithmetic")
    window.objective.setText("Add two numbers")
    if link:
        window.links = (link_slide(window.host, source(window.host, window.deck), "instructor"),)
    assert window.save()
    return window.current


def source(host, deck):
    ref = SlideRef(id="s1", id_slug="s1-EN-Addition", path=host.root / "s1-EN-Addition.tex",
                   language="en", language_read="en", title="Addition", version="1-0-0", position=1)
    host.slides[(deck.path, "instructor", "en")] = [ref]
    host.sources[ref] = SlideSource(text="Two plus two = four")
    return ref


def test_create_edit_review_and_media_revisions(env, monkeypatch, tmp_path):
    host, _, bank, _, window = env
    item = fill(window)
    assert bank.get(item.id).topic == "Arithmetic"
    window.approve_button.click()
    assert window.busy
    host.drain()
    reviewed = window.current
    assert reviewed.status == "reviewed"
    image_path = tmp_path / "figure.png"
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(image_path))
    monkeypatch.setattr("exam_forge.ui.editor.QFileDialog.getOpenFileName", lambda *a: (str(image_path), ""))
    window.attach_image()
    assert "describe every image" in window.status_line.text()
    assert not window.image_rows[0].thumb.pixmap().isNull()
    window.image_rows[0].alt.setText("A red square")
    assert window.approve_button.isEnabled()
    window.save_button.click()
    edited = window.current
    assert edited.status == "draft" and edited.revision == reviewed.revision + 1
    assert edited.media[0].alt == "A red square"
    assert bank.read_media(edited.media[0]) == image_path.read_bytes()
    assert not bank.get(item.id, reviewed.revision).media
    assert bank.get(item.id, reviewed.revision).status == "reviewed"
    assert len(controller_items(window)) == 1


def controller_items(window):
    return window.bank.load()[0]


def test_conflict_keeps_every_field(env, monkeypatch):
    _, _, bank, _, window = env
    item = fill(window)
    window.stem.setPlainText("My unsaved text")
    window.rows[1].why.setPlainText("Typed rationale")
    window.difficulty.setCurrentData("hard")
    bank.save(replace(item, stem="Their edit"))
    assert not window.save()
    assert window.stem.toPlainText() == "My unsaved text"
    assert window.rows[1].why.toPlainText() == "Typed rationale"
    assert window.difficulty.currentData() == "hard"
    assert bank.get(item.id).stem == "Their edit"
    assert "stale revision" in window.notice.text()



def test_review_marker_on_open_check_now_and_before_approve_retire(env, monkeypatch):
    host, deck, bank, controller, window = env
    ref = source(host, deck)
    # link_slide is pure I/O, captured just as the picker captures it in a worker.
    with ThreadPoolExecutor() as executor:
        link = executor.submit(link_slide, host, ref, "instructor").result()
    item = fill(window)
    window.links = (link,)
    window.save()
    host.sources[ref] = SlideSource(text="Changed addition")
    notices = []
    def warn(*args):
        notices.append(args[2])
        return QMessageBox.StandardButton.No
    monkeypatch.setattr(QMessageBox, "warning", warn)
    window.approve_button.click()
    host.drain()
    assert bank.get(item.id).status == "draft"
    assert bank.get(item.id).source_changed
    assert window.source_banner.isVisible() and window.still_button.isVisible()
    assert "Changed addition" not in notices[0]  # report names the question and source, not raw TeX
    assert "What is two plus two?" in notices[0] and "s1" in notices[0]
    sidebar = controller.sidebar(deck)
    assert "1 Source changed" in " ".join(l.text() for l in sidebar.findChildren(QLabel))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: QMessageBox.StandardButton.Yes)
    window.approve_button.click()
    host.drain()
    assert window.current.status == "reviewed" and window.current.source_changed
    window.retire_button.click()
    host.drain()
    assert window.current.status == "retired"
    host.sources[ref] = SlideSource(text="Two plus two = four")
    window.check_button.click()
    host.drain()
    assert not bank.get(item.id).source_changed
    assert host.read_threads and all(t != host.ui_thread for t in host.read_threads)
    host.sources[ref] = SlideSource(text="Changed again")
    controller.open(deck)
    host.drain()
    assert bank.get(item.id).source_changed


def test_background_check_preserves_unsaved_fields_and_refreshes_deck_text(env):
    host, deck, _, _, window = env
    fill(window)
    window.stem.setPlainText("Still typing")
    window.objective.setText("Unsaved")
    deck.path.write_text("fresh deck contents")
    seen = []
    original = host.deck_slides
    def read(deck, *args):
        seen.append(deck.text)
        return original(deck, *args)
    host.deck_slides = read
    ref = source(host, deck)
    with ThreadPoolExecutor() as executor:
        window.links = (executor.submit(link_slide, host, ref, "instructor").result(),)
    window.save()
    window.stem.setPlainText("Still typing after saving")
    window.check_sources()
    host.drain()
    assert seen and set(seen) == {"fresh deck contents"}
    assert window.stem.toPlainText() == "Still typing after saving" and window.dirty()


def png(color="red"):
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    return png_bytes(image)


def test_pasted_and_dropped_images_join_the_question_and_text_still_pastes(env, tmp_path):
    *_, window = env
    fill(window)
    pasted = QMimeData()
    pasted.setImageData(QImage.fromData(png("red")))
    window.stem.insertFromMimeData(pasted)
    assert len(window.attachments) == 1
    path = tmp_path / "blue.png"
    path.write_bytes(png("blue"))
    dropped = QMimeData()
    dropped.setUrls([QUrl.fromLocalFile(str(path))])
    assert window.stem.canInsertFromMimeData(dropped)
    window.stem.insertFromMimeData(dropped)
    assert len(window.attachments) == 2
    both = QMimeData()
    both.setText("copied words")
    both.setImageData(QImage.fromData(png("green")))
    window.stem.insertFromMimeData(both)
    assert len(window.attachments) == 2 and "copied words" in window.stem.toPlainText()


def test_clipboard_button_and_unusable_images(env, tmp_path, monkeypatch):
    *_, window = env
    fill(window)
    errors = []
    monkeypatch.setattr(window, "error", errors.append)
    QApplication.clipboard().clear()
    window.paste_image()
    assert errors and not window.attachments
    QApplication.clipboard().setImage(QImage.fromData(png()))
    window.paste_button.click()
    assert len(window.attachments) == 1
    window.paste_button.click()
    assert len(window.attachments) == 1 and "already attached" in str(errors[-1])
    svg = tmp_path / "drawing.png"
    svg.write_bytes(b"<svg xmlns='http://www.w3.org/2000/svg'/>")
    window.add_image_file(str(svg))
    assert len(window.attachments) == 1 and "PNG, JPEG" in str(errors[-1])
    window.image_rows[0].remove.click()
    assert not window.attachments and not window.image_rows


def test_an_image_missing_from_the_bank_does_not_stop_the_editor(env):
    _, _, bank, _, window = env
    item = fill(window)
    media = bank.store_media("plot.png", png(), "A plot")
    window.attachments = (media,)
    window.save()
    (bank.folder / "media" / media.name).unlink()
    window.populate(bank.get(item.id))
    assert window.image_rows[0].thumb.text() == "Image missing"


def test_export_includes_bank_media_and_excludes_elp_draft_and_retired(env, tmp_path, monkeypatch):
    host, _, bank, _, window = env
    item = fill(window)
    media = bank.store_media("plot.png", b"picture bytes", "A plot")
    window.attachments = (media,)
    assert window.save()
    window.approve_button.click()
    host.drain()
    reviewed = window.current
    bank.create(Item("elp", 1, "PRIVATE ELP", item.options, use="elp"))
    bank.set_status("elp", 1, "reviewed")
    bank.create(Item("draft", 1, "DRAFT", item.options))
    bank.create(Item("retired", 1, "RETIRED", item.options))
    bank.set_status("retired", 1, "reviewed")
    bank.set_status("retired", 2, "retired")
    window.reload()
    path = tmp_path / "export.xml"
    path.write_text("old content")
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    window.export_button.click()
    window.export_menu.moodle_action.trigger()
    host.drain()
    xml = path.read_bytes()
    root = ET.fromstring(xml)
    assert len(root) == 1
    assert root[0].findtext("name/text") == f"{reviewed.id} r{reviewed.revision}"
    assert root[0].find("questiontext/file").attrib["name"] == media.name
    assert b"PRIVATE" not in xml and b"DRAFT" not in xml and b"RETIRED" not in xml
    # Hash failures leave an existing export intact.
    (bank.folder / "media" / media.name).write_bytes(b"changed")
    window.export()
    host.drain()
    assert path.read_bytes() == xml
    assert "hash mismatch" in window.notice.text()


def test_window_survives_switch_decks_close_and_reuse(env, tmp_path):
    host, deck, _, controller, window = env
    item = fill(window)
    other = FakeDeck("other", host.root / "other.tex")
    other.path.write_text("")
    folder = tmp_path / "otherbank"
    folder.mkdir()
    open_chosen_folder(host, other, folder)
    second = controller.open(other)
    host.drain()
    assert second is not window and second.shell.parent() is None and window.shell.parent() is None
    assert window.isVisible() and not window.isModal()
    assert window.current.id == item.id
    window.close()
    assert not window.isVisible()
    assert controller.open(deck) is window
    host.drain()
    assert window.isVisible() and window.current.id == item.id
    assert len(controller.windows) == 2


def test_sidebar_reads_focus_at_click_and_creates_linked_draft(env):
    host, deck, bank, controller, window = env
    ref = source(host, deck)
    sidebar = controller.sidebar(deck)
    write = next(b for b in sidebar.findChildren(type(host.Button(""))) if b.text() == "Write a question for this slide…")
    assert not any(b.text() == write.text() for b in window.findChildren(type(write)))
    write.click()
    assert "No slide is in focus" in host.messages[-1][0]
    host.current_focus = Focus(deck.path, ref.path, "slide")
    write.click()
    host.drain()
    assert window.new_item and window.links[0].slide_id == ref.id
    window.stem.setPlainText("From the slide")
    assert window.save()
    assert bank.get(window.current.id).sources[0].view == "instructor"


def test_needs_attention_ai_assisted_permanent_and_palette(env):
    _, _, bank, _, window = env
    item = fill(window)
    ai = bank.create(Item("ai", 1, "AI question", item.options, topic="Other", sources=item.sources,
                          origin=Origin("ai", "local", "model")))
    bank.set_source_changed(ai.id, True)
    window.reload()
    window.view = "attention"
    window.render()
    assert [window.slide_list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(window.slide_list.count())] == ["ai"]
    window.open_card("ai")
    assert window.draft_tag.isVisible() and not window.ai_tag.isVisible()
    assert window.draft_tag.toolTip() == window.ai_tag.toolTip() == "local · model"
    window.approve_button.click()
    # This check has no links so it clears the source flag.
    window.host.drain()
    window.render()
    assert window.slide_list.count() == 0 or window.view == "all"
    assert window.ai_tag.isVisible()
    # A dark palette leaves fields/surfaces inherited, with no literal background styles.
    palette = window.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#202020"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#eeeeee"))
    window.setPalette(palette)
    assert "background" not in window.styleSheet()


def test_status_check_error_and_concurrent_edit_cannot_approve(env):
    host, deck, bank, _, window = env
    item = fill(window)
    window.change_status("reviewed")
    bank.save(replace(item, stem="Edited elsewhere during check"))
    host.drain()
    assert bank.get(item.id).status == "draft"
    assert "changed during the check" in window.notice.text()
    assert window.stem.toPlainText() == item.stem
    deck.path.unlink()
    window.check_sources()
    host.drain()
    assert not window.busy and host.messages[-1][1]
    assert window.stem.toPlainText() == item.stem


def test_missing_remembered_bank_prompts_and_can_relocate(app, tmp_path, monkeypatch):
    host = GuiHost(tmp_path / "project")
    host.root.mkdir()
    deck = FakeDeck("deck", host.root / "deck.tex")
    deck.path.write_text("")
    missing = tmp_path / "missing"
    host.settings().set("bank_folders", {str(deck.path.resolve()): str(missing)})
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    existing = Bank(relocated, create=True)
    asked = []
    def pick(*args):
        asked.append(True)
        return str(relocated)
    monkeypatch.setattr("exam_forge.ui.first_use.QFileDialog.getExistingDirectory", pick)
    exam_forge.activate(host)
    host.finish_activation()
    controller = host.commands["exam-forge.open"].run.__self__
    window = controller.open(deck)
    host.drain()
    assert asked == [True] and window.bank.id == existing.id
    assert host.settings().get("bank_folders")[str(deck.path.resolve())] == str(relocated)
    window.hide()
    window.deleteLater()


def test_navigation_saves_before_selecting(env, monkeypatch):
    host, _, bank, _, window = env
    first = fill(window)
    bank.create(Item("second", 1, "Second", first.options))
    window.reload()
    window.stem.setPlainText("Saved while navigating")
    monkeypatch.setattr(QMessageBox, "question", lambda *a: QMessageBox.StandardButton.Save)
    window.view = "unlinked"
    window.render()
    window.view = "all"
    assert window.open_card("second")
    assert window.current.id == "second"
    assert bank.get(first.id).stem == "Saved while navigating"


def test_export_rechecks_stale_list(env, tmp_path, monkeypatch):
    host, _, bank, _, window = env
    item = fill(window)
    bank.set_status(item.id, item.revision, "reviewed")
    window.reload()
    reviewed = bank.get(item.id)
    bank.save(replace(reviewed, stem="Changed elsewhere", status="draft", use="elp"))
    path = tmp_path / "out.xml"
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    window.export()
    host.drain()
    assert not path.exists()
    assert "No reviewed" in window.notice.text()


def test_reopen_missing_bank_preserves_editor(env, tmp_path, monkeypatch):
    host, deck, bank, controller, window = env
    fill(window)
    window.stem.setPlainText("Unsaved")
    (bank.folder / "bank.json").unlink()
    monkeypatch.setattr("exam_forge.ui.first_use.QFileDialog.getExistingDirectory", lambda *a: "")
    assert controller.open(deck) is None
    assert window.stem.toPlainText() == "Unsaved"


def test_export_warns_about_newly_changed_source(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    ref = source(host, deck)
    with ThreadPoolExecutor() as executor:
        link = executor.submit(link_slide, host, ref, "instructor").result()
    item = fill(window)
    window.links = (link,)
    window.save()
    window.approve_button.click()
    host.drain()
    assert bank.get(item.id).status == "reviewed"
    host.sources[ref] = SlideSource(text="Changed after list loaded")
    notices = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: notices.append(a[2]) or QMessageBox.StandardButton.No)
    path = tmp_path / "out.xml"
    monkeypatch.setattr("exam_forge.ui.questions_tab.QFileDialog.getSaveFileName", lambda *a: (str(path), ""))
    window.export()
    host.drain()
    assert notices and item.stem in notices[0] and "s1" in notices[0]
    assert bank.get(item.id).source_changed and not path.exists()


def test_first_open_migrates_legacy_topics_once_and_saving_drops_the_proposal(env):
    from exam_forge.topics import TopicStore, add_topic
    host, deck, bank, _, window = env
    link = link_slide(host, source(host, deck), "instructor")
    item = bank.create(Item("legacy", 1, stem="Q", topic="Probability", sources=(link,)))
    TopicStore(bank.folder).path.unlink()
    window.load_topics()
    assert [(t.name, t.slides) for t in window.topics] == [("Probability", (link.slide_id,))]
    assert window.topic_name_of(item) == "Probability"
    first = window.topics
    window.load_topics()
    assert window.topics == first
    window.exam_tab.selection = object()
    assert window.save_topics(add_topic(window.topics, "Bayes"))
    assert window.exam_tab.selection is None
    assert [t.name for t in TopicStore(bank.folder).load()] == ["Probability", "Bayes"]


def settings_button(host, deck):
    host.current_focus = Focus(deck.path, None, "slide")
    page = host.settings_tabs["Exam Forge"]()
    return page, next(b for b in page.findChildren(QPushButton) if b.text() == "Change folder…")


def pick_folder(monkeypatch, folder):
    monkeypatch.setattr("exam_forge.ui.first_use.QFileDialog.getExistingDirectory", lambda *a: str(folder))


def test_settings_copy_the_bank_to_an_empty_folder_and_leave_the_old_one(env, tmp_path, monkeypatch):
    host, deck, bank, controller, window = env
    item = fill(window)
    media = bank.store_media("plot.png", png(), "A plot")
    window.attachments = (media,)
    window.save()
    (bank.folder / "unrelated.txt").write_text("not the bank's")
    (bank.folder / "1.manifest.json").write_text("{}")
    old = bank.folder
    before = sorted(p.name for p in old.iterdir())
    target = tmp_path / "moved"
    target.mkdir()
    pick_folder(monkeypatch, target)
    _, button = settings_button(host, deck)
    button.click()
    host.drain()
    assert window.bank.folder == target and window.bank.get(item.id).media == (media,)
    assert window.bank.read_media(media) == png()
    assert (target / "1.manifest.json").exists() and not (target / "unrelated.txt").exists()
    assert sorted(p.name for p in old.iterdir()) == before
    assert host.settings().get("bank_folders")[str(deck.path.resolve())] == str(target)
    assert window.folder_label.text() == str(target)


def test_settings_use_a_folder_with_a_bank_only_after_asking(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    other = tmp_path / "other"
    other.mkdir()
    existing = Bank(other, create=True)
    pick_folder(monkeypatch, other)
    answers = [QMessageBox.StandardButton.No, QMessageBox.StandardButton.Yes]
    monkeypatch.setattr("exam_forge.ui.first_use.QMessageBox.question", lambda *a: answers.pop(0))
    _, button = settings_button(host, deck)
    button.click()
    assert window.bank.folder == bank.folder
    button.click()
    host.drain()
    assert window.bank.id == existing.id


def test_a_refused_unsaved_question_keeps_the_folder(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    fill(window)
    window.stem.setPlainText("Edited")
    monkeypatch.setattr("exam_forge.ui.editor.QMessageBox.question", lambda *a: QMessageBox.StandardButton.Cancel)
    target = tmp_path / "moved"
    target.mkdir()
    pick_folder(monkeypatch, target)
    _, button = settings_button(host, deck)
    button.click()
    assert window.bank.folder == bank.folder and not any(target.iterdir())
    assert window.stem.toPlainText() == "Edited"


def test_a_failed_copy_leaves_nothing_behind(env, tmp_path, monkeypatch):
    host, deck, bank, _, window = env
    item = fill(window)
    media = bank.store_media("plot.png", png(), "A plot")
    target = tmp_path / "moved"
    target.mkdir()
    pick_folder(monkeypatch, target)
    real_copy = shutil.copy2
    def failing(source, copy):
        if Path(source).name == "bank.json":
            raise OSError("disk full")
        return real_copy(source, copy)
    monkeypatch.setattr("exam_forge.folder.shutil.copy2", failing)
    messages = []
    monkeypatch.setattr(host, "show_message", lambda text, error=False: messages.append((text, error)))
    _, button = settings_button(host, deck)
    button.click()
    assert messages == [("disk full", True)] and not any(target.iterdir())
    assert window.bank.folder == bank.folder and window.bank.get(item.id) and media


def test_a_deck_without_a_window_changes_folder_and_recounts(env, tmp_path, monkeypatch):
    host, deck, bank, controller, window = env
    controller.windows.clear()
    controller.counts[controller.key(deck)] = (1, 1, 0, 0, 0)
    target = tmp_path / "moved"
    target.mkdir()
    pick_folder(monkeypatch, target)
    assert controller.change_folder(deck)
    assert controller.key(deck) not in controller.counts
    assert (target / "bank.json").is_file()
    controller.windows[controller.key(deck)] = window


def test_settings_find_the_open_deck_without_a_window_or_sidebar_box(app, tmp_path, monkeypatch):
    host = GuiHost(tmp_path / "project")
    host.root.mkdir()
    deck = FakeDeck("course", host.root / "course.tex")
    deck.path.write_text("deck")
    exam_forge.activate(host)
    host.finish_activation()
    page = host.settings_tabs["Exam Forge"]()
    assert not [b for b in page.findChildren(QPushButton) if b.text() == "Change folder…"]
    target = tmp_path / "chosen"
    target.mkdir()
    pick_folder(monkeypatch, target)
    page, button = settings_button(host, deck)
    assert any(label.text() == "course: not chosen yet" for label in page.findChildren(QLabel))
    button.click()
    assert host.settings().get("bank_folders")[str(deck.path.resolve())] == str(target)

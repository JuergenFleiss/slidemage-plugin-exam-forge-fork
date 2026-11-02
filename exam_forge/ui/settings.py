# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Exam Forge settings, built when the host opens Settings."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QPlainTextEdit, QSpinBox, QWidget

from ..ai_support import ai_service
from ..answer_rules import AnswerRulesStore
from ..bank import BankError
from ..drafting import DEFAULT_GUIDANCE
from ..elp import ElpRules
from ..model import MAX_OPTIONS, AnswerRule, AnswerRules


def add_folder_section(host, controller, page, folder_changed=lambda: None):
    deck = controller.open_deck()
    if deck is None:
        page.body.addWidget(host.Field("Question folder", host.caption("Open a deck to change its question folder.")))
        return
    path = host.caption("")
    path.setWordWrap(True)
    change = host.Button("Change folder…", compact=True)

    def show():
        folder = controller.bank_folder(deck)
        path.setText(f"{deck.name}: {folder if folder is not None else 'not chosen yet'}")

    def pick():
        if controller.change_folder(deck, page):
            show()
            folder_changed()

    change.clicked.connect(pick)
    show()
    page.body.addWidget(host.Field("Question folder", path))
    page.body.addWidget(change, 0, Qt.AlignmentFlag.AlignLeft)


def add_rules_section(host, controller, page):
    """The open deck's answer rules, saved in its question folder as soon as they are valid. Returns a callable that
    reads them again, for when the folder changed."""
    def spin(low, high):
        box = QSpinBox()
        box.setRange(low, high)
        return box

    def line(*parts):
        widget = QWidget()
        row = QHBoxLayout(widget)
        row.setContentsMargins(0, 0, 0, 0)
        for part in parts:
            row.addWidget(host.Label(part) if isinstance(part, str) else part)
        row.addStretch()
        return widget

    # Revision: answers from, to; correct from, to. ELP: one number of answers; correct from, to.
    rev = [spin(2, MAX_OPTIONS), spin(2, MAX_OPTIONS), spin(1, MAX_OPTIONS - 1), spin(1, MAX_OPTIONS - 1)]
    elp = [spin(2, MAX_OPTIONS), spin(1, MAX_OPTIONS - 1), spin(1, MAX_OPTIONS - 1)]
    revision_field = host.Field("Revision quiz (Moodle)", line("Answers", rev[0], "to", rev[1],
                                                                "· Correct", rev[2], "to", rev[3]))
    elp_field = host.Field("ELP exam", line("Answers", elp[0], "· Correct", elp[1], "to", elp[2]))
    notice = host.caption("")
    notice.setWordWrap(True)
    reset = host.Button("Default answer rules", compact=True)
    widgets = (revision_field, elp_field, reset)
    page.body.addWidget(host.Field("Answer rules", host.caption(
        "How many answers a question has and how many of them are correct, for this deck. Reviewed questions "
        "that no longer fit are flagged and left out of exports and exams until you fix them.")))
    for widget in (revision_field, elp_field, notice):
        page.body.addWidget(widget)
    page.body.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)
    state = {"store": None, "loading": False}

    def show(rules):
        state["loading"] = True
        r, e = rules.revision, rules.elp
        for box, value in zip((*rev, *elp), (r.min_options, r.max_options, r.min_correct, r.max_correct,
                                             e.min_options, e.min_correct, e.max_correct)):
            box.setValue(value)
        state["loading"] = False

    def load():
        deck = controller.open_deck()
        folder = controller.bank_folder(deck) if deck is not None else None
        state["store"] = AnswerRulesStore(folder) if folder is not None else None
        for widget in widgets:
            widget.setEnabled(state["store"] is not None)
        if state["store"] is None:
            notice.setText("Open a deck and choose its question folder to set its answer rules.")
            show(AnswerRules())
            return
        try:
            show(state["store"].load())
            notice.clear()
        except (OSError, BankError, ValueError) as exc:
            show(AnswerRules())
            notice.setText(f"{exc} Saving replaces the file.")

    def save(rules):
        try:
            ElpRules(answers=rules.elp)
            state["store"].save(rules)
        except (OSError, ValueError) as exc:
            notice.setText(f"Not saved: {exc}")
            return
        notice.clear()
        controller.answer_rules_changed(controller.open_deck())

    def changed(*_):
        if state["loading"] or state["store"] is None:
            return
        count, low, high = (box.value() for box in elp)
        try:
            rules = AnswerRules(AnswerRule(*(box.value() for box in rev)), AnswerRule(count, count, low, high))
        except ValueError as exc:
            notice.setText(f"Not saved: {exc}")
            return
        save(rules)

    def default():
        show(AnswerRules())
        if state["store"] is not None:
            save(AnswerRules())

    for box in (*rev, *elp):
        box.valueChanged.connect(changed)
    reset.clicked.connect(default)
    load()
    return load


def build_settings(host, controller):
    on_change = controller.refresh_ai_support
    page = host.Page()
    reload_rules = []
    add_folder_section(host, controller, page, lambda: reload_rules[0]())
    reload_rules.append(add_rules_section(host, controller, page))
    checkbox = QCheckBox("Enable AI support")
    checkbox.setChecked(bool(host.settings().get("ai_support", False)))
    notice = page.ai_notice = host.caption("")
    notice.setWordWrap(True)
    page.body.addWidget(checkbox)
    page.body.addWidget(notice)
    guidance = QPlainTextEdit()
    guidance.setPlainText(host.settings().get("drafting_guidance") or DEFAULT_GUIDANCE)
    field = host.Field("AI drafting guidance", guidance)
    reset = host.Button("Reset to default")
    page.body.addWidget(field)
    page.body.addWidget(reset)

    def save_guidance():
        value = guidance.toPlainText()
        settings = host.settings()
        settings.set("drafting_guidance", value if value.strip() and value != DEFAULT_GUIDANCE else None)
        settings.save()

    def reset_guidance():
        guidance.setPlainText(DEFAULT_GUIDANCE)
        save_guidance()

    guidance.textChanged.connect(save_guidance)
    reset.clicked.connect(reset_guidance)

    def update(enabled):
        field.setVisible(enabled)
        reset.setVisible(enabled)
        settings = host.settings()
        settings.set("ai_support", enabled)
        settings.save()
        service = ai_service(host) if enabled else None
        if enabled and (service is None or not service.available()):
            notice.setText("AI support needs an AI plugin, such as Claude or Ollama. Install and enable one in Settings.")
        else:
            notice.clear()
        on_change()

    checkbox.toggled.connect(update)
    if checkbox.isChecked():
        update(True)
    else:
        field.hide()
        reset.hide()
    return page

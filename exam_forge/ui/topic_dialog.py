# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create, rename, fill and delete a topic. Nothing is written until Done or Delete topic."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
)

from ..topics import add_topic, assign_slide, delete_topic, rename_topic, topic_color
from .editor import is_dark
from .sheet import Sheet
from .work import SCROLL_STYLE, slide_title


class TopicDialog(Sheet):
    def __init__(self, shell, topic=None):
        super().__init__(shell.host, "Edit topic" if topic else "New topic", shell)
        self.shell, self.topic = shell, topic
        self.topic_id, self.created = topic.id if topic else None, False
        self.initial = set(topic.slides) if topic else set()
        self.buttons, self.dots = {}, {}
        host = shell.host
        self.resize(640, 560)
        layout = self.body
        self.name = QLineEdit(topic.name if topic else "")
        layout.addWidget(host.Field("Name", self.name))
        layout.addWidget(host.caption("Slides in this topic. A slide belongs to one topic; ticking one takes it over."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_STYLE)
        grid_host = host.Surface()
        self.grid = QGridLayout(grid_host)
        scroll.setWidget(grid_host)
        layout.addWidget(scroll, 1)
        slides = shell.slides or []
        if not slides:
            self.grid.addWidget(host.caption("No slides found in this deck yet."), 0, 0)
        for index, ref in enumerate(slides):
            cell = QHBoxLayout()
            dot = QLabel()
            dot.setTextFormat(Qt.TextFormat.RichText)
            title = f"{ref.position} · {slide_title(ref)}"
            button = host.Button(title, compact=True)
            button.setText(button.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, 240))
            button.setToolTip(title)
            button.setCheckable(True)
            button.setChecked(ref.id in self.initial)
            button.toggled.connect(self.recolor)
            cell.addWidget(dot)
            cell.addWidget(button, 1)
            self.grid.addLayout(cell, index // 2, index % 2)
            self.buttons[ref.id], self.dots[ref.id] = button, dot
        self.grid.setRowStretch(self.grid.rowCount(), 1)
        self.problem = host.caption("")
        self.problem.setWordWrap(True)
        self.problem.hide()
        layout.addWidget(self.problem)
        self.delete_button = self.add_button("Delete topic")
        self.delete_button.setVisible(topic is not None)
        self.delete_button.clicked.connect(self.delete)
        self.add_button("Cancel", role="reject")
        self.done_button = self.add_button("Done", kind="primary")
        self.done_button.clicked.connect(self.finish)
        self.recolor()

    def own_color(self):
        key = self.topic.color if self.topic else add_topic(self.shell.topics, "x")[-1].color
        return topic_color(key, is_dark(self.shell))

    def recolor(self, *_):
        dark = is_dark(self.shell)
        for slide, dot in self.dots.items():
            owner = self.shell.owner(slide)
            if self.buttons[slide].isChecked():
                color = self.own_color()
            else:
                color = topic_color(owner.color, dark) if owner and (not self.topic or owner.id != self.topic.id) else ""
            dot.setText(f"<span style='color:{color}'>●</span>" if color else "")
            dot.setFixedWidth(14)

    def say(self, text):
        self.problem.setText(text)
        self.problem.setVisible(bool(text))
        if text:
            self.problem.setStyleSheet(f"color: {topic_color('red', is_dark(self.shell))};")

    def finish(self):
        name = self.name.text().strip()
        if not name:
            self.say("Give the topic a name.")
            return
        mine = self.topic.id if self.topic else None
        if any(t.name.strip().casefold() == name.casefold() and t.id != mine for t in self.shell.topics):
            self.say("Another topic already has this name.")
            return
        topics = self.shell.topics
        if self.topic is None:
            topics = add_topic(topics, name)
            self.topic_id = topics[-1].id
        elif name != self.topic.name:
            topics = rename_topic(topics, self.topic.id, name)
        for slide, button in self.buttons.items():
            if button.isChecked() and slide not in self.initial:
                topics = assign_slide(topics, self.topic_id, slide)
            elif not button.isChecked() and slide in self.initial:
                topics = assign_slide(topics, None, slide)
        if self.shell.save_topics(topics):
            self.created = self.topic is None
            self.accept()

    def delete(self):
        answer = QMessageBox.question(self.sheet, "Delete topic", f"Delete the topic \"{self.topic.name}\"? Its slides will have "
                                      "no topic. Questions are kept.", QMessageBox.StandardButton.Yes |
                                      QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes and self.shell.save_topics(delete_topic(self.shell.topics, self.topic.id)):
            self.accept()

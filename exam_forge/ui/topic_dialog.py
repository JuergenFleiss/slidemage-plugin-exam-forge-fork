# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create, rename, fill and delete a topic. Nothing is written until Done or Delete topic."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLineEdit

from ..model import add_topic, assign_slide, delete_topic, next_color, rename_topic
from .kit import Sheet, confirm, dot, scroll_area, slide_title, tone


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
        grid_host = host.Surface()
        self.grid = QGridLayout(grid_host)
        layout.addWidget(scroll_area(grid_host), 1)
        slides = shell.slides or []
        if not slides:
            self.grid.addWidget(host.caption("No slides found in this deck yet."), 0, 0)
        for index, ref in enumerate(slides):
            cell = QHBoxLayout()
            mark = dot()
            title = f"{ref.position} · {slide_title(ref)}"
            button = host.Button(title, compact=True)
            button.setText(button.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, 240))
            button.setToolTip(title)
            button.setCheckable(True)
            button.setChecked(ref.id in self.initial)
            button.toggled.connect(self.recolor)
            cell.addWidget(mark)
            cell.addWidget(button, 1)
            self.grid.addLayout(cell, index // 2, index % 2)
            self.buttons[ref.id], self.dots[ref.id] = button, mark
        self.grid.setRowStretch(self.grid.rowCount(), 1)
        self.problem = tone(host.caption(""), "error")
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

    def recolor(self, *_):
        """A ticked slide shows this topic's dot; another topic's slide shows that one's."""
        own = self.topic.color if self.topic else next_color(self.shell.topics)
        for slide, mark in self.dots.items():
            owner = self.shell.owner(slide)
            other = owner if owner and (self.topic is None or owner.id != self.topic.id) else None
            color = own if self.buttons[slide].isChecked() else other.color if other else ""
            mark.setText("●" if color else "")
            tone(mark, color)

    def say(self, text):
        self.problem.setText(text)
        self.problem.setVisible(bool(text))

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
        if (confirm(self.sheet, "Delete topic", f"Delete the topic \"{self.topic.name}\"? Its slides will have no topic. "
                    "Questions are kept.") and self.shell.save_topics(delete_topic(self.shell.topics, self.topic.id))):
            self.accept()

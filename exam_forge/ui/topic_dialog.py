# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""Create, rename, fill and delete a topic. Nothing is written until Done or Delete topic."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..topics import add_topic, assign_slide, delete_topic, rename_topic, topic_color
from .editor import is_dark


class TopicDialog(QDialog):
    def __init__(self, shell, topic=None):
        super().__init__(shell)
        self.shell, self.topic = shell, topic
        self.topic_id, self.created = topic.id if topic else None, False
        self.initial = set(topic.slides) if topic else set()
        self.buttons, self.dots = {}, {}
        host = shell.host
        self.setWindowTitle("Edit topic" if topic else "New topic")
        self.resize(560, 520)
        layout = QVBoxLayout(self)
        self.name = QLineEdit(topic.name if topic else "")
        layout.addWidget(host.Field("Name", self.name))
        layout.addWidget(QLabel("Slides in this topic. A slide belongs to one topic; ticking one takes it over."))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        grid_host = QWidget()
        self.grid = QGridLayout(grid_host)
        scroll.setWidget(grid_host)
        layout.addWidget(scroll, 1)
        slides = shell.slides or []
        if not slides:
            self.grid.addWidget(QLabel("No slides found in this deck yet."), 0, 0)
        for index, ref in enumerate(slides):
            cell = QHBoxLayout()
            dot = QLabel()
            dot.setTextFormat(Qt.TextFormat.RichText)
            button = host.Button(f"{ref.position} · {ref.title or ref.id_slug}", compact=True)
            button.setCheckable(True)
            button.setChecked(ref.id in self.initial)
            button.toggled.connect(self.recolor)
            cell.addWidget(dot)
            cell.addWidget(button, 1)
            self.grid.addLayout(cell, index // 2, index % 2)
            self.buttons[ref.id], self.dots[ref.id] = button, dot
        self.grid.setRowStretch(self.grid.rowCount(), 1)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.hide()
        layout.addWidget(self.problem)
        row = QHBoxLayout()
        self.delete_button = host.Button("Delete topic")
        self.delete_button.setVisible(topic is not None)
        self.delete_button.clicked.connect(self.delete)
        row.addWidget(self.delete_button)
        row.addStretch()
        cancel = host.Button("Cancel")
        cancel.clicked.connect(self.reject)
        self.done_button = host.Button("Done", "primary")
        self.done_button.clicked.connect(self.finish)
        row.addWidget(cancel)
        row.addWidget(self.done_button)
        layout.addLayout(row)
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
        answer = QMessageBox.question(self, "Delete topic", f"Delete the topic \"{self.topic.name}\"? Its slides will have "
                                      "no topic. Questions are kept.", QMessageBox.StandardButton.Yes |
                                      QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes and self.shell.save_topics(delete_topic(self.shell.topics, self.topic.id)):
            self.accept()

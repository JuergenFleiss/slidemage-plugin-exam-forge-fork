# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The setting gates service access and reports missing host routes."""
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QCheckBox, QLabel

import exam_forge
from exam_forge.ai_support import ai_service
from fake_host import FakeHost


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def host(app):
    fake = FakeHost()
    fake.service = Mock(return_value=Mock(available=Mock(return_value=False)))
    exam_forge.activate(fake)
    fake.finish_activation()
    return fake


def widgets(host):
    page = host.settings_tabs["Exam Forge"]()
    return page, page.findChild(QCheckBox), page.findChild(QLabel)


def test_off_by_default_and_after_disabling(host):
    page, checkbox, notice = widgets(host)
    assert not checkbox.isChecked()
    assert ai_service(host) is None
    host.service.assert_not_called()
    checkbox.setChecked(True)
    checkbox.setChecked(False)
    host.service.reset_mock()
    assert not host.settings().get("ai_support")
    assert ai_service(host) is None
    assert not notice.text()
    host.service.assert_not_called()


def test_on_without_route_explains_what_is_needed(host):
    page, checkbox, notice = widgets(host)
    checkbox.setChecked(True)
    assert host.settings().get("ai_support") is True
    assert "AI plugin" in notice.text()
    host.service.assert_called_with("ai.chat", 1)
    assert host.settings_tabs["Exam Forge"]().findChild(QCheckBox).isChecked()


def test_on_with_route_returns_service(host):
    host.service.return_value.available.return_value = True
    page, checkbox, notice = widgets(host)
    checkbox.setChecked(True)
    assert not notice.text()
    assert ai_service(host) is host.service.return_value
    host.service.assert_called_with("ai.chat", 1)

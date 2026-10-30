# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The single gate for Exam Forge's optional AI service."""


def ai_service(host):
    """Return the host service only while the author has enabled AI support."""
    if not host.settings().get("ai_support", False):
        return None
    return host.service("ai.chat", 1)

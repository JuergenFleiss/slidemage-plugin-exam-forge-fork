# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
"""The single gate for Exam Forge's optional AI service."""


# An AI request that ends with neither a reply nor an error: declined, cancelled, or not answered by the plugin.
NO_REPLY = ("No reply from the AI plugin, so nothing changed. If you did not decline the request, check the AI "
            "plugin in Settings, Extensions.")


def ai_service(host):
    """Return the host service only while the author has enabled AI support."""
    if not host.settings().get("ai_support", False):
        return None
    return host.ai_chat()

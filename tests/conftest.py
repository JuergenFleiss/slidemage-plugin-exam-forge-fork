# SPDX-FileCopyrightText: 2026 Jürgen Fleiß
# SPDX-License-Identifier: GPL-3.0-or-later
import gc

import pytest


@pytest.fixture(autouse=True)
def _free_widgets_on_the_ui_thread():
    """A test's widgets are freed here, not whenever a collection happens to run on a worker thread: destroying a Qt
    widget off the UI thread hangs. The real plugin keeps its windows for as long as SlideMage runs."""
    yield
    gc.collect()

# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Shared seams for the DEFT stage-handler tests."""

from __future__ import annotations

import time
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@contextmanager
def _patched_sleep():
    """Mock the stage's ``time.sleep`` WITHOUT touching the shared time module.

    ``patch("...handlers.time.sleep")`` resolves ``handlers.time`` to the global
    ``time`` module and sets ``sleep`` on it, so the mock is process-wide for the
    duration of the block. Two things follow, and the second one bites:

    1. every other thread's ``time.sleep`` returns INSTANTLY, so a poller left
       running by an earlier test stops throttling and spins at full speed; and
    2. every one of those calls is recorded on this mock.

    A ``sleep.assert_not_called()`` then fails citing thousands of calls the
    stage never made. Measured at ~31k in 300ms against a single background
    thread; CI saw 4972 on a stage that returns before it can reach its first
    sleep. It passes locally whenever nothing else happens to be sleeping, which
    is what makes it an order-dependent flake rather than a steady failure.

    Rebinding the module-level NAME keeps the mock local to the stage under
    test. ``monotonic`` is carried through real: the stage uses it for elapsed
    time and a MagicMock would not compare.
    """
    sleep = MagicMock()
    with patch(
        "src.tasks.handlers.deft.handlers.time",
        SimpleNamespace(sleep=sleep, monotonic=time.monotonic),
    ):
        yield sleep


@pytest.fixture
def patched_sleep():
    """The :func:`_patched_sleep` context manager, for use inside a ``with``.

    Handed over as a factory rather than an already-entered patch so a test
    keeps deciding where the patch starts, alongside its other ``with`` entries.
    """
    return _patched_sleep

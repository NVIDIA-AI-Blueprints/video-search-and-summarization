# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for handlers/alert_config/hydration.py (the alert-config ES hydration)."""

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handlers.alert_config import hydrate_cache


def _doc(alert_type, **extra):
    return {"alert_type": alert_type, "prompt": "p", **extra}


@pytest.fixture
def primary():
    return MagicMock()


@pytest.fixture
def cache():
    return MagicMock()


def test_hydrate_copies_every_record_into_cache_and_memory(primary, cache):
    records = {"collision": _doc("collision"), "unsafe_behavior": _doc("unsafe_behavior")}
    primary.get_all.return_value = records
    memory: dict = {"stale": _doc("stale")}

    count = hydrate_cache(primary, cache, memory)

    assert count == 2
    assert memory == records  # stale entry evicted
    assert cache.set.call_count == 2
    cache.set.assert_any_call("collision", records["collision"])
    cache.set.assert_any_call("unsafe_behavior", records["unsafe_behavior"])


def test_hydrate_empty_primary_clears_memory(primary, cache):
    primary.get_all.return_value = {}
    memory = {"stale": _doc("stale")}

    count = hydrate_cache(primary, cache, memory)

    assert count == 0
    assert memory == {}
    cache.set.assert_not_called()


def test_hydrate_continues_when_cache_set_fails(primary, cache):
    records = {"collision": _doc("collision"), "unsafe_behavior": _doc("unsafe_behavior")}
    primary.get_all.return_value = records
    # Cache write fails for the first record but must not abort hydration.
    cache.set.side_effect = [RuntimeError("redis down"), None]
    memory: dict = {}

    count = hydrate_cache(primary, cache, memory)

    assert count == 2
    assert memory == records  # memory snapshot complete regardless
    assert cache.set.call_count == 2


def test_hydrate_propagates_primary_failure(primary, cache):
    primary.get_all.side_effect = RuntimeError("es down")
    memory: dict = {"keep": _doc("keep")}

    with pytest.raises(RuntimeError):
        hydrate_cache(primary, cache, memory)

    # On a primary failure we must not leave memory half-written. The
    # contract is: caller is responsible for fail-fast before hydration,
    # so the only safe behaviour here is to bubble up without mutating.
    assert memory == {"keep": _doc("keep")}
    cache.set.assert_not_called()

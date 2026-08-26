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

"""Alert-config startup must recover from a newly-created ES index race."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from persistence.exceptions import PersistenceError
from web import main


class FakeElasticsearchError(Exception):
    """Minimal elasticsearch.ApiError shape used through wrapped causes."""

    def __init__(self, status: int):
        super().__init__(f"Elasticsearch returned HTTP {status}")
        self.meta = SimpleNamespace(status=status)


def wrapped_es_error(status: int) -> PersistenceError:
    try:
        raise FakeElasticsearchError(status)
    except FakeElasticsearchError as exc:
        try:
            raise PersistenceError("Failed to list documents in 'alert_configs'") from exc
        except PersistenceError as wrapped:
            return wrapped


@pytest.fixture(autouse=True)
def reset_startup_state():
    main._startup_ready = False
    main._startup_error = "startup has not completed"
    yield
    main._startup_ready = False
    main._startup_error = "startup has not completed"


@pytest.mark.asyncio
async def test_transient_503_retries_then_marks_startup_ready():
    transient = wrapped_es_error(503)
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[transient, transient, object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()

    assert get_service.call_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0]
    assert main._startup_ready is True
    assert main._startup_error == ""


@pytest.mark.asyncio
async def test_non_retryable_error_aborts_without_sleeping():
    permanent = wrapped_es_error(400)
    with patch("web.api.alert_config_routes._get_service",
               side_effect=permanent) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        with pytest.raises(PersistenceError, match="Failed to list documents"):
            await main.startup_event()

    get_service.assert_called_once_with()
    sleep.assert_not_awaited()
    assert main._startup_ready is False
    assert "Failed to list documents" in main._startup_error


@pytest.mark.asyncio
async def test_retry_exhaustion_aborts_instead_of_latching_a_live_503():
    transient = wrapped_es_error(503)
    with patch("web.api.alert_config_routes._get_service",
               side_effect=transient) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        with pytest.raises(PersistenceError, match="Failed to list documents"):
            await main.startup_event()

    assert get_service.call_count == main._ALERT_CONFIG_INIT_MAX_ATTEMPTS
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0, 4.0]
    assert main._startup_ready is False
    assert "Failed to list documents" in main._startup_error


@pytest.mark.asyncio
async def test_connection_error_is_retryable():
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[ConnectionError("ES starting"), object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()

    assert get_service.call_count == 2
    sleep.assert_awaited_once_with(1.0)
    assert main._startup_ready is True

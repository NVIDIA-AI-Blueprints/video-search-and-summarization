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

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

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


@pytest_asyncio.fixture(autouse=True)
async def reset_startup_state():
    main._startup_ready = False
    main._startup_error = "startup has not completed"
    main._alert_config_init_task = None
    yield
    await main.shutdown_event()
    main._startup_ready = False
    main._startup_error = "startup has not completed"


@pytest.mark.asyncio
async def test_startup_returns_while_transient_503_is_being_retried():
    transient = wrapped_es_error(503)
    retry_started = asyncio.Event()
    allow_retry = asyncio.Event()

    async def controlled_sleep(delay):
        assert delay == 1.0
        retry_started.set()
        await allow_retry.wait()

    with patch("web.api.alert_config_routes._get_service",
               side_effect=[transient, object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", side_effect=controlled_sleep):
        await main.startup_event()
        assert main._alert_config_init_task is not None
        assert not main._alert_config_init_task.done()
        assert main._startup_ready is False
        assert main._startup_error == "alert-config store initialisation is in progress"
        assert main._startup_failure().status_code == 503

        await retry_started.wait()
        assert main._startup_ready is False
        assert "Failed to list documents" in main._startup_error

        allow_retry.set()
        await main._alert_config_init_task

    assert get_service.call_count == 2
    assert main._startup_ready is True
    assert main._startup_error == ""
    assert main._startup_failure() is None


@pytest.mark.asyncio
async def test_non_retryable_error_remains_not_ready_without_retrying():
    permanent = wrapped_es_error(400)
    with patch("web.api.alert_config_routes._get_service",
               side_effect=permanent) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()
        await main._alert_config_init_task

    get_service.assert_called_once_with()
    sleep.assert_not_awaited()
    assert main._startup_ready is False
    assert "Failed to list documents" in main._startup_error


@pytest.mark.asyncio
async def test_transient_errors_continue_with_capped_backoff_until_success():
    transient = wrapped_es_error(503)
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[transient] * 5 + [object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()
        await main._alert_config_init_task

    assert get_service.call_count == 6
    assert [call.args[0] for call in sleep.await_args_list] == [1.0, 2.0, 4.0, 8.0, 8.0]
    assert main._startup_ready is True


@pytest.mark.asyncio
async def test_connection_error_is_retryable():
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[ConnectionError("ES starting"), object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()
        await main._alert_config_init_task

    assert get_service.call_count == 2
    sleep.assert_awaited_once_with(1.0)
    assert main._startup_ready is True


@pytest.mark.asyncio
async def test_shutdown_cancels_a_pending_retry_task():
    transient = wrapped_es_error(503)
    retry_started = asyncio.Event()

    async def blocked_sleep(_delay):
        retry_started.set()
        await asyncio.Event().wait()

    with patch("web.api.alert_config_routes._get_service",
               side_effect=transient), \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", side_effect=blocked_sleep):
        await main.startup_event()
        task = main._alert_config_init_task
        await retry_started.wait()
        await main.shutdown_event()

    assert task.cancelled()
    assert main._alert_config_init_task is None

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
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from persistence.exceptions import PersistenceError, PersistenceUnavailableError
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
    # One element, not a repeating side effect: if a future change ever
    # classifies this as transient, the loop asks a second time and gets
    # StopIteration, so the test fails on the count below. Repeating the
    # exception would instead retry forever against a mocked sleep and hang.
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[permanent]) as get_service, \
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
    # What cancelling reaches is the sleep between attempts, nothing more. A
    # build already running does so on a worker thread, and Python cannot
    # interrupt a thread: it finishes, and publishes its result, after this
    # returns. Reading task.cancelled() as "the build stopped" would be wrong.
    #
    # Two consequences, both bounded rather than fixed. The orphan keeps the
    # construction lock until it finishes, and interpreter exit joins it --
    # asyncio.run drains the default executor -- so the process outlives the
    # shutdown signal by however long the in-flight build takes. That is
    # capped by the client's request timeout, not open-ended, and it is
    # strictly shorter than blocking the loop for the same work would be.


@pytest.mark.asyncio
async def test_unreachable_backend_is_retryable():
    """The store refuses to build when the backend fails its health check.

    That failure carries no HTTP status to classify -- the health check
    returns a bool, so nothing is raised from the client -- which is why the
    exception has to be recognised by type. Left unrecognised it reads as
    permanent, and readiness stays 503 after Elasticsearch recovers, which is
    the whole failure this retry exists to prevent.
    """
    unavailable = PersistenceUnavailableError(
        "Persistence layer enabled but Elasticsearch is unreachable"
    )
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[unavailable, object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()
        await main._alert_config_init_task

    assert get_service.call_count == 2
    sleep.assert_awaited_once_with(1.0)
    assert main._startup_ready is True


@pytest.mark.asyncio
async def test_the_build_does_not_run_on_the_event_loop():
    """Building the store must not happen on the loop thread.

    It does synchronous Elasticsearch work -- two pings and a hydrating
    search -- each capped by the client's request timeout but seconds long
    against a backend that accepts connections without answering. On the loop
    thread that stalls /health for the length of every retry, and /health is
    the endpoint a deployment gates on, so the retry would take out the thing
    it exists to keep answering.
    """
    loop_thread = threading.current_thread()
    ran_on = []

    def record():
        ran_on.append(threading.current_thread())
        return object()

    with patch("web.api.alert_config_routes._get_service", side_effect=record), \
         patch.object(main, "validate_always_on_config_at_startup"):
        await main.startup_event()
        await main._alert_config_init_task

    assert ran_on, "the builder was never called"
    assert ran_on[0] is not loop_thread, (
        "the store was built on the event loop thread; /health would stall "
        "for the length of every retry"
    )
    assert main._startup_ready is True


@pytest.mark.asyncio
async def test_a_transport_error_subclass_is_retryable():
    """Transport failures are recognised by base class, not by name.

    elastic-transport raises TlsError while a TLS listener is still coming
    up. It is a ConnectionError subclass carrying a different name and no
    HTTP status, so matching names classified it as permanent -- readiness
    would latch at 503 against an Elasticsearch that was merely still
    starting, which is the failure this retry exists to prevent.
    """
    import elastic_transport

    try:
        raise elastic_transport.TlsError("handshake in progress")
    except elastic_transport.TlsError as exc:
        try:
            raise PersistenceError("Failed to list documents in 'alert_configs'") from exc
        except PersistenceError as wrapped:
            transient = wrapped

    with patch("web.api.alert_config_routes._get_service",
               side_effect=[transient, object()]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()
        await main._alert_config_init_task

    assert get_service.call_count == 2
    sleep.assert_awaited_once_with(1.0)
    assert main._startup_ready is True


@pytest.mark.asyncio
async def test_a_serialization_error_is_not_retryable():
    """The sibling that must not be retried.

    SerializationError shares TransportError with the connection failures, so
    matching on that shared base would sweep it in. A payload that will not
    parse will not parse on the next attempt either.
    """
    import elastic_transport

    try:
        raise elastic_transport.SerializationError("unparseable response")
    except elastic_transport.SerializationError as exc:
        try:
            raise PersistenceError("Failed to list documents in 'alert_configs'") from exc
        except PersistenceError as wrapped:
            permanent = wrapped

    # A one-element list, not a bare exception: if a future change ever
    # classifies this as transient, the second attempt exhausts the list and
    # the loop stops on the resulting error, so the assertion below fails on
    # the call count. A bare exception would instead be raised on every
    # attempt and, against a mocked sleep, retry forever -- the test would
    # hang rather than fail.
    with patch("web.api.alert_config_routes._get_service",
               side_effect=[permanent]) as get_service, \
         patch.object(main, "validate_always_on_config_at_startup"), \
         patch.object(main.asyncio, "sleep", new=AsyncMock()) as sleep:
        await main.startup_event()
        await main._alert_config_init_task

    get_service.assert_called_once_with()
    sleep.assert_not_awaited()
    assert main._startup_ready is False

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Raw SDK experiments retain validation, errors, and explicit client ownership."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flows.raw_sdk import RawSdkRunner
from vss_cli.config import Deployment, Service
from vss_core.search_core.errors import InvalidInputError
from vss_core.search_core.host import VSSSearch


@pytest.fixture
def environment(monkeypatch):
    services = {
        name: Service(url="http://fixture/" + name)
        for name in ["elasticsearch", "rt_embed", "rtvi_cv"]
    }
    deployment = Deployment(base_url="http://fixture", services=services)
    host = Mock()
    host.search = AsyncMock()
    host.aclose = AsyncMock()
    host.search.return_value = Mock()
    host.search.return_value.model_dump.return_value = {"data": []}
    factory = Mock(return_value=host)
    monkeypatch.setattr(VSSSearch, "from_runtime", factory)
    return deployment, host, factory


@pytest.mark.asyncio
async def test_serial_requests_share_one_host_and_close(environment):
    deployment, host, factory = environment
    async with RawSdkRunner(
        deployment=deployment, tuning={"no_merge_adjacent": True}
    ) as runner:
        assert await runner.search("embed", {"query": "forklift"}) == {"data": []}
        assert await runner.search("attribute", {"attributes": ["red shirt"]}) == {
            "data": []
        }
    assert factory.call_count == 1
    assert not hasattr(factory.call_args.args[0], "frame_scoring")
    assert factory.call_args.args[0].merge_adjacent is False
    assert host.search.await_count == 2
    host.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,payload",
    [
        ("embed", {"query": ""}),
        ("attribute", {"attributes": []}),
        ("attribute", {"attributes": ["red shirt"], "max_hits_per_source": 1}),
        ("embed", {"query": "x", "typo": True}),
    ],
)
async def test_invalid_input_never_calls_backend(environment, path, payload):
    deployment, host, _ = environment
    async with RawSdkRunner(deployment=deployment) as runner:
        with pytest.raises((ValueError, InvalidInputError)):
            await runner.search(path, payload)
    host.search.assert_not_awaited()
    host.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_backend_failure_is_not_an_empty_result_and_closes(environment):
    deployment, host, _ = environment
    host.search.side_effect = ConnectionError("fixture unavailable")
    with pytest.raises(ConnectionError, match="fixture unavailable"):
        async with RawSdkRunner(deployment=deployment) as runner:
            await runner.search("embed", {"query": "forklift"})
    host.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_service_is_configuration_failure(environment):
    from vss_cli.config import ConfigError

    deployment, host, _ = environment
    del deployment.services["rtvi_cv"]
    async with RawSdkRunner(deployment=deployment) as runner:
        with pytest.raises(ConfigError):
            await runner.search("attribute", {"attributes": ["red shirt"]})
    host.search.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tuning", [{"no_critic": False}, {"critic_eval_count": 10}, {"unknown": 0}]
)
async def test_unsupported_configuration_rejected(environment, tuning):
    deployment, _, factory = environment
    with pytest.raises(ValueError):
        async with RawSdkRunner(deployment=deployment, tuning=tuning):
            pass
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_cancellation_cleans_up_and_query_overlap_is_rejected(environment):
    deployment, host, _ = environment
    entered = asyncio.Event()

    async def wait(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    host.search.side_effect = wait
    async with RawSdkRunner(deployment=deployment) as runner:
        task = asyncio.create_task(runner.search("embed", {"query": "forklift"}))
        await entered.wait()
        with pytest.raises(RuntimeError, match="serial"):
            await runner.search("embed", {"query": "cart"})
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    host.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_outside_context_rejected(environment):
    deployment, host, _ = environment
    runner = RawSdkRunner(deployment=deployment)
    with pytest.raises(RuntimeError):
        await runner.search("embed", {"query": "forklift"})
    host.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_context_exit_cancels_unfinished_child_query_and_closes(environment):
    deployment, host, _ = environment
    entered = asyncio.Event()

    async def wait(**kwargs):
        entered.set()
        await asyncio.Event().wait()

    host.search.side_effect = wait
    async with RawSdkRunner(deployment=deployment) as runner:
        task = asyncio.create_task(runner.search("embed", {"query": "forklift"}))
        await entered.wait()
    assert task.cancelled()
    host.aclose.assert_awaited_once()


def test_cross_loop_reuse_rejected_before_backend_call(environment):
    deployment, host, _ = environment
    runner = RawSdkRunner(deployment=deployment)
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(runner.__aenter__())
        with pytest.raises(RuntimeError, match="original event loop"):
            asyncio.run(runner.search("embed", {"query": "forklift"}))
        host.search.assert_not_awaited()
        loop.run_until_complete(runner.__aexit__())
        host.aclose.assert_awaited_once()
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_query_cache_policy_clears_owned_caches_before_request(environment):
    from types import SimpleNamespace

    deployment, host, _ = environment
    cache = Mock()
    host._search = SimpleNamespace(
        _embed=SimpleNamespace(_embed=SimpleNamespace(_text_cache=cache)),
        _attribute=SimpleNamespace(),
    )
    async with RawSdkRunner(
        deployment=deployment, embedding_cache_policy="query"
    ) as runner:
        await runner.search("embed", {"query": "forklift"})
        await runner.search("embed", {"query": "cart"})
    assert cache.clear.call_count == 2


def test_invalid_cache_policy_rejected():
    with pytest.raises(ValueError, match="embedding_cache_policy"):
        RawSdkRunner(embedding_cache_policy="invalid")

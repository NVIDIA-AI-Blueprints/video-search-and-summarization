# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for the Agent-owned RT-VLM tagging lifecycle."""

import asyncio
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.api import vlm_tagging


@pytest.mark.asyncio
async def test_unexpected_live_consumer_failure_stops_remote_stream() -> None:
    admitted_then_failed = asyncio.Event()
    stopped = asyncio.Event()
    ingestor = MagicMock()
    ingestor.register_live_stream = AsyncMock()

    async def failing_live_tags(*_args: Any, admitted: asyncio.Future[None], **_kwargs: Any):
        admitted.set_result(None)
        admitted_then_failed.set()
        await asyncio.sleep(0)
        raise RuntimeError("SSE disconnected")
        yield 0

    async def stop_live(*_args: Any, **_kwargs: Any) -> None:
        stopped.set()

    ingestor.iter_live_tags = failing_live_tags
    ingestor.stop_live = AsyncMock(side_effect=stop_live)
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.aclose = AsyncMock()

    vlm_tagging._LIVE_TAG_JOBS.clear()
    with (
        patch("vss_agents.api.vlm_tagging.TagIngestor", return_value=ingestor),
        patch("vss_agents.api.vlm_tagging.httpx.AsyncClient", return_value=client),
    ):
        await vlm_tagging.start_live_tagging(
            vlm_base_url="http://rt-vlm",
            vlm_model="vlm-model",
            sensor_id="sensor",
            source_name="camera",
            stream_url="rtsp://camera/live",
            chunk_duration=5,
        )
        await asyncio.wait_for(admitted_then_failed.wait(), timeout=1)
        await asyncio.wait_for(stopped.wait(), timeout=1)

    assert "sensor" not in vlm_tagging._LIVE_TAG_JOBS
    ingestor.stop_live.assert_awaited_once_with(client, sensor_id="sensor")

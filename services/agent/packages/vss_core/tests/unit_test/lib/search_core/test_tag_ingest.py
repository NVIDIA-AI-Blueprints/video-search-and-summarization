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
"""Tests for controlled RT-VLM tag document ingestion."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_core.search_core.tag_ingest import TagIngestor


def _ingestor() -> TagIngestor:
    return TagIngestor(
        vlm_base_url="http://rt-vlm",
        vlm_model="vlm-model",
    )


def test_validate_chunk_accepts_contract_and_timestamp() -> None:
    _ingestor()._validate_chunk(
        {
            "chunk_id": 7,
            "start_time": "2026-08-11T10:00:00.000Z",
            "end_time": "2026-08-11T10:00:05.000Z",
            "content": '{"tags":[" Forklift ","worker","forklift"],"description":" Loading a pallet. "}',
        },
        source_type="Video",
    )


def test_validate_chunk_requires_request_id_for_live_session() -> None:
    chunk = {
        "chunk_id": 0,
        "start_time": "10",
        "end_time": "15",
        "content": '{"tags":["person"],"description":"A person walks."}',
    }

    with pytest.raises(ValueError, match="response id"):
        _ingestor()._validate_chunk(chunk, source_type="Camera")
    _ingestor()._validate_chunk(chunk, source_type="Camera", request_id="session-one")


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '{"tags":[],"description":"empty"}',
        '{"tags":["worker"]}',
        '{"tags":["worker"],"description":""}',
        '{"subject:person":true,"action:walking":false}',
        f'{{"tags":["{"x" * 65}"],"description":"visible"}}',
        f'{{"tags":["worker"],"description":"{"x" * 1025}"}}',
    ],
)
def test_validate_chunk_rejects_malformed_tag_json(content: str) -> None:
    with pytest.raises(ValueError):
        _ingestor()._validate_chunk(
            {"chunk_id": 0, "start_time": "1", "end_time": "2", "content": content},
            source_type="Camera",
            request_id="session-one",
        )


def test_completion_validates_without_elasticsearch_write() -> None:
    count = _ingestor()._validate_completion(
        {
            "id": "session-one",
            "chunk_responses": [
                {
                    "chunk_id": 2,
                    "start_time": "10",
                    "end_time": "15",
                    "content": '{"tags":["person"],"description":"A person walks."}',
                }
            ],
        },
        sensor_id="abc-def",
        source_type="Camera",
    )

    assert count == 1


def test_validate_chunk_accepts_fenced_exact_contract() -> None:
    _ingestor()._validate_chunk(
        {
            "chunk_id": 0,
            "start_time": "1",
            "end_time": "2",
            "content": '```json\n{"tags":["worker"],"description":"A worker walks."}\n```',
        },
        source_type="Camera",
        request_id="session-one",
    )


def test_completion_skips_bad_chunk_and_validates_later_live_chunk() -> None:
    count = _ingestor()._validate_completion(
        {
            "id": "session-one",
            "chunk_responses": [
                {"chunk_id": 0, "start_time": "0", "end_time": "5", "content": "not-json"},
                {
                    "chunk_id": 1,
                    "start_time": "5",
                    "end_time": "10",
                    "content": '{"tags":["forklift"],"description":"A forklift moves."}',
                },
            ],
        },
        sensor_id="sensor",
        source_type="Camera",
    )

    assert count == 1


@pytest.mark.asyncio
async def test_uploaded_video_removes_temporary_rt_vlm_asset() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    response = MagicMock()
    response.json.return_value = {
        "chunk_responses": [
            {
                "chunk_id": 0,
                "start_time": "2026-08-11T10:00:00.000Z",
                "end_time": "2026-08-11T10:00:05.000Z",
                "content": '{"tags":["worker"],"description":"A worker walks."}',
            }
        ]
    }
    cleanup = MagicMock(status_code=200)
    client.post = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=cleanup)

    with patch("vss_core.search_core.tag_ingest.httpx.AsyncClient", return_value=client):
        count = await _ingestor().ingest_video(
            sensor_id="123e4567-e89b-12d3-a456-426614174000",
            video_url="http://vst/clip.mp4",
            creation_time="2026-08-11T10:00:00.000Z",
        )

    assert count == 1
    response.raise_for_status.assert_called_once_with()
    client.delete.assert_awaited_once_with(
        "http://rt-vlm/v1/files/123e4567-e89b-12d3-a456-426614174000",
        headers={"x-stream-id": "123e4567-e89b-12d3-a456-426614174000"},
    )


@pytest.mark.asyncio
async def test_uploaded_video_rejects_zero_valid_chunks_and_cleans_up() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    response = MagicMock()
    response.json.return_value = {
        "chunk_responses": [{"chunk_id": 0, "start_time": "0", "end_time": "5", "content": "not-json"}]
    }
    client.post = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=MagicMock(status_code=204))

    with patch("vss_core.search_core.tag_ingest.httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="no valid tag chunks"):
            await _ingestor().ingest_video(
                sensor_id="sensor",
                video_url="http://vst/clip.mp4",
                creation_time="2025-01-01T00:00:00.000Z",
            )

    client.delete.assert_awaited_once()


@pytest.mark.asyncio
async def test_uploaded_video_cleans_up_when_response_json_is_malformed() -> None:
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    response = MagicMock()
    response.json.side_effect = ValueError("malformed JSON")
    client.post = AsyncMock(return_value=response)
    client.delete = AsyncMock(return_value=MagicMock(status_code=204))

    with patch("vss_core.search_core.tag_ingest.httpx.AsyncClient", return_value=client):
        with pytest.raises(ValueError, match="malformed JSON"):
            await _ingestor().ingest_video(
                sensor_id="sensor",
                video_url="http://vst/clip.mp4",
                creation_time="2025-01-01T00:00:00.000Z",
            )

    client.delete.assert_awaited_once()

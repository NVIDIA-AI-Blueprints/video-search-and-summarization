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

from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_core.search_core.tag_ingest import TagIngestor


class _ElasticRecorder:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def index(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(kwargs)
        return {"result": "created"}

    async def search(self, **_kwargs: Any) -> dict[str, Any]:
        return {}

    async def aclose(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return "http://elasticsearch"


def _ingestor(es: _ElasticRecorder | None = None) -> TagIngestor:
    return TagIngestor(
        vlm_base_url="http://rt-vlm",
        vlm_model="vlm-model",
        es=es or _ElasticRecorder(),
    )


def test_build_document_normalizes_contract_and_timestamp() -> None:
    document_id, document = _ingestor().build_document(
        {
            "chunk_id": 7,
            "start_time": "2026-08-11T10:00:00.000Z",
            "end_time": "2026-08-11T10:00:05.000Z",
            "content": '{"tags":[" Forklift ","worker","forklift"],"description":" Loading a pallet. "}',
        },
        sensor_id="abc-def",
        source_name="warehouse.mp4",
        source_type="Video",
    )

    assert document_id == "vlm-tag:abc-def:7"
    assert document["text"] == '{"description":"Loading a pallet.","tags":["forklift","worker"]}'
    assert document["sensor"] == {"id": "abc-def", "type": "Video", "description": "warehouse.mp4"}
    metadata = document["metadata"]["content_metadata"]
    assert metadata["sensorId"] == "abc-def"
    assert metadata["doc_type"] == "raw_events"
    assert metadata["end_ntp_float"] - metadata["start_ntp_float"] == 5


def test_build_document_uses_request_id_for_live_session_identity() -> None:
    chunk = {
        "chunk_id": 0,
        "start_time": "10",
        "end_time": "15",
        "content": '{"tags":["person"],"description":"A person walks."}',
    }

    first_id, _ = _ingestor().build_document(
        chunk,
        sensor_id="sensor",
        source_name="camera",
        source_type="Camera",
        request_id="session-one",
    )
    second_id, _ = _ingestor().build_document(
        chunk,
        sensor_id="sensor",
        source_name="camera",
        source_type="Camera",
        request_id="session-two",
    )

    assert first_id == "vlm-tag:sensor:session-one:0"
    assert second_id == "vlm-tag:sensor:session-two:0"


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        '{"tags":[],"description":"empty"}',
        '{"tags":["worker"]}',
        '{"tags":["worker"],"description":""}',
        f'{{"tags":["{"x" * 65}"],"description":"visible"}}',
        f'{{"tags":["worker"],"description":"{"x" * 1025}"}}',
    ],
)
def test_build_document_rejects_malformed_tag_json(content: str) -> None:
    with pytest.raises(ValueError):
        _ingestor().build_document(
            {"chunk_id": 0, "start_time": "1", "end_time": "2", "content": content},
            sensor_id="sensor",
            source_name="camera",
            source_type="Camera",
        )


@pytest.mark.asyncio
async def test_completion_indexes_deterministic_per_source_document() -> None:
    es = _ElasticRecorder()
    ingestor = _ingestor(es)

    count = await ingestor._index_completion(
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
        source_name="camera one",
        source_type="Camera",
    )

    assert count == 1
    assert es.calls[0]["index"] == "default_abc_def"
    assert es.calls[0]["id"] == "vlm-tag:abc-def:session-one:2"
    assert es.calls[0]["refresh"] == "wait_for"


@pytest.mark.asyncio
async def test_completion_skips_bad_chunk_and_indexes_later_live_chunk() -> None:
    es = _ElasticRecorder()
    ingestor = _ingestor(es)

    count = await ingestor._index_completion(
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
        source_name="camera",
        source_type="Camera",
    )

    assert count == 1
    assert es.calls[0]["id"] == "vlm-tag:sensor:session-one:1"


@pytest.mark.asyncio
async def test_uploaded_video_removes_temporary_rt_vlm_asset() -> None:
    es = _ElasticRecorder()
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
        count = await _ingestor(es).ingest_video(
            sensor_id="123e4567-e89b-12d3-a456-426614174000",
            source_name="clip",
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
                source_name="clip",
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
                source_name="clip",
                video_url="http://vst/clip.mp4",
                creation_time="2025-01-01T00:00:00.000Z",
            )

    client.delete.assert_awaited_once()

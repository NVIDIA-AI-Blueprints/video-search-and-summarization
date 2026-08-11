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
"""RT-VLM tag generation and direct Elasticsearch indexing.

The HTTP response is the publication boundary: only validated tagging output
is indexed. This keeps interactive RT-VLM calls out of the tag index without
requiring request-scoped Kafka behavior from RT-VLM.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from collections.abc import Mapping
from datetime import UTC
from datetime import datetime
import json
import logging
import math
import re
from typing import TYPE_CHECKING
from typing import Any

import httpx

if TYPE_CHECKING:
    import asyncio

    from .clients.protocols import ElasticIndex

DEFAULT_TAG_PROMPT = (
    "Analyze only this video interval. Return JSON only with exactly two fields: "
    '"tags", an array of concise visible concepts, actions, objects, and events; '
    'and "description", one concise factual sentence. Do not infer facts that are not visible.'
)

_MAX_TAGS = 32
_MAX_TAG_LENGTH = 64
_MAX_DESCRIPTION_LENGTH = 1024
_INDEX_UNSAFE_RE = re.compile(r"[-/\\ ]")

logger = logging.getLogger(__name__)


class TagIngestor:
    """Generate controlled chunk tags with RT-VLM and index them for BM25."""

    def __init__(
        self,
        *,
        vlm_base_url: str,
        vlm_model: str,
        es: ElasticIndex,
        prompt: str = DEFAULT_TAG_PROMPT,
        chunk_duration: int = 5,
        request_timeout: float = 600.0,
    ) -> None:
        if not vlm_base_url.strip():
            raise ValueError("vlm_base_url must not be empty")
        if not vlm_model.strip():
            raise ValueError("vlm_model must not be empty")
        if not prompt.strip():
            raise ValueError("tag prompt must not be empty")
        if chunk_duration <= 0:
            raise ValueError("chunk_duration must be positive")
        self._vlm_base_url = vlm_base_url.rstrip("/")
        self._vlm_model = vlm_model
        self._es = es
        self._prompt = prompt
        self._chunk_duration = chunk_duration
        self._request_timeout = request_timeout

    def _query(
        self, *, sensor_id: str, stream: bool, url: str | None = None, creation_time: str | None = None
    ) -> dict[str, Any]:
        query: dict[str, Any] = {
            "id": sensor_id,
            "model": self._vlm_model,
            "prompt": self._prompt,
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "chunk_duration": self._chunk_duration,
            "stream": stream,
        }
        if url is not None:
            query["url"] = url
        if creation_time is not None:
            query["creation_time"] = creation_time
        return query

    async def ingest_video(
        self,
        *,
        sensor_id: str,
        source_name: str,
        video_url: str,
        creation_time: str,
    ) -> int:
        """Tag an uploaded video and return the number of indexed chunks."""
        async with httpx.AsyncClient(timeout=self._request_timeout) as client:
            response = await client.post(
                f"{self._vlm_base_url}/v1/generate_captions",
                json=self._query(
                    sensor_id=sensor_id,
                    stream=False,
                    url=video_url,
                    creation_time=creation_time,
                ),
                headers={"x-stream-id": sensor_id},
            )
            response.raise_for_status()
            try:
                payload = response.json()
                indexed = await self._index_completion(
                    payload,
                    sensor_id=sensor_id,
                    source_name=source_name,
                    source_type="Video",
                )
                if indexed == 0:
                    raise ValueError("RT-VLM returned no valid tag chunks")
                return indexed
            finally:
                try:
                    cleanup = await client.delete(
                        f"{self._vlm_base_url}/v1/files/{sensor_id}",
                        headers={"x-stream-id": sensor_id},
                    )
                    if cleanup.status_code not in (200, 204, 404):
                        cleanup.raise_for_status()
                except Exception:
                    logger.warning("Could not remove temporary RT-VLM asset %s", sensor_id, exc_info=True)

    async def register_live_stream(
        self,
        client: httpx.AsyncClient,
        *,
        sensor_id: str,
        source_name: str,
        stream_url: str,
    ) -> None:
        response = await client.post(
            f"{self._vlm_base_url}/v1/streams/add",
            json={
                "streams": [
                    {
                        "liveStreamUrl": stream_url,
                        "description": source_name,
                        "sensor_name": sensor_id,
                        "id": sensor_id,
                    }
                ]
            },
            headers={"x-stream-id": sensor_id},
        )
        response.raise_for_status()
        payload = response.json() if response.content else {}
        if isinstance(payload, Mapping) and payload.get("errors") and not payload.get("results"):
            raise ValueError(f"RT-VLM stream registration failed: {payload['errors']}")

    async def iter_live_tags(
        self,
        client: httpx.AsyncClient,
        *,
        sensor_id: str,
        source_name: str,
        admitted: asyncio.Future[None] | None = None,
    ) -> AsyncIterator[int]:
        """Consume a live caption SSE response and yield each indexed chunk count."""
        async with client.stream(
            "POST",
            f"{self._vlm_base_url}/v1/generate_captions",
            json=self._query(sensor_id=sensor_id, stream=True),
            headers={"Accept": "text/event-stream", "x-stream-id": sensor_id},
        ) as response:
            response.raise_for_status()
            if admitted is not None and not admitted.done():
                admitted.set_result(None)
            async for line in response.aiter_lines():
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                data = line[5:].strip() if line.startswith("data:") else line
                if data == "[DONE]":
                    return
                payload = json.loads(data)
                count = await self._index_completion(
                    payload,
                    sensor_id=sensor_id,
                    source_name=source_name,
                    source_type="Camera",
                )
                if count:
                    yield count

    async def stop_live(self, client: httpx.AsyncClient, *, sensor_id: str) -> None:
        headers = {"x-stream-id": sensor_id}
        stop_response = await client.delete(
            f"{self._vlm_base_url}/v1/generate_captions/{sensor_id}",
            headers=headers,
        )
        if stop_response.status_code not in (200, 204, 404):
            stop_response.raise_for_status()
        delete_response = await client.delete(
            f"{self._vlm_base_url}/v1/streams/delete/{sensor_id}",
            headers=headers,
        )
        if delete_response.status_code not in (200, 204, 404):
            delete_response.raise_for_status()

    async def _index_completion(
        self,
        payload: Any,
        *,
        sensor_id: str,
        source_name: str,
        source_type: str,
    ) -> int:
        if not isinstance(payload, Mapping):
            raise ValueError("RT-VLM response must be an object")
        chunks = payload.get("chunk_responses", [])
        if not isinstance(chunks, list):
            raise ValueError("RT-VLM chunk_responses must be a list")
        request_id = payload.get("id")
        if source_type == "Camera" and (not isinstance(request_id, str) or not request_id.strip()):
            raise ValueError("RT-VLM live response id must be a non-empty string")
        indexed = 0
        for chunk in chunks:
            try:
                document_id, document = self.build_document(
                    chunk,
                    sensor_id=sensor_id,
                    source_name=source_name,
                    source_type=source_type,
                    request_id=request_id if isinstance(request_id, str) else "",
                )
            except (TypeError, ValueError):
                logger.warning("Skipping malformed RT-VLM tag chunk for sensor %s", sensor_id, exc_info=True)
                continue
            index_name = f"default_{_INDEX_UNSAFE_RE.sub('_', sensor_id)}"
            await self._es.index(index=index_name, id=document_id, document=document, refresh="wait_for")
            indexed += 1
        return indexed

    @staticmethod
    def build_document(
        chunk: Any,
        *,
        sensor_id: str,
        source_name: str,
        source_type: str,
        request_id: str = "",
    ) -> tuple[str, dict[str, Any]]:
        if not isinstance(chunk, Mapping):
            raise ValueError("RT-VLM chunk must be an object")
        chunk_id = chunk.get("chunk_id")
        if isinstance(chunk_id, bool) or not isinstance(chunk_id, int) or chunk_id < 0:
            raise ValueError("RT-VLM chunk_id must be a non-negative integer")
        tags, description = TagIngestor._normalize_content(chunk.get("content"))
        start_epoch = TagIngestor._timestamp(chunk.get("start_time"))
        end_epoch = TagIngestor._timestamp(chunk.get("end_time"))
        if end_epoch < start_epoch:
            raise ValueError("RT-VLM chunk end precedes start")
        text = json.dumps({"tags": tags, "description": description}, separators=(",", ":"), sort_keys=True)
        document = {
            "text": text,
            "sensor": {"id": sensor_id, "type": source_type, "description": source_name},
            "metadata": {
                "source": source_name,
                "content_metadata": {
                    "streamId": sensor_id,
                    "sensorId": sensor_id,
                    "cameraId": sensor_id,
                    "chunkIdx": chunk_id,
                    "doc_type": "raw_events",
                    "start_ntp_float": start_epoch,
                    "end_ntp_float": end_epoch,
                },
            },
        }
        if source_type == "Camera":
            if not request_id.strip():
                raise ValueError("RT-VLM live response id must be a non-empty string")
            document_id = f"vlm-tag:{sensor_id}:{request_id}:{chunk_id}"
        else:
            document_id = f"vlm-tag:{sensor_id}:{chunk_id}"
        return document_id, document

    @staticmethod
    def _normalize_content(content: Any) -> tuple[list[str], str]:
        if not isinstance(content, str):
            raise ValueError("RT-VLM tag content must be a JSON string")
        payload = json.loads(content)
        if not isinstance(payload, dict) or set(payload) != {"tags", "description"}:
            raise ValueError("RT-VLM tag content must contain exactly tags and description")
        raw_tags = payload["tags"]
        description = payload["description"]
        if not isinstance(raw_tags, list) or not 1 <= len(raw_tags) <= _MAX_TAGS:
            raise ValueError("RT-VLM tags must contain between 1 and 32 items")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("RT-VLM description must be a non-empty string")
        description = description.strip()
        if len(description) > _MAX_DESCRIPTION_LENGTH:
            raise ValueError(f"RT-VLM description must not exceed {_MAX_DESCRIPTION_LENGTH} characters")
        tags: list[str] = []
        for raw_tag in raw_tags:
            if not isinstance(raw_tag, str) or not raw_tag.strip():
                raise ValueError("RT-VLM tags must be non-empty strings")
            tag = raw_tag.strip().lower()
            if len(tag) > _MAX_TAG_LENGTH:
                raise ValueError(f"RT-VLM tags must not exceed {_MAX_TAG_LENGTH} characters")
            if tag not in tags:
                tags.append(tag)
        return tags, description

    @staticmethod
    def _timestamp(value: Any) -> float:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("RT-VLM timestamp must be a non-empty string")
        try:
            timestamp = float(value)
        except ValueError:
            try:
                timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).timestamp()
            except ValueError as exc:
                raise ValueError("RT-VLM timestamp must be numeric or ISO 8601") from exc
        if not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("RT-VLM timestamp must be finite and non-negative")
        return timestamp

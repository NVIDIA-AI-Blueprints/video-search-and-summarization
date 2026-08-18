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
"""Agent lifecycle bridge for uploaded-video and RTSP VLM tagging."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
import logging

import httpx

from vss_core.search_core import TagIngestor

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _LiveTagJob:
    task: asyncio.Task[None]
    ingestor: TagIngestor


_LIVE_TAG_JOBS: dict[str, _LiveTagJob] = {}


async def ingest_uploaded_video_tags(
    *,
    vlm_base_url: str,
    vlm_model: str,
    sensor_id: str,
    video_url: str,
    creation_time: str,
    chunk_duration: int,
) -> int:
    """Run the finite uploaded-video tagging job; Kafka owns persistence."""
    ingestor = TagIngestor(
        vlm_base_url=vlm_base_url,
        vlm_model=vlm_model,
        chunk_duration=chunk_duration,
    )
    return await ingestor.ingest_video(
        sensor_id=sensor_id,
        video_url=video_url,
        creation_time=creation_time,
    )


async def start_live_tagging(
    *,
    vlm_base_url: str,
    vlm_model: str,
    sensor_id: str,
    source_name: str,
    stream_url: str,
    chunk_duration: int,
) -> None:
    """Register a live stream and retain its SSE consumer as an agent task."""
    await stop_live_tagging(sensor_id=sensor_id, ignore_missing=True)
    ingestor = TagIngestor(
        vlm_base_url=vlm_base_url,
        vlm_model=vlm_model,
        chunk_duration=chunk_duration,
        request_timeout=60.0,
    )
    registration_client = httpx.AsyncClient(timeout=60.0)
    try:
        await ingestor.register_live_stream(
            registration_client,
            sensor_id=sensor_id,
            source_name=source_name,
            stream_url=stream_url,
        )
    except BaseException:
        try:
            await ingestor.stop_live(registration_client, sensor_id=sensor_id)
        except Exception:
            logger.warning("Could not roll back RT-VLM registration for sensor %s", sensor_id, exc_info=True)
        raise
    finally:
        await registration_client.aclose()

    async def consume() -> None:
        try:
            timeout = httpx.Timeout(connect=60.0, read=None, write=60.0, pool=60.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async for _ in ingestor.iter_live_tags(
                    client,
                    sensor_id=sensor_id,
                    admitted=admitted,
                ):
                    pass
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if not admitted.done():
                admitted.set_exception(exc)
            logger.exception("VLM live tagging failed for sensor %s", sensor_id)
        finally:
            current = _LIVE_TAG_JOBS.get(sensor_id)
            unexpected_exit = current is not None and current.task is asyncio.current_task()
            if unexpected_exit:
                _LIVE_TAG_JOBS.pop(sensor_id, None)
                try:
                    async with httpx.AsyncClient(timeout=60.0) as cleanup_client:
                        await ingestor.stop_live(cleanup_client, sensor_id=sensor_id)
                except Exception:
                    logger.warning(
                        "Could not stop RT-VLM after live tagging ended for sensor %s",
                        sensor_id,
                        exc_info=True,
                    )

    admitted: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(consume(), name=f"vlm-tagging-{sensor_id}")
    _LIVE_TAG_JOBS[sensor_id] = _LiveTagJob(task=task, ingestor=ingestor)
    try:
        await asyncio.wait_for(asyncio.shield(admitted), timeout=60.0)
    except BaseException:
        try:
            await stop_live_tagging(sensor_id=sensor_id, vlm_base_url=vlm_base_url)
        except Exception:
            logger.warning("Could not roll back VLM live tagging for sensor %s", sensor_id, exc_info=True)
        raise


async def stop_live_tagging(
    *,
    sensor_id: str,
    vlm_base_url: str = "",
    ignore_missing: bool = False,
) -> None:
    """Stop the local consumer and remove the stream from RT-VLM."""
    job = _LIVE_TAG_JOBS.pop(sensor_id, None)
    if job is None:
        if ignore_missing or not vlm_base_url:
            return
        headers = {"x-stream-id": sensor_id}
        async with httpx.AsyncClient(timeout=60.0) as client:
            for path in (f"/v1/generate_captions/{sensor_id}", f"/v1/streams/delete/{sensor_id}"):
                response = await client.delete(f"{vlm_base_url.rstrip('/')}{path}", headers=headers)
                if response.status_code not in (200, 204, 404):
                    response.raise_for_status()
        return

    job.task.cancel()
    with suppress(asyncio.CancelledError):
        await job.task

    async with httpx.AsyncClient(timeout=60.0) as client:
        await job.ingestor.stop_live(client, sensor_id=sensor_id)

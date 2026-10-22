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

"""Private HTTP/SSE transport for the RTVI ingestion backend."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from httpx_sse import aconnect_sse

from .contracts import RequestSource, VlmModelInfo


class _RTVITransportError(Exception):
    """RTVI transport request or response failure."""


class _RTVITransport:
    """Private async transport used by the public RTVI backend."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = 600,
        api_key: str | None = None,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
        ready_path: str = "/v1/health/ready",
        environment_prefix: str = "RTVI",
        default_base_url: str = "http://127.0.0.1:8018",
    ) -> None:
        self._owns_client = client is None
        self._prepared_uploads: set[str] = set()
        resolved_api_key = (
            api_key
            if api_key is not None
            else os.getenv(f"{environment_prefix}_API_KEY")
        )
        self._headers = (
            {"Authorization": f"Bearer {resolved_api_key}"} if resolved_api_key else {}
        )
        self._model = (
            model if model is not None else os.getenv(f"{environment_prefix}_MODEL")
        )
        self._ready_path = ready_path
        self._client = client or httpx.AsyncClient(
            base_url=(
                base_url
                or os.getenv(f"{environment_prefix}_BASE_URL")
                or default_base_url
            ).rstrip("/"),
            timeout=httpx.Timeout(timeout_s),
        )

    async def __aenter__(self) -> _RTVITransport:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def check_health(self) -> bool:
        """Return whether RTVI reports that it is ready."""

        try:
            response = await self._client.get(
                self._ready_path,
                headers=self._headers,
            )
            return response.status_code == httpx.codes.OK
        except httpx.HTTPError:
            return False

    async def add_shared_stream(
        self,
        *,
        source_id: str,
        camera_id: str,
        frame_source: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Register a per-request CUDA IPC source through the CV-compatible API."""

        response = await self._request(
            "POST",
            "/api/v1/stream/add",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": camera_id or source_id,
                    "camera_name": camera_id or source_id,
                    "frame_source": frame_source,
                    "change": "camera_add",
                    "metadata": metadata,
                },
            },
        )
        try:
            body = response.json()
        except ValueError as error:
            raise _RTVITransportError(
                "RTVI shared stream response contained malformed JSON"
            ) from error
        asset_id = body.get("asset_id") if isinstance(body, dict) else None
        if not isinstance(asset_id, str) or not asset_id:
            raise _RTVITransportError(
                "RTVI shared stream response did not contain an asset_id"
            )
        return asset_id

    async def remove_shared_stream(self, *, camera_id: str) -> None:
        """Detach a shared stream and wait for service-side cleanup."""

        await self._request(
            "POST",
            "/api/v1/stream/remove",
            json={
                "key": "sensor",
                "value": {
                    "camera_id": camera_id,
                    "change": "camera_remove",
                },
            },
        )

    async def list_models(self) -> tuple[str, ...]:
        """Return model identifiers exposed by RTVI."""

        response = await self._request("GET", "/v1/models")
        data = response.json()
        return tuple(
            model["id"]
            for model in data.get("data", ())
            if isinstance(model, dict) and isinstance(model.get("id"), str)
        )

    async def get_model_info(self) -> VlmModelInfo:
        """Return the configured model or discover the first RTVI model."""

        model = VlmModelInfo()
        if self._model is not None:
            model.id = self._model
            return model

        response = await self._request("GET", "/v1/models")
        models = response.json().get("data", ())
        if not models:
            raise _RTVITransportError("RTVI returned no model information")
        model_data = models[0]
        model.id = model_data.get("id", "")
        model.created = model_data.get("created", 0)
        model.owned_by = model_data.get("owned_by", "")
        model.api_type = model_data.get("api_type", "")
        return model

    async def upload_video(self, path: str | Path) -> RequestSource:
        """Upload a local video and return its ingestion source."""

        video_path = Path(path)
        media_type = mimetypes.guess_type(video_path.name)[0] or "video/mp4"
        with video_path.open("rb") as video:
            response = await self._request(
                "POST",
                "/v1/files",
                data={"purpose": "vision", "media_type": "video"},
                files={
                    "file": (
                        video_path.name,
                        video,
                        media_type,
                    )
                },
            )
        body = _response_object(response, "RTVI upload response")
        asset_id = body.get("id")
        if not isinstance(asset_id, str) or not asset_id:
            raise _RTVITransportError("RTVI upload response did not contain an id")
        return RequestSource(source_id=asset_id)

    async def prepare_source(self, source: RequestSource) -> RequestSource:
        """Upload local files while leaving endpoint IDs and URLs unchanged."""

        if source.url is None:
            return source
        path = Path(source.url)
        if not path.is_file():
            return source
        prepared = await self.upload_video(path)
        prepared.camera_id = source.camera_id
        self._prepared_uploads.add(prepared.source_id)
        return prepared

    async def release_source(self, source: RequestSource) -> None:
        """Delete a source uploaded automatically for ingestion."""

        if source.source_id not in self._prepared_uploads:
            return
        deletion = asyncio.create_task(self.delete_video(source))
        cancellation: asyncio.CancelledError | None = None
        while not deletion.done():
            try:
                await asyncio.shield(deletion)
            except asyncio.CancelledError as error:
                cancellation = cancellation or error
        deletion.result()
        self._prepared_uploads.discard(source.source_id)
        if cancellation is not None:
            raise cancellation

    async def delete_video(
        self,
        source: RequestSource | str,
        *,
        attempts: int = 10,
        retry_delay_s: float = 0.2,
    ) -> None:
        """Delete an uploaded video after RTVI releases it."""

        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        source_id = source.source_id if isinstance(source, RequestSource) else source
        for attempt in range(attempts):
            try:
                await self._request("DELETE", f"/v1/files/{source_id}")
                return
            except _RTVITransportError as error:
                cause = error.__cause__
                is_conflict = (
                    isinstance(cause, httpx.HTTPStatusError)
                    and cause.response.status_code == httpx.codes.CONFLICT
                )
                if not is_conflict or attempt == attempts - 1:
                    raise
                await asyncio.sleep(retry_delay_s)

    @asynccontextmanager
    async def uploaded_video(self, path: str | Path) -> AsyncIterator[RequestSource]:
        """Upload a video and delete it when the context exits."""

        source = await self.upload_video(path)
        try:
            yield source
        finally:
            await self.delete_video(source)

    async def generate_captions_stream(
        self,
        *,
        source_id: str,
        prompt: str,
        model: str,
        url: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
        generation_config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield RTVI ``VlmCaptionsCompletionResponse`` SSE payloads."""

        payload: dict[str, Any] = {
            "id": source_id,
            "prompt": prompt,
            "model": model,
            "stream": True,
            "chunk_duration": chunk_duration,
            "chunk_overlap_duration": chunk_overlap_duration,
        }
        if url is not None:
            payload["url"] = url
            payload["media_type"] = "video"
        if generation_config:
            payload.update(generation_config)

        async for body in self._stream_json(
            "/v1/generate_captions",
            payload,
            source_id=source_id,
            response_name="caption",
        ):
            yield body

    async def generate_frame_captions(
        self,
        *,
        frames: list[Any],
        chunk_start_s: float,
        chunk_end_s: float,
        prompt: str,
        model: str,
        generation_config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run sampled RGB frames through RTVI's normal multi-image pipeline."""

        if not frames:
            raise ValueError("frames must not be empty")
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if not model:
            raise ValueError("model must not be empty")
        if generation_config:
            owned_fields = {
                "id",
                "prompt",
                "model",
                "stream",
                "chunk_duration",
                "chunk_overlap_duration",
                "media_type",
                "frame_times",
                "frame_chunk_start",
                "frame_chunk_end",
            }
            reserved = owned_fields & generation_config.keys()
            if reserved:
                names = ", ".join(sorted(reserved))
                raise ValueError(
                    f"generation_config cannot override reserved fields: {names}"
                )

        upload_ids: list[str] = []
        primary_error: BaseException | None = None
        try:
            for index, frame in enumerate(frames):
                upload_ids.append(await self._upload_rgb_frame(frame, index))
            payload: dict[str, Any] = {
                "id": upload_ids,
                "prompt": prompt,
                "model": model,
                "stream": False,
                "chunk_duration": 0,
                "chunk_overlap_duration": 0,
                "media_type": "image",
                "frame_times": [frame.timestamp_ns / 1_000_000_000 for frame in frames],
                "frame_chunk_start": chunk_start_s,
                "frame_chunk_end": chunk_end_s,
            }
            if generation_config:
                payload.update(generation_config)
            response = await self._request(
                "POST", "/v1/generate_captions", json=payload
            )
            try:
                body = response.json()
            except ValueError as error:
                raise _RTVITransportError(
                    "RTVI returned malformed frame caption JSON"
                ) from error
            chunks = body.get("chunk_responses") if isinstance(body, dict) else None
            if not isinstance(chunks, list) or len(chunks) != 1:
                raise _RTVITransportError(
                    "RTVI did not return one multi-image caption chunk"
                )
            chunk = chunks[0]
            if not isinstance(chunk, dict):
                raise _RTVITransportError(
                    "RTVI returned an invalid frame caption chunk"
                )
            return chunk
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_error: _RTVITransportError | None = None
            for upload_id in upload_ids:
                try:
                    await self.release_source(RequestSource(source_id=upload_id))
                except _RTVITransportError as error:
                    cleanup_error = cleanup_error or error
            if cleanup_error is not None and primary_error is None:
                raise cleanup_error

    async def _upload_rgb_frame(self, frame: Any, index: int) -> str:
        """Encode one owned RGB frame and register it as an image asset."""

        jpeg = _encode_rgb_frame_jpeg(frame)
        filename = f"shared-frame-{index:03d}.jpg"
        response = await self._request(
            "POST",
            "/v1/files",
            data={"purpose": "vision", "media_type": "image"},
            files={"file": (filename, jpeg, "image/jpeg")},
        )
        body = _response_object(response, "RTVI frame upload response")
        asset_id = body.get("id")
        if not isinstance(asset_id, str) or not asset_id:
            raise _RTVITransportError(
                "RTVI frame upload response did not contain an id"
            )
        self._prepared_uploads.add(asset_id)
        return asset_id

    async def _stream_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        source_id: str,
        response_name: str,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield validated JSON objects from an RTVI data-only SSE response."""

        try:
            async with aconnect_sse(
                self._client,
                "POST",
                path,
                json=payload,
                headers={**self._headers, "x-stream-id": source_id},
            ) as event_source:
                event_source.response.raise_for_status()
                async for event in event_source.aiter_sse():
                    data = event.data.strip()
                    if not data or data in {"[DONE]", "ping", ": ping"}:
                        continue
                    try:
                        body = json.loads(data)
                    except json.JSONDecodeError as error:
                        raise _RTVITransportError(
                            f"RTVI returned malformed {response_name} SSE data"
                        ) from error
                    if not isinstance(body, dict):
                        raise _RTVITransportError(
                            f"RTVI returned invalid {response_name} SSE data"
                        )
                    if body.get("error"):
                        raise _RTVITransportError(str(body["error"]))
                    yield body
        except httpx.HTTPStatusError as error:
            detail = _safe_error_detail(error.response)
            raise _RTVITransportError(
                f"RTVI returned HTTP {error.response.status_code}: {detail}"
            ) from error
        except httpx.HTTPError as error:
            raise _RTVITransportError(f"RTVI request failed: {error}") from error

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            headers = {**self._headers, **kwargs.pop("headers", {})}
            response = await self._client.request(
                method,
                path,
                headers=headers,
                **kwargs,
            )
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as error:
            detail = _safe_error_detail(error.response)
            raise _RTVITransportError(
                f"RTVI returned HTTP {error.response.status_code}: {detail}"
            ) from error
        except httpx.HTTPError as error:
            raise _RTVITransportError(f"RTVI request failed: {error}") from error


class _RTVIEmbedTransport(_RTVITransport):
    """Private RTVI transport configured for the RT-Embed service."""

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_s: float = 600,
        api_key: str | None = None,
        model: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_s=timeout_s,
            api_key=api_key,
            model=model,
            client=client,
            ready_path="/v1/ready",
            environment_prefix="RTVI_EMBED",
            default_base_url="http://127.0.0.1:8017",
        )

    async def generate_video_embeddings_stream(
        self,
        *,
        source_id: str,
        model: str,
        url: str | None = None,
        chunk_duration: int = 0,
        chunk_overlap_duration: int = 0,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield RTVI embedding payloads for uploaded files or live streams."""

        stream = url is None and source_id not in self._prepared_uploads
        payload: dict[str, Any] = {
            "id": [source_id],
            "model": model,
            "stream": stream,
            "chunk_duration": chunk_duration,
            "chunk_overlap_duration": chunk_overlap_duration,
        }
        if url is not None:
            payload["url"] = url
            payload["media_type"] = "video"
        if stream:
            async for body in self._stream_json(
                "/v1/generate_video_embeddings",
                payload,
                source_id=source_id,
                response_name="video embedding",
            ):
                yield body
            return

        response = await self._request(
            "POST",
            "/v1/generate_video_embeddings",
            json=payload,
            headers={"x-stream-id": source_id},
        )
        try:
            body = response.json()
        except ValueError as error:
            raise _RTVITransportError(
                "RTVI returned malformed video embedding JSON"
            ) from error
        if not isinstance(body, dict):
            raise _RTVITransportError("RTVI returned invalid video embedding JSON")
        if body.get("error"):
            raise _RTVITransportError(str(body["error"]))
        yield body

    async def generate_frame_embeddings(
        self,
        *,
        frames: list[Any],
        chunk_start_s: float,
        chunk_end_s: float,
        model: str,
    ) -> dict[str, Any]:
        """Upload sampled RGB frames and return one multi-image embedding."""

        if not frames:
            raise ValueError("frames must not be empty")
        if not model:
            raise ValueError("model must not be empty")

        upload_ids: list[str] = []
        primary_error: BaseException | None = None
        try:
            for index, frame in enumerate(frames):
                upload_ids.append(await self._upload_rgb_frame(frame, index))
            response = await self._request(
                "POST",
                "/v1/generate_video_embeddings",
                json={
                    "id": upload_ids,
                    "model": model,
                    "stream": False,
                    "chunk_duration": 0,
                    "chunk_overlap_duration": 0,
                    "media_type": "image",
                    "frame_times": [
                        frame.timestamp_ns / 1_000_000_000 for frame in frames
                    ],
                    "frame_chunk_start": chunk_start_s,
                    "frame_chunk_end": chunk_end_s,
                },
            )
            try:
                body = response.json()
            except ValueError as error:
                raise _RTVITransportError(
                    "RTVI-Embed returned malformed frame embedding JSON"
                ) from error
            if not isinstance(body, dict):
                raise _RTVITransportError(
                    "RTVI-Embed returned invalid frame embedding JSON"
                )
            chunks = body.get("chunk_responses")
            if not isinstance(chunks, list) or len(chunks) != 1:
                raise _RTVITransportError(
                    "RTVI-Embed did not return one multi-image embedding chunk"
                )
            chunk = chunks[0]
            if not isinstance(chunk, dict):
                raise _RTVITransportError(
                    "RTVI-Embed returned an invalid frame embedding chunk"
                )
            return chunk
        except BaseException as error:
            primary_error = error
            raise
        finally:
            cleanup_error: _RTVITransportError | None = None
            for upload_id in upload_ids:
                try:
                    await self.release_source(RequestSource(source_id=upload_id))
                except _RTVITransportError as error:
                    cleanup_error = cleanup_error or error
            if cleanup_error is not None and primary_error is None:
                raise cleanup_error


def _encode_rgb_frame_jpeg(frame: Any) -> bytes:
    """Encode an owned HxWx3 RGB frame for an RTVI image request."""

    try:
        import cv2
        import numpy as np

        rgb = np.asarray(frame.tensor)
        if rgb.ndim != 3 or rgb.shape[2] != 3:
            raise ValueError("shared frame must have HxWx3 RGB pixels")
        bgr = np.ascontiguousarray(rgb[:, :, ::-1])
        encoded, jpeg = cv2.imencode(".jpg", bgr)
        if not encoded:
            raise ValueError("JPEG encoding failed")
        return jpeg.tobytes()
    except (ImportError, TypeError, ValueError) as error:
        raise _RTVITransportError("could not encode a shared RGB frame") from error


def _safe_error_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.reason_phrase
    if isinstance(body, dict):
        for key in ("detail", "message", "error"):
            if key in body:
                return str(body[key])
    return response.reason_phrase


def _response_object(
    response: httpx.Response,
    response_name: str,
) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError as error:
        raise _RTVITransportError(
            f"{response_name} contained malformed JSON"
        ) from error
    if not isinstance(body, dict):
        raise _RTVITransportError(f"{response_name} must be a JSON object")
    return body

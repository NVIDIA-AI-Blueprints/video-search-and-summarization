# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Source-bound GPU worker access for frame masks and neural inspection."""
import asyncio
import time
from pathlib import Path

import httpx
from fastapi import HTTPException


class SegmentationProxy:
    def __init__(self, store, worker_url: str | None, transport=None):
        self.store = store
        self.worker_url = worker_url
        self.transport = transport

    def context(self, source_id: str | None) -> tuple[dict, str]:
        with self.store.lock:
            source = self.store.source()
            if source_id is not None and source_id != source["id"]:
                raise HTTPException(409, "Selected source differs from segmentation source_id")
            try:
                relative = self.store.media_path().resolve().relative_to(self.store.settings.data_dir.resolve())
            except ValueError as exc:
                raise HTTPException(409, "GPU segmentation requires a selected VIOS recording") from exc
            return source, str(relative)

    async def request(self, method: str, endpoint: str, source: dict, payload=None):
        if not self.worker_url:
            raise HTTPException(503, "GPU segmentation worker is not configured")
        try:
            async with httpx.AsyncClient(timeout=15, transport=self.transport, follow_redirects=False) as client:
                response = await client.request(method, self.worker_url + endpoint,
                                                params={"source_id": source["id"], "source_sha256": source["sha256"],
                                                        "stream_id": source.get("stream_id", ""),
                                                        "source_clock_origin": source.get("actual_start_time", "")},
                                                json=payload)
            if response.status_code >= 400:
                try:
                    detail = response.json().get("detail", "GPU segmentation request failed")
                except ValueError:
                    detail = "GPU segmentation request failed"
                raise HTTPException(response.status_code, str(detail)[:300])
            result = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise HTTPException(503, "GPU segmentation worker is unavailable") from exc
        if (not isinstance(result, dict) or result.get("source_id") != source["id"]
                or result.get("source_sha256") != source["sha256"]):
            raise HTTPException(502, "Segmentation worker returned a different source identity")
        if (result.get("stream_id") != source.get("stream_id")
                or result.get("source_clock_origin") != source.get("actual_start_time")):
            raise HTTPException(502, "Segmentation worker returned a different recording clock")
        current, _ = self.context(source["id"])
        if any(current.get(key) != source.get(key) for key in ("sha256", "stream_id", "actual_start_time")):
            raise HTTPException(409, "Selected recording identity or clock changed during segmentation retrieval")
        return result

    async def status(self, source_id=None):
        source, _ = self.context(source_id)
        if not self.worker_url:
            return {"status": "unavailable", "progress": 0, "source_id": source["id"],
                    "source_sha256": source["sha256"], "error": "GPU segmentation worker is not configured"}
        return await self.request("GET", "/jobs", source)

    async def result(self, source_id=None):
        source, _ = self.context(source_id)
        return await self.request("GET", "/jobs/result", source)

    async def start(self, source_id, force=False):
        source, relative = self.context(source_id)
        payload = {"source_id": source["id"], "source_sha256": source["sha256"], "source_file": relative,
                   "duration": source["duration"], "width": source["width"], "height": source["height"],
                   "fps": source["fps"], "force": force,
                   "source_clock_origin": source.get("actual_start_time"), "stream_id": source.get("stream_id")}
        return await self.request("POST", "/jobs", source, payload)

    async def analyze(self, source_id, expected, progress_callback=None, force=False, timeout=1800):
        """One bounded worker job; errors never fall back to calibrated fills."""
        deadline = time.monotonic() + timeout
        state = await self.start(source_id, force=force)
        while True:
            actual_hashes = {name: (state.get("models") or {}).get(name, {}).get("checkpoint_sha256")
                             for name in ("bottle", "liquid")}
            if (actual_hashes != expected["model_hashes"]
                    or state.get("pipeline_sha256") != expected["segmentation_pipeline_sha256"]
                    or state.get("sampling_mode") != "every-source-frame-v2"):
                raise ValueError("GPU worker differs from the pinned neural measurement contract")
            if progress_callback:
                progress_callback(.92 * min(1., max(0., float(state.get("progress", 0)))))
            if state["status"] == "complete":
                result = await self.result(source_id)
                models = {name: result["models"][name]["checkpoint_sha256"] for name in ("bottle", "liquid")}
                if (result.get("schema_version") != 2 or models != expected["model_hashes"]
                        or result.get("provenance", {}).get("pipeline_sha256") != expected["segmentation_pipeline_sha256"]
                        or result.get("sample_fps") != result.get("source_fps")):
                    raise ValueError("Completed masks do not match the required full-frame model contract")
                return result
            if state["status"] != "running":
                raise RuntimeError(state.get("error", "GPU segmentation did not reach a completed state"))
            if time.monotonic() >= deadline:
                raise TimeoutError("GPU segmentation exceeded the inspection wait limit; its job may still be running")
            await asyncio.sleep(min(1., max(0., deadline-time.monotonic())))
            state = await self.status(source_id)

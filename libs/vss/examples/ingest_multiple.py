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

"""Decode once for VLM, RTVI-VLM, and RTVI-Embed.

The defaults target local RTVI-VLM and RTVI-Embed services::

    uv run --package vss-ingestion python examples/ingest_multiple.py video.mp4 \
        --caption-model nim_nvidia_cosmos3-nano-reasoner_bf16-final
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import httpx

from vss.ingestion import (
    EmbeddingChunkResponse,
    RequestSource,
    VlmChunkResponse,
    ingest,
)

PROMPT = (
    "Describe the important visible events with their start time, "
    "end time, type, and description."
)


def _status(message: str) -> None:
    print(f"[ingest_multiple] {message}", flush=True)


def _timeline(responses: Sequence[object]) -> list[tuple[str, int, int]]:
    timeline: list[tuple[str, int, int]] = []
    for response in responses:
        chunk = getattr(response, "chunk", None)
        if chunk is None:
            raise RuntimeError("backend returned a response without chunk metadata")
        timeline.append(
            (
                chunk.sourceId,
                chunk.start_pts,
                chunk.end_pts,
            )
        )
    return timeline


async def _discover_model(base_url: str, api_key: str | None) -> str:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(f"{base_url.rstrip('/')}/models", headers=headers)
        response.raise_for_status()
    models = response.json().get("data", ())
    if not models or not isinstance(models[0].get("id"), str):
        raise RuntimeError("caption service returned no model")
    return models[0]["id"]


async def main(
    video_path: Path,
    *,
    decoder: str,
    chunk_duration: int,
    caption_base_url: str,
    caption_model: str | None,
    caption_api_key: str | None,
    rtvi_base_url: str,
    rtvi_model: str | None,
    embed_base_url: str,
) -> None:
    _status(f"input video: {video_path}")
    _status(f"decoder: {decoder}; chunk duration: {chunk_duration}s")
    if caption_model is None:
        _status(f"discovering caption model from {caption_base_url}...")
        caption_model = await _discover_model(caption_base_url, caption_api_key)
        _status(f"caption model discovered: {caption_model}")
    else:
        _status(f"caption model: {caption_model}")
    rtvi_model = rtvi_model or caption_model
    _status(f"RTVI-VLM: {rtvi_base_url} ({rtvi_model})")
    _status(f"RTVI-Embed: {embed_base_url}")

    os.environ["VLM_BASE_URL"] = caption_base_url
    os.environ["VLM_MODEL"] = caption_model
    os.environ["RTVI_BASE_URL"] = rtvi_base_url
    os.environ["RTVI_MODEL"] = rtvi_model
    os.environ["RTVI_EMBED_BASE_URL"] = embed_base_url
    if caption_api_key:
        os.environ["VLM_API_KEY"] = caption_api_key

    source = RequestSource.for_file(str(video_path))
    started = time.monotonic()
    _status("decoding once...")
    _status("ingesting decoded frames concurrently into vlm, rtvi, and rtvi_embed...")
    results = await ingest(
        source,
        backends=["vlm", "rtvi", "rtvi_embed"],
        decoder=decoder,
        single_decode=True,
        cuda_ipc=False,
        chunk_duration=chunk_duration,
        prompt=PROMPT,
    )
    elapsed = time.monotonic() - started
    _status("decoding done")
    _status("ingestion done")

    caption_timeline = _timeline(results["vlm"])
    rtvi_timeline = _timeline(results["rtvi"])
    embedding_timeline = _timeline(results["rtvi_embed"])
    if not (caption_timeline == rtvi_timeline == embedding_timeline):
        raise RuntimeError("backend outputs do not share the same source timeline")

    print(
        f"\nsingle decode completed in {elapsed:.2f}s; "
        f"{len(caption_timeline)} shared chunks"
    )
    for backend_name in ("vlm", "rtvi", "rtvi_embed"):
        print(f"\n[{backend_name}] {len(results[backend_name])} chunks")
        for response in results[backend_name]:
            chunk = response.chunk
            assert chunk is not None
            start_s = chunk.start_pts / 1_000_000_000
            end_s = chunk.end_pts / 1_000_000_000
            print(
                f"  chunk {chunk.chunkIdx}: {start_s:.3f}s - {end_s:.3f}s",
                flush=True,
            )
            if backend_name == "rtvi_embed":
                embedding = cast(EmbeddingChunkResponse, response)
                print(f"    embedding dimensions: {len(embedding.embeddings)}")
            else:
                caption = cast(VlmChunkResponse, response)
                print(f"    caption: {caption.vlm_response}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--decoder",
        choices=("pynv",),
        default="pynv",
        help="decoder shared by all backends (default: pynv)",
    )
    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=30,
        help="chunk duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--caption-base-url",
        default=os.getenv("VLM_BASE_URL", "http://127.0.0.1:8018/v1"),
    )
    parser.add_argument(
        "--caption-model",
        default=os.getenv("VLM_MODEL"),
    )
    parser.add_argument(
        "--caption-api-key",
        default=os.getenv("VLM_API_KEY"),
    )
    parser.add_argument(
        "--rtvi-base-url",
        default=os.getenv("RTVI_BASE_URL", "http://127.0.0.1:8018"),
    )
    parser.add_argument(
        "--rtvi-model",
        default=os.getenv("RTVI_MODEL"),
    )
    parser.add_argument(
        "--embed-base-url",
        default=os.getenv("RTVI_EMBED_BASE_URL", "http://127.0.0.1:8017"),
    )
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"video does not exist: {args.video}")
    if args.chunk_duration <= 0:
        parser.error("--chunk-duration must be greater than zero")
    asyncio.run(
        main(
            args.video,
            decoder=args.decoder,
            chunk_duration=args.chunk_duration,
            caption_base_url=args.caption_base_url,
            caption_model=args.caption_model,
            caption_api_key=args.caption_api_key,
            rtvi_base_url=args.rtvi_base_url,
            rtvi_model=args.rtvi_model,
            embed_base_url=args.embed_base_url,
        )
    )

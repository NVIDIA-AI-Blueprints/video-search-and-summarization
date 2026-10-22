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

"""Decode once for RTVI-VLM, RTVI-Embed, and RTVI-CV over CUDA IPC.

All services must run on the same host and GPU and mount the broker socket
directory at the same absolute path::

    uv run --package vss-ingestion --extra rtvi-cv \
        python examples/ingest_multiple_cv.py video.mp4
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import cast

from vss.ingestion import (
    CvChunkResponse,
    EmbeddingChunkResponse,
    RequestSource,
    VlmChunkResponse,
    ingest,
)

PROMPT = (
    "Describe the important visible events with their start time, "
    "end time, type, and description."
)
BACKENDS = ("rtvi", "rtvi_embed", "rtvi_cv")


def _status(message: str) -> None:
    print(f"[ingest_multiple_cv] {message}", flush=True)


def _timeline(responses: Sequence[object]) -> list[tuple[str, int, int]]:
    timeline: list[tuple[str, int, int]] = []
    for response in responses:
        chunk = getattr(response, "chunk", None)
        if chunk is None:
            raise RuntimeError("backend returned a response without chunk metadata")
        timeline.append((chunk.sourceId, chunk.start_pts, chunk.end_pts))
    return timeline


async def main(
    video_path: Path,
    *,
    chunk_duration: int,
    rtvi_base_url: str,
    rtvi_model: str | None,
    embed_base_url: str,
    cv_base_url: str,
    cv_kafka_bootstrap_servers: str,
    cv_kafka_topic: str,
    socket_dir: Path,
    device_id: int,
) -> None:
    _status(f"input video: {video_path}")
    _status(f"decoder: pynv; chunk duration: {chunk_duration}s")
    _status(f"RTVI-VLM: {rtvi_base_url} ({rtvi_model or 'service default'})")
    _status(f"RTVI-Embed: {embed_base_url}")
    _status(f"RTVI-CV: {cv_base_url}")
    _status(f"RTVI-CV Kafka: {cv_kafka_bootstrap_servers} topic={cv_kafka_topic}")
    _status(f"CUDA IPC socket directory: {socket_dir}; GPU device: {device_id}")

    socket_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["RTVI_BASE_URL"] = rtvi_base_url
    os.environ["RTVI_EMBED_BASE_URL"] = embed_base_url
    os.environ["RTVI_CV_BASE_URL"] = cv_base_url
    os.environ["RTVI_CV_KAFKA_BOOTSTRAP_SERVERS"] = cv_kafka_bootstrap_servers
    os.environ["RTVI_CV_KAFKA_TOPIC"] = cv_kafka_topic
    os.environ["VSS_SHARED_DECODE_DEVICE_ID"] = str(device_id)
    os.environ["VSS_CUDA_IPC_SOCKET_DIR"] = str(socket_dir)
    if rtvi_model is not None:
        os.environ["RTVI_MODEL"] = rtvi_model

    source = RequestSource.for_file(str(video_path))
    started = time.monotonic()
    _status("attaching RTVI-VLM, RTVI-Embed, and RTVI-CV...")
    _status("starting one decoder and ingesting CUDA IPC frames...")
    results = await ingest(
        source,
        backends=list(BACKENDS),
        decoder="pynv",
        single_decode=True,
        cuda_ipc=True,
        chunk_duration=chunk_duration,
        prompt=PROMPT,
    )
    elapsed = time.monotonic() - started
    _status("decoding done; all backend ingestion completed")

    timelines = [_timeline(results[name]) for name in BACKENDS]
    if any(timeline != timelines[0] for timeline in timelines[1:]):
        raise RuntimeError("backend outputs do not share the same source timeline")

    print(
        f"\nsingle CUDA IPC decode completed in {elapsed:.2f}s; "
        f"{len(timelines[0])} shared chunks"
    )
    for backend_name in BACKENDS:
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
            if backend_name == "rtvi_cv":
                cv = cast(CvChunkResponse, response)
                tracking_ids = {
                    detection.tracking_id
                    for detection in cv.detections
                    if detection.tracking_id is not None
                }
                print(
                    f"    detections: {len(cv.detections)}; tracks: {len(tracking_ids)}"
                )
            elif backend_name == "rtvi_embed":
                embedding = cast(EmbeddingChunkResponse, response)
                print(f"    embedding dimensions: {len(embedding.embeddings)}")
            else:
                caption = cast(VlmChunkResponse, response)
                print(f"    caption: {caption.vlm_response}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=30,
        help="chunk duration in seconds (default: 30)",
    )
    parser.add_argument(
        "--rtvi-base-url",
        default=os.getenv("RTVI_BASE_URL", "http://127.0.0.1:8018"),
    )
    parser.add_argument("--rtvi-model", default=os.getenv("RTVI_MODEL"))
    parser.add_argument(
        "--embed-base-url",
        default=os.getenv("RTVI_EMBED_BASE_URL", "http://127.0.0.1:8017"),
    )
    parser.add_argument(
        "--cv-base-url",
        default=os.getenv("RTVI_CV_BASE_URL", "http://127.0.0.1:9000"),
    )
    parser.add_argument(
        "--cv-kafka-bootstrap-servers",
        default=os.getenv("RTVI_CV_KAFKA_BOOTSTRAP_SERVERS", "127.0.0.1:9092"),
    )
    parser.add_argument(
        "--cv-kafka-topic",
        default=os.getenv("RTVI_CV_KAFKA_TOPIC", "ds-perception"),
    )
    parser.add_argument(
        "--socket-dir",
        type=Path,
        default=Path(os.getenv("VSS_CUDA_IPC_SOCKET_DIR", "/run/vss/frame-broker")),
        help="host directory mounted at the same path in every RTVI container",
    )
    parser.add_argument(
        "--device-id",
        type=int,
        default=int(os.getenv("VSS_SHARED_DECODE_DEVICE_ID", "0")),
    )
    args = parser.parse_args()
    if not args.video.is_file():
        parser.error(f"video does not exist: {args.video}")
    if args.chunk_duration <= 0:
        parser.error("--chunk-duration must be greater than zero")
    if args.device_id < 0:
        parser.error("--device-id must be nonnegative")
    asyncio.run(
        main(
            args.video,
            chunk_duration=args.chunk_duration,
            rtvi_base_url=args.rtvi_base_url,
            rtvi_model=args.rtvi_model,
            embed_base_url=args.embed_base_url,
            cv_base_url=args.cv_base_url,
            cv_kafka_bootstrap_servers=args.cv_kafka_bootstrap_servers,
            cv_kafka_topic=args.cv_kafka_topic,
            socket_dir=args.socket_dir,
            device_id=args.device_id,
        )
    )

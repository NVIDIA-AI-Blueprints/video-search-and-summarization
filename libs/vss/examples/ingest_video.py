# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Ingest a local video with the CPU-decoded VLM backend."""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from vss.ingestion import RequestSource, ingest


async def main(video_path: Path) -> None:
    results = await ingest(
        RequestSource.for_file(str(video_path)),
        backends=["vlm"],
        decoder="opencv",
        chunk_duration=30,
        prompt=(
            "Describe the important visible events with their start time, "
            "end time, type, and description."
        ),
    )
    for chunk in results["vlm"]:
        print(chunk.vlm_response)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    args = parser.parse_args()
    asyncio.run(main(args.video))

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

"""Ingest and summarize a local video with CPU or GPU VLM decoding.

Configure an OpenAI-compatible multimodal VLM endpoint, then run::

    export VLM_BASE_URL=http://127.0.0.1:8000
    export VLM_MODEL=your-model
    uv run python examples/summarize_vlm.py video.mp4 --decoder gpu
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from vss.ingestion import RequestSource, ingest
from vss.summarization import summarize_video


async def main(video_path: Path, chunk_duration: int, decoder: str) -> None:
    """Ingest with the selected decoder, then summarize the caption chunks."""

    selected_decoder = "opencv" if decoder == "cpu" else "pynv"
    results = await ingest(
        RequestSource.for_file(str(video_path)),
        backends=["vlm"],
        decoder=selected_decoder,
        chunk_duration=chunk_duration,
        prompt=(
            "Return the important visible events with their start_time, "
            "end_time, type, and description."
        ),
    )
    chunks = results["vlm"]
    completion = await summarize_video(chunks)
    result = json.loads(completion.choices[0].message.content)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument(
        "--decoder",
        choices=("cpu", "gpu"),
        default="cpu",
        help="decode with OpenCV on CPU or PyNvVideoCodec on GPU (default: cpu)",
    )
    parser.add_argument(
        "--chunk-duration",
        type=int,
        default=30,
        help="seconds of video per VLM request (default: 30)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.video, args.chunk_duration, args.decoder))

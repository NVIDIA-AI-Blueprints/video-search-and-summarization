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

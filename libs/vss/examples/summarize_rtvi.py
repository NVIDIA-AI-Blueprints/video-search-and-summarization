# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
import asyncio
import json
import sys
from pathlib import Path

from vss.ingestion import RequestSource, ingest
from vss.summarization import summarize_video


async def main(video_path: Path) -> None:
    source = RequestSource.for_file(str(video_path))
    job = ingest(
        source,
        backends=["rtvi"],
        prompt=(
            "Return the important visible events with their "
            "start_time, end_time, type, and description."
        ),
        chunk_duration=60,
    )
    chunks = (await job)["rtvi"]
    completion = await summarize_video(chunks)

    result = json.loads(completion.choices[0].message.content)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1])))

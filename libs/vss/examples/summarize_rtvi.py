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

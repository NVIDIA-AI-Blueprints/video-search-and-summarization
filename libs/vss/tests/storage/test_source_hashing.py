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

"""Content identity remains complete; cancellation stops hashing between reads."""

import asyncio
import hashlib
import threading
from pathlib import Path

from vss.ingestion import RequestSource


async def test_source_identity_hashes_every_block(tmp_path):
    from vss.ingestion._persistent import source_identity

    content = b"a" * (2 * 1024 * 1024) + b"different final block"
    path = tmp_path / "video.mp4"
    path.write_bytes(content)
    digest, stat = await source_identity(RequestSource(url=str(path)))
    assert digest == hashlib.sha256(content).hexdigest()
    assert stat[2] == len(content)


async def test_hash_cancellation_stops_after_in_flight_read(tmp_path, monkeypatch):
    import pytest

    from vss.ingestion._persistent import source_identity

    path = tmp_path / "video.mp4"
    path.write_bytes(b"a" * (4 * 1024 * 1024))
    entered = asyncio.Event()
    release = threading.Event()
    closed = threading.Event()
    loop = asyncio.get_running_loop()
    reads = []
    original_open = Path.open

    class ObservedStream:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            try:
                return self.stream.__exit__(*args)
            finally:
                closed.set()

        def read(self, size):
            reads.append(size)
            if len(reads) == 1:
                loop.call_soon_threadsafe(entered.set)
                if not release.wait(3):
                    raise TimeoutError("test did not release the source read")
            return self.stream.read(size)

    def observed_open(self, *args, **kwargs):
        stream = original_open(self, *args, **kwargs)
        return ObservedStream(stream) if self == path else stream

    monkeypatch.setattr(Path, "open", observed_open)
    task = asyncio.create_task(source_identity(RequestSource(url=str(path))))
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        for _ in range(3):
            task.cancel()
            await asyncio.sleep(0)
        assert not task.done()  # Cleanup still waits for the one blocked read.
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1)
        assert closed.is_set()
        assert reads == [1024 * 1024]
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

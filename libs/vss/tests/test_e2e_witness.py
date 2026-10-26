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

"""Stress incremental caption framing and strict append-only semantics."""

import json

import httpx
import pytest

from benchmarks.e2e.witness import CaptionWitness, ObservedStream, WitnessError

IDENTITY = "00000000-0000-4000-8000-000000000001"


def event(index=0, start="0", end="1", **changes):
    item = {
        "chunk_id": index,
        "start_time": start,
        "end_time": end,
        "content": "A café appears.",
        "frame_count": 8,
    }
    item.update(changes)
    return {"id": IDENTITY, "model": "fixture", "chunk_responses": [item]}


def encode(body):
    return b"data: " + json.dumps(body, ensure_ascii=False).encode() + b"\r\n\r\n"


def wire(body=None):
    return encode(body or event()) + b"data: [DONE]\r\n\r\n"


def test_every_byte_split_preserves_utf8_crlf_and_hash():
    data = wire()
    expected = None
    for split in range(len(data) + 1):
        witness = CaptionWitness("fixture", 2)
        witness.feed(data[:split])
        witness.feed(data[split:])
        facts = witness.finish()
        if expected is None:
            expected = facts
        assert facts == expected
    witness = CaptionWitness("fixture", 2)
    for byte in data:
        witness.feed(bytes([byte]))
    assert witness.finish() == expected


@pytest.mark.parametrize(
    "data",
    [
        encode(event()),  # no terminal
        wire() + b"data: [DONE]\n\n",  # duplicate terminal
        wire() + encode(event(1, "1", "2")),
        encode(event()) + encode(event()),  # duplicate/update/cumulative event
        encode(event(1)),  # missing chunk zero
        encode(event()) + encode(event(1, ".5", "2")),
        wire(event(end="3")),
        wire(event(end="nan")),
        wire(event(frame_count=True)),
        wire(event(content="")),
        b"data: \xff\n\ndata: [DONE]\n\n",
        b"event: message\n\n",
        b"data: {\n\n",
        b"data: [DONE]\n\n",
        pytest.param(b"data: " + b"x" * 32769, id="oversized-event"),
    ],
)
def test_malformed_incomplete_updates_or_out_of_order_fail_closed(data):
    witness = CaptionWitness("fixture", 2)
    with pytest.raises(WitnessError):
        witness.feed(data)
        witness.finish()


@pytest.mark.parametrize(
    "change", [{"id": "bad"}, {"model": "other"}, {"error": "provider failed"}]
)
def test_identity_is_checked_before_any_caption_is_accepted(change):
    body = event()
    body.update(change)
    witness = CaptionWitness("fixture", 2)
    with pytest.raises(WitnessError):
        witness.feed(wire(body))


def test_query_cannot_change_and_total_wire_is_bounded():
    witness = CaptionWitness("fixture", 2)
    witness.feed(encode(event()))
    body = event(1, "1", "2")
    body["id"] = "00000000-0000-4000-8000-000000000002"
    with pytest.raises(WitnessError):
        witness.feed(encode(body))
    with pytest.raises(WitnessError):
        CaptionWitness("fixture", 2).feed(b":" * (1024**2 + 1))


async def test_observer_forwards_first_bytes_before_upstream_finishes():
    import asyncio

    release = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield encode(event())
            await release.wait()
            yield b"data: [DONE]\n\n"

        async def aclose(self):
            self.closed = True

    upstream = Stream()
    witness = CaptionWitness("fixture", 2)
    stream = ObservedStream(upstream, witness)
    iterator = stream.__aiter__()
    assert await asyncio.wait_for(anext(iterator), 1) == encode(event())
    assert not release.is_set() and not witness.done
    release.set()
    assert await anext(iterator) == b"data: [DONE]\n\n"
    with pytest.raises(StopAsyncIteration):
        await anext(iterator)
    await stream.aclose()
    assert upstream.closed and witness.finish()["done"]


async def test_cancelled_consumer_closes_upstream_without_successful_terminal():
    import asyncio

    blocked = asyncio.Event()
    closed = asyncio.Event()

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield encode(event())
            blocked.set()
            await asyncio.Event().wait()

        async def aclose(self):
            closed.set()

    witness = CaptionWitness("fixture", 2)
    stream = ObservedStream(Stream(), witness)

    async def consume():
        try:
            async for _ in stream:
                pass
        finally:
            await stream.aclose()

    task = asyncio.create_task(consume())
    await blocked.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert closed.is_set()
    with pytest.raises(WitnessError):
        witness.finish()

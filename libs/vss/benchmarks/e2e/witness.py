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

"""Bounded append-only RTVI SSE witness, observing the actual client stream.

Admitted contract: data-only JSON events, stable UUID/model, consecutive final
chunks, non-overlapping source-relative windows, then exactly one [DONE].
Cumulative lists/revisions are rejected until a separate contract is qualified.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid

import httpx


class WitnessError(ValueError):
    pass


class CaptionWitness:
    def __init__(self, model: str, duration_s: float):
        self.model, self.duration = model, duration_s
        self.pending = bytearray()
        self.digest = hashlib.sha256()
        self.bytes = 0
        self.done = False
        self.identity = None
        self.chunks: list[dict] = []
        self.previous_end = 0.0
        self.event: list[bytes] = []

    def feed(self, block: bytes) -> None:
        self.bytes += len(block)
        if self.bytes > 1024 * 1024:
            raise WitnessError("wire_limit")
        self.digest.update(block)
        # Consume incrementally even if a transport hands over a large block.
        for line in block.splitlines(keepends=True):
            self.pending.extend(line)
            if len(self.pending) > 32768:
                raise WitnessError("line_limit")
            if self.pending.endswith(b"\n"):
                raw = bytes(self.pending).rstrip(b"\r\n")
                self.pending.clear()
                self._line(raw)

    def _line(self, raw: bytes) -> None:
        if not raw:
            if self.event:
                self._event(b"\n".join(self.event))
                self.event.clear()
            return
        if raw.startswith(b":"):
            return
        if not raw.startswith(b"data:") or self.done:
            raise WitnessError("sse_protocol")
        self.event.append(raw[5:].lstrip(b" "))
        if sum(map(len, self.event)) > 32768:
            raise WitnessError("event_limit")

    def _event(self, raw: bytes) -> None:
        if raw == b"[DONE]":
            if self.done:
                raise WitnessError("duplicate_terminal")
            self.done = True
            return
        try:
            body = json.loads(raw.decode("utf-8", errors="strict"))
            identity = str(uuid.UUID(body["id"]))
            if (
                body["model"] != self.model
                or (self.identity is not None and self.identity != identity)
                or body.get("error")
            ):
                raise WitnessError("identity_or_provider_error")
            self.identity = identity
            for item in body["chunk_responses"]:
                index = item["chunk_id"]
                start, end = float(item["start_time"]), float(item["end_time"])
                frames, content = item["frame_count"], item["content"]
                if (
                    type(index) is not int
                    or index != len(self.chunks)
                    or len(self.chunks) >= 64
                    or type(frames) is not int
                    or not 0 < frames <= 100000
                    or not isinstance(content, str)
                    or not content.strip()
                    or len(content.encode()) > 8192
                    or item.get("error")
                    or not math.isfinite(start)
                    or not math.isfinite(end)
                    or not self.previous_end <= start < end <= self.duration + 0.01
                ):
                    raise WitnessError("chunk_order_window_or_content")
                self.previous_end = end
                self.chunks.append(
                    {
                        "index": index,
                        "frames": frames,
                        "start_ns": round(start * 1e9),
                        "end_ns": round(end * 1e9),
                        "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                        "usage_available": all(
                            type(item.get(k)) is int and item[k] >= 0
                            for k in ("input_tokens", "output_tokens")
                        ),
                        "finish_reason": item.get("finish_reason")
                        if item.get("finish_reason") in {"stop", "length"}
                        else "not_reported",
                    }
                )
        except (KeyError, TypeError, ValueError, UnicodeError) as error:
            raise WitnessError("invalid_caption_event") from error

    def finish(self) -> dict:
        if self.pending or self.event or not self.done or not self.chunks:
            raise WitnessError("incomplete_caption_stream")
        return {
            "contract": "append_only_final_chunks.v1",
            "done": True,
            "wire_sha256": self.digest.hexdigest(),
            "wire_bytes": self.bytes,
            "chunks": self.chunks,
            "query_identity_scope": "stable_uuid_only_asset_binding_unqualified",
        }


class ObservedStream(httpx.AsyncByteStream):
    def __init__(self, stream: httpx.AsyncByteStream, witness: CaptionWitness):
        self.stream, self.witness = stream, witness

    async def __aiter__(self):
        async for block in self.stream:
            self.witness.feed(block)
            yield block
        self.witness.finish()

    async def aclose(self):
        await self.stream.aclose()

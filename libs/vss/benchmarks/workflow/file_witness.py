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

"""Opt-in final-file chunk-set evidence; never enables qualified comparisons.

This separate contract permits unordered arrival and adds request, usage and
integrity guards. It never deduplicates, admits revisions or infers completion.
"""

import hashlib
import json
import math
import uuid

from benchmarks.e2e.witness import CaptionWitness, WitnessError


def unique_object(pairs):
    body = {}
    for key, value in pairs:
        if key in body:
            raise WitnessError("duplicate_json_key")
        body[key] = value
    return body


class FinalFileCaptionWitness(CaptionWitness):
    def __init__(self, profile, expected, asset):
        super().__init__(profile.vlm_model, profile.duration_s)
        if type(expected) is not int or not 1 <= expected <= 64:
            raise ValueError("expected_chunk_budget")
        self.profile, self.expected, self.asset = profile, expected, asset
        self.by_index = {}
        self.failure = None
        self.request_sha256 = None

    def bind_request(self, request):
        try:
            if self.failure:
                raise WitnessError(self.failure)
            if self.request_sha256 is not None:
                raise WitnessError("repeated_caption_request")
            if (
                request.method != "POST"
                or request.url.path != "/v1/generate_captions"
                or request.url.query
                or request.headers.get("x-stream-id") != self.asset
                or len(request.content) > 65536
            ):
                raise WitnessError("caption_request_route")
            expected = {
                "id": self.asset,
                "model": self.model,
                "prompt": self.profile.caption_prompt,
                "stream": True,
                "chunk_duration": self.profile.chunk_duration_s,
                "chunk_overlap_duration": 0,
                **self.profile.caption_settings(),
            }
            body = json.loads(request.content, object_pairs_hook=unique_object)

            # Canonical JSON distinguishes booleans from integers; no extra URL,
            # identity or generation overrides can be silently accepted.
            def canonical(obj):
                return json.dumps(obj, sort_keys=True, allow_nan=False)

            if canonical(body) != canonical(expected):
                raise WitnessError("caption_request_settings")
            self.request_sha256 = hashlib.sha256(request.content).hexdigest()
        except (ValueError, TypeError, UnicodeError) as error:
            self.failure = (
                str(error) if isinstance(error, WitnessError) else "invalid_request"
            )
            raise WitnessError(self.failure) from None

    def feed(self, block):
        if self.failure:
            raise WitnessError(self.failure)
        try:
            super().feed(block)
        except WitnessError as error:
            self.failure = str(error)
            raise

    def _event(self, raw):
        if raw == b"[DONE]":
            if self.done:
                raise WitnessError("duplicate_terminal")
            self.done = True
            return
        try:
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
            if not isinstance(body, dict) or not isinstance(body.get("id"), str):
                raise WitnessError("invalid_caption_event")
            identity = str(uuid.UUID(body["id"]))
            items = body["chunk_responses"]
            if (
                body["model"] != self.model
                or (self.identity is not None and self.identity != identity)
                or body.get("error")
            ):
                raise WitnessError("identity_or_provider_error")
            if not isinstance(items, list) or not 1 <= len(items) <= self.expected:
                raise WitnessError("invalid_chunk_list")
            self.identity = identity
            for item in items:
                self._chunk(item)
        except WitnessError:
            raise
        except (KeyError, TypeError, ValueError, UnicodeError, OverflowError):
            raise WitnessError("invalid_caption_event") from None

    def _chunk(self, item):
        if not isinstance(item, dict):
            raise WitnessError("invalid_caption_chunk")
        index, frames, content = item["chunk_id"], item["frame_count"], item["content"]
        if (
            type(index) is not int
            or not 0 <= index < self.expected
            or index in self.by_index
        ):
            raise WitnessError("duplicate_or_unexpected_index")
        if (
            type(frames) is not int
            or not 0 < frames <= 100000
            or not isinstance(content, str)
            or not content.strip()
            or len(content.encode()) > 8192
            or item.get("error")
        ):
            raise WitnessError("chunk_content_or_frames")
        times = [item[k] for k in ("start_time", "end_time")]
        if any(type(v) not in (str, int, float) for v in times):
            raise WitnessError("invalid_window")
        start, end = map(float, times)
        expected_start = index * self.profile.chunk_duration_s
        expected_end = min(
            expected_start + self.profile.chunk_duration_s, self.duration
        )
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or not 0 <= start < end <= self.duration + 0.01
            or abs(start - expected_start) > 0.1
            or abs(end - expected_end) > 0.1
        ):
            raise WitnessError("invalid_window")
        tokens = [item.get(k) for k in ("input_tokens", "output_tokens")]
        if any(type(v) is not int or not 0 <= v <= 10000000 for v in tokens):
            raise WitnessError("missing_or_invalid_caption_usage")
        reason = item.get("finish_reason")
        if reason not in (None, "stop", "length"):
            raise WitnessError("unsupported_caption_finish")
        record = {
            "index": index,
            "frames": frames,
            "start_ns": int(start * 1e9),
            "end_ns": int(end * 1e9),
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "input_tokens": tokens[0],
            "output_tokens": tokens[1],
            "finish_reason": reason or "not_reported",
            "usage_available": True,
        }
        self.by_index[index] = record
        self.chunks.append(record)
        cap = self.profile.caption_max_tokens
        if (
            tokens[1] > cap
            or reason == "length"
            or (reason is None and tokens[1] >= cap)
        ):
            raise WitnessError("caption_truncated_or_at_cap")

    def observation(self):
        return {
            "contract": "final_file_chunk_set.v1",
            "failure": self.failure,
            "request_settings_and_upload_id_verified": self.request_sha256 is not None,
            "request_sha256": self.request_sha256,
            "wire_sha256": self.digest.hexdigest(),
            "wire_bytes": self.bytes,
            "done": self.done,
            "arrival_order": [c["index"] for c in self.chunks],
            "chunks": sorted(self.chunks, key=lambda c: c["index"]),
            "caption_completion": "rejected"
            if self.failure
            else "unqualified"
            if any(c["finish_reason"] == "not_reported" for c in self.chunks)
            or not self.chunks
            else "reported_stop",
            "query_identity_scope": "stable_uuid_only_asset_binding_unqualified",
        }

    def finish(self):
        if self.failure:
            raise WitnessError(self.failure)
        if (
            self.request_sha256 is None
            or self.pending
            or self.event
            or not self.done
            or set(self.by_index) != set(range(self.expected))
        ):
            self.failure = "incomplete_caption_set"
            raise WitnessError(self.failure)
        ordered = sorted(self.chunks, key=lambda c: c["index"])
        if any(
            a["end_ns"] > b["start_ns"]
            for a, b in zip(ordered, ordered[1:], strict=False)
        ):
            self.failure = "overlapping_windows"
            raise WitnessError(self.failure)
        return self.observation()

    def validate_normalized(self, chunks):
        records = self.finish()["chunks"]
        if len(chunks) != len(records):
            self.failure = "normalized_chunk_mismatch"
            raise WitnessError("normalized_chunk_mismatch")
        for caption, record in zip(chunks, records, strict=True):
            c = caption.chunk
            if (
                c is None
                or c.sourceId != self.asset
                or c.chunkIdx != record["index"]
                or c.start_pts != record["start_ns"]
                or c.end_pts != record["end_ns"]
                or caption.rtvi_frame_count != record["frames"]
                or caption.vlm_stats.get("input_tokens") != record["input_tokens"]
                or caption.vlm_stats.get("output_tokens") != record["output_tokens"]
                or hashlib.sha256(caption.vlm_response.encode()).hexdigest()
                != record["content_sha256"]
            ):
                self.failure = "normalized_chunk_mismatch"
                raise WitnessError("normalized_chunk_mismatch")

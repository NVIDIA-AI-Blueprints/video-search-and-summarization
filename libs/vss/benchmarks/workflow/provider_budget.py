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

"""Explicit workflow-provider quotas; separate from the eight-request probes.

The local coordinator owns one non-resumable ledger shared by its gateways.
No credential or request content is written here. A debit precedes forwarding;
unknown settlement closes the entire experiment rather than permitting retries.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorkflowPolicy:
    role: str
    max_requests: int
    max_requested_tokens: int
    wall_s: int = 600
    frames_per_request: int = 8

    def __post_init__(self):
        if (
            self.role not in {"vlm", "llm"}
            or type(self.max_requests) is not int
            or not 1 <= self.max_requests <= 16
            or type(self.max_requested_tokens) is not int
            or not 1 <= self.max_requested_tokens <= 32768
            or type(self.wall_s) is not int
            or not 1 <= self.wall_s <= 600
            or self.frames_per_request != 8
        ):
            raise ValueError("workflow_provider_policy")

    @property
    def request_limit(self):
        return (16 if self.role == "vlm" else 1) * 1024**2


def jpeg_witness(body, *, expected):
    """Ordered JPEG byte identities, never a claim about pixels or consumption."""
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("workflow_vlm_messages")
    hashes = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("workflow_vlm_message")
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise ValueError("workflow_vlm_content")
        for part in content:
            if not isinstance(part, dict):
                raise ValueError("workflow_vlm_part")
            if part.get("type") == "text" and isinstance(part.get("text"), str):
                continue
            if part.get("type") != "image_url" or not isinstance(
                part.get("image_url"), dict
            ):
                raise ValueError("workflow_vlm_inline_images_only")
            url = part["image_url"].get("url")
            prefix = "data:image/jpeg;base64,"
            if not isinstance(url, str) or not url.startswith(prefix):
                raise ValueError("workflow_vlm_inline_jpeg_only")
            raw = base64.b64decode(url[len(prefix) :], validate=True)
            if not 4 <= len(raw) <= 2 * 1024**2 or not (
                raw.startswith(b"\xff\xd8") and raw.endswith(b"\xff\xd9")
            ):
                raise ValueError("workflow_vlm_jpeg_bytes")
            hashes.append(hashlib.sha256(raw).hexdigest())
            if len(hashes) > expected:
                raise ValueError("workflow_vlm_frame_count")
    if len(hashes) != expected:
        raise ValueError("workflow_vlm_frame_count")
    return {
        "schema_version": "ordered_inline_jpeg_bytes.1",
        "image_count": len(hashes),
        "decoded_jpeg_bytes_sha256": hashes,
        "internal_consumption_verified": False,
    }


class WorkflowLedger:
    """One finite pilot, one process, fresh directory; deliberately no resume."""

    def __init__(self, root: Path, *, max_requests, max_tokens, wall_s):
        if (
            type(max_requests) is not int
            or not 1 <= max_requests <= 96
            or type(max_tokens) is not int
            or not 1 <= max_tokens <= 196608
            or type(wall_s) not in (int, float)
            or not math.isfinite(wall_s)
            or not 0 < wall_s <= 1200
        ):
            raise ValueError("workflow_ledger_bounds")
        root.mkdir(mode=0o700, parents=False, exist_ok=False)
        self.root, self.started = root, time.monotonic()
        self.max_requests, self.max_tokens, self.wall_s = (
            max_requests,
            max_tokens,
            wall_s,
        )
        self.requests = self.tokens = 0
        self.halted, self.in_flight = False, False
        self.lock = threading.Lock()
        self._write(
            "intent.json",
            {
                "schema_version": "workflow_provider_ledger.1",
                "max_requests": max_requests,
                "max_requested_output_tokens": max_tokens,
                "wall_limit_s": wall_s,
                "retries": 0,
                "resume_supported": False,
                "acceptance_eligible": False,
            },
        )

    def _write(self, name, value):
        fd = os.open(self.root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        # Persist directory entries too; process recreation still refuses reuse.
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @property
    def remaining_s(self):
        return self.wall_s - (time.monotonic() - self.started)

    def debit(self, cap, *, role, request_sha256):
        with self.lock:
            if (
                self.halted
                or self.in_flight
                or self.remaining_s <= 0
                or self.requests >= self.max_requests
                or type(cap) is not int
                or not 1 <= cap <= 2048
                or self.tokens + cap > self.max_tokens
                or role not in {"vlm", "llm"}
            ):
                self.halted = True
                raise ValueError("workflow_ledger_closed")
            self.requests += 1
            self.tokens += cap
            self.in_flight = True
            # Any failure writing this receipt closes the pilot before forwarding.
            try:
                self._write(
                    f"{self.requests:03d}-intent.json",
                    {
                        "role": role,
                        "request_sha256": request_sha256,
                        "ordinal": self.requests,
                        "requested_output_tokens": cap,
                        "cumulative_requested_output_tokens": self.tokens,
                    },
                )
            except BaseException:
                self.halted = True
                raise
            return self.requests

    def settle(self, ordinal, *, valid):
        with self.lock:
            if not self.in_flight or ordinal != self.requests:
                self.halted = True
                raise ValueError("workflow_ledger_settlement")
            self.halted |= not valid
            self.in_flight = False
            try:
                self._write(
                    f"{ordinal:03d}-settlement.json",
                    {"response_controls_valid": bool(valid), "halted": self.halted},
                )
            except BaseException:
                self.halted = True
                raise

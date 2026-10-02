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

"""Apply the chart's locked policy at vLLM's chat request boundary.

The middleware replaces conflicting settings supplied by clients. It logs
policy fields only, never prompts or media bodies.
"""

import hashlib
import json
import os
from pathlib import Path


POLICY = json.loads(Path(__file__).with_name("request-policy.json").read_text())
POLICY_SHA256 = hashlib.sha256(
    json.dumps(POLICY, sort_keys=True, separators=(",", ":")).encode()
).hexdigest()
MAX_BODY_BYTES = int(os.environ.get("VLLM_REQUEST_POLICY_MAX_BODY_BYTES", "16777216"))
PAYLOAD_TOO_LARGE = b'{"detail":"Request body exceeds the configured limit"}'


async def _send_payload_too_large(send):
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(PAYLOAD_TOO_LARGE)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": PAYLOAD_TOO_LARGE})


class RequestPolicy:
    def __init__(self, app, max_body_bytes=MAX_BODY_BYTES):
        self.app = app
        self.max_body_bytes = max_body_bytes

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path", "").rstrip("/") != "/v1/chat/completions"
        ):
            return await self.app(scope, receive, send)

        for key, value in scope.get("headers", []):
            if key.lower() != b"content-length":
                continue
            try:
                if int(value) > self.max_body_bytes:
                    return await _send_payload_too_large(send)
            except ValueError:
                pass  # Let vLLM reject malformed content-length headers.

        raw = bytearray()
        while True:
            event = await receive()
            if event["type"] == "http.disconnect":
                return
            chunk = event.get("body", b"")
            if len(raw) + len(chunk) > self.max_body_bytes:
                return await _send_payload_too_large(send)
            raw.extend(chunk)
            if not event.get("more_body", False):
                break

        applied = False
        try:
            payload = json.loads(raw)
            if isinstance(payload, dict):
                # The modern alias otherwise takes precedence over max_tokens.
                payload.pop("max_completion_tokens", None)
                payload.update(POLICY)
                raw = json.dumps(payload, separators=(",", ":")).encode()
                applied = True
                print(
                    "VLLM_EFFECTIVE_POLICY "
                    + json.dumps({"sha256": POLICY_SHA256, **POLICY}, sort_keys=True),
                    flush=True,
                )
        except (ValueError, UnicodeDecodeError):
            pass  # Let vLLM return its normal validation error.

        body = bytes(raw)
        del raw
        scope = dict(scope)
        scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() not in (b"content-length", b"transfer-encoding")
        ] + [(b"content-length", str(len(body)).encode())]
        delivered = False

        async def replay():
            nonlocal delivered, body
            if not delivered:
                delivered = True
                event = {"type": "http.request", "body": body, "more_body": False}
                body = b""
                return event
            return await receive()

        async def send_with_policy(event):
            if applied and event["type"] == "http.response.start":
                event = dict(event)
                event["headers"] = list(event.get("headers", [])) + [
                    (b"x-vllm-policy-sha256", POLICY_SHA256.encode())
                ]
            await send(event)

        await self.app(scope, replay, send_with_policy)

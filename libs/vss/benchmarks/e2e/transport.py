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

"""Bounded response observations before public adapters normalize completion data."""

from __future__ import annotations

import hashlib
import json

import httpx

from .witness import CaptionWitness, ObservedStream


class JSONStream(httpx.AsyncByteStream):
    def __init__(self, stream, observer, status):
        self.stream, self.observer, self.status = stream, observer, status

    async def __aiter__(self):
        body = bytearray()
        async for block in self.stream:
            if len(body) + len(block) > 1024**2:
                raise ValueError("provider_response_limit")
            body.extend(block)
            yield block
        self.observer.capture(bytes(body), self.status)

    async def aclose(self):
        await self.stream.aclose()


class ProviderObserver:
    def __init__(self, model):
        self.model = model
        self.completions: list[dict] = []
        self.inputs: list[dict] = []

    def request(self, request):
        if len(self.inputs) >= 64 or len(request.content) > 16 * 1024**2:
            raise ValueError("provider request budget")
        body = json.loads(request.content)
        messages = body["messages"]
        images = sum(
            part.get("type") == "image_url"
            for message in messages
            if isinstance(message.get("content"), list)
            for part in message["content"]
        )
        self.inputs.append(
            {
                "messages_sha256": hashlib.sha256(
                    json.dumps(messages, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "image_count": images,
            }
        )

    def capture(self, raw: bytes, status: int):
        if len(self.completions) >= 64:
            raise ValueError("provider_completion_limit")
        body = json.loads(raw)
        usage = body.get("usage") or {}
        choices = body.get("choices") or []
        reason = choices[0].get("finish_reason") if len(choices) == 1 else None
        tokens = [usage.get(k) for k in ("prompt_tokens", "completion_tokens")]
        known = all(type(v) is int and 0 <= v <= 10000000 for v in tokens)
        self.completions.append(
            {
                "http_status": status,
                "model_matches": body.get("model") == self.model,
                "finish_reason": reason if reason in {"stop", "length"} else "unknown",
                "usage_available": known,
                "input_tokens": tokens[0] if known else None,
                "output_tokens": tokens[1] if known else None,
            }
        )


class ObservingTransport(httpx.AsyncBaseTransport):
    def __init__(
        self,
        observer: ProviderObserver,
        witness: CaptionWitness | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
    ):
        self.observer, self.witness = observer, witness
        self.inner = inner or httpx.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request):
        request.headers["Accept-Encoding"] = "identity"
        if request.url.path.endswith("/chat/completions"):
            self.observer.request(request)
        response = await self.inner.handle_async_request(request)
        if response.headers.get("content-encoding", "identity") != "identity":
            await response.aclose()
            raise ValueError("compressed witness responses are unsupported")
        if request.url.path.endswith("/generate_captions"):
            if self.witness is None:
                raise ValueError("caption witness is required")
            response.stream = ObservedStream(response.stream, self.witness)
        else:
            capture = (
                self.observer
                if request.url.path.endswith("/chat/completions")
                else None
            )
            # Also bound non-completion responses without recording completions.
            response.stream = JSONStream(
                response.stream, capture or _Discard(), response.status_code
            )
        return response

    async def aclose(self):
        await self.inner.aclose()


class _Discard:
    def capture(self, *_):
        pass

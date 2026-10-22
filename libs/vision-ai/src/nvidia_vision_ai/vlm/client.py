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

"""Async calls to an OpenAI-compatible VLM endpoint.

Error contract:

* :class:`BackendUnreachableError` -- transport failure or timeout, or HTTP
  429/5xx still failing after retries.
* :class:`BackendResponseError` -- HTTP 4xx (``status_code`` set), a body
  that is not a chat completion, or an empty answer.
* :class:`InvalidRequestError` -- the local ``file`` cannot be read.

Error messages never contain the API key: any occurrence is redacted.

Connect errors and 429/5xx are retried (3 attempts). Read timeouts are not:
``Endpoint.timeout`` applies per network operation (httpx semantics, not a
total deadline), and retrying a slow video request would multiply it.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import secrets
from typing import TYPE_CHECKING
from typing import Any

import httpx

from nvidia_vision_ai._foundation.errors import BackendResponseError
from nvidia_vision_ai._foundation.errors import BackendUnreachableError
from nvidia_vision_ai._foundation.errors import InvalidRequestError
from nvidia_vision_ai._foundation.retry import create_retry_strategy
from nvidia_vision_ai._foundation.sanitize import scrub_log

from .models import Endpoint
from .models import ModelsResponse
from .models import VlmRequest
from .models import VlmResponse
from .payload import build_payload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from collections.abc import Callable
    from pathlib import Path

_LOG = logging.getLogger(__name__)
_BACKEND = "vlm"
_ATTEMPTS = 3
_RETRYABLE = (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError)
#: Raw bytes read per chunk when inlining a file; a multiple of 3 so each
#: chunk base64-encodes without padding.
_FILE_CHUNK = 3 * 65536


class _RetryableStatus(Exception):
    """HTTP 429/5xx: raised inside the retry loop so tenacity retries."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"HTTP {response.status_code}")
        self.response = response


def _new_client(timeout: int) -> httpx.AsyncClient:
    """One client per call. Tests patch this to inject a mock transport."""
    return httpx.AsyncClient(timeout=httpx.Timeout(float(timeout)))


def _api_url(base_url: str, path: str) -> str:
    """Resolve ``/chat/completions`` or ``/models`` against any accepted base URL form."""
    url = base_url.rstrip("/")
    url = url.removesuffix("/chat/completions")
    if not url.endswith("/v1"):
        url = f"{url}/v1"
    return f"{url}{path}"


def _redact(text: object, endpoint: Endpoint) -> str:
    """``scrub_log(text)`` with every occurrence of the API key replaced by ``***``."""
    clean = scrub_log(text)
    if endpoint.api_key is not None:
        clean = clean.replace(endpoint.api_key.get_secret_value(), "***")
    return clean


def _headers(endpoint: Endpoint) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if endpoint.api_key is not None:
        headers["Authorization"] = f"Bearer {endpoint.api_key.get_secret_value()}"
    return headers


async def _send(
    endpoint: Endpoint,
    method: str,
    path: str,
    *,
    content: Callable[[], AsyncIterator[bytes]] | None = None,
    json_body: dict[str, Any] | None = None,
) -> httpx.Response:
    """Send with retries and map failures onto the library error contract."""
    url = _api_url(endpoint.base_url, path)
    response: httpx.Response | None = None
    async with _new_client(endpoint.timeout) as client:
        try:
            async for attempt in create_retry_strategy(_ATTEMPTS, exceptions=(*_RETRYABLE, _RetryableStatus)):
                with attempt:
                    response = await client.request(
                        method,
                        url,
                        headers=_headers(endpoint),
                        json=json_body,
                        content=content() if content is not None else None,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        raise _RetryableStatus(response)
                    break
        except _RetryableStatus as exc:
            detail = _redact(exc.response.text[:200], endpoint)
            raise BackendUnreachableError(_BACKEND, f"HTTP {exc.response.status_code} from {url}: {detail}") from None
        except httpx.TimeoutException:
            raise BackendUnreachableError(_BACKEND, f"timed out after {endpoint.timeout}s calling {url}") from None
        except httpx.HTTPError as exc:
            # ``from None``: the chained exception's text may quote request headers.
            detail = _redact(exc, endpoint)
            raise BackendUnreachableError(_BACKEND, f"{type(exc).__name__} calling {url}: {detail}") from None
    # The retry strategy re-raises its last error, so a missing response is a bug.
    assert response is not None
    if response.status_code >= 400:
        detail = _redact(response.text[:200], endpoint)
        raise BackendResponseError(
            _BACKEND, f"HTTP {response.status_code} from {url}: {detail}", status_code=response.status_code
        )
    return response


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise BackendResponseError(_BACKEND, "response was not valid JSON") from None
    if not isinstance(body, dict):
        raise BackendResponseError(_BACKEND, "response JSON was not an object")
    return body


def _extract_answer(completion: dict[str, Any]) -> str:
    """Text of ``choices[0].message.content``; list-form content parts are joined."""
    try:
        content = completion["choices"][0]["message"].get("content", "")
    except (KeyError, IndexError, TypeError, AttributeError):
        raise BackendResponseError(_BACKEND, "response has no choices[0].message") from None
    if isinstance(content, list):
        content = "\n".join(
            part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    if not isinstance(content, str) or not content.strip():
        raise BackendResponseError(_BACKEND, "response contained no text answer")
    return content


def _inline_file_body(request: VlmRequest, file_path: Path) -> Callable[[], AsyncIterator[bytes]]:
    """A factory of JSON byte streams with ``file_path`` inlined as a base64 data URI.

    At most one raw chunk and its encoding live in memory at a time, instead of
    the whole encoded video several times over. A factory, not a stream, so a
    retried attempt re-reads the file from the start.
    """
    sentinel = f"__b64_{secrets.token_hex(8)}__"
    raw = json.dumps(build_payload(request, sentinel))
    head, _, tail = raw.partition(json.dumps(sentinel))

    async def stream() -> AsyncIterator[bytes]:
        yield (head + '"data:video/mp4;base64,').encode()
        with file_path.open("rb") as fh:
            while chunk := await asyncio.to_thread(fh.read, _FILE_CHUNK):
                yield base64.b64encode(chunk)
        yield ('"' + tail).encode()

    return stream


async def analyze(request: VlmRequest) -> VlmResponse:
    """Ask ``request.prompt`` about the video and return the model's answer."""
    if request.file is not None:
        # Fail as an input error now, rather than as a transport error from
        # inside the body stream once the request is under way.
        try:
            request.file.open("rb").close()
        except OSError as exc:
            raise InvalidRequestError(f"cannot read local file {request.file}: {exc.strerror}") from None
        response = await _send(
            request.endpoint, "POST", "/chat/completions", content=_inline_file_body(request, request.file)
        )
    else:
        assert request.media_url is not None  # guaranteed by VlmRequest validation
        response = await _send(
            request.endpoint, "POST", "/chat/completions", json_body=build_payload(request, request.media_url)
        )
    completion = _json(response)
    answer = _extract_answer(completion)
    model = completion.get("model")
    completion_id = completion.get("id")
    usage = completion.get("usage")
    return VlmResponse(
        answer=answer,
        model=model if isinstance(model, str) and model else request.model,
        completion_id=completion_id if isinstance(completion_id, str) else None,
        usage=usage if isinstance(usage, dict) else None,
    )


async def list_models(endpoint: Endpoint) -> ModelsResponse:
    """Model ids from ``GET /v1/models``, in the order the endpoint lists them."""
    body = _json(await _send(endpoint, "GET", "/models"))
    data = body.get("data")
    if not isinstance(data, list):
        raise BackendResponseError(_BACKEND, "/models response has no data list")
    return ModelsResponse(
        models=[item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
    )

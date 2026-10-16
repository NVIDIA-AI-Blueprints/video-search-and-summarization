# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generic bounded transport for OpenAI-compatible chat completions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from typing import Any

import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from collections.abc import Callable
    from collections.abc import Collection

from vss_core._foundation.errors import BackendUnreachableError
from vss_core._foundation.errors import ConfigurationError
from vss_core._foundation.retry import create_retry_strategy
from vss_core._foundation.sanitize import scrub_log

_RETRYABLE_ERRORS = (httpx.TimeoutException, httpx.TransportError)


class _RetryableChatStatusError(Exception):
    """Internal retry signal for throttling and server failures."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"HTTP {status_code}")


class _RetryableChatTransportError(Exception):
    """Retry transport failures without logging an unsafe original message."""

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(kind)


class OpenAIChatRejectedError(Exception):
    """A deterministic 4xx response with a credential-safe body excerpt."""

    def __init__(self, status_code: int, detail: str, *, attempts: int = 1) -> None:
        self.status_code = status_code
        self.detail = detail
        self.attempts = attempts
        suffix = f": {detail}" if detail else ""
        super().__init__(f"HTTP {status_code}{suffix}")


class OpenAIChatRequestError(Exception):
    """An exhausted transport or retryable-status request."""

    def __init__(self, detail: str, kind: str, *, attempts: int = 1, status_code: int | None = None) -> None:
        self.detail = detail
        self.kind = kind
        self.status_code = status_code
        self.attempts = attempts
        super().__init__(detail)


def _safe_response_detail(response: httpx.Response) -> str:
    """Keep useful benign diagnostics without echoing likely credentials."""
    detail = scrub_log(response.text[:200])
    lowered = detail.lower()
    if any(marker in lowered for marker in ("bearer ", "token", "api_key", "apikey", "password", "secret")):
        return ""
    return detail


def _safe_exception_detail(error: Exception) -> str:
    """Retain benign transport context without copying endpoints or secrets."""
    detail = scrub_log(error)
    lowered = detail.lower()
    if "://" in detail or any(
        marker in lowered for marker in ("bearer ", "token", "api_key", "apikey", "password", "secret")
    ):
        return type(error).__name__
    return detail or type(error).__name__


def normalize_openai_base_url(base_url: str) -> str:
    """Normalize an origin, ``/v1`` base, or complete chat endpoint."""
    normalized = base_url.strip().rstrip("/")
    if not normalized:
        raise ConfigurationError("OpenAI-compatible base_url must be non-empty")
    if normalized.endswith("/chat/completions") or normalized.endswith("/v1"):
        return normalized
    return f"{normalized}/v1"


def chat_completions_url(base_url: str) -> str:
    """Return the complete chat-completions endpoint for ``base_url``."""
    normalized = normalize_openai_base_url(base_url)
    if normalized.endswith("/chat/completions"):
        return normalized
    return f"{normalized}/chat/completions"


def extract_chat_content(
    payload: object,
    *,
    stringify_other: bool = False,
    none_as_empty: bool = False,
) -> str:
    """Extract text from the first OpenAI-compatible assistant message."""
    try:
        assert isinstance(payload, dict)
        choices = payload["choices"]
        assert isinstance(choices, list)
        choice = choices[0]
        assert isinstance(choice, dict)
        message = choice["message"]
        assert isinstance(message, dict)
        content = message["content"]
    except (AssertionError, KeyError, IndexError, TypeError) as error:
        raise ValueError("OpenAI-compatible response contained no message content") from error
    if isinstance(content, str):
        return content
    if content is None and none_as_empty:
        return ""
    if isinstance(content, list):
        pieces: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            text = part.get("text")
            if isinstance(text, str):
                pieces.append(text)
        return "\n".join(pieces)
    if stringify_other:
        return str(content)
    raise ValueError("OpenAI-compatible message content must be text")


class OpenAIChatTransport:
    """One reusable async client with bounded retries and safe diagnostics."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        attempts: int = 4,
        backend: str = "openai-compatible-chat",
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_statuses: Collection[int] | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ConfigurationError("OpenAI-compatible timeout_seconds must be > 0")
        if attempts < 1:
            raise ConfigurationError("OpenAI-compatible attempts must be >= 1")
        if not backend.strip():
            raise ConfigurationError("OpenAI-compatible backend name must be non-empty")
        if client is not None and transport is not None:
            raise ConfigurationError("provide either an httpx client or transport, not both")
        self.url = chat_completions_url(base_url)
        self._api_key = api_key
        self._attempts = attempts
        self._backend = backend
        self._retry_statuses = frozenset(retry_statuses) if retry_statuses is not None else None
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds), transport=transport)

    async def complete(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST one JSON payload and return its decoded response object."""
        try:
            response = await self.post(payload, headers=headers)
        except OpenAIChatRejectedError as error:
            raise ConfigurationError(
                f"OpenAI-compatible request was rejected with HTTP {error.status_code}; "
                "check the configured endpoint, model, credentials, and request fields"
            ) from None
        except OpenAIChatRequestError as error:
            raise BackendUnreachableError(
                self._backend,
                f"request failed after {self._attempts} bounded attempt(s) ({error.kind})",
            ) from None
        try:
            decoded = response.json()
            if not isinstance(decoded, dict):
                raise ValueError("OpenAI-compatible response must be a JSON object")
            return decoded
        except (json.JSONDecodeError, ValueError) as error:
            raise BackendUnreachableError(self._backend, str(error)) from None

    async def post(
        self,
        payload: dict[str, Any],
        *,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """POST JSON with shared URL, authorization, and bounded retry policy.

        Typed failures let an established adapter preserve its public wording
        while new consumers can use :meth:`complete`'s neutral diagnostics.
        """
        response, _ = await self.post_with_attempts(payload, headers=headers)
        return response

    async def post_with_attempts(
        self,
        payload: dict[str, Any] | None,
        *,
        headers: dict[str, str] | None = None,
        body_factory: Callable[[], AsyncGenerator[bytes]] | None = None,
    ) -> tuple[httpx.Response, int]:
        """POST JSON or a replayable stream using one bounded retry policy."""
        if (payload is None) == (body_factory is None):
            raise ValueError("provide exactly one JSON payload or body factory")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        if self._api_key:
            request_headers["Authorization"] = f"Bearer {self._api_key}"
        attempt_number = 0
        try:
            async for retry in create_retry_strategy(
                retries=self._attempts,
                exceptions=(_RetryableChatTransportError, _RetryableChatStatusError),
            ):
                attempt_number = retry.retry_state.attempt_number
                with retry:
                    body = body_factory() if body_factory is not None else None
                    try:
                        kwargs = {"content": body} if body is not None else {"json": payload}
                        response = await self._client.post(self.url, headers=request_headers, **kwargs)
                    except _RETRYABLE_ERRORS as error:
                        raise _RetryableChatTransportError(type(error).__name__) from None
                    finally:
                        if body is not None:
                            await body.aclose()
                    try:
                        retryable = (
                            response.status_code in self._retry_statuses
                            if self._retry_statuses is not None
                            else response.status_code == 429 or response.status_code >= 500
                        )
                        if retryable:
                            raise _RetryableChatStatusError(response.status_code)
                        if 400 <= response.status_code < 500:
                            raise OpenAIChatRejectedError(
                                response.status_code,
                                _safe_response_detail(response),
                                attempts=attempt_number,
                            )
                        response.raise_for_status()
                        return response, attempt_number
                    finally:
                        await response.aclose()
        except OpenAIChatRejectedError:
            raise
        except OSError:
            raise OpenAIChatRequestError(
                "cannot read request body", "source", attempts=max(1, attempt_number)
            ) from None
        except (httpx.HTTPError, _RetryableChatStatusError, _RetryableChatTransportError) as error:
            status = (
                error.status_code
                if isinstance(error, _RetryableChatStatusError)
                else (error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None)
            )
            # Do not echo transport exception messages: they can include request data.
            raise OpenAIChatRequestError(
                f"HTTP {status}" if status is not None else type(error).__name__,
                error.kind if isinstance(error, _RetryableChatTransportError) else type(error).__name__,
                attempts=max(1, attempt_number),
                status_code=status,
            ) from None
        raise AssertionError("unreachable: retry strategy reraises exhausted request errors")

    async def aclose(self) -> None:
        """Close the owned HTTP client."""
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "OpenAIChatRejectedError",
    "OpenAIChatRequestError",
    "OpenAIChatTransport",
    "chat_completions_url",
    "extract_chat_content",
    "normalize_openai_base_url",
]

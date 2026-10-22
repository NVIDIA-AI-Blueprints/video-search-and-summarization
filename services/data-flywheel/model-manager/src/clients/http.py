# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Shared HTTP helper: a ``requests`` call with bounded exponential backoff.

Transient failures (connection error / timeout / HTTP 5xx) are retried up to
``max_retries`` times. Connection/timeout exhaustion is surfaced as a
``ModelManagerError`` (retryable). Non-transient responses (2xx/4xx and any 5xx
returned after retries are exhausted) are handed back to the caller, which owns
the status-code semantics for its endpoint.

Retrying a timeout is only safe for calls whose effect is a pure read or is
cheap to repeat, so callers that start expensive work opt out via
``retry_on_timeout=False``; see the parameter's documentation.

On retryability: AOI's ``/v1/models/*`` error envelope carries its own
``retryable`` boolean, and this module ignores it in favour of a per-branch
decision at each call site. Three reasons. It is not universally present — the
``/v1/model-routing`` endpoint answers with a different envelope
(``{"error": "<code>", "message": ...}``) that has no such field, so trusting it
would apply to half the control plane and silently default the other half. The
word also means something different on each side: the server is answering "can
this HTTP call be repeated", whereas a Model Manager ``retryable`` tells DFW's
promotion state machine whether re-driving the whole verb can succeed. And where
the two would disagree, the local reading is the more useful one —
``idempotency_in_progress`` is flagged retryable by the server, while the right
response is to keep polling the build already running rather than to repeat the
POST. The server's error *codes* are used freely; they are stable and always
present. Consulted as a cross-check while writing these branches, the flag agrees
with every mapping here.
"""

import logging
from time import sleep

import requests

from src.core.errors import ErrorCode, ModelManagerError

logger = logging.getLogger(__name__)


def request_with_retries(
    method: str,
    url: str,
    *,
    timeout: float,
    max_retries: int = 2,
    backoff_base: float = 0.5,
    unreachable_code: ErrorCode = "internal",
    retry_on_timeout: bool = True,
    **kwargs,
) -> requests.Response:
    """Issue ``method url`` with retries on transient errors; return the response.

    Raises ``ModelManagerError`` only when the downstream is unreachable (all
    connection/timeout attempts exhausted). The raised error carries
    ``details={"timed_out": ...}`` so a caller can tell the two exhaustion modes
    apart: a refused/reset connection never reached the downstream, while a
    timeout leaves the request's fate genuinely unknown.

    ``retry_on_timeout=False`` suppresses the retry for that ambiguous case. A
    request that was received and is still being served looks exactly like one
    that was lost, so replaying it drives the downstream to start the work again
    — for a call that triggers a minutes-long engine build, one client-side
    timeout would otherwise fan out into several concurrent builds within the
    backoff window. Connection errors are still retried: those did not land.
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            timed_out = isinstance(exc, requests.Timeout)
            logger.warning(
                "transient error calling %s %s (attempt %d/%d): %s",
                method,
                url,
                attempt + 1,
                max_retries + 1,
                exc,
            )
            if attempt < max_retries and (retry_on_timeout or not timed_out):
                sleep(backoff_base * (2**attempt))
                continue
            raise ModelManagerError(
                code=unreachable_code,
                message=f"{method} {url} failed after {attempt + 1} attempts: {exc}",
                retryable=True,
                http_status=503,
                details={"timed_out": timed_out},
            ) from exc

        if response.status_code >= 500 and attempt < max_retries:
            logger.warning(
                "server error %d from %s %s (attempt %d/%d); retrying",
                response.status_code,
                method,
                url,
                attempt + 1,
                max_retries + 1,
            )
            # Release the discarded response before retrying. With stream=True the
            # body is never read, so the pooled connection would otherwise leak;
            # a no-op for already-consumed non-stream responses.
            response.close()
            sleep(backoff_base * (2**attempt))
            continue

        return response

    # Unreachable: the loop always returns or raises. Present for type-checkers.
    raise ModelManagerError(  # pragma: no cover
        code=unreachable_code,
        message=f"{method} {url} failed: {last_exc}",
        retryable=True,
        http_status=503,
    )


def json_object(response: requests.Response) -> dict:
    """Return the response body as a dict, or ``{}`` when it is not one.

    ``response.json()`` raises on a body that is not JSON at all, and a 2xx is no
    guarantee of one: a proxy or gateway between here and the service can answer
    200 with an HTML error page. That exception is not a ``ModelManagerError``, so
    it would sail through the ``except ModelManagerError`` guards the callers use
    to degrade gracefully and surface as a 500 from an endpoint that has a
    perfectly good "could not read it" answer available.
    """
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def list_field(body: dict, key: str) -> list | None:
    """Read an array field; ``None`` when it is present but not an array.

    An *absent* key is legitimate — an older or partial payload simply omits it —
    and yields an empty list. A key present with ``null`` or a non-array value is
    a malformed payload: the sender claimed to answer and the answer is unusable.
    Those two have to be told apart by key presence, because ``dict.get`` maps
    both to ``None``, and ``dict.get(key, [])`` returns its default only for the
    absent case — so the null case reaches the caller as a ``None`` that raises on
    iteration.

    The distinction matters because callers act on it differently: an empty list
    is an answer ("nothing here") while ``None`` is the absence of one, and the
    consumers here gate an unload and a directory delete on being able to tell
    those apart. Mirrors ``_list_field`` in DFW's ``model_manager_client``, which
    documents this trap from the other side of the same wire.

    ``str``/``bytes`` are rejected despite being iterable: a bare string would
    otherwise be read element-wise into a plausible-looking list of characters.
    """
    if key not in body:
        return []
    value = body[key]
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return None


def conflict(operation: str, detail: str, *, retryable: bool) -> ModelManagerError:
    """Return the error for a control-plane call refused by the current state.

    A 409 is not a malformed request and not a fault: the call was well-formed and
    is incompatible with what the serving stack is doing right now. That deserves
    one shape across the control plane, because the same situation can be caught
    either locally or by the server depending on timing — a version becoming
    routed between the Model Manager's pre-check and its unload, say — and a
    caller's handling should not hinge on which of the two noticed. ``cleanup``
    already reports its local version of this as ``conflict``/409.

    ``retryable`` is left to the caller rather than derived from the server's error
    code, because only the caller knows whether the condition clears on its own (an
    in-flight idempotent operation) or needs an operator to act first (a version
    still taking traffic, a routing mode disabled by configuration). The server's
    own ``retryable`` flag is deliberately not consulted; see the module note.
    """
    return ModelManagerError(
        code="conflict",
        message=f"AOI control plane refused the {operation}: {detail}",
        retryable=retryable,
        http_status=409,
    )


def access_denied(operation: str, status_code: int, detail: str) -> ModelManagerError:
    """Return the error for a downstream call refused on credentials (401/403).

    Every caller's generic fallback branch is retryable, and a rejected
    credential is the one upstream failure that repeating cannot fix: it would
    spin until the caller's budget ran out and then report a 502, hiding a
    misconfiguration behind a message about the service being unwell. So this is
    surfaced non-retryable, with the original status preserved rather than
    flattened, and it is shared so the three write paths cannot drift into
    classifying the same rejection three different ways.

    The code is ``internal`` only because the frozen ``ErrorCode`` enum has no
    auth member; the message carries the actual meaning.
    """
    return ModelManagerError(
        code="internal",
        message=(
            f"AOI control plane refused the {operation} with HTTP {status_code}: {detail}. "
            "This is a credentials/permissions problem, not a transient fault — retrying "
            "cannot resolve it."
        ),
        retryable=False,
        http_status=status_code,
    )


def error_code_of(response: requests.Response) -> str | None:
    """Return the AOI error code from a JSON error body, if present.

    Two body shapes are in circulation. The control-plane contract nests the
    code — ``{"error": {"code": "idempotency_in_progress"}}`` — while older AOI
    builds put a bare string under the same key. Both are read, because every
    409/404 branch in the clients dispatches on this value and a shape this
    function cannot parse reads as "no code at all": a recoverable conflict (a
    re-attached in-flight build, an already-loaded version) would then fall
    through to the terminal generic-failure branch and fail a promotion that is
    in fact progressing normally.
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, str):
        return error
    if isinstance(error, dict):
        code = error.get("code")
        return code if isinstance(code, str) else None
    return None

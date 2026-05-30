# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Helpers for classifying Elasticsearch dependency errors into HTTP-friendly
responses.

When the ES cluster rejects a write or read (typically because
``cluster.max_shards_per_node`` is exhausted, or because the dependency is
otherwise unavailable), LVS used to swallow the underlying exception and
either hang the request or return an empty / structurally-valid response.
This module centralises the translation from raw ES exceptions to a
``(http_status, user_message)`` pair that the HTTP layer can surface as a
503, while always logging the full underlying error at ERROR for operator
triage.

User-facing messages are intentionally generic: ES status, body, and type
information go to the LVS server log only and are never echoed back to
HTTP clients. There is no opt-in for verbose passthrough — operators read
the logs.
"""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)


_SHARD_LIMIT_USER_MESSAGE = (
    "Service temporarily unavailable: Elasticsearch shard limit exceeded. "
    "See server logs for details."
)
_GENERIC_ES_USER_MESSAGE = (
    "Service temporarily unavailable: Elasticsearch dependency error ({status}). "
    "See server logs for details."
)
_INTERNAL_ERROR_USER_MESSAGE = "Internal server error. See server logs for details."


def _walk_causes(exc: BaseException, max_depth: int = 8):
    """Yield the exception and each link of its ``__cause__`` / ``__context__``
    chain, bounded so a malformed cycle can't loop forever.

    LVS wraps ES exceptions through several layers (RagAdapter raises a
    ViaException ``from exc``, ContextManager may re-raise from the
    inter-process queue, etc.), so the original ES status / fingerprint
    is typically a couple of links deep. We walk the chain and let the
    classifier inspect every link.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    depth = 0
    while current is not None and depth < max_depth:
        if id(current) in seen:
            return
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__
        depth += 1


def _extract_status(exc: BaseException) -> int | None:
    """Best-effort extraction of an HTTP status from an ES client exception.

    The ``elasticsearch`` Python client exposes ``status_code`` on
    ``ApiError`` and ``TransportError``; ``urllib3`` / ``requests``
    surface ``status``. We walk the ``__cause__`` chain because LVS-side
    wrappers (RagAdapter -> ViaException) hide the original status from
    the top-level exception. Returns ``None`` when no link in the chain
    has a usable HTTP status — the caller treats this as a non-ES
    failure.
    """
    for link in _walk_causes(exc):
        for attr in ("status_code", "status"):
            value = getattr(link, attr, None)
            if isinstance(value, int):
                return value
    return None


def _looks_like_shard_cap(exc: BaseException) -> bool:
    """Identify the ``cluster.max_shards_per_node`` exhaustion error.

    Elasticsearch returns HTTP 400 with a ``validation_exception`` whose
    body contains a phrase like ``this cluster currently has [N]/[N]
    maximum normal shards open``. The phrase ``maximum normal shards
    open`` is stable across ES 7/8/9 and is the unambiguous fingerprint
    for this failure class. ``max_shards_per_node`` is also matched as a
    secondary fingerprint in case future ES versions reword the message.
    Walks the ``__cause__`` chain because LVS wraps ES errors through
    several layers before they reach the classifier.
    """
    for link in _walk_causes(exc):
        message = str(link)
        if "maximum normal shards open" in message or "max_shards_per_node" in message:
            return True
    return False


def classify_es_error(exc: BaseException) -> Tuple[int, str]:
    """Map an Elasticsearch / Logstash dependency exception to an HTTP
    response shape suitable for surfacing through ``/v1/summarize`` and
    related endpoints.

    Returns
    -------
    tuple[int, str]
        ``(http_status, user_message)`` where ``user_message`` is safe to
        return verbatim in an HTTP response body (no ES internals).

    Notes
    -----
    The full exception (status, type, body) is always logged at ERROR.
    User-facing strings are deliberately generic so cluster internals
    never leak into HTTP responses — operators read the LVS logs for
    triage detail.
    """
    message = str(exc)
    status = _extract_status(exc)

    logger.error(
        "Elasticsearch dependency error: status=%s detail=%s",
        status,
        message,
    )

    if _looks_like_shard_cap(exc):
        return 503, _SHARD_LIMIT_USER_MESSAGE

    if isinstance(status, int) and 400 <= status < 600:
        return 503, _GENERIC_ES_USER_MESSAGE.format(status=status)

    return 500, _INTERNAL_ERROR_USER_MESSAGE

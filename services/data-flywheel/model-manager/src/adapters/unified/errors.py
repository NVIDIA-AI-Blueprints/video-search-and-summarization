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

"""Mapping the unified error envelope onto ``ModelManagerError``.

The unified contract's envelope is::

    {"status": "HTTP/1.1 409 Conflict",
     "reason": "MODEL_IN_USE, model is an active routing target",
     "error": {"code": "MODEL_IN_USE", "message": "...", "field": "...", "hint": "..."}}

Two things make this more than a table lookup.

First, the code alone is not a discriminator: the shipped AOI implementation
returns ``MODEL_NOT_LOADED`` with **404** (nothing staged to load) and with
**409** (routing target not READY). Those want opposite handling, so every
mapping here keys on the ``(status, code)`` pair.

That keying is also what lets one client serve two generations of backend. The
revised contract reserves ``MODEL_NOT_LOADED`` for 404 -- "including when a
native implementation historically reported that condition as 400" -- and gives
in-progress work its own code, ``UPDATE_IN_FLIGHT`` at 409. Both spellings are
accepted at 409, so a backend that has adopted the revision and one that has not
are handled by the same table without a profile switch.

Second, not every 4xx carries this envelope at all. The service's observability
middleware short-circuits before the handler and answers with FastAPI's own
``{"detail": ...}`` for an unparseable Content-Length (400), an oversized body
(413) and admission-limit shedding (429). A parser that assumes the envelope
would read those as an absent code and mis-map them, so the detail shape is a
first-class fallback rather than an edge case.
"""

import logging

import requests

from src.core.errors import ErrorCode, ModelManagerError

logger = logging.getLogger(__name__)

# Codes the unified spec documents, plus the three the AOI implementation adds
# beyond it (INVALID_REQUEST, INVALID_ARGUMENT, MODE_DISABLED). The spec leaves
# ``error.code`` an unconstrained string, so this is a best-known list and never
# an exhaustive one -- an unrecognized code falls through to the status-only
# mapping rather than being treated as a parse failure.
FIELD_UNSUPPORTED = "FIELD_UNSUPPORTED"
VERSION_INVALID = "VERSION_INVALID"
REVISION_CONFLICT = "REVISION_CONFLICT"
MODEL_NOT_LOADED = "MODEL_NOT_LOADED"
MODEL_IN_USE = "MODEL_IN_USE"
MODEL_OPERATION_FAILED = "MODEL_OPERATION_FAILED"
INVALID_REQUEST = "INVALID_REQUEST"
INVALID_ARGUMENT = "INVALID_ARGUMENT"
MODE_DISABLED = "MODE_DISABLED"
# Added by the revised contract. FIELD_UNKNOWN and FIELD_UNSUPPORTED look alike
# but assign blame differently -- see the branch below. UPDATE_IN_FLIGHT is the
# standardized spelling for "a native model update is still running".
FIELD_UNKNOWN = "FIELD_UNKNOWN"
UPDATE_IN_FLIGHT = "UPDATE_IN_FLIGHT"


def error_of(response: requests.Response) -> tuple[str | None, str]:
    """Return ``(code, message)`` from a unified error body.

    Falls back to the middleware's ``{"detail": ...}`` shape, then to the raw
    body text, so a caller always has something to put in front of an operator.
    """
    try:
        body = response.json()
    except ValueError:
        return None, (response.text or "").strip()[:500]
    if not isinstance(body, dict):
        return None, str(body)[:500]

    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code")
        message = error.get("message")
        hint = error.get("hint")
        text = message if isinstance(message, str) else str(body.get("reason", ""))
        if isinstance(hint, str) and hint:
            text = f"{text} ({hint})"
        return (code if isinstance(code, str) else None), text

    # Middleware-level rejections never reach the unified handler and answer in
    # FastAPI's own shape.
    detail = body.get("detail")
    if isinstance(detail, str):
        return None, detail
    reason = body.get("reason")
    return None, reason if isinstance(reason, str) else str(body)[:500]


def error_field(response: requests.Response) -> str | None:
    """Return the unified envelope's ``error.field`` pointer, if it carries one."""
    try:
        body = response.json()
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    field = error.get("field") if isinstance(error, dict) else None
    return field if isinstance(field, str) else None


def translate(
    operation: str,
    response: requests.Response,
    *,
    unreachable_code: ErrorCode = "internal",
) -> ModelManagerError:
    """Map a non-2xx unified response onto a ``ModelManagerError``.

    ``operation`` names the verb for the message ("load", "routing update", ...);
    ``unreachable_code`` is the code to use for a 5xx, so a failed load reports
    ``load_failed`` while a failed status read reports ``internal``.
    """
    status = response.status_code
    code, message = error_of(response)
    detail = f"{code}: {message}" if code else message

    # A 5xx is transient by nature; the caller retries.
    if status >= 500:
        return ModelManagerError(
            code=unreachable_code,
            message=f"unified {operation} failed ({status}): {detail}",
            retryable=True,
            http_status=502,
        )

    if status == 409:
        # Two codes here clear WITHOUT anyone intervening, and only those two.
        #
        # MODEL_NOT_LOADED at 409 is the shipped backend's spelling of "the
        # routing target is not READY yet", which a warming candidate resolves
        # by itself. UPDATE_IN_FLIGHT is self-clearing by contract -- the spec's
        # own remedy is "wait until model/status no longer reports updating,
        # then retry" -- so classifying it terminal destroys a promotion over a
        # condition that resolves in seconds.
        #
        # Re-driving either is safe for the same reason the 408/429 branch below
        # is: a 409 REJECTED the request, so it provably had no effect. This is
        # NOT the accepted-but-still-building case that ``client.load`` refuses
        # to replay; the replay happens a level up, on DFW's idempotent pull.
        #
        # MODEL_IN_USE stays terminal even though the contract broadened it to
        # cover actively-serving streams as well as routing references. The
        # revision deliberately gives the two causes no machine discriminator
        # ("MODEL_IN_USE covers both routing-policy references and actively
        # serving streams"), so telling a draining instance from a permanent
        # routing reference would mean pattern-matching example prose -- and our
        # retry budget is far shorter than a stream drain in any case.
        #
        # ``UnifiedActivation._load_to_ready`` keys its already-loaded tolerance
        # off this flag: a retryable 409 is never resolved against model-status.
        # Any code added here must therefore mean "not servable right now", not
        # "already there".
        retryable = code in (MODEL_NOT_LOADED, UPDATE_IN_FLIGHT)
        return ModelManagerError(
            code="conflict",
            message=f"unified {operation} refused: {detail}",
            retryable=retryable,
            http_status=409,
        )

    if status == 404:
        return ModelManagerError(
            code="not_found",
            message=f"unified {operation} target not found: {detail}",
            retryable=False,
            http_status=404,
        )

    if status in (408, 429):
        # Backpressure, not a bad request. The service's observability middleware
        # sheds POST/PUT/PATCH overflow after a 10ms wait on an in-flight
        # semaphore it shares with inference traffic, so a routing write issued
        # while the pod is busy is dropped BEFORE the handler runs. The call
        # provably had no effect, which makes re-driving it both safe and the only
        # thing that can succeed. Classified non-retryable it would instead fail
        # the promotion terminally and trigger a rollback.
        return ModelManagerError(
            code=unreachable_code,
            message=f"unified {operation} was shed ({status}): {detail}",
            retryable=True,
            http_status=503,
        )

    if status in (401, 403):
        return ModelManagerError(
            code="internal",
            message=(
                f"unified {operation} refused with HTTP {status}: {detail}. This is a "
                "credentials/permissions problem, not a transient fault — retrying cannot "
                "resolve it."
            ),
            retryable=False,
            http_status=status,
        )

    if code in (FIELD_UNSUPPORTED, FIELD_UNKNOWN):
        field = (error_field(response) or "").lower()
        if "routes" in field:
            # The one unsupported field this service emits ON PURPOSE, and only
            # when a deployment asked for it. The remedy is the setting, not the
            # promotion policy -- and this branch goes FIRST because the stage
            # heuristic below matches on a substring: a backend that faults the
            # nested ``value.routes[0].shadow`` rather than ``value.routes``
            # would otherwise be told to disable shadow, which does not remove
            # the arm, and the next rung would fail again with nothing pointing
            # back at the change the operator actually made.
            return ModelManagerError(
                code="bad_request",
                message=(
                    f"the serving backend does not support explicit stream routes: {detail}. "
                    "Set MODEL_MANAGER_UNIFIED_STREAM_ROUTES=false for this backend; only "
                    "RTVI-CV implements them."
                ),
                retryable=False,
                http_status=400,
            )
        stage = next((s for s in ("canary", "shadow") if s in field), None)
        if stage is not None:
            # The rollout asked for a stage this backend does not implement. That
            # is a promotion-policy question, not a Model Manager defect: the
            # policy is what selects the stages, so the remedy names the policy
            # rather than anything in this service.
            return ModelManagerError(
                code="bad_request",
                message=(
                    f"the serving backend does not support {stage} routing: {detail}. Set "
                    f"promotion_policy.deployment_stages.{stage}=false for this use case."
                ),
                retryable=False,
                http_status=400,
            )
        # Anything else is a body this service built that the backend cannot
        # accept -- a Model Manager defect rather than a caller error, so it is
        # logged loudly and surfaced as internal.
        #
        # FIELD_UNKNOWN joins FIELD_UNSUPPORTED here rather than getting a
        # branch of its own. The revision splits them by meaning (unknown =
        # the operation does not define the field at all; unsupported = valid
        # field, this profile lacks it), but both indict the wire body, and
        # untreated a FIELD_UNKNOWN falls to the terminal bad_request default
        # below with NO error log -- pointing an operator at the caller for a
        # body only this service composes. Sharing the branch also keeps the
        # stage heuristic above reachable for a backend that is loose about the
        # new split, which the shipped one is: it answers FIELD_UNSUPPORTED for
        # the literal "is not a recognized field" case. Such a backend
        # answering FIELD_UNKNOWN on a canary field still gets the actionable
        # policy remedy instead of a 500 blaming us for its capability gap.
        logger.error("unified %s sent an unsupported field: %s", operation, detail)
        return ModelManagerError(
            code="internal",
            message=f"unified {operation} sent an unsupported field: {detail}",
            retryable=False,
            http_status=500,
        )

    return ModelManagerError(
        code="bad_request",
        message=f"unified {operation} rejected ({status}): {detail}",
        retryable=False,
        http_status=400,
    )

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

"""The one place a non-2xx response is rendered.

``ErrorEnvelope`` is documented as "the single error shape every non-2xx Model
Manager response renders", but nothing enforced it: the handlers caught
``ModelManagerError`` and everything else fell through to Starlette, which
answers ``text/plain`` ``Internal Server Error``. That is not merely ugly --
DFW's ``_error_from_response`` has no envelope to read and falls back on
``retryable = status >= 500``, so a mount that will never become writable is
re-driven until the promotion's budget is gone and the operator is told
"internal".

``install_error_handlers`` makes the contract hold by construction rather than
by every call site remembering: the four handlers below cover the four ways a
response can leave the app without passing through a route's own ``except`` --
a domain error raised outside one, a request rejected before the route is
reached, a routing-level 404/405, and anything else at all.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.api.schemas import Error, ErrorEnvelope
from src.core.errors import ErrorCode, ModelManagerError

logger = logging.getLogger(__name__)


def envelope_response(
    status_code: int,
    *,
    code: ErrorCode,
    message: str,
    retryable: bool,
    details: dict | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Render the frozen ``ErrorEnvelope`` at ``status_code``."""
    envelope = ErrorEnvelope(
        error=Error(code=code, message=message, retryable=retryable, details=details)
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(), headers=headers)


def error_response(exc: ModelManagerError) -> JSONResponse:
    """Render a ``ModelManagerError`` as the frozen ``ErrorEnvelope`` response."""
    return envelope_response(
        exc.http_status,
        code=exc.code,
        message=exc.message,
        retryable=exc.retryable,
        details=exc.details,
    )


def install_error_handlers(app: FastAPI) -> None:
    """Guarantee every non-2xx this app emits is an ``ErrorEnvelope``."""

    @app.exception_handler(ModelManagerError)
    async def _model_manager_error(_: Request, exc: ModelManagerError) -> JSONResponse:
        """Envelope a domain error raised outside a handler's own ``try``.

        The handlers still catch it themselves -- this covers a raise from a
        dependency or a response model, where their ``except`` cannot see it.
        """
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """Re-render FastAPI's built-in 422 ``{"detail": [...]}`` as the envelope.

        The per-field report is preserved under ``details``; only the outer
        shape changes, so a caller reading ``error.code`` and a caller reading
        the field errors are both served.

        ``loc``/``msg``/``type`` only: FastAPI's default echoes the rejected
        ``input`` back, and that value is by definition one this service refused.
        ``Infinity`` is the concrete case -- it is a legal JSON *request* body to
        a lenient parser but ``json.dumps(allow_nan=False)`` cannot write it into
        a response, so echoing it makes the 422 handler itself raise and the
        caller gets a 500 for a bad request. Not echoing it also keeps the
        response free of attacker-chosen bytes.
        """
        report = [
            {"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")}
            for err in exc.errors()
        ]
        return envelope_response(
            422,
            code="bad_request",
            message="Request validation failed",
            retryable=False,
            details={"errors": jsonable_encoder(report)},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """Envelope the routing-level 404/405 Starlette raises before any handler.

        ``exc.headers`` is forwarded, not dropped. Starlette attaches ``Allow``
        to its 405, and RFC 9110 s15.5.6 says a 405 MUST carry it -- enveloping
        the body while discarding the headers turns a spec-compliant response
        into a non-compliant one. The same forwarding covers ``WWW-Authenticate``
        on a 401 and ``Retry-After`` on a 503 if either is ever raised this way.
        """
        return envelope_response(
            exc.status_code,
            code="not_found" if exc.status_code == 404 else "bad_request",
            message=str(exc.detail),
            retryable=False,
            headers=exc.headers,
        )

    @app.exception_handler(Exception)
    async def _unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        """The backstop: anything not classified above still leaves as an envelope.

        Reports the exception class and nothing else, and logs the traceback.
        Naming it honestly matters: a ``TypeError`` from a bug here must not be
        reported as a staging failure with a mount remedy that does not apply.

        ``retryable=False`` because an unclassified error is by definition one
        nothing here can promise will clear, and every exception that reaches
        this point is a bug -- a deterministic one, so a retry re-runs it. The
        pre-existing un-enveloped 500 read as retryable, which is how a
        permanently unwritable mount became an endless re-drive. Anything that
        genuinely may clear should be raised as a ``ModelManagerError`` with
        ``retryable=True`` at the site that knows, not guessed at here.
        """
        logger.exception(
            "unhandled %s serving %s %s",
            type(exc).__name__,
            request.method,
            request.url.path,
        )
        return envelope_response(
            500,
            code="internal",
            message=(
                f"Unhandled {type(exc).__name__} serving {request.method} "
                f"{request.url.path}; see the Model Manager logs"
            ),
            retryable=False,
        )

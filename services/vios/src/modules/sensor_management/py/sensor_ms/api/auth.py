# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Bearer-auth dependency for mutating endpoints (swagger `bearerAuth`).

VERIFIED against the live scaled deployment (2026-06-09): the sensor-ms does NOT enforce bearer
tokens itself — `useMultiUser=false`, mutating endpoints (e.g. POST /sensor/scan) return HTTP 200
with no Authorization header, and the C++ sensor module contains no UserAuthHandler/bearer checks.
Authentication is an INGRESS concern (nginx/Envoy in front of the service); the swagger `bearerAuth`
documents the external contract, not in-service enforcement.

So `require_bearer` is a documentation marker (it surfaces the bearer scheme in the generated
OpenAPI) that passes through by default — matching the real service. It only enforces when
`use_multi_user` is enabled, which is a separate auth/login feature (USER_SESSIONS, login endpoints)
outside the scope of this control-plane microservice; that path is intentionally left unimplemented
rather than faked.
"""
from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from ..config import get_config
from .errors import VmsError, VmsErrorCode

# auto_error=False: surface the scheme in OpenAPI without rejecting tokenless requests.
_bearer = HTTPBearer(auto_error=False)


async def require_bearer(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str | None:
    """Pass-through by default (matches the scaled sensor-ms). Returns the username when multi-user
    auth is enabled, else None."""
    cfg = get_config()
    if not cfg.use_multi_user:
        return None  # verified: no in-service enforcement when multi-user is off
    if creds is None or not creds.credentials:
        raise VmsError(VmsErrorCode.ClientUnauthorizedError, "Missing bearer token")
    # Multi-user session validation is a separate feature (login/USER_SESSIONS), not part of this
    # control-plane MS. Surface a clear error rather than silently accepting/rejecting.
    raise VmsError(VmsErrorCode.ServiceUnavailableError,
                   "multi-user auth not provided by the sensor microservice")

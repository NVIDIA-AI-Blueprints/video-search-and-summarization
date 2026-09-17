# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""VSS deployment version endpoint: ``GET /api/v1/version``."""

import os
import re
from typing import Literal

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field

# The official Semantic Versioning 2.0.0 grammar
# (https://semver.org/#is-there-a-suggested-regular-expression-regex-to-check-a-semver-string),
# with its capture groups made non-capturing.
#
# This module is the source of truth for the version contract. Two other places
# validate the same strings and must stay byte-identical to the expression
# below:
#   * ``services/agent/scripts/check_vss_version.py`` — duplicates it, because
#     that script is a standalone stdlib-only copy-and-run tool (importing
#     ``vss_agents`` would defeat its purpose). ``test_version.py`` asserts the
#     two are identical so they cannot drift.
#   * ``eval/scripts/tests/vss_version.py`` in the ``ci-vss-oss`` repo — the
#     eval preflight that hard-fails a deployment reporting a bad version.
#
# Deliberately NOT the relaxed ``VERSION_PATTERN`` used by the RTVI services
# (``services/rtvi/rt-embed/src/api_models/common.py``): that one accepts
# ``03.3.0``, ``3.3.0-.`` and ``3.3.0-01``, which the eval preflight rejects.
# Reusing it would let this endpoint serve HTTP 200 for versions CI hard-fails.
SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)

# Dedicated variable for the version this deployment reports. Kept separate from
# VSS_AGENT_VERSION because that one also feeds container image-tag resolution
# (``deploy/docker/containers.env``), so it frequently carries an image tag such
# as ``develop-latest`` rather than a version. Checked first; VSS_AGENT_VERSION
# remains the fallback for deployments that have not adopted the new variable.
DEPLOYMENT_VERSION_ENV_VARS = ("VSS_DEPLOYMENT_VERSION", "VSS_AGENT_VERSION")

_UNAVAILABLE_DETAIL = "The deployed VSS version is unavailable or is not valid Semantic Versioning 2.0.0."


class VersionResponse(BaseModel):
    """Public VSS deployment version."""

    service: Literal["vss"] = "vss"
    version: str = Field(description="Deployed VSS version in Semantic Versioning 2.0.0 format.")


def resolve_deployment_version() -> str | None:
    """Return the configured deployment version, or ``None`` when unusable.

    The first variable in :data:`DEPLOYMENT_VERSION_ENV_VARS` that is set to a
    non-empty value wins; a value that is set but not valid SemVer yields
    ``None`` rather than falling through, so a misconfigured deployment reports
    a problem instead of silently serving a different variable's value.
    """
    for name in DEPLOYMENT_VERSION_ENV_VARS:
        configured = os.getenv(name, "").strip()
        if not configured:
            continue
        return configured if SEMVER_PATTERN.fullmatch(configured) else None
    return None


def register_version_route(app: FastAPI) -> None:
    """Register the deployment version endpoint."""

    @app.get("/api/v1/version", response_model=VersionResponse, summary="Get deployed VSS version")
    async def version() -> VersionResponse:
        resolved = resolve_deployment_version()
        if resolved is None:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
        return VersionResponse(version=resolved)

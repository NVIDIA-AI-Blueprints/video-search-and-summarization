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

"""VSS deployment version endpoint: ``GET /api/v1/version``.

The version contract (strict Semantic Versioning 2.0.0) and how a deployment's
version is resolved live in the library, :mod:`vss_core.version`: what this
endpoint reports is the version of the ``nvidia-vss-core`` library the process
imported -- nothing else, and nothing outranks it. Those names are re-exported
here, because that is where callers and tests reach for them.
"""

from typing import Literal

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field

from vss_core.version import SEMVER_PATTERN
from vss_core.version import resolve_deployment_version

__all__ = [
    "SEMVER_PATTERN",
    "VersionResponse",
    "register_version_route",
    "resolve_deployment_version",
]

_UNAVAILABLE_DETAIL = "The deployed VSS version is unavailable or is not valid Semantic Versioning 2.0.0."


class VersionResponse(BaseModel):
    """Public VSS deployment version."""

    service: Literal["vss"] = "vss"
    version: str = Field(description="Deployed VSS version in Semantic Versioning 2.0.0 format.")


def register_version_route(app: FastAPI) -> None:
    """Register the deployment version endpoint."""

    @app.get("/api/v1/version", response_model=VersionResponse, summary="Get deployed VSS version")
    async def version() -> VersionResponse:
        resolved = resolve_deployment_version()
        if resolved is None:
            raise HTTPException(status_code=503, detail=_UNAVAILABLE_DETAIL)
        return VersionResponse(version=resolved)

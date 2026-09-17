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

"""VSS deployment version endpoint."""

import os
import re
from typing import Literal

from fastapi import FastAPI
from fastapi import HTTPException
from pydantic import BaseModel
from pydantic import Field

_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class VersionResponse(BaseModel):
    """Public VSS deployment version."""

    service: Literal["vss"] = Field(description="Service identified by this response.")
    version: str = Field(description="Deployed VSS version in Semantic Versioning 2.0.0 format.")


def get_deployed_version() -> str:
    """Return the configured SemVer deployment version.

    ``VSS_AGENT_VERSION`` is set by both the Docker Compose and Helm
    deployments and identifies the release that supplied the running agent.
    """
    version = os.getenv("VSS_AGENT_VERSION", "").strip()
    if not _SEMVER_PATTERN.fullmatch(version):
        raise HTTPException(
            status_code=503,
            detail="The deployed VSS version is unavailable or is not valid Semantic Versioning 2.0.0.",
        )
    return version


def register_version_route(app: FastAPI) -> None:
    """Register the deployment version endpoint."""

    @app.get(
        "/api/v1/version",
        response_model=VersionResponse,
        summary="Get deployed VSS version",
        tags=["version"],
    )
    async def version() -> VersionResponse:
        return VersionResponse(service="vss", version=get_deployed_version())

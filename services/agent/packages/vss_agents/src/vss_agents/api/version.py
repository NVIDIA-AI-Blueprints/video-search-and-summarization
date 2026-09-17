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

# Same SemVer rule the RTVI services validate their versions against
# (``services/rtvi/rt-embed/src/api_models/common.py::VERSION_PATTERN``), so a
# version accepted by one VSS service is accepted by all of them.
_SEMVER_PATTERN = re.compile(r"\d+\.\d+\.\d+(-[A-Za-z0-9\-.]+)?(\+[A-Za-z0-9\-.]+)?")


class VersionResponse(BaseModel):
    """Public VSS deployment version."""

    service: Literal["vss"] = "vss"
    version: str = Field(description="Deployed VSS version in Semantic Versioning 2.0.0 format.")


def register_version_route(app: FastAPI) -> None:
    """Register the deployment version endpoint."""

    @app.get("/api/v1/version", response_model=VersionResponse, summary="Get deployed VSS version")
    async def version() -> VersionResponse:
        configured = os.getenv("VSS_AGENT_VERSION", "").strip()
        if not _SEMVER_PATTERN.fullmatch(configured):
            raise HTTPException(
                status_code=503,
                detail="The deployed VSS version is unavailable or is not valid Semantic Versioning 2.0.0.",
            )
        return VersionResponse(version=configured)

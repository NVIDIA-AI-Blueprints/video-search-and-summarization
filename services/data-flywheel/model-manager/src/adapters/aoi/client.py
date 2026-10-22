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

"""HTTP client for the AOI model-routing API (aoi_client/openapi.json).

GET/PUT /v1/model-routing. Routing config bodies use the single | shadow |
canary discriminated union keyed on ``mode``. ``canary_percent`` must be strictly
between 0 and 100 for canary routing.

The routing PUT is the only call that moves production traffic, and it refuses a
target that is not already READY — loading a version and routing to it are
separate steps, in that order, and this endpoint will not wait for the first one.

Unlike load/unload this endpoint reads the raw JSON body, so it is not exposed to
the "untyped body silently defaults ``version`` to every staged version" hazard
that makes an explicit content type mandatory over there.
"""

import logging

from src.clients.http import (
    access_denied,
    conflict,
    error_code_of,
    json_object,
    request_with_retries,
)
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)


class AOIClientClient:
    """Client for GET/PUT /v1/model-routing."""

    def __init__(self, base_url: str | None = None, timeout: float | None = None):
        self.base_url = (base_url or settings.aoi_client_url or settings.aoi_control_url).rstrip(
            "/"
        )
        self.timeout = timeout if timeout is not None else settings.request_timeout
        self.max_retries = settings.request_max_retries
        self.backoff_base = settings.request_backoff_base

    def get_routing(self) -> dict:
        """Return the active ModelRoutingState (single | shadow | canary)."""
        response = request_with_retries(
            "GET",
            f"{self.base_url}/v1/model-routing",
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
        )
        if response.status_code // 100 != 2:
            raise ModelManagerError(
                code="internal",
                message=f"AOI Client get_routing failed ({response.status_code})",
                retryable=True,
                http_status=502,
            )
        return json_object(response)

    def put_routing(self, config: dict) -> dict:
        """Replace the active routing config; return the resulting ModelRoutingState."""
        if config.get("mode") == "canary":
            percent = config.get("canary_percent")
            if percent is None or not (0 < percent < 100):
                raise ModelManagerError(
                    code="bad_request",
                    message=(
                        "canary_percent must be strictly between 0 and 100 "
                        f"for canary routing (got {percent!r})"
                    ),
                    retryable=False,
                    http_status=400,
                )
        response = request_with_retries(
            "PUT",
            f"{self.base_url}/v1/model-routing",
            timeout=self.timeout,
            max_retries=self.max_retries,
            backoff_base=self.backoff_base,
            json=config,
        )
        if response.status_code // 100 == 2:
            return json_object(response)

        error = error_code_of(response)
        if response.status_code in (400, 422):
            raise ModelManagerError(
                code="bad_request",
                message=f"AOI Client rejected routing update: {error or response.text}",
                retryable=False,
                http_status=400,
            )
        if response.status_code in (401, 403):
            raise access_denied("routing update", response.status_code, error or response.text)
        if response.status_code == 409:
            # Two different 409s arrive here and they are not equally recoverable.
            # ``model_routing_target_not_ready`` is a conflict with the current
            # serving state that clears itself once the version finishes loading,
            # so it stays retryable. ``model_routing_mode_disabled`` means shadow or
            # canary is switched off in the service's configuration: no amount of
            # waiting enables it, and retrying only burns the caller's budget before
            # reporting a config problem as a timeout. Neither is an internal fault,
            # so both keep the request's own status rather than becoming a 502.
            mode_disabled = error == "model_routing_mode_disabled"
            raise conflict(
                f"{config.get('mode')!r} routing update",
                error or response.text,
                retryable=not mode_disabled,
            )
        raise ModelManagerError(
            code="internal",
            message=f"AOI Client put_routing failed ({response.status_code}): {error or response.text}",
            retryable=True,
            http_status=502,
        )

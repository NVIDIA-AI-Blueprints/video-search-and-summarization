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

"""Registry that resolves a client name to a concrete ``ServingAdapter``.

``ModelManager`` selects its adapter here from ``settings.client``. Adding a new
serving backend is a three-step change: implement ``ServingAdapter``, register
its factory in ``_ADAPTERS`` below, and widen the ``client`` literal in
``src/config.py`` so the new name is accepted at startup.

``unified`` is ONE client for every backend that speaks the Unified Model Control
API, whichever use case it serves. There is no per-backend variant, because the
adapter sends only the contract's portable core and reads load completion off the
response status rather than off a declared capability. What differs between two
unified deployments is their URL, and — on the DFW side — which promotion stages
the policy asks for.
"""

from collections.abc import Callable

from src.adapters.aoi.adapter import AOIServingAdapter
from src.adapters.base import ServingAdapter
from src.adapters.unified.adapter import UnifiedServingAdapter
from src.core.errors import ModelManagerError

# Maps a config ``client`` value to a zero-arg factory for its adapter.
_ADAPTERS: dict[str, Callable[[], ServingAdapter]] = {
    "aoi": AOIServingAdapter,
    "unified": UnifiedServingAdapter,
}


def get_adapter(name: str) -> ServingAdapter:
    """Return the ``ServingAdapter`` registered under ``name``."""
    factory = _ADAPTERS.get(name)
    if factory is None:
        raise ModelManagerError(
            code="internal",
            message=(f"Unknown serving client {name!r}; known clients: {sorted(_ADAPTERS)}"),
            retryable=False,
            http_status=500,
        )
    return factory()

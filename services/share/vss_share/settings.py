# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Environment-backed settings for the VSS share service.

Every knob is read once at import. The service is stateless apart from Redis,
so a config change means a container restart -- matching the rest of the VSS
compose stack.
"""

from __future__ import annotations

import os


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


def _csv(name: str, default: str = "") -> list[str]:
    raw = os.environ.get(name, default).strip()
    return [part.strip() for part in raw.split(",") if part.strip()]


# --- Storage -------------------------------------------------------------

REDIS_URL: str = os.environ.get("SHARE_REDIS_URL", "redis://redis:6379/0")

#: How long a published view stays resolvable. Views hold surveillance
#: thumbnails, so this is a retention control, not just a cache knob.
TTL_SECONDS: int = _int("SHARE_TTL_SECONDS", 7 * 24 * 3600)

#: Entropy of a view id. 16 bytes -> 128 bits, url-safe base64.
ID_BYTES: int = _int("SHARE_ID_BYTES", 16)


# --- Payload limits ------------------------------------------------------

#: Reject oversized posts outright rather than truncating: a silently
#: shortened result set reads as a complete answer when it is not.
MAX_RESULTS: int = _int("SHARE_MAX_RESULTS", 500)
MAX_PAYLOAD_BYTES: int = _int("SHARE_MAX_PAYLOAD_BYTES", 4 * 1024 * 1024)


# --- Thumbnail proxy -----------------------------------------------------

#: Allowlist of URL prefixes the thumbnail proxy will fetch. The agent supplies
#: screenshot_url values; without this the service would be an open proxy that
#: anything on the network could aim at arbitrary hosts. Empty list disables
#: thumbnail proxying entirely (payloads still serve, images just will not).
THUMB_ALLOWED_PREFIXES: list[str] = _csv(
    "SHARE_THUMB_ALLOWED_PREFIXES",
    "http://vst-ingress:30888/,http://vst-ingress:81/",
)

THUMB_TIMEOUT_SECONDS: float = float(os.environ.get("SHARE_THUMB_TIMEOUT_SECONDS", "10"))
THUMB_MAX_BYTES: int = _int("SHARE_THUMB_MAX_BYTES", 8 * 1024 * 1024)


# --- HTTP ----------------------------------------------------------------

PORT: int = _int("SHARE_PORT", 9095)
HOST: str = os.environ.get("SHARE_HOST", "0.0.0.0")

#: Origins allowed to read published views. Defaults to the wildcard because a
#: view is already gated by its unguessable id, and the read-only UI is
#: typically served from a different origin than this service.
CORS_ALLOW_ORIGINS: list[str] = _csv("SHARE_CORS_ALLOW_ORIGINS", "*")

#: Public origin used to build the shareable link returned from POST /api/view.
#: Must be the externally reachable https origin, not an internal service name.
PUBLIC_BASE_URL: str = os.environ.get("SHARE_PUBLIC_BASE_URL", "").rstrip("/")

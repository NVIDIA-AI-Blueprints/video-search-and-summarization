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
"""Shared presign helper for Model Manager pull locators.

The Model Manager downloads the candidate weights (and optional spec) over a
short-lived, credential-free HTTPS URL. Both callers resolve the durable
object-store locators to a fresh presigned URL right before the pull:

- :class:`StartPromotionHandler` mints the FIRST presigned URL at handoff.
- The Promotion Controller RE-MINTS on every pull attempt so a slow/retrying
  pull can never outlive the URL's TTL (``deft_config.presign_expires_s``).

Factored out of the AOI handler so the controller can re-presign WITHOUT
importing the AOI handler module. Pass-through behavior is preserved: only
``s3://``-or-bare-key locators are signed; ``http``/``https``/``file`` locators
are direct dev/test URLs and pass through untouched (keeping the test harness
and dev URLs working without a Storage Backend round-trip).
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse


class PresignError(RuntimeError):
    """A pull locator that required signing could not be presigned.

    Raised fail-closed: the Model Manager cannot pull without the signed URL.
    Callers decide the policy — the handler aborts the promotion; the controller
    logs a warning and falls back to the already-stored URL.
    """


def needs_signing(uri: str) -> bool:
    """True for an object-store locator (``s3://`` URI or a bare key).

    A direct ``http``/``https``/``file`` locator is a dev/test URL and passes
    through untouched (returns False).
    """
    return urlparse(uri).scheme.lower() in ("s3", "")


def presign_pull_locators(
    deft_config: Any, locators: dict[str, str | None]
) -> dict[str, str | None]:
    """Resolve object-store pull locators to short-lived presigned HTTPS URLs.

    ``locators`` maps a role (``"artifact"``/``"spec"``) to its locator (or
    None). Only OBJECT-STORE locators are signed (see :func:`needs_signing`); a
    locator already ``http``/``https``/``file`` passes through unchanged. The
    signed URLs are minted here — right before the pull — via
    ``POST /v1/get_presigned_urls`` (aoi-api!8) so the credential-free link the
    Model Manager receives is fresh. Fails CLOSED: if a locator that needs
    signing is missing from ``items`` or listed in ``failed`` it raises
    :class:`PresignError`.
    """
    # Lazy import so patching ``src.backends.deft.storage_backend_client``
    # .StorageBackendClient in tests takes effect, and so this module stays
    # import-light for the controller's guarded import.
    from src.backends.deft.storage_backend_client import StorageBackendClient

    to_sign = sorted({uri for uri in locators.values() if uri and needs_signing(uri)})
    if not to_sign:
        return dict(locators)

    response = StorageBackendClient(deft_config).get_presigned_urls(
        to_sign, expires_in=deft_config.presign_expires_s
    )
    signed = {
        item["input"]: item["presigned_url"]
        for item in (response.get("items") or [])
        if item.get("input") and item.get("presigned_url")
    }
    failed = {entry.get("input"): entry.get("error") for entry in (response.get("failed") or [])}

    resolved: dict[str, str | None] = {}
    for role, uri in locators.items():
        if not uri or not needs_signing(uri):
            resolved[role] = uri
            continue
        if uri in signed:
            resolved[role] = signed[uri]
        else:
            reason = failed.get(uri) or "not returned by the Storage Backend"
            raise PresignError(
                f"Could not presign the {role} locator {uri!r} for the Model Manager "
                f"pull: {reason}. Aborting before promotion — the pull cannot proceed "
                "without a signed download URL."
            )
    return resolved

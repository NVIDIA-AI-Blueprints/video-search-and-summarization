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

"""Public contracts for caller-supplied immutable media identities."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from .contracts import RequestSource


class ImmutableSourceIdentity(BaseModel):
    """Trusted identity for exact immutable bytes used by checkpointed ingestion.

    The provider promises that ``provider_namespace`` and ``revision`` identify
    the exact local file bytes represented by ``sha256`` and ``size_bytes``.
    ``sha256`` is lowercase 64-character hex, ``size_bytes`` is a nonnegative
    integer, and namespace/revision labels are nonblank strings up to 256 chars.
    This is an assertion of immutable media identity, not a checksum verified
    by the library. Checkpoint fingerprints bind all four fields on submit and
    resume; default hashing checkpoints retain their existing identity scheme.
    """

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        revalidate_instances="always",
    )

    schema_version: Literal["1.0"] = "1.0"
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: StrictInt = Field(ge=0)
    provider_namespace: StrictStr = Field(min_length=1, max_length=256)
    revision: StrictStr = Field(min_length=1, max_length=256)

    @field_validator("provider_namespace", "revision")
    @classmethod
    def reject_whitespace_only(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("identity labels must contain a non-whitespace character")
        return value


class SourceIdentityProvider(Protocol):
    """Caller-owned async resolver for a trusted immutable local media identity.

    Implementations that perform blocking work must offload it themselves.
    The library passes a detached submission-time RequestSource snapshot. Your
    implementation must bind its identity to the exact immutable local file at
    source.url, not just to source_id. Returning a stale hash for a mutable path
    can cause unsafe replay. The library checks regular-file stat and size but
    skips reading content when this provider is explicitly selected.
    Providers are used only for persistent local checkpointed ingestion, never
    serialized or closed by the library. Cancellation propagates; malformed
    results and provider errors become sanitized StorageConfigurationError.
    """

    async def resolve(self, source: RequestSource) -> ImmutableSourceIdentity:
        """Return identity for the exact immutable local bytes in ``source``.

        The returned ImmutableSourceIdentity includes digest, size, provider
        namespace and revision. Fetching/downloading or replacing media is not
        part of this callback; local stat must remain unchanged while it runs.
        """
        ...

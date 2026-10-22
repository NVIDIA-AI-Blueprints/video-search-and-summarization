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

"""Typed errors raised by the Model Manager core + clients.

``ModelManagerError`` carries a Model Promotion ``ErrorCode`` so the API layer
can render it directly as the frozen ``ErrorEnvelope`` schema with the right
HTTP status and ``retryable`` hint.
"""

from typing import Literal

ErrorCode = Literal[
    "not_found",
    "bad_request",
    "version_exists",
    "checksum_mismatch",
    "signature_invalid",
    "registry_unreachable",
    "load_failed",
    "readiness_timeout",
    "dataset_empty",
    "conflict",
    "internal",
]


class ModelManagerError(Exception):
    """Domain error mapped to the Model Promotion ``ErrorEnvelope`` at the edge."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        http_status: int = 500,
        details: dict | None = None,
    ):
        super().__init__(message)
        self.code: ErrorCode = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.details = details

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"ModelManagerError(code={self.code!r}, http_status={self.http_status}, "
            f"retryable={self.retryable}, message={self.message!r})"
        )

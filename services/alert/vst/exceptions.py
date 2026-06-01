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

"""Custom exceptions for VST operations.

Hierarchy:
    VSTError
    ├── VSTRecordingNotFoundError   — 404, no recording for stream/time
    ├── VSTOverloadedError          — 429 / 503, resource pressure
    ├── VSTUnavailableError         — other 5xx, service down
    ├── VSTTimeoutError             — network timeout / connection refused
    └── VSTClientError              — 4xx (non-404), bad request from AB
"""


class VSTError(Exception):
    """Base exception for all VST failures. Carries diagnostic context."""

    def __init__(self, message, status_code=None, response_body=None, category="unknown"):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body
        self.category = category


class VSTRecordingNotFoundError(VSTError):
    """VST returned 404 — no recording exists for the requested stream/time window."""
    pass


class VSTOverloadedError(VSTError):
    """VST returned 429 or 503 — resource pressure or rate limited."""
    pass


class VSTUnavailableError(VSTError):
    """VST returned 5xx (non-503) — service is down or erroring."""
    pass


class VSTTimeoutError(VSTError):
    """Network timeout or connection refused when reaching VST."""
    pass


class VSTClientError(VSTError):
    """VST returned 4xx (non-404) — bad request from Alert Bridge side."""
    pass

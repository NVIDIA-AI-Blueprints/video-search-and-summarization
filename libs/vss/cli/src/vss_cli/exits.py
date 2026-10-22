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

"""Exit codes, shared by every command group (SDD §8).

0-4 shipped with the original search CLI and are frozen -- a harness already
branches on them. 5-7 are additive, and each exists because a harness needs to
react differently: 5 means disambiguate, 6 means answer at the level you got,
7 means decide whether to spend the work again.
"""

from __future__ import annotations

from enum import IntEnum


class Exit(IntEnum):
    """Process exit codes. Frozen below 5; additive at and above it."""

    SUCCESS = 0
    #: Unexpected error. Report failure; nothing actionable.
    ERROR = 1
    #: Invalid input. The call itself is wrong -- fix and retry.
    INVALID_INPUT = 2
    #: Backend unreachable. Infrastructure, not the request; retry is sane.
    BACKEND_UNREACHABLE = 3
    #: Configuration error, including a missing or stale deployment config.
    CONFIGURATION = 4
    #: Handle not found -- unknown job_id/asset_id/event_id. Disambiguate.
    NOT_FOUND = 5
    #: Partial: retrieval succeeded, a later stage did not. The payload says
    #: which (e.g. ``persisted: false``). Retry only the failed stage -- never
    #: the whole job, or the work is done twice.
    PARTIAL = 6
    #: Timeout. The marker carries the job_id of a record written ``timeout``,
    #: so the job can be identified with status/get -- not rejoined. The work
    #: itself is gone; only the caller can decide to spend it again.
    TIMEOUT = 7

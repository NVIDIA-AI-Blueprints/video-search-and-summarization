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
"""Canonical job-type identifiers.

Single source of truth for the ``"llm_job"`` / ``"embedding_job"`` strings used
across the API (``job_type`` / ``config_type``), the task dispatcher, and the
backend capability declaration (``BackendProvider.supported_job_types``).

``str``-based so the wire format is unchanged and ``JobType.LLM == "llm_job"``
holds — existing raw-string comparisons and set membership keep working.
"""

from __future__ import annotations

from enum import Enum


class JobType(str, Enum):
    LLM = "llm_job"
    EMBEDDING = "embedding_job"
    AOI = "aoi_job"
    # Its own job type, not a ``use_case`` on ``aoi_job``: job_type decides the
    # submission edge (pipeline, trigger branch, whether ``workload_id`` is
    # required, which YAML block is the default), use_case decides runtime.
    ITS = "its_job"


#: The DEFT-backed job-type family, and ``DeftBackendProvider.supported_job_types``.
#:
#: Every submission-edge gate tests this rather than ``== JobType.AOI``, which
#: would silently answer "no" for a new DEFT job type. Lives here rather than on
#: the provider because ``src/api/`` must stay importable on an NMP-only image,
#: where the ``deft`` extra is absent — this module imports only ``enum``.
DEFT_JOB_TYPES: frozenset[JobType] = frozenset({JobType.AOI, JobType.ITS})


def is_deft_job_type(job_type: str | JobType | None) -> bool:
    """Whether ``job_type`` (a ``JobType`` or the raw wire string) is DEFT-backed.

    ``None`` and unknown strings are False, keeping callers fail-closed onto the
    NMP branch they already had.
    """
    return job_type in DEFT_JOB_TYPES

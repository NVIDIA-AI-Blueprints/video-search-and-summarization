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
"""Backend capability gate for the API edge.

The LLM/embedding pipelines are NMP-only; a DEFT image must reject them. Job
creation (``/jobs``), config storage (``/configs``), and trigger creation
(``/triggers``) all share the ``job_type`` / ``config_type`` vocabulary, so a
single helper gates them against the active backend's ``supported_job_types``.

Lives in its own module (rather than ``endpoints.py``) so both the main router
and the triggers sub-router can import it without a circular import.
"""

from __future__ import annotations

from fastapi import HTTPException

from src.backends.factory import get_backend_provider


def assert_supported_type(value: str, *, field_name: str) -> None:
    """Reject a job/config type the active backend can't run (e.g. an LLM/embedding
    job, config, or trigger on a DEFT image). Raises HTTP 400 if unsupported."""
    provider = get_backend_provider()
    if not provider.supports_job_type(value):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{field_name} '{value}' is not supported by the active backend "
                f"'{provider.kind}'. Supported: {sorted(provider.supported_job_types)}."
            ),
        )

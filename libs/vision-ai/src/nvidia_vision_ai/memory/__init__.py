# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Job memory: the ``JobStore`` protocol and its pydantic records.

Stores live with their callers; the CLI's SQLite store is ``nvidia_vision_ai_cli.store``.
"""

from .models import JobError
from .models import JobQuery
from .models import JobRecord
from .models import JobStatus
from .models import new_job_id
from .models import utc_now
from .store import JobStore

__all__ = [
    "JobError",
    "JobQuery",
    "JobRecord",
    "JobStatus",
    "JobStore",
    "new_job_id",
    "utc_now",
]

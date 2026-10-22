# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Job records: typed ids and UTC timestamps."""

from __future__ import annotations

from datetime import UTC
from datetime import datetime

from nvidia_vision_ai.memory import JobRecord
from nvidia_vision_ai.memory import new_job_id


def test_job_ids_are_typed_and_unique() -> None:
    ids = {new_job_id("vlm") for _ in range(1000)}
    assert len(ids) == 1000
    assert all(i.startswith("vlm-") and len(i) == 30 for i in ids)


def test_naive_datetimes_are_utc() -> None:
    record = JobRecord(job_id="vlm-x", type="vlm", status="running", created_at=datetime(2026, 1, 1))
    assert record.created_at.tzinfo == UTC

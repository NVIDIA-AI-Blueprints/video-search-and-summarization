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

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

from datetime import datetime


def parse_timestamp(ts_str: str) -> datetime:
    """Parse an ISO-8601 timestamp string into a datetime object."""
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


def timestamp_to_ms(dt: datetime) -> float:
    """Convert a datetime to milliseconds since epoch."""
    return (dt - datetime(1970, 1, 1, tzinfo=dt.tzinfo)).total_seconds() * 1000

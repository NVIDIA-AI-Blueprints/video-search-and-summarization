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

"""Dependency-injection protocols for VST consumers."""

from __future__ import annotations

from typing import Literal
from typing import Protocol
from typing import runtime_checkable


@runtime_checkable
class VSTSnapshot(Protocol):
    """VST surface for snapshots, source resolution, and clip URLs."""

    def build_screenshot_url(
        self,
        *,
        sensor_id: str,
        timestamp: str,
        internal: bool = False,
    ) -> str: ...

    async def resolve_stream_id(self, sensor_id: str) -> str: ...

    async def get_name_to_stream_id_map(self) -> dict[str, str]: ...

    async def get_timelines_map(self) -> dict[str, tuple[str, str]]: ...

    async def get_timeline(self, sensor_id: str) -> tuple[str, str]: ...

    async def get_video_clip_url(
        self,
        *,
        sensor_id: str,
        start_timestamp: str,
        end_timestamp: str,
        time_format: Literal["iso", "offset"],
        internal: bool = True,
        disable_audio: bool = True,
    ) -> str: ...

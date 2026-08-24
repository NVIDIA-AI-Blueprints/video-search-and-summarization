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

from pydantic import BaseModel
from typing import List, Dict, Any, Optional

class HeartbeatKwargs(BaseModel):
    sensor_name: str
    sensor_location: str
    prompt: str
    type: str = "imageSampling"

    class Config:
        json_schema_extra = {
            "example": {
                "sensor_name": "cam_lobby_01",
                "sensor_location": "Building A - Lobby",
                "prompt": "Describe the current scene.",
                "type": "imageSampling",
            }
        }


class HeartbeatConfig(BaseModel):
    task: str = "utils.scheduler.tasks.emit_heartbeat"
    schedule: float
    args: List[Any] = []
    kwargs: HeartbeatKwargs

    class Config:
        json_schema_extra = {
            "example": {
                "task": "utils.scheduler.tasks.emit_heartbeat",
                "schedule": 60.0,
                "args": [],
                "kwargs": {
                    "sensor_name": "cam_lobby_01",
                    "sensor_location": "Building A - Lobby",
                    "prompt": "Describe the current scene.",
                    "type": "imageSampling",
                },
            }
        }


class HeartbeatRequest(BaseModel):
    name: str
    config: HeartbeatConfig

    class Config:
        json_schema_extra = {
            "example": {
                "name": "cam_lobby_01",
                "config": {
                    "task": "utils.scheduler.tasks.emit_heartbeat",
                    "schedule": 60.0,
                    "args": [],
                    "kwargs": {
                        "sensor_name": "cam_lobby_01",
                        "sensor_location": "Building A - Lobby",
                        "prompt": "Describe the current scene.",
                        "type": "imageSampling",
                    },
                },
            }
        }

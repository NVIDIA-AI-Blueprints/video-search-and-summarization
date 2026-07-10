# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""
Shared per-sensor Frame protobuf builders for the fusion integration test.

Scenarios (e.g. sample_scenario) use these to construct the per-camera Frames
that get produced to the raw topic; at each instant every sensor reports the
SAME object ids (so the service aggregates by id) with slightly different bbox3d
coordinates, so the fused output is a non-trivial element-wise mean to assert on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List

import schema_pb2  # from src/ (wired via tests/conftest.py)
import measurement_fusion as mf


@dataclass
class ObjectSpec:
    id: str
    type: str
    confidence: float
    coords: List[float]  # 12 floats


@dataclass
class SensorFrame:
    instant: int
    sensor_id: str
    posix_ts: float
    frame: schema_pb2.Frame
    objects: List[ObjectSpec] = field(default_factory=list)


def make_object(spec: ObjectSpec) -> schema_pb2.Object:
    obj = schema_pb2.Object()
    obj.id = spec.id
    obj.type = spec.type
    obj.confidence = spec.confidence
    obj.bbox3d.coordinates[:] = spec.coords
    return obj


def make_frame(sensor_id: str, posix_ts: float, specs: List[ObjectSpec]) -> schema_pb2.Frame:
    frame = schema_pb2.Frame()
    frame.version = "4.0"
    frame.id = f"{sensor_id}-{int(posix_ts * 1000)}"
    frame.sensorId = sensor_id
    frame.timestamp.CopyFrom(mf._posix_to_proto_timestamp(posix_ts))
    frame.objects.extend(make_object(s) for s in specs)
    return frame

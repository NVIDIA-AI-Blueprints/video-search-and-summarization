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
Unit tests for the pure BEV fusion logic in measurement_fusion.py.

These exercise fuse_frames() and its helpers directly (no broker, no docker),
locking in the fusion contract the service guarantees:
  - fused timestamp  = arithmetic mean of input sensor timestamps
  - bbox3d coords    = element-wise mean across sensors (12 values)
  - confidence       = mean across sensors
  - object type      = majority vote (confidence-weighted tie-break)
  - timestamp bucket = same-instant frames across sensors share one key
  - fused sensorId   = "bev-sensor-1", info carries per-sensor timestamps
"""

import pytest

# Imported from src/ (wired onto sys.path by tests/conftest.py).
import schema_pb2
import measurement_fusion as mf


def _make_object(obj_id, obj_type, confidence, coords):
    obj = schema_pb2.Object()
    obj.id = obj_id
    obj.type = obj_type
    obj.confidence = confidence
    obj.bbox3d.coordinates[:] = coords
    return obj


def _make_frame(sensor_id, posix_ts, objects):
    frame = schema_pb2.Frame()
    frame.version = "4.0"
    frame.id = f"{sensor_id}-frame"
    frame.sensorId = sensor_id
    frame.timestamp.CopyFrom(mf._posix_to_proto_timestamp(posix_ts))
    frame.objects.extend(objects)
    return frame


def _coords(base):
    """A deterministic 12-float bbox3d coordinate vector offset by `base`."""
    return [float(base + i) for i in range(12)]


pytestmark = pytest.mark.unit


def test_fused_timestamp_is_mean_of_sensors():
    frames = {
        "Camera_00": _make_frame("Camera_00", 1000.0, [_make_object("p1", "Person", 0.8, _coords(0))]),
        "Camera_01": _make_frame("Camera_01", 1000.020, [_make_object("p1", "Person", 0.6, _coords(2))]),
    }
    fused = mf.fuse_frames(bucket_key=42, sensor_frames=frames)
    fused_ts = mf._parse_proto_timestamp(fused.timestamp)
    assert fused_ts == pytest.approx((1000.0 + 1000.020) / 2, abs=1e-6)


def test_bbox3d_coordinates_are_elementwise_mean():
    frames = {
        "Camera_00": _make_frame("Camera_00", 1000.0, [_make_object("p1", "Person", 0.8, _coords(0))]),
        "Camera_01": _make_frame("Camera_01", 1000.0, [_make_object("p1", "Person", 0.8, _coords(4))]),
    }
    fused = mf.fuse_frames(bucket_key=1, sensor_frames=frames)
    assert len(fused.objects) == 1
    got = list(fused.objects[0].bbox3d.coordinates)
    expected = [(a + b) / 2 for a, b in zip(_coords(0), _coords(4))]
    assert got == pytest.approx(expected, abs=1e-5)


def test_confidence_is_averaged():
    frames = {
        "Camera_00": _make_frame("Camera_00", 5.0, [_make_object("p1", "Person", 0.9, _coords(0))]),
        "Camera_01": _make_frame("Camera_01", 5.0, [_make_object("p1", "Person", 0.5, _coords(0))]),
        "Camera_02": _make_frame("Camera_02", 5.0, [_make_object("p1", "Person", 0.1, _coords(0))]),
    }
    fused = mf.fuse_frames(bucket_key=2, sensor_frames=frames)
    assert fused.objects[0].confidence == pytest.approx((0.9 + 0.5 + 0.1) / 3, abs=1e-6)
    # bbox3d.confidence mirrors the averaged object confidence.
    assert fused.objects[0].bbox3d.confidence == pytest.approx((0.9 + 0.5 + 0.1) / 3, abs=1e-6)


def test_object_type_majority_vote():
    # 2 sensors say Forklift, 1 says Person -> Forklift wins.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("o1", "Forklift", 0.5, _coords(0))]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("o1", "Forklift", 0.5, _coords(0))]),
        "Camera_02": _make_frame("Camera_02", 1.0, [_make_object("o1", "Person", 0.9, _coords(0))]),
    }
    fused = mf.fuse_frames(bucket_key=3, sensor_frames=frames)
    assert fused.objects[0].type == "Forklift"


def test_object_type_tie_broken_by_confidence():
    # 1 vs 1 tie -> higher total confidence wins (Person 0.9 > Forklift 0.2).
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("o1", "Forklift", 0.2, _coords(0))]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("o1", "Person", 0.9, _coords(0))]),
    }
    fused = mf.fuse_frames(bucket_key=4, sensor_frames=frames)
    assert fused.objects[0].type == "Person"


def test_objects_aggregated_by_id():
    # Two distinct object ids, each seen by both sensors -> two fused objects.
    frames = {
        "Camera_00": _make_frame(
            "Camera_00", 2.0,
            [_make_object("a", "Person", 0.8, _coords(0)), _make_object("b", "Forklift", 0.7, _coords(10))],
        ),
        "Camera_01": _make_frame(
            "Camera_01", 2.0,
            [_make_object("a", "Person", 0.6, _coords(2)), _make_object("b", "Forklift", 0.9, _coords(12))],
        ),
    }
    fused = mf.fuse_frames(bucket_key=5, sensor_frames=frames)
    by_id = {o.id: o for o in fused.objects}
    assert set(by_id) == {"a", "b"}
    assert list(by_id["a"].bbox3d.coordinates) == pytest.approx(
        [(x + y) / 2 for x, y in zip(_coords(0), _coords(2))], abs=1e-5
    )


def test_fused_sensor_id_and_info_map():
    frames = {
        "Camera_00": _make_frame("Camera_00", 1000.0, [_make_object("p1", "Person", 0.8, _coords(0))]),
        "Camera_01": _make_frame("Camera_01", 1000.0, [_make_object("p1", "Person", 0.8, _coords(0))]),
    }
    fused = mf.fuse_frames(bucket_key=99, sensor_frames=frames)
    assert fused.sensorId == "bev-sensor-1"
    assert fused.id == "99"
    # info carries one RFC3339 timestamp per source sensor.
    assert set(fused.info.keys()) == {"Camera_00", "Camera_01"}
    assert all(v.endswith("Z") for v in fused.info.values())


def test_ts_bucket_key_groups_same_instant_across_sensors():
    # Frames within half a 30-FPS frame (< BUCKET_MS) round to the same bucket.
    base = 1_700_000_000.0
    skew_s = (mf.BUCKET_MS / 2.0) / 1000.0  # well inside one bucket
    assert mf._ts_bucket_key(base) == mf._ts_bucket_key(base + skew_s)
    # A full frame apart (30 FPS ~ 33 ms) lands in a different bucket.
    assert mf._ts_bucket_key(base) != mf._ts_bucket_key(base + 0.033)


def test_element_wise_mean_empty_is_empty():
    assert mf._element_wise_mean([]) == []

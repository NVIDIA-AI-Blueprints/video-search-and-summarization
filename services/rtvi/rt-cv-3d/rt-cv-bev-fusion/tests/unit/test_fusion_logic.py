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
  - FUSION_METHOD    = "average" (every view, unweighted) or "gated_weighted" (default:
                       gate, then area-weighted mean); anything else raises at import
  - visibility gate  = views under VISIBILITY_MIN refused; an object no view sees well
                       enough is not published for that bucket
  - bbox3d coords    = mean across fused views, weighted by 2D box area
  - confidence       = mean across fused views
  - object type      = majority vote over fused views (confidence-weighted tie-break)
  - timestamp bucket = same-instant frames across sensors share one key
  - fused sensorId   = "bev-sensor-1", info carries per-sensor timestamps

The gate must stay inert for producers that do not report visibility, so both
directions are pinned: an absent field admits, a reported 0.0 refuses. Tests set
FUSION_METHOD explicitly rather than trusting the environment.
"""

import pytest

# Imported from src/ (wired onto sys.path by tests/conftest.py).
import schema_pb2
import measurement_fusion as mf


def _make_object(obj_id, obj_type, confidence, coords, visibility=None, box=None):
    """One sensor's view of an object.

    visibility=None leaves the key off the info map entirely, as a producer without
    outputVisibility looks on the wire. box is (width, height), the fusion weight.
    """
    obj = schema_pb2.Object()
    obj.id = obj_id
    obj.type = obj_type
    obj.confidence = confidence
    obj.bbox3d.coordinates[:] = coords
    if visibility is not None:
        obj.info["visibility"] = str(visibility)
    if box is not None:
        width, height = box
        obj.bbox.leftX, obj.bbox.topY = 0.0, 0.0
        obj.bbox.rightX, obj.bbox.bottomY = float(width), float(height)
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


def test_element_wise_mean_zero_weights_fall_back_to_plain_mean():
    # No 2D boxes in the payload means every weight is 0.0; the mean must survive it
    # rather than divide by zero.
    got = mf._element_wise_mean([[0.0, 10.0], [4.0, 20.0]], [0.0, 0.0])
    assert got == pytest.approx([2.0, 15.0], abs=1e-6)


@pytest.fixture
def gate_at_half(monkeypatch):
    """Pin method and threshold so these tests ignore the environment."""
    monkeypatch.setattr(mf, "FUSION_METHOD", "gated_weighted")
    monkeypatch.setattr(mf, "VISIBILITY_MIN", 0.5)


@pytest.fixture
def method_average(monkeypatch):
    monkeypatch.setattr(mf, "FUSION_METHOD", "average")


def test_default_fusion_method_is_gated_weighted():
    assert mf.FUSION_METHOD == "gated_weighted"
    assert set(mf.FUSION_METHODS) == {"average", "gated_weighted"}


def test_average_ignores_visibility_entirely(method_average):
    # Same input that gated_weighted would reduce to the 0.9 view alone.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.2)]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("p1", "Person", 0.8, _coords(4), visibility=0.9)]),
    }
    fused = mf.fuse_frames(bucket_key=20, sensor_frames=frames)
    expected = [(a + b) / 2 for a, b in zip(_coords(0), _coords(4))]
    assert list(fused.objects[0].bbox3d.coordinates) == pytest.approx(expected, abs=1e-5)


def test_average_publishes_objects_no_view_sees_well(method_average):
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.0)]),
    }
    fused = mf.fuse_frames(bucket_key=21, sensor_frames=frames)
    assert len(fused.objects) == 1


def test_unknown_fusion_method_is_rejected(monkeypatch):
    # A throwaway module, so a bad config cannot disturb `mf` for other tests.
    import importlib.util

    monkeypatch.setenv("FUSION_METHOD", "by-visibility")  # hyphen: a plausible typo
    spec = importlib.util.spec_from_file_location("mf_badconfig", mf.__file__)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ValueError, match="FUSION_METHOD"):
        spec.loader.exec_module(module)


def test_average_does_not_weight_by_bbox_area(method_average):
    # gated_weighted would weight these 1:4; average must stay a plain mean.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0,
                                 [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.9, box=(10, 10))]),
        "Camera_01": _make_frame("Camera_01", 1.0,
                                 [_make_object("p1", "Person", 0.8, _coords(4), visibility=0.9, box=(20, 20))]),
    }
    fused = mf.fuse_frames(bucket_key=22, sensor_frames=frames)
    expected = [(a + b) / 2 for a, b in zip(_coords(0), _coords(4))]
    assert list(fused.objects[0].bbox3d.coordinates) == pytest.approx(expected, abs=1e-5)


def test_low_visibility_view_is_refused(gate_at_half):
    # The 0.2 view is barely in frame; its position must not pull the fused one.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.2)]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("p1", "Person", 0.8, _coords(4), visibility=0.9)]),
    }
    fused = mf.fuse_frames(bucket_key=10, sensor_frames=frames)
    assert len(fused.objects) == 1
    assert list(fused.objects[0].bbox3d.coordinates) == pytest.approx(_coords(4), abs=1e-5)


def test_object_dropped_when_no_view_is_visible_enough(gate_at_half):
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.4)]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("p1", "Person", 0.8, _coords(4), visibility=0.1)]),
    }
    fused = mf.fuse_frames(bucket_key=11, sensor_frames=frames)
    assert list(fused.objects) == []


def test_zero_visibility_is_refused_not_read_as_missing(gate_at_half):
    # Regression: 0.0 is falsy, so `visibility or default` would admit it.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.0)]),
    }
    fused = mf.fuse_frames(bucket_key=12, sensor_frames=frames)
    assert list(fused.objects) == []


def test_threshold_is_inclusive(gate_at_half):
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.5)]),
    }
    fused = mf.fuse_frames(bucket_key=13, sensor_frames=frames)
    assert len(fused.objects) == 1


@pytest.mark.parametrize("reported", [None, "", "n/a"])
def test_unreported_visibility_admits_the_view(gate_at_half, reported):
    # A producer omitting the field must not have every object silently discarded.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=reported)]),
    }
    fused = mf.fuse_frames(bucket_key=14, sensor_frames=frames)
    assert len(fused.objects) == 1
    assert "visibility" not in fused.objects[0].info


def test_coordinates_weighted_by_bbox_area(gate_at_half):
    # 20x20 is four times the area of 10x10: (100*a + 400*b) / 500.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0,
                                 [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.9, box=(10, 10))]),
        "Camera_01": _make_frame("Camera_01", 1.0,
                                 [_make_object("p1", "Person", 0.8, _coords(4), visibility=0.9, box=(20, 20))]),
    }
    fused = mf.fuse_frames(bucket_key=15, sensor_frames=frames)
    expected = [0.2 * a + 0.8 * b for a, b in zip(_coords(0), _coords(4))]
    assert list(fused.objects[0].bbox3d.coordinates) == pytest.approx(expected, abs=1e-5)


def test_type_vote_and_confidence_ignore_refused_views(gate_at_half):
    # Both Forklift views are refused, so the one visible Person wins the vote.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("o1", "Forklift", 0.9, _coords(0), visibility=0.1)]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("o1", "Forklift", 0.9, _coords(0), visibility=0.2)]),
        "Camera_02": _make_frame("Camera_02", 1.0, [_make_object("o1", "Person", 0.4, _coords(0), visibility=0.8)]),
    }
    fused = mf.fuse_frames(bucket_key=16, sensor_frames=frames)
    assert fused.objects[0].type == "Person"
    assert fused.objects[0].confidence == pytest.approx(0.4, abs=1e-6)


def test_fused_visibility_is_mean_over_admitted_views(gate_at_half):
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.6)]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=1.0)]),
        "Camera_02": _make_frame("Camera_02", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.2)]),
    }
    fused = mf.fuse_frames(bucket_key=17, sensor_frames=frames)
    assert float(fused.objects[0].info["visibility"]) == pytest.approx(0.8, abs=1e-6)

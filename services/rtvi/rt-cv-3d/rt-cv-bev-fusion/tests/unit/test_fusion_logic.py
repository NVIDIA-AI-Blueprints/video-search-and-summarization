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
  - FUSION_METHOD    = one of four ungated baselines (first/closest/mean/median) or
                       "rays" (default: gate, then ray WLS); else raises at import
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

import collections

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


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Filter, conflict radius and auto foot offset are all on by default and
    keyed by object id, which every test reuses. Tests opt back in explicitly."""
    monkeypatch.setattr(mf, "TEMPORAL_FILTER", False)
    monkeypatch.setattr(mf, "_TRACKS", {})
    monkeypatch.setattr(mf, "CONFLICT_RADIUS", 0.0)
    monkeypatch.setattr(mf, "MAX_SPEED", 0.0)
    monkeypatch.setattr(mf, "_LASTPUB", {})
    monkeypatch.setattr(mf, "_SPLITS", {})
    monkeypatch.setattr(mf, "_LASTOUT", {})
    monkeypatch.setattr(mf, "_ID_FREED", {})
    monkeypatch.setattr(mf, "_ID_MAX", 0)
    monkeypatch.setattr(mf, "SPLIT_ON_REACQUIRE", False)
    monkeypatch.setattr(mf, "FOOT_OFFSET", "off")
    monkeypatch.setattr(mf, "_FOOT_PAIRS", collections.deque(maxlen=100))
    monkeypatch.setattr(mf, "_FOOT_STATE", {"value": 0.0, "pairs": 0, "next_bucket": 0})


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
    monkeypatch.setattr(mf, "FUSION_METHOD", "rays")
    monkeypatch.setattr(mf, "VISIBILITY_MIN", 0.5)


@pytest.fixture
def method_mean(monkeypatch):
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")


def test_default_fusion_method_is_rays():
    assert mf.FUSION_METHOD == "rays"
    assert set(mf.FUSION_METHODS) == {"first", "closest", "mean", "median", "rays"}


def test_mean_ignores_visibility_entirely(method_mean):
    # Same input that rays would reduce to the 0.9 view alone.
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.2)]),
        "Camera_01": _make_frame("Camera_01", 1.0, [_make_object("p1", "Person", 0.8, _coords(4), visibility=0.9)]),
    }
    fused = mf.fuse_frames(bucket_key=20, sensor_frames=frames)
    expected = [(a + b) / 2 for a, b in zip(_coords(0), _coords(4))]
    assert list(fused.objects[0].bbox3d.coordinates) == pytest.approx(expected, abs=1e-5)


def test_mean_publishes_objects_no_view_sees_well(method_mean):
    frames = {
        "Camera_00": _make_frame("Camera_00", 1.0, [_make_object("p1", "Person", 0.8, _coords(0), visibility=0.0)]),
    }
    fused = mf.fuse_frames(bucket_key=21, sensor_frames=frames)
    assert len(fused.objects) == 1


def test_median_outvotes_rather_than_outweighs(monkeypatch):
    # Two views agree near 10; the third is 40 m off with four times the box area,
    # which is exactly the case an area-weighted mean gets wrong.
    monkeypatch.setattr(mf, "FUSION_METHOD", "median")
    monkeypatch.setattr(mf, "MAX_DIST", 0.0)
    fused = mf.fuse_frames(23, _views(("C0", _at(10, 0), (10, 10)),
                                      ("C1", _at(11, 0), (10, 10)),
                                      ("C2", _at(50, 0), (20, 20))))
    assert list(fused.objects[0].bbox3d.coordinates)[0] == pytest.approx(11.0, abs=1e-5)


def test_unknown_fusion_method_is_rejected(monkeypatch):
    # A throwaway module, so a bad config cannot disturb `mf` for other tests.
    import importlib.util

    monkeypatch.setenv("FUSION_METHOD", "by-visibility")  # hyphen: a plausible typo
    spec = importlib.util.spec_from_file_location("mf_badconfig", mf.__file__)
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(ValueError, match="FUSION_METHOD"):
        spec.loader.exec_module(module)


def test_mean_does_not_weight_by_bbox_area(method_mean):
    # rays would weight these 1:4; mean must stay a plain mean.
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


def _at(x, y):
    """bbox3d whose ground position is (x, y); the rest is padding."""
    return [float(x), float(y)] + [0.0] * 10


def _views(*specs):
    """specs: (sensor, coords, box[, type, conf, vis]) -> frames dict."""
    frames = {}
    for sid, coords, box, *rest in specs:
        typ, conf, vis = (list(rest) + ["Person", 0.8, 0.9])[:3] if rest else ("Person", 0.8, 0.9)
        frames[sid] = _make_frame(sid, 1.0, [_make_object("p1", typ, conf, coords, visibility=vis, box=box)])
    return frames


# --- geometry-aware methods -------------------------------------------------
# A ground projection is precise across the camera ray and vague along it, so a
# wide baseline pins a position neither view could fix alone.

@pytest.fixture
def two_cameras(monkeypatch):
    """A due south, B due west, 40 m out, 4 m up. Range matters: the anisotropy
    is slant/height, ~10 here, so the rays genuinely cross."""
    monkeypatch.setattr(mf, "CAMERAS", {"Camera_A": {"x": 0.0, "y": -40.0, "h": 4.0, "f": 1000.0},
                                        "Camera_B": {"x": -40.0, "y": 0.0, "h": 4.0, "f": 1000.0}})
    monkeypatch.setattr(mf, "MAX_DIST", 0.0)     # these sit past the gate on purpose


def test_rays_recovers_the_crossing_point(two_cameras, monkeypatch):
    # Each camera errs only along its own ray. The mean would say (1.5, 1.5).
    monkeypatch.setattr(mf, "FUSION_METHOD", "rays")
    coords = list(mf.fuse_frames(50, _views(("Camera_A", _at(0, 3), (100, 100)),
                                            ("Camera_B", _at(3, 0), (100, 100)))).objects[0].bbox3d.coordinates)
    assert (coords[0], coords[1]) == pytest.approx((0.0, 0.0), abs=0.15)


def test_rays_falls_back_below_two_calibrated_views(two_cameras, monkeypatch):
    # sigma_along is finite, so one view alone would solve to its own position.
    monkeypatch.setattr(mf, "FUSION_METHOD", "rays")
    monkeypatch.setattr(mf, "CAMERAS", {"Camera_A": {"x": 0.0, "y": -40.0, "h": 4.0, "f": 1000.0}})
    coords = list(mf.fuse_frames(51, _views(("Camera_A", _at(0, 2), (100, 100)),
                                            ("Camera_B", _at(0, 4), (100, 100)))).objects[0].bbox3d.coordinates)
    assert coords[1] == pytest.approx(3.0, abs=1e-5)


def test_closest_takes_the_nearest_camera(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "FUSION_METHOD", "closest")
    coords = list(mf.fuse_frames(52, _views(("Camera_A", _at(0, 1), (10, 10)),
                                            ("Camera_B", _at(-2, 0), (10, 10)))).objects[0].bbox3d.coordinates)
    assert coords[0] == pytest.approx(-2.0, abs=1e-5)


def test_first_is_stable_against_arrival_order(monkeypatch):
    # "first" is by sensor id, or the result would follow network timing.
    monkeypatch.setattr(mf, "FUSION_METHOD", "first")
    monkeypatch.setattr(mf, "MAX_DIST", 0.0)
    specs = (("Camera_A", _at(1, 0), (10, 10)), ("Camera_B", _at(9, 0), (10, 10)))
    got = {list(mf.fuse_frames(53, _views(*o)).objects[0].bbox3d.coordinates)[0]
           for o in (specs, specs[::-1])}
    assert got == {1.0}


@pytest.mark.parametrize("env, match", [
    ({"FUSION_METHOD": "by-visibility"}, "FUSION_METHOD"),
    ({"FUSION_METHOD": "closest", "CALIBRATION_PATH": "/nonexistent.json"}, "calibration"),
])
def test_bad_configuration_is_rejected_at_import(monkeypatch, env, match):
    # A throwaway module, so a bad config cannot disturb `mf` for other tests.
    import importlib.util
    for k, v in env.items(): monkeypatch.setenv(k, v)
    spec = importlib.util.spec_from_file_location("mf_badconfig", mf.__file__)
    with pytest.raises(ValueError, match=match):
        spec.loader.exec_module(importlib.util.module_from_spec(spec))


# --- range gate and temporal filter -----------------------------------------

def test_range_gate_drops_far_views_but_keeps_uncalibrated_ones(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_DIST", 25.0)
    monkeypatch.setattr(mf, "FUSION_METHOD", "rays")
    # Camera_A is 40 m out and refused; Camera_B is 10 m out and kept.
    coords = list(mf.fuse_frames(60, _views(("Camera_A", _at(0, 0), (10, 10)),
                                            ("Camera_B", _at(-30, 0), (10, 10)))).objects[0].bbox3d.coordinates)
    assert coords[0] == pytest.approx(-30.0, abs=1e-5)
    # Every view too far: the object is not published at all.
    monkeypatch.setattr(mf, "MAX_DIST", 5.0)
    assert list(mf.fuse_frames(61, _views(("Camera_A", _at(0, 0), (10, 10)))).objects) == []
    # Range unknown without calibration, so the view survives.
    monkeypatch.setattr(mf, "CAMERAS", {})
    monkeypatch.setattr(mf, "MAX_DIST", 1.0)
    assert len(mf.fuse_frames(62, _views(("Camera_A", _at(500, 500), (10, 10)))).objects) == 1


@pytest.fixture
def filtering(monkeypatch):
    monkeypatch.setattr(mf, "TEMPORAL_FILTER", True)
    monkeypatch.setattr(mf, "FUSION_METHOD", "rays")
    monkeypatch.setattr(mf, "MAX_DIST", 0.0)


def _step(bucket, x, y, oid="p1"):
    frames = {"Camera_A": _make_frame("Camera_A", 1.0,
              [_make_object(oid, "Person", 0.8, _at(x, y), box=(10, 10))])}
    return mf.fuse_frames(bucket, frames)


def test_filter_publishes_the_first_observation_unchanged(filtering):
    coords = list(_step(100, 3.0, 4.0).objects[0].bbox3d.coordinates)
    assert (coords[0], coords[1]) == pytest.approx((3.0, 4.0), abs=1e-9)


def test_filter_pulls_back_an_implausible_jump(filtering):
    for b in range(100, 110): _step(b, 0.0, 0.0)
    # 8 m in one bucket is far past anything ACCEL_SIGMA allows.
    assert 0.0 <= list(_step(110, 8.0, 0.0).objects[0].bbox3d.coordinates)[0] < 4.0


def test_track_restarts_after_a_gap_and_state_is_pruned(filtering, monkeypatch):
    monkeypatch.setattr(mf, "FILTER_RESET_BUCKETS", 5)
    for b in range(200, 210): _step(b, 0.0, 0.0)
    assert "p1" in mf._TRACKS
    # Seen again much later: predicting across the gap would be invention.
    assert list(_step(400, 9.0, 0.0).objects[0].bbox3d.coordinates)[0] == pytest.approx(9.0, abs=1e-9)
    _step(500, 1.0, 1.0, oid="p2")     # p1 now older than the reset window
    assert "p1" not in mf._TRACKS


# --- foot offset ------------------------------------------------------------

def test_foot_offset_pushes_each_view_away_from_its_own_camera(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "FOOT_OFFSET", "0.04")
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    # Camera_A sits at (0,-40) h=4, so a point 40 m out moves 40*0.04/4 = 0.4 m further.
    coords = list(mf.fuse_frames(70, _views(("Camera_A", _at(0, 0), (10, 10)))).objects[0].bbox3d.coordinates)
    assert (coords[0], coords[1]) == pytest.approx((0.0, 0.4), abs=1e-6)


def test_foot_offset_auto_measures_the_value_that_makes_cameras_agree(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "FOOT_OFFSET", "auto")
    monkeypatch.setattr(mf, "FOOT_OFFSET_MIN_PAIRS", 1)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    # Both cameras report 0.2 m short of (0,0) along their own ray; the offset
    # that reconciles them is 0.2 * h / d = 0.2 * 4 / 40 = 0.02 m.
    for bucket in range(3):
        mf.fuse_frames(80 + bucket, _views(("Camera_A", _at(0, -0.2), (10, 10)),
                                           ("Camera_B", _at(-0.2, 0), (10, 10))))
        monkeypatch.setitem(mf._FOOT_STATE, "next_bucket", 0)
    assert mf.foot_offset() == pytest.approx(0.02, abs=0.0026)


def test_foot_offset_auto_applies_nothing_until_enough_pairs(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "FOOT_OFFSET", "auto")
    monkeypatch.setattr(mf, "FOOT_OFFSET_MIN_PAIRS", 10_000)
    mf.fuse_frames(90, _views(("Camera_A", _at(0, -0.2), (10, 10)),
                              ("Camera_B", _at(-0.2, 0), (10, 10))))
    assert mf.foot_offset() == 0.0


# --- false associations -----------------------------------------------------

def test_conflicting_views_are_declined_without_history(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "CONFLICT_RADIUS", 2.0)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    # 8 m apart, nothing known about the track: the mean would sit between two people.
    assert list(mf.fuse_frames(100, _views(("Camera_A", _at(0, 0), (10, 10)),
                                           ("Camera_B", _at(8, 0), (10, 10)))).objects) == []


def test_conflicting_views_resolve_toward_the_track_prediction(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "CONFLICT_RADIUS", 2.0)
    monkeypatch.setattr(mf, "TEMPORAL_FILTER", True)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    for bucket in range(110, 116):        # establish a track sitting near the origin
        mf.fuse_frames(bucket, _views(("Camera_A", _at(0, 0), (10, 10))))
    coords = list(mf.fuse_frames(116, _views(("Camera_A", _at(0, 0), (10, 10)),
                                             ("Camera_B", _at(8, 0), (10, 10)))).objects[0].bbox3d.coordinates)
    assert coords[0] == pytest.approx(0.0, abs=0.5)     # the 8 m view is dropped


def test_agreeing_views_are_left_alone(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "CONFLICT_RADIUS", 2.0)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    coords = list(mf.fuse_frames(120, _views(("Camera_A", _at(0, 0), (10, 10)),
                                             ("Camera_B", _at(1, 0), (10, 10)))).objects[0].bbox3d.coordinates)
    assert coords[0] == pytest.approx(0.5, abs=1e-6)


# --- fixed-lag smoothing ----------------------------------------------------

def test_smoothed_position_needs_a_later_bucket(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "SMOOTH_LAG", 3)
    monkeypatch.setattr(mf, "TEMPORAL_FILTER", True)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(130, _views(("Camera_A", _at(0, 0), (10, 10))))
    assert mf._smoothed_position("p1", 130) is None      # nothing after it yet
    for bucket in (131, 132):
        mf.fuse_frames(bucket, _views(("Camera_A", _at(0, 0), (10, 10))))
    assert mf._smoothed_position("p1", 131) == pytest.approx((0.0, 0.0), abs=1e-6)
    assert mf._smoothed_position("p1", 99) is None       # no longer retained


# --- speed gate ---------------------------------------------------------------

def test_speed_gate_withholds_an_impossible_step(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(200, _views(("Camera_A", _at(0, 0), (10, 10))))
    # 30 m in one 17 ms bucket is ~1700 m/s.
    assert list(mf.fuse_frames(201, _views(("Camera_A", _at(30, 0), (10, 10)))).objects) == []


def test_speed_gate_accepts_a_persistent_move(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    monkeypatch.setattr(mf, "REACQUIRE", 3)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(210, _views(("Camera_A", _at(0, 0), (10, 10))))
    seen = [len(mf.fuse_frames(210 + i, _views(("Camera_A", _at(30, 0), (10, 10)))).objects)
            for i in range(1, 5)]
    # refused twice, then taken: the id was reused or the track really moved
    assert seen[:2] == [0, 0] and seen[2] == 1


def test_speed_gate_allows_ordinary_walking(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(220, _views(("Camera_A", _at(0, 0), (10, 10))))
    # 1.5 m/s over two buckets is a person walking
    step = 1.5 * 2 * mf.BUCKET_MS / 1000.0
    assert len(mf.fuse_frames(222, _views(("Camera_A", _at(step, 0), (10, 10)))).objects) == 1


def test_reacquire_keeps_the_id_by_default(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    monkeypatch.setattr(mf, "REACQUIRE", 2)
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(300, _views(("Camera_A", _at(0, 0), (10, 10))))
    for b in (301, 302):
        out = mf.fuse_frames(b, _views(("Camera_A", _at(30, 0), (10, 10)))).objects
    assert [o.id for o in out] == ["p1"]          # same id, position leapt


def test_split_on_reacquire_starts_a_new_id(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    monkeypatch.setattr(mf, "REACQUIRE", 2)
    monkeypatch.setattr(mf, "SPLIT_ON_REACQUIRE", True)
    monkeypatch.setattr(mf, "_ID_MAX", 5)          # ids 1..5 are in play
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(310, _views(("Camera_A", _at(0, 0), (10, 10))))
    for b in (311, 312):
        out = mf.fuse_frames(b, _views(("Camera_A", _at(30, 0), (10, 10)))).objects
    # the old id never leaps; the relocation is published as a new track
    new = out[0].id
    assert new != "p1" and new.isdigit()
    assert mf.published_id("p1") == new


def test_smoothing_may_refine_but_not_relocate(monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    assert not mf._smoothing_relocates("t", 100, 0.0, 0.0)      # first, always fine
    assert not mf._smoothing_relocates("t", 101, 0.1, 0.0)      # a refinement
    assert mf._smoothing_relocates("t", 102, 40.0, 0.0)         # a relocation


def test_free_id_prefers_the_longest_unused_below_the_max(monkeypatch):
    monkeypatch.setattr(mf, "_ID_MAX", 5)
    monkeypatch.setattr(mf, "_LASTPUB", {"1": (9, 0, 0, 0), "2": (9, 0, 0, 0)})
    monkeypatch.setattr(mf, "_ID_FREED", {"3": 50, "4": 10})
    assert mf._free_id() == "5"          # never used at all: the oldest there is
    monkeypatch.setattr(mf, "_ID_FREED", {"3": 50, "4": 10, "5": 90})
    assert mf._free_id() == "4"          # of the used ones, gone the longest


def test_free_id_never_exceeds_the_max_in_play(monkeypatch):
    monkeypatch.setattr(mf, "_ID_MAX", 2)
    monkeypatch.setattr(mf, "_LASTPUB", {"1": (9, 0, 0, 0), "2": (9, 0, 0, 0)})
    assert mf._free_id() is None         # all busy, and it will not invent a 3


def test_object_is_dropped_when_no_id_is_free(two_cameras, monkeypatch):
    monkeypatch.setattr(mf, "MAX_SPEED", 10.0)
    monkeypatch.setattr(mf, "REACQUIRE", 2)
    monkeypatch.setattr(mf, "SPLIT_ON_REACQUIRE", True)
    monkeypatch.setattr(mf, "_ID_MAX", 2)
    monkeypatch.setattr(mf, "_LASTPUB", {"1": (310, 0, 0, 0), "2": (310, 0, 0, 0)})
    monkeypatch.setattr(mf, "FUSION_METHOD", "mean")
    mf.fuse_frames(310, _views(("Camera_A", _at(0, 0), (10, 10))))
    out = [len(mf.fuse_frames(b, _views(("Camera_A", _at(30, 0), (10, 10)))).objects)
           for b in (311, 312, 313)]
    assert out == [0, 0, 0]          # nothing to hand it, so nothing published


@pytest.mark.unit
def test_smoothed_frames_publish_in_bucket_order():
    """Concurrent flushes must not interleave buckets on the output topic.

    Two threads release overlapping ranges of held frames at once; every frame
    must still be published, exactly once, in non-decreasing bucket order.
    """
    import threading
    import time

    svc = mf.MeasurementFusionService.__new__(mf.MeasurementFusionService)
    svc._lock = threading.Lock()
    svc._publish_lock = threading.Lock()
    svc._pending = {}
    svc._published = 0
    published = []
    svc._publish = lambda payload: published.append(int(payload))
    # Skip smoothing maths: this test is about ordering, not positions. The
    # sleep widens the detach->publish window so an unserialised publish
    # interleaves reliably instead of depending on GIL scheduling luck.
    def emit(ready):
        for b, _ in ready:
            time.sleep(0.0005)
            svc._publish(str(b).encode())
    svc._emit_smoothed = emit

    buckets = list(range(200))
    for b in buckets:
        svc._pending[b] = b

    barrier = threading.Barrier(2)

    def releaser(step):
        barrier.wait()
        for up_to in range(0, 200, step):
            svc._release_smoothed(up_to)
        svc._release_smoothed(199)

    threads = [threading.Thread(target=releaser, args=(s,)) for s in (3, 7)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert sorted(published) == buckets, "every held frame is published exactly once"
    assert published == sorted(published), f"published out of order: {published[:12]}"

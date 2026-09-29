# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Boundary correctness and source-specific regression checks.

Real-footage checks consume a separately generated analysis, set through
VISION_ANALYSIS_PATH; no results are generated or invented by the tests.
"""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from backend.vision import _decode_command, analyze_video, find_anchor, load_calibration, measure_bottle, segment_strip


def synthetic_surface(row=100, height=200, width=60):
    hsv = np.zeros((height, width, 3), np.uint8)
    hsv[:] = (24, 130, 220)
    hsv[row:] = (14, 180, 205)
    return hsv


def test_known_surface_is_measured_from_pixels():
    scene = load_calibration()["scenes"][0]
    for row in [35, 80, 125, 165]:
        result = segment_strip(synthetic_surface(row), scene)
        assert result["state"] == "measured_boundary"
        assert abs(result["level"] - (1 - row / 200)) < .035
        assert result["confidence"] > .8


def test_no_liquid_means_unknown_below_window_not_fake_zero():
    result = segment_strip(synthetic_surface(200), load_calibration()["scenes"][0])
    assert result["level"] is None
    assert result["state"] == "below_visible_window"


def test_dark_occluder_hiding_surface_is_not_a_precise_height():
    image = synthetic_surface(100)
    image[80:120] = (0, 0, 30)
    result = segment_strip(image, load_calibration()["scenes"][0])
    assert result["level"] is None
    assert result["state"] in {"surface_occluded", "occluded"}


def test_unknown_source_is_rejected_before_calibrated_analysis(tmp_path):
    path = tmp_path / "unreviewed.mp4"
    path.write_bytes(b"different footage")
    with pytest.raises(ValueError, match="no reviewed calibration"):
        analyze_video(str(path))


def test_blank_frame_does_not_create_anchor():
    assert find_anchor(np.zeros((806, 1280, 3), np.uint8), load_calibration()["scenes"][0]) is None


def test_exact_source_frame_selection_without_fps_resampling():
    command = _decode_command("input.mp4", 1280, 806, 5, 30)
    assert command[command.index("-vf") + 1] == "select=not(mod(n\\,6)),scale=1280:806"
    assert command[command.index("-vsync") + 1] == "0"
    with pytest.raises(ValueError, match="divide"):
        _decode_command("input.mp4", 1280, 806, 4, 30)


def test_offscreen_measurement_is_unknown_instead_of_rescaled():
    scene = load_calibration()["scenes"][0]
    result = measure_bottle(np.zeros((806, 1280, 3), np.uint8), scene, scene["bottles"][0], (0., 300.))
    assert result["measurement_state"] == "out_of_frame"
    assert result["level"] is None
    assert result["measurement_box_clipped"]
    assert result["surface"] == []
    for field in ["box", "measurement_box"]:
        x, y, w, h = result[field]
        assert 0 <= x <= x + w <= 1.000001
        assert 0 <= y <= y + h <= 1.000001


@pytest.fixture(scope="module")
def actual_analysis():
    path = os.environ.get("VISION_ANALYSIS_PATH")
    if not path:
        pytest.skip("Set VISION_ANALYSIS_PATH to independently computed actual-video analysis")
    return json.loads(Path(path).read_text())


def _sample(result, time):
    return min(result["samples"], key=lambda row: abs(row["t"] - time))


def test_original_frames_show_rising_boundary(actual_analysis):
    # Broad ranges were visually reviewed against frames at 21 and 24 seconds;
    # these are geometric validation bounds, not labels supplied to the engine.
    for name in ["a-front", "a-middle", "a-rear"]:
        values = []
        for t, low, high in [(21., .28, .48), (24., .48, .74), (28., .94, 1.)]:
            bottle = next(b for b in _sample(actual_analysis, t)["bottles"] if b["id"] == name)
            assert bottle["level"] is not None, (name, t, bottle)
            assert low <= bottle["level"] <= high, (name, t, bottle["level"])
            values.append(bottle["level"])
        assert values == sorted(values)


def test_rail_hidden_boundaries_and_partial_second_view_are_unknown(actual_analysis):
    for t in [19., 20.]:
        for name in ["a-front", "a-middle"]:
            bottle = next(b for b in _sample(actual_analysis, t)["bottles"] if b["id"] == name)
            assert bottle["level"] is None
            assert bottle["measurement_state"] == "surface_occluded"
    for t in [43., 46., 49., 57.]:
        bottle = _sample(actual_analysis, t)["bottles"][0]
        assert bottle["level"] is None
        assert bottle["measurement_state"] == "partial_view_not_validated"


def test_uncalibrated_and_overflow_shots_have_no_invented_heights(actual_analysis):
    for t in [0., 12., 63., 67., 80., 100.]:
        row = _sample(actual_analysis, t)
        assert row["scene_id"] is None
        assert row["bottles"] == []
        assert row["phase"] == "outside_calibrated_view"


def test_distinct_identity_across_shots_and_normalized_geometry(actual_analysis):
    ids = {}
    for row in actual_analysis["samples"]:
        for b in row["bottles"]:
            assert ids.setdefault(b["id"], row["scene_id"]) == row["scene_id"]
            assert b["level"] is None or 0 <= b["level"] <= 1
            for point in b["surface"] + b["mask"]:
                assert all(0 <= coordinate <= 1 for coordinate in point)
            assert b["level_kind"] == "visible-height"
            # The second camera pans down at ~54 s: its bottle display region
            # reaches outside the source. The visible overlay must be clipped,
            # while its unvalidated measurement remains null.
            for field in ["box", "measurement_box"]:
                x, y, w, h = b[field]
                assert 0 <= x <= x + w <= 1.000001, (row["t"], b["id"], field)
                assert 0 <= y <= y + h <= 1.000001, (row["t"], b["id"], field)
    assert len(actual_analysis["samples"]) >= 425


def test_events_have_actual_measurement_evidence(actual_analysis):
    for event in actual_analysis["events"]:
        if event["type"] == "reference_crossing":
            row = _sample(actual_analysis, event["t"])
            bottle = next(b for b in row["bottles"] if b["id"] == event["bottle_id"])
            assert bottle["level"] >= .90
        assert event["type"] not in {"spill", "underfilled", "overfilled"}

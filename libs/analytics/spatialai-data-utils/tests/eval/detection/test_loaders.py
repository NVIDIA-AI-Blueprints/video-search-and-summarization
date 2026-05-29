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

import json
import math

import pytest

from spatialai_data_utils.core.geometry.rotation import euler_to_quaternion
from spatialai_data_utils.eval.detection.loaders import load_boxes_from_jsonl
from spatialai_data_utils.utils.datetime_utils import parse_timestamp


def _write_jsonl(tmp_path, filename, rows):
    path = tmp_path / filename
    with open(path, "w") as file:
        for row in rows:
            file.write(json.dumps(row) + "\n")
    return str(path)


def _box_row(timestamp, confidence=0.9, object_id=1):
    return {
        "id": object_id,
        "sensorId": "Camera_01",
        "timestamp": timestamp,
        "objects": [
            {
                "id": object_id,
                "type": "Person",
                "bbox3d": {
                    "coordinates": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],
                    "confidence": confidence,
                },
            }
        ],
    }


def test_parse_timestamp_handles_fractional_and_whole_seconds():
    assert parse_timestamp("2025-01-01T00:00:00.123Z").microsecond == 123000
    assert parse_timestamp("2025-01-01T00:00:00Z").second == 0


def test_get_frame_id_for_timestamp_increments_by_fps(tmp_path):
    gt_path = _write_jsonl(
        tmp_path,
        "gt.jsonl",
        [
            _box_row("2025-01-01T00:00:00.000Z", object_id=10),
            _box_row("2025-01-01T00:00:00.050Z", object_id=11),
        ],
    )
    pred_path = _write_jsonl(
        tmp_path,
        "pred.jsonl",
        [
            _box_row("2025-01-01T00:00:00.000Z", object_id=20),
            _box_row("2025-01-01T00:00:00.050Z", object_id=21),
        ],
    )

    _, pred_boxes = load_boxes_from_jsonl(gt_path, pred_path, fps=10.0)

    assert set(pred_boxes.sample_tokens) == {"1", "2"}


def test_euler_to_quaternion_shapes():
    assert euler_to_quaternion(0.0, 0.0, 0.0) == (1.0, 0.0, 0.0, 0.0)

    quat = euler_to_quaternion(0.0, math.pi / 2, 0.0)
    assert len(quat) == 4
    assert all(isinstance(value, float) for value in quat)


def test_find_base_timestamp_uses_earliest_first_line(tmp_path):
    gt_path = _write_jsonl(
        tmp_path,
        "gt.jsonl",
        [_box_row("2025-01-01T00:00:01.000Z", object_id=10)],
    )
    pred_path = _write_jsonl(
        tmp_path,
        "pred.jsonl",
        [
            _box_row("2025-01-01T00:00:00.500Z", object_id=20),
            _box_row("2025-01-01T00:00:01.000Z", object_id=21),
        ],
    )

    gt_boxes, pred_boxes = load_boxes_from_jsonl(
        gt_path,
        pred_path,
        fps=2.0,
        confidence_threshold=0.0,
    )
    assert set(pred_boxes.sample_tokens) == {"1", "2"}
    assert set(gt_boxes.sample_tokens) == {"2"}


def test_load_pred_boxes_groups_by_frame(tmp_path):
    gt_path = _write_jsonl(
        tmp_path,
        "gt.jsonl",
        [_box_row("2025-01-01T00:00:00.000Z")],
    )
    pred_path = _write_jsonl(
        tmp_path,
        "pred.jsonl",
        [
            _box_row("2025-01-01T00:00:00.000Z", confidence=0.9),
            _box_row("2025-01-01T00:00:00.500Z", confidence=0.8),
        ],
    )

    _, pred_boxes = load_boxes_from_jsonl(
        gt_path,
        pred_path,
        fps=2.0,
        confidence_threshold=0.0,
    )

    assert set(pred_boxes.sample_tokens) == {"1", "2"}
    assert len(pred_boxes["1"]) == 1
    assert len(pred_boxes["2"]) == 1


def test_load_gt_boxes_filters_by_prediction_timestamps_and_offset(tmp_path):
    pred_path = _write_jsonl(
        tmp_path,
        "pred.jsonl",
        [
            _box_row("2025-01-01T00:00:00.000Z", object_id=20),
            _box_row("2025-01-01T00:00:00.500Z", object_id=21),
        ],
    )
    gt_path = _write_jsonl(
        tmp_path,
        "gt.jsonl",
        [
            _box_row("2025-01-01T00:00:00.000Z", object_id=10),
            _box_row("2025-01-01T00:00:00.500Z", object_id=11),
        ],
    )

    gt_boxes, _ = load_boxes_from_jsonl(
        gt_path,
        pred_path,
        fps=2.0,
        confidence_threshold=0.0,
        ground_truth_frame_offset_secs=0.5,
    )

    assert set(gt_boxes.sample_tokens) == {"1"}


@pytest.mark.parametrize(
    ("missing_side", "expected_message"),
    [
        ("gt", "Ground truth JSONL file not found"),
        ("pred", "Prediction JSONL file not found"),
    ],
)
def test_load_boxes_from_jsonl_missing_file_raises(tmp_path, missing_side, expected_message):
    existing_path = _write_jsonl(
        tmp_path,
        "existing.jsonl",
        [_box_row("2025-01-01T00:00:00.000Z")],
    )
    missing_path = str(tmp_path / "missing.jsonl")

    gt_path = missing_path if missing_side == "gt" else existing_path
    pred_path = missing_path if missing_side == "pred" else existing_path

    with pytest.raises(FileNotFoundError, match=expected_message):
        load_boxes_from_jsonl(gt_path, pred_path, fps=30.0)


# ===========================================================
# Coverage supplement (merged from test_loaders_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.detection.loaders.load_boxes_from_jsonl``
— pins the small branches the existing tests don't reach: naive UTC
timestamp shortcut, single-quote→double-quote JSONL repair, invalid
class warn+skip on both GT and pred sides, and confidence-below-
threshold drop on the prediction side.
"""

import json
import logging

import pytest

from spatialai_data_utils.eval.detection.loaders import (
    _find_base_timestamp,
    _parse_detection_timestamp,
    load_boxes_from_jsonl,
)

# --- additional imports for migrated chunks ---
import os


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _line(*, timestamp, objects, single_quoted=False):
    """Build one NVSchema-style JSONL line; ``single_quoted=True``
    swaps double-quotes for single so the loader's repair branch
    kicks in (single-quote → double-quote replacement)."""
    payload = json.dumps({"timestamp": timestamp, "objects": objects})
    if single_quoted:
        # Replace JSON's double-quotes with single-quotes for the
        # whole line — the loader detects "no double-quote present
        # AND single-quote present" and runs ``.replace("'", '"')``.
        payload = payload.replace('"', "'")
    return payload + "\n"


def _obj(*, type_="person", coords=None, confidence=None):
    if coords is None:
        coords = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    bbox = {"coordinates": coords}
    if confidence is not None:
        bbox["confidence"] = confidence
    return {"type": type_, "bbox3d": bbox}


def _write(path, lines):
    path.write_text("".join(lines))


# ---------------------------------------------------------------------------
# _parse_detection_timestamp / _find_base_timestamp
# ---------------------------------------------------------------------------


def test_parse_detection_timestamp_returns_naive_when_input_is_naive():
    """When the input timestamp lacks tz info, the helper returns the
    naive datetime as-is (no astimezone conversion)."""
    out = _parse_detection_timestamp("2025-01-01T12:00:00.000000")
    assert out.tzinfo is None


def test_find_base_timestamp_raises_when_neither_file_has_data(tmp_path):
    """The empty-files fast path emits a clear ValueError instead of
    a bare ``min([])``."""
    (tmp_path / "gt.json").write_text("")
    (tmp_path / "pred.json").write_text("")
    with pytest.raises(ValueError, match="No parseable timestamps"):
        _find_base_timestamp(str(tmp_path / "gt.json"),
                              str(tmp_path / "pred.json"))


def test_find_base_timestamp_repairs_single_quoted_first_line(tmp_path):
    """A first-line single-quoted record (some legacy emitters do
    this) should still be parseable via the repair branch."""
    p = tmp_path / "gt.json"
    p.write_text(_line(
        timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj()], single_quoted=True,
    ))
    # No raise -> the repair branch fired.
    ts = _find_base_timestamp(str(p), str(p))
    assert ts is not None


# ---------------------------------------------------------------------------
# load_boxes_from_jsonl — extra branches
# ---------------------------------------------------------------------------


class TestLoadBoxesExtraBranches:
    def test_single_quoted_records_repaired_on_both_sides(self, tmp_path):
        """Both GT and pred loops have the ``.replace("'", '"')``
        repair branch — exercise both by writing single-quoted
        records to each file."""
        gt = tmp_path / "gt.json"
        pred = tmp_path / "pred.json"
        _write(gt, [_line(timestamp="2025-01-01T12:00:00.000Z",
                           objects=[_obj()], single_quoted=True)])
        _write(pred, [_line(timestamp="2025-01-01T12:00:00.000Z",
                             objects=[_obj()], single_quoted=True)])
        gt_boxes, pred_boxes = load_boxes_from_jsonl(
            str(gt), str(pred), fps=30,
        )
        # Records survived parsing despite the single-quote shape.
        assert len(pred_boxes.boxes) == 1
        assert len(gt_boxes.boxes) == 1

    def test_invalid_pred_class_logged_and_skipped(self, tmp_path, caplog):
        """A prediction row with an unknown ``type`` (not in
        ``map_sub_class_to_primary_class``) is warned and dropped —
        the rest of the record's objects still load."""
        gt = tmp_path / "gt.json"
        pred = tmp_path / "pred.json"
        _write(gt, [_line(timestamp="2025-01-01T12:00:00.000Z",
                           objects=[_obj()])])
        _write(pred, [_line(timestamp="2025-01-01T12:00:00.000Z",
                             objects=[_obj(type_="bogus_class"),
                                       _obj(confidence=0.9)])])
        with caplog.at_level(logging.WARNING):
            _, pred_boxes = load_boxes_from_jsonl(
                str(gt), str(pred), fps=30,
            )
        # Only the valid pred object survived.
        assert sum(len(v) for v in pred_boxes.boxes.values()) == 1
        assert "Skipped invalid class 'bogus_class'" in caplog.text
        assert "from prediction file" in caplog.text

    def test_invalid_gt_class_logged_and_skipped(self, tmp_path, caplog):
        gt = tmp_path / "gt.json"
        pred = tmp_path / "pred.json"
        _write(gt, [_line(timestamp="2025-01-01T12:00:00.000Z",
                           objects=[_obj(type_="bogus_class"), _obj()])])
        _write(pred, [_line(timestamp="2025-01-01T12:00:00.000Z",
                             objects=[_obj(confidence=0.9)])])
        with caplog.at_level(logging.WARNING):
            gt_boxes, _ = load_boxes_from_jsonl(
                str(gt), str(pred), fps=30,
            )
        assert sum(len(v) for v in gt_boxes.boxes.values()) == 1
        assert "Skipped invalid class 'bogus_class'" in caplog.text
        assert "from ground truth file" in caplog.text

    def test_pred_below_confidence_threshold_is_dropped(self, tmp_path):
        """Predictions whose ``bbox3d.confidence`` is below
        ``confidence_threshold`` are silently dropped (no warning —
        this is the per-frame filter, not a data-quality issue)."""
        gt = tmp_path / "gt.json"
        pred = tmp_path / "pred.json"
        _write(gt, [_line(timestamp="2025-01-01T12:00:00.000Z",
                           objects=[_obj()])])
        _write(pred, [_line(
            timestamp="2025-01-01T12:00:00.000Z",
            objects=[
                _obj(confidence=0.1),  # below 0.5 -> dropped
                _obj(confidence=0.9),  # above -> kept
            ],
        )])
        _, pred_boxes = load_boxes_from_jsonl(
            str(gt), str(pred), fps=30, confidence_threshold=0.5,
        )
        assert sum(len(v) for v in pred_boxes.boxes.values()) == 1


# --- helpers shared across the test classes below ---
def _ec_write_jsonl(tmp_path, name, rows):
    path = os.path.join(str(tmp_path), name)
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    return path

def _ec_make_obj(class_name, conf=0.9):
    return {
        "type": class_name,
        "bbox3d": {
            "coordinates": [0.0] * 9,
            "confidence": conf,
            "embedding": [{}],
        },
        "embedding": {},
    }

class TestLoadBoxesFromJsonl:
    """Tests for ``load_boxes_from_jsonl``."""

    def _make_jsonl(self, tmp_path, name, rows):
        return _ec_write_jsonl(tmp_path, name, rows)

    def test_no_parseable_timestamps_raises(self, tmp_path):
        """Empty GT and prediction inputs should not synthesize a base timestamp."""
        from spatialai_data_utils.eval.detection.loaders import (
            load_boxes_from_jsonl,
        )

        gt_path = self._make_jsonl(tmp_path, "gt.jsonl", [])
        pred_path = self._make_jsonl(tmp_path, "pred.jsonl", [])

        with pytest.raises(ValueError, match="No parseable timestamps"):
            load_boxes_from_jsonl(gt_path, pred_path, fps=30.0)

    def _row(self, ts, conf, frame_obj_class="Person"):
        return {
            "id": 0,
            "sensorId": "Camera_01",
            "timestamp": ts,
            "objects": [{
                "type": frame_obj_class,
                "bbox3d": {
                    "coordinates": [0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0],
                    "confidence": conf,
                },
            }],
        }

    def test_gt_detection_score_is_sentinel(self, tmp_path):
        """GT boxes always use the ``-1.0`` no-confidence sentinel.

        Whatever confidence the GT JSON carries (a numeric value, a
        JSON-string like ``"0.95"``, or no field at all) is ignored —
        ground truth has no meaningful "score" and using the sentinel
        keeps GT boxes from being ranked into AP/PR curves and from
        crashing sort routines on string types.
        """
        from spatialai_data_utils.eval.detection.loaders import (
            load_boxes_from_jsonl,
        )
        ts = "2025-01-01T00:00:00.000Z"
        gt_row_string = self._row(ts, "0.95")
        gt_row_numeric = self._row(ts, 0.77)
        gt_row_missing = {
            "id": 0, "sensorId": "Camera_01", "timestamp": ts,
            "objects": [{
                "type": "Person",
                "bbox3d": {"coordinates": [0.0] * 9},
            }],
        }
        for label, gt_rows in [
            ("string confidence", [gt_row_string]),
            ("numeric confidence", [gt_row_numeric]),
            ("missing confidence", [gt_row_missing]),
        ]:
            gt_path = self._make_jsonl(tmp_path, f"gt_{label}.jsonl", gt_rows)
            pred_path = self._make_jsonl(tmp_path, f"pred_{label}.jsonl",
                                         [self._row(ts, 0.85)])
            gt_boxes, _ = load_boxes_from_jsonl(gt_path, pred_path, fps=30.0)
            gt_box = gt_boxes.boxes[gt_boxes.sample_tokens[0]][0]
            assert gt_box.detection_score == -1.0, (
                f"{label}: expected GT sentinel -1.0, got {gt_box.detection_score!r}"
            )

# ===================================================================
# Missing-file diagnostics for load_boxes_from_jsonl
# ===================================================================
#
# The loader pre-validates both ``gt_path`` and ``pred_path`` so that a
# missing file raises a labelled ``FileNotFoundError`` *before* the
# load-progress ``logging.info`` lines fire (which would otherwise
# misleadingly announce a load that's about to crash inside ``open``).

class TestLoadBoxesFromJsonlOffsetAlignment:
    """``ground_truth_frame_offset_secs`` aligns GT and pred sample_tokens.

    Pre-fix the GT branch read its ``frame_id`` from the *raw* timestamp
    via ``_get_frame_id`` and *then* subtracted ``gt_offset_frames``
    after the conversion, while the pred branch used the raw frame id.
    With any non-zero offset the two ``sample_token`` keys parted ways
    for the same physical instant, and downstream evaluators saw all
    detections as FN/FP.  The fix shifts the GT timestamp by
    ``timedelta(seconds=ground_truth_frame_offset_secs)`` *before* the
    timestamp -> frame_id conversion so both sides land on the same key.
    """

    def _make_row(self, ts, conf=0.9):
        return {
            "id": 0,
            "sensorId": "Camera_01",
            "timestamp": ts,
            "objects": [{
                "type": "Person",
                "bbox3d": {
                    "coordinates": [0.0] * 9,
                    "confidence": conf,
                },
            }],
        }

    def _write_synthetic(self, tmp_path, num_frames, fps):
        """Write GT and pred JSONLs with identical timestamps (the realistic
        case after the upstream splitter)."""
        from datetime import datetime, timedelta
        base = datetime(2025, 1, 1, 0, 0, 0)
        timestamps = [
            (base + timedelta(seconds=i / fps)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            for i in range(num_frames)
        ]
        gt_path = _ec_write_jsonl(tmp_path, "gt.jsonl",
                               [self._make_row(t) for t in timestamps])
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl",
                                 [self._make_row(t) for t in timestamps])
        return gt_path, pred_path

    def test_zero_offset_yields_identical_sample_tokens(self, tmp_path):
        """Offset=0 is the regression baseline: both sides match exactly."""
        from spatialai_data_utils.eval.detection.loaders import (
            load_boxes_from_jsonl,
        )
        gt_path, pred_path = self._write_synthetic(tmp_path, num_frames=5, fps=30.0)
        gt, pred = load_boxes_from_jsonl(
            gt_path, pred_path, fps=30.0,
            ground_truth_frame_offset_secs=0.0,
        )
        assert set(gt.sample_tokens) == set(pred.sample_tokens), (
            "GT and pred sample_tokens must be identical when no offset is "
            "applied."
        )

    def test_nonzero_offset_keeps_overlap_aligned(self, tmp_path):
        """Pre-fix this assertion failed because GT was offset post-conversion."""
        from spatialai_data_utils.eval.detection.loaders import (
            load_boxes_from_jsonl,
        )
        gt_path, pred_path = self._write_synthetic(tmp_path, num_frames=5, fps=30.0)
        gt, pred = load_boxes_from_jsonl(
            gt_path, pred_path, fps=30.0,
            ground_truth_frame_offset_secs=2 / 30.0,  # 2 frames at 30 fps
        )
        # Every surviving GT sample_token must appear in pred (the
        # filter is now ``frame_id in prediction_frame_ids``).  Pred may
        # still contain extra warmup tokens that fell outside GT's
        # adjusted window — that's expected; it's the *symmetric*
        # mismatch (no overlap at all) that was the bug.
        pred_set = set(pred.sample_tokens)
        gt_set = set(gt.sample_tokens)
        assert gt_set <= pred_set, (
            f"GT sample_tokens ({sorted(gt_set, key=int)}) should be a "
            f"subset of pred sample_tokens ({sorted(pred_set, key=int)}) "
            f"after the offset is applied to the GT timestamp; pre-fix "
            f"these sets were disjoint for any non-zero offset."
        )
        # And the overlap must be non-empty whenever the offset is
        # smaller than the synthetic sequence length.
        assert gt_set, "Adjusted GT lost every frame — offset misapplied?"

class TestLoadBoxesFromJsonlMissingFiles:
    """Missing GT or prediction file paths raise a clear ``FileNotFoundError``."""

    def test_missing_pred_path_raises_labelled_error(self, tmp_path):
        from spatialai_data_utils.eval.detection.loaders import (
            load_boxes_from_jsonl,
        )
        gt_path = _ec_write_jsonl(tmp_path, "gt.jsonl", [
            {"id": 0, "sensorId": "Camera_01",
             "timestamp": "2025-01-01T00:00:00.000Z",
             "objects": [_ec_make_obj("Person")]},
        ])
        missing = os.path.join(str(tmp_path), "does_not_exist.jsonl")
        with pytest.raises(FileNotFoundError, match=r"Prediction.*does_not_exist"):
            load_boxes_from_jsonl(gt_path, missing, fps=30.0)

    def test_missing_gt_path_raises_labelled_error(self, tmp_path):
        from spatialai_data_utils.eval.detection.loaders import (
            load_boxes_from_jsonl,
        )
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl", [
            {"id": 0, "sensorId": "Camera_01",
             "timestamp": "2025-01-01T00:00:00.000Z",
             "objects": [_ec_make_obj("Person")]},
        ])
        missing = os.path.join(str(tmp_path), "does_not_exist.jsonl")
        with pytest.raises(FileNotFoundError, match=r"Ground truth.*does_not_exist"):
            load_boxes_from_jsonl(missing, pred_path, fps=30.0)

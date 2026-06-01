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
import logging
import os

import pytest

from spatialai_data_utils.eval.common.preprocessing import (
    _safe_frame_id,
    split_files_by_sensor,
    split_files_per_class,
    split_files_per_sensor_and_class,
)


def test_split_files_by_sensor(tmp_path):
    # Prepare minimal GT and Pred JSONL files
    gt_path = tmp_path / "gt.jsonl"
    pred_path = tmp_path / "pred.jsonl"

    gt_lines = [
        {
            "id": 0,
            "sensorId": "Camera",
            "objects": [
                {"type": "Person", "bbox3d": {"confidence": 0.9}},
            ],
        },
        {
            "id": 1,
            "sensorId": "Camera_01",
            "objects": [
                {"type": "Person", "bbox3d": {"confidence": 0.8}},
            ],
        },
    ]

    pred_lines = [
        {
            "id": 0,
            "sensorId": "bev-sensor-1",
            "objects": [
                {"type": "Person", "bbox3d": {"confidence": 0.4}},
                {"type": "Person", "bbox3d": {"confidence": 0.7}},
            ],
        },
        {
            "id": 1,
            "sensorId": "bev-sensor-2",
            "objects": [
                {"type": "Person", "bbox3d": {"confidence": 0.6}},
            ],
        },
    ]

    import json
    with open(gt_path, "w") as f:
        for line in gt_lines:
            f.write(json.dumps(line) + "\n")

    with open(pred_path, "w") as f:
        for line in pred_lines:
            f.write(json.dumps(line) + "\n")

    map_camera_name_to_bev_name = {
        "Camera": ["bev-sensor-1"],
        "Camera_01": ["bev-sensor-2"],
    }
    output_dir = str(tmp_path / "out")

    split_files_by_sensor(
        gt_path=str(gt_path),
        pred_path=str(pred_path),
        output_base_dir=output_dir,
        map_camera_name_to_bev_name=map_camera_name_to_bev_name,
        confidence_threshold=0.6,
        num_frames_to_eval=2,
    )

    import os

    bev1_dir = os.path.join(output_dir, "bev-sensor-1")
    assert os.path.isdir(bev1_dir)
    bev1_gt_file = os.path.join(bev1_dir, "gt.json")
    assert os.path.isfile(bev1_gt_file)
    with open(bev1_gt_file) as f:
        bev1_gt_lines = [json.loads(line) for line in f if line.strip()]
    assert len(bev1_gt_lines) == 1
    assert {line["id"] for line in bev1_gt_lines} == {0}

    bev1_pred_file = os.path.join(bev1_dir, "pred.json")
    assert os.path.isfile(bev1_pred_file)
    with open(bev1_pred_file) as f:
        bev1_pred_lines = [json.loads(line) for line in f if line.strip()]
    assert len(bev1_pred_lines) == 1
    assert bev1_pred_lines[0]["id"] == 0
    assert [obj["bbox3d"]["confidence"] for obj in bev1_pred_lines[0]["objects"]] == [0.7]

    bev2_dir = os.path.join(output_dir, "bev-sensor-2")
    assert os.path.isdir(bev2_dir)
    bev2_gt_file = os.path.join(bev2_dir, "gt.json")
    assert os.path.isfile(bev2_gt_file)
    with open(bev2_gt_file) as f:
        bev2_gt_lines = [json.loads(line) for line in f if line.strip()]
    assert len(bev2_gt_lines) == 1
    assert {line["id"] for line in bev2_gt_lines} == {1}

    bev2_pred_file = os.path.join(bev2_dir, "pred.json")
    assert os.path.isfile(bev2_pred_file)
    with open(bev2_pred_file) as f:
        bev2_pred_lines = [json.loads(line) for line in f if line.strip()]
    assert len(bev2_pred_lines) == 1
    assert bev2_pred_lines[0]["id"] == 1
    assert [obj["bbox3d"]["confidence"] for obj in bev2_pred_lines[0]["objects"]] == [0.6]


# ===========================================================
# Coverage supplement (merged from test_preprocessing_coverage.py)
# ===========================================================
#
# Pins the defensive warn-and-skip branches in ``_safe_frame_id``
# and the three ``split_files_*`` helpers.


# ---------------------------------------------------------------------------
# _safe_frame_id — missing-id and non-numeric-id branches
# ---------------------------------------------------------------------------


def test_safe_frame_id_missing_id_returns_none(caplog):
    with caplog.at_level(logging.WARNING):
        out = _safe_frame_id({"timestamp": "...", "objects": []}, "ground truth")
    assert out is None
    assert "without 'id' field" in caplog.text


def test_safe_frame_id_non_numeric_id_returns_none(caplog):
    with caplog.at_level(logging.WARNING):
        out = _safe_frame_id({"id": "not-a-number"}, "prediction")
    assert out is None
    assert "non-numeric 'id'" in caplog.text


def test_safe_frame_id_valid_returns_int():
    assert _safe_frame_id({"id": "7"}, "ground truth") == 7


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _record(*, frame_id, timestamp, sensor_id="Camera_01",
             objects, single_quoted=False):
    """One NVSchema record as a JSONL line.

    ``single_quoted=True`` emits the line with single quotes so the
    loader's single→double-quote repair branch fires."""
    payload = json.dumps({
        "id": frame_id, "timestamp": timestamp, "sensorId": sensor_id,
        "objects": objects,
    })
    if single_quoted:
        payload = payload.replace('"', "'")
    return payload + "\n"


def _obj(*, type_="person", coords=None, confidence=0.9):
    if coords is None:
        coords = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    return {
        "type": type_,
        "bbox3d": {"coordinates": coords, "confidence": confidence},
    }


# ---------------------------------------------------------------------------
# split_files_per_sensor_and_class — invalid class warn-and-skip
# ---------------------------------------------------------------------------


def test_split_per_sensor_and_class_warns_on_unknown_class(tmp_path, caplog):
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    gt.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj(type_="bogus_class"), _obj(type_="person")],
    ))
    pred.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj(type_="bogus_class"), _obj(type_="person")],
    ))
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    with caplog.at_level(logging.WARNING):
        split_files_per_sensor_and_class(
            str(gt), str(pred), str(out_dir),
            map_camera_name_to_bev_name={"Camera_01": ["bev-1"]},
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
    # The valid "person" class row was written; "bogus_class" was warned.
    assert (out_dir / "bev-1" / "Person").is_dir()
    assert "Class bogus_class not found" in caplog.text


def test_split_per_sensor_and_class_skips_missing_id_records(tmp_path, caplog):
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    # First record valid; second missing 'id'.
    gt.write_text(
        _record(frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
                 objects=[_obj()])
        + json.dumps({"timestamp": "2025-01-01T12:00:00.033Z",
                       "sensorId": "Camera_01", "objects": [_obj()]}) + "\n",
    )
    pred.write_text(_record(frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
                             objects=[_obj()]))
    with caplog.at_level(logging.WARNING):
        split_files_per_sensor_and_class(
            str(gt), str(pred), str(tmp_path / "out"),
            map_camera_name_to_bev_name={"Camera_01": ["bev-1"]},
            num_frames_to_eval=10,
        )
    assert "without 'id' field" in caplog.text


def test_split_per_sensor_and_class_repairs_single_quoted_records(tmp_path):
    """Both GT and pred file loops have the single→double-quote
    repair branch — exercise it by emitting single-quoted records."""
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    gt.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj()], single_quoted=True,
    ))
    pred.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj()], single_quoted=True,
    ))
    # No raise -> repair branch fired and records parsed.
    split_files_per_sensor_and_class(
        str(gt), str(pred), str(tmp_path / "out"),
        map_camera_name_to_bev_name={"Camera_01": ["bev-1"]},
        num_frames_to_eval=10,
    )


# ---------------------------------------------------------------------------
# split_files_per_class — unknown class + missing id
# ---------------------------------------------------------------------------


def test_split_per_class_warns_on_unknown_class(tmp_path, caplog):
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    gt.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj(type_="bogus_class"), _obj()],
    ))
    pred.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj(type_="bogus_class"), _obj()],
    ))
    with caplog.at_level(logging.WARNING):
        split_files_per_class(
            str(gt), str(pred), str(tmp_path / "out"),
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
    assert "Class bogus_class not found" in caplog.text


def test_split_per_class_skips_pred_below_confidence(tmp_path):
    """Predictions with bbox3d.confidence < threshold are silently
    dropped (no warning — this is the per-frame filter, not a
    data-quality issue)."""
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    gt.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj()],
    ))
    pred.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj(confidence=0.1)],  # below 0.5
    ))
    split_files_per_class(
        str(gt), str(pred), str(tmp_path / "out"),
        confidence_threshold=0.5, num_frames_to_eval=10,
    )
    # No raise — the low-confidence pred was simply dropped.


# ---------------------------------------------------------------------------
# split_files_by_sensor — bridge between detection / mtmc pipelines
# ---------------------------------------------------------------------------


def test_split_files_by_sensor_skips_missing_id_records(tmp_path, caplog):
    gt = tmp_path / "gt.json"
    pred = tmp_path / "pred.json"
    # Pred has a record missing 'id'.
    gt.write_text(_record(
        frame_id=0, timestamp="2025-01-01T12:00:00.000Z",
        objects=[_obj()],
    ))
    pred.write_text(
        json.dumps({"timestamp": "2025-01-01T12:00:00.000Z",
                     "sensorId": "Camera_01", "objects": [_obj()]}) + "\n"
    )
    with caplog.at_level(logging.WARNING):
        split_files_by_sensor(
            str(gt), str(pred), str(tmp_path / "out"),
            map_camera_name_to_bev_name={"Camera_01": ["bev-1"]},
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
    assert "without 'id' field" in caplog.text


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

def _ec_read_jsonl(path):
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out

class TestSplitFilesBySensor:
    """Tests for ``split_files_by_sensor`` end-to-end behaviour and resource cleanup."""

    def _write_sample(self, tmp_path, gt_rows, pred_rows):
        gt_path = _ec_write_jsonl(tmp_path, "gt.jsonl", gt_rows)
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl", pred_rows)
        out_dir = os.path.join(str(tmp_path), "by_sensor")
        return gt_path, pred_path, out_dir

    def test_happy_path_writes_expected_files(self, tmp_path):
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        gt = [
            {"id": 0, "sensorId": "Camera_01", "objects": [_ec_make_obj("Person")]},
            {"id": 1, "sensorId": "Camera_02", "objects": [_ec_make_obj("Person")]},
        ]
        pred = [
            {"id": 0, "sensorId": "bev-sensor-1", "objects": [_ec_make_obj("Person")]},
        ]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        cam_to_bev = {"Camera_01": ["bev-sensor-1"], "Camera_02": ["bev-sensor-1"]}
        split_files_by_sensor(
            gt_path, pred_path, out_dir, cam_to_bev,
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
        assert os.path.isfile(os.path.join(out_dir, "bev-sensor-1", "gt.json"))
        assert os.path.isfile(os.path.join(out_dir, "bev-sensor-1", "pred.json"))
        gt_lines = _ec_read_jsonl(os.path.join(out_dir, "bev-sensor-1", "gt.json"))
        assert len(gt_lines) == 2

    def test_confidence_threshold_filters_predictions(self, tmp_path):
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        gt = [{"id": 0, "sensorId": "Camera_01", "objects": [_ec_make_obj("Person")]}]
        pred = [{"id": 0, "sensorId": "bev-sensor-1", "objects": [
            _ec_make_obj("Person", conf=0.9),
            _ec_make_obj("Person", conf=0.1),
        ]}]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        split_files_by_sensor(
            gt_path, pred_path, out_dir, {"Camera_01": ["bev-sensor-1"]},
            confidence_threshold=0.5, num_frames_to_eval=10,
        )
        pred_lines = _ec_read_jsonl(os.path.join(out_dir, "bev-sensor-1", "pred.json"))
        assert len(pred_lines) == 1
        assert len(pred_lines[0]["objects"]) == 1
        assert pred_lines[0]["objects"][0]["bbox3d"]["confidence"] == 0.9

    def test_num_frames_to_eval_caps_input(self, tmp_path):
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        gt = [
            {"id": i, "sensorId": "Camera_01", "objects": [_ec_make_obj("Person")]}
            for i in range(5)
        ]
        pred = [
            {"id": i, "sensorId": "bev-sensor-1", "objects": [_ec_make_obj("Person")]}
            for i in range(5)
        ]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        split_files_by_sensor(
            gt_path, pred_path, out_dir, {"Camera_01": ["bev-sensor-1"]},
            confidence_threshold=0.0, num_frames_to_eval=2,
        )
        assert len(_ec_read_jsonl(os.path.join(out_dir, "bev-sensor-1", "gt.json"))) == 2
        assert len(_ec_read_jsonl(os.path.join(out_dir, "bev-sensor-1", "pred.json"))) == 2

    def test_camera_in_multiple_groups_fans_out_gt(self, tmp_path):
        """A camera mapped to two BEV groups produces one GT file per group."""
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        gt = [{"id": 0, "sensorId": "Camera_01", "objects": [_ec_make_obj("Person")]}]
        pred = [
            {"id": 0, "sensorId": "bev-sensor-1", "objects": [_ec_make_obj("Person")]},
            {"id": 0, "sensorId": "bev-sensor-2", "objects": [_ec_make_obj("Person")]},
        ]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        split_files_by_sensor(
            gt_path, pred_path, out_dir,
            {"Camera_01": ["bev-sensor-1", "bev-sensor-2"]},
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
        for bev in ("bev-sensor-1", "bev-sensor-2"):
            assert os.path.isfile(os.path.join(out_dir, bev, "gt.json"))

    def test_writers_closed_after_success(self, tmp_path):
        """Output files should be closed (truncatable / re-readable) after the call."""
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        gt = [{"id": 0, "sensorId": "Camera_01", "objects": [_ec_make_obj("Person")]}]
        pred = [{"id": 0, "sensorId": "bev-sensor-1", "objects": [_ec_make_obj("Person")]}]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        split_files_by_sensor(
            gt_path, pred_path, out_dir, {"Camera_01": ["bev-sensor-1"]},
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
        # If a writer is still open we'd get a stale read; truncating the
        # file here is the strongest portable signal that the descriptor
        # was released by the function (try/finally branch ran).
        gt_out = os.path.join(out_dir, "bev-sensor-1", "gt.json")
        with open(gt_out, "w") as f:
            f.truncate(0)
        assert os.path.getsize(gt_out) == 0

    def test_writers_closed_when_input_malformed_mid_stream(self, tmp_path):
        """A bad line raises, but the ``try/finally`` branch must still close writers.

        Pre-fix, the ``open(..., "w")`` calls were leaked when the
        in-loop ``json.loads`` raised — every writer left in
        ``sensor_gt_writers`` stayed open until GC.  After the fix the
        ``finally`` branch flushes and closes them deterministically;
        this is observable as the partial file being readable / re-
        writable while the propagated error is the original
        ``json.JSONDecodeError``.
        """
        from spatialai_data_utils.eval.common.preprocessing import split_files_by_sensor
        gt_path = os.path.join(str(tmp_path), "gt.jsonl")
        with open(gt_path, "w") as f:
            f.write(json.dumps({"id": 0, "sensorId": "Camera_01",
                                "objects": [_ec_make_obj("Person")]}) + "\n")
            f.write("{not valid json\n")  # mid-stream poison
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl", [
            {"id": 0, "sensorId": "bev-sensor-1", "objects": [_ec_make_obj("Person")]},
        ])
        out_dir = os.path.join(str(tmp_path), "by_sensor")
        with pytest.raises(json.JSONDecodeError):
            split_files_by_sensor(
                gt_path, pred_path, out_dir,
                {"Camera_01": ["bev-sensor-1"]},
                confidence_threshold=0.0, num_frames_to_eval=10,
            )
        # The first (good) line should have made it to disk before the
        # poison line aborted the loop, and the file should be closed —
        # we should be able to re-open / overwrite it.
        gt_out = os.path.join(out_dir, "bev-sensor-1", "gt.json")
        assert os.path.isfile(gt_out)
        with open(gt_out, "w") as f:
            f.write("")  # Replaces content; would error if FD still held in some envs.

class TestSplitFilesPerClass:
    """Tests for ``split_files_per_class`` with the synthetic warehouse class set."""

    def _write_sample(self, tmp_path, gt_rows, pred_rows):
        gt_path = _ec_write_jsonl(tmp_path, "gt.jsonl", gt_rows)
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl", pred_rows)
        out_dir = os.path.join(str(tmp_path), "by_class")
        return gt_path, pred_path, out_dir

    def test_happy_path_splits_per_primary_class(self, tmp_path):
        from spatialai_data_utils.eval.common.preprocessing import split_files_per_class
        gt = [
            {"id": 0, "sensorId": "Camera_01", "objects": [
                _ec_make_obj("Person"),
                _ec_make_obj("Forklift"),
            ]},
        ]
        pred = [
            {"id": 0, "sensorId": "Camera_01", "timestamp": "2025-01-01T00:00:00Z",
             "objects": [_ec_make_obj("Person"), _ec_make_obj("Forklift")]},
        ]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        split_files_per_class(
            gt_path, pred_path, out_dir,
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
        for primary in ("Person", "Forklift"):
            assert os.path.isfile(os.path.join(out_dir, primary, "gt.json"))
            assert os.path.isfile(os.path.join(out_dir, primary, "pred.json"))

    def test_subclass_remap_uses_primary_class_dir(self, tmp_path):
        """Sub-class names like ``cardbox`` get remapped to their primary class folder."""
        from spatialai_data_utils.eval.common.preprocessing import split_files_per_class
        gt = [{"id": 0, "sensorId": "Camera_01",
               "objects": [_ec_make_obj("CardBox")]}]
        pred = [{"id": 0, "sensorId": "Camera_01",
                 "objects": [_ec_make_obj("CardBox")]}]
        gt_path, pred_path, out_dir = self._write_sample(tmp_path, gt, pred)
        split_files_per_class(
            gt_path, pred_path, out_dir,
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
        # CardBox should land in the "Box" primary class directory.
        assert os.path.isdir(os.path.join(out_dir, "Box"))
        assert not os.path.isdir(os.path.join(out_dir, "CardBox"))

class TestSplitFilesPerSensorAndClass:
    """Tests for ``split_files_per_sensor_and_class`` (BEV x class fan-out)."""

    def test_happy_path_writes_per_bev_per_class(self, tmp_path):
        from spatialai_data_utils.eval.common.preprocessing import (
            split_files_per_sensor_and_class,
        )
        gt = [
            {"id": 0, "sensorId": "Camera_01",
             "objects": [_ec_make_obj("Person"), _ec_make_obj("Forklift")]},
        ]
        pred = [
            {"id": 0, "sensorId": "bev-sensor-1",
             "objects": [_ec_make_obj("Person"), _ec_make_obj("Forklift")]},
        ]
        gt_path = _ec_write_jsonl(tmp_path, "gt.jsonl", gt)
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl", pred)
        out_dir = os.path.join(str(tmp_path), "by_sensor_class")
        split_files_per_sensor_and_class(
            gt_path, pred_path, out_dir, {"Camera_01": ["bev-sensor-1"]},
            confidence_threshold=0.0, num_frames_to_eval=10,
        )
        for primary in ("Person", "Forklift"):
            sub = os.path.join(out_dir, "bev-sensor-1", primary)
            assert os.path.isfile(os.path.join(sub, "gt.json"))


# ===================================================================
# Detection JSONL loader (eval/detection/loaders.py)
# ===================================================================
#
# GT-side ``detection_score`` is fixed to the codebase-wide ``-1.0``
# sentinel (matching ``DetectionBox``'s default and the GT contract in
# ``eval/common/loaders.py``) — predictions are ranked by score for
# AP/PR, so a real GT score would just pollute the curves and any
# stray string-typed confidence in the JSON would also crash sort.
# Pred-side scores are still preserved verbatim (and coerced to
# ``float`` so a JSON-string confidence like ``"0.95"`` doesn't blow

# ===================================================================
# split_files_per_class confidence-filter ordering
# ===================================================================
#
# Pre-fix the per-class entry was allocated *before* the confidence
# check, so a frame whose only object was filtered out still emitted
# a ``"objects": []`` line in the per-class ``pred.json``.  The order
# now matches ``split_files_per_sensor_and_class.process_objects_pred``.

class TestSplitFilesPerClassConfidenceFilter:
    """Empty class entries no longer leak through ``split_files_per_class``."""

    def test_all_low_confidence_frame_yields_no_pred_line(self, tmp_path):
        from spatialai_data_utils.eval.common.preprocessing import split_files_per_class

        gt_rows = [
            {"id": 0, "sensorId": "Camera_01",
             "timestamp": "2025-01-01T00:00:00.000000Z",
             "objects": [_ec_make_obj("Person")]},
            {"id": 1, "sensorId": "Camera_01",
             "timestamp": "2025-01-01T00:00:00.033333Z",
             "objects": [_ec_make_obj("Person")]},
        ]
        pred_rows = [
            # Frame 0: only object is below threshold → no per-class line.
            {"id": 0, "sensorId": "Camera_01",
             "timestamp": "2025-01-01T00:00:00.000000Z",
             "objects": [_ec_make_obj("Person", conf=0.1)]},
            # Frame 1: object passes threshold → exactly one per-class line.
            {"id": 1, "sensorId": "Camera_01",
             "timestamp": "2025-01-01T00:00:00.033333Z",
             "objects": [_ec_make_obj("Person", conf=0.9)]},
        ]
        gt_path = _ec_write_jsonl(tmp_path, "gt.jsonl", gt_rows)
        pred_path = _ec_write_jsonl(tmp_path, "pred.jsonl", pred_rows)
        out_dir = os.path.join(str(tmp_path), "by_class")
        split_files_per_class(
            gt_path, pred_path, out_dir,
            confidence_threshold=0.5, num_frames_to_eval=10,
        )
        pred_lines = _ec_read_jsonl(os.path.join(out_dir, "Person", "pred.json"))
        # Pre-fix: 2 lines (one with ``"objects": []``).  Post-fix: 1 line.
        assert len(pred_lines) == 1
        assert len(pred_lines[0]["objects"]) == 1
        assert pred_lines[0]["objects"][0]["bbox3d"]["confidence"] == 0.9

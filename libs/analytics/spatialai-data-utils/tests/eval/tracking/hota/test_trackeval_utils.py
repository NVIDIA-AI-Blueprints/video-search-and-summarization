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

"""Tests for ``eval.tracking.hota.trackeval_utils``.

Targets the tractable layer of the TrackEval orchestrator — the
NVSchema-JSONL → MOT-text converters, the per-sequence ``seqinfo.ini``
writer, the seqmaps file writer, and the dataset-config / folder-layout
preparation helpers. The two heavy end-to-end orchestrators
(``evaluate_tracking_per_BEV_sensor`` / ``..._all_BEV_sensors``) call
through to the real TrackEval engine and are covered indirectly by the
``aicity_mtmc_eval`` orchestrator tests; we don't duplicate that work
here.
"""

import configparser
import json
import os

import pytest

from spatialai_data_utils.eval.tracking.hota.trackeval_utils import (
    _setup_tracking_output,
    make_seq_ini_file,
    make_seq_maps_file,
    prepare_evaluation_folder,
    prepare_ground_truth_file,
    prepare_prediction_file,
    setup_evaluation_configs,
)


# ---------------------------------------------------------------------------
# Fixture helpers — NVSchema JSONL rows
# ---------------------------------------------------------------------------


def _nvschema_line(*, timestamp, sensor_id="bev-sensor-1", objects):
    return json.dumps({
        "version": "4.0",
        "id": "0",
        "sensorId": sensor_id,
        "timestamp": timestamp,
        "objects": objects,
    }) + "\n"


def _obj(*, obj_id="1", coords=None):
    if coords is None:
        coords = [1.0, 2.0, 0.5, 0.6, 1.2, 1.8, 0.0, 0.0, 0.0]
    return {
        "id": obj_id,
        "type": "Person",
        "confidence": 0.9,
        "bbox3d": {"coordinates": coords, "confidence": 0.9},
    }


def _read_mot_rows(path):
    with open(path) as f:
        return [line.rstrip("\n").split() for line in f if line.strip()]


# ---------------------------------------------------------------------------
# prepare_ground_truth_file
# ---------------------------------------------------------------------------


class TestPrepareGroundTruthFile:
    def test_converts_jsonl_to_mot_with_one_row_per_object(self, tmp_path):
        gt_in = tmp_path / "gt.jsonl"
        gt_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z",
                            objects=[_obj(obj_id="A"), _obj(obj_id="B")]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.033Z",
                            objects=[_obj(obj_id="A")]),
        ]))
        gt_out = tmp_path / "gt.mot"
        prepare_ground_truth_file(
            str(gt_in), str(gt_out), fps=30, ground_truth_frame_offset_secs=0.0,
        )
        rows = _read_mot_rows(gt_out)
        # 2 frames x 2 objects in frame 1 + 1 object in frame 2 = 3 MOT rows
        assert len(rows) == 3
        # First column is 1-indexed frame_id
        assert {row[0] for row in rows} == {"1", "2"}

    def test_object_ids_are_remapped_to_dense_small_integers(self, tmp_path):
        """Long string IDs (like UUIDs) are mapped to sequential
        small ints in MOT order. The mapping is stable for repeated
        IDs across frames."""
        gt_in = tmp_path / "gt.jsonl"
        gt_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z",
                            objects=[_obj(obj_id="uuid-A"), _obj(obj_id="uuid-B")]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.033Z",
                            objects=[_obj(obj_id="uuid-A")]),
        ]))
        gt_out = tmp_path / "gt.mot"
        prepare_ground_truth_file(
            str(gt_in), str(gt_out), fps=30, ground_truth_frame_offset_secs=0.0,
        )
        rows = _read_mot_rows(gt_out)
        # uuid-A -> 1, uuid-B -> 2 (insertion order). All "uuid-A" rows
        # share the same MOT id.
        a_rows = [r for r in rows if r[1] == "1"]
        assert len(a_rows) == 2

    def test_offset_seconds_drops_early_frames(self, tmp_path):
        """``ground_truth_frame_offset_secs * fps`` frames at the
        start of the sequence are dropped to align with predictions
        that lag behind GT."""
        gt_in = tmp_path / "gt.jsonl"
        gt_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z",
                            objects=[_obj(obj_id="A")]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.500Z",
                            objects=[_obj(obj_id="A")]),
        ]))
        gt_out = tmp_path / "gt.mot"
        prepare_ground_truth_file(
            str(gt_in), str(gt_out), fps=30,
            ground_truth_frame_offset_secs=0.1,  # 3 frames @ 30 fps
        )
        rows = _read_mot_rows(gt_out)
        # The earlier (frame 1) row is dropped; only frame > 3 survives.
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# prepare_prediction_file
# ---------------------------------------------------------------------------


class TestPreparePredictionFile:
    def test_basic_jsonl_to_mot_conversion(self, tmp_path):
        pred_in = tmp_path / "pred.jsonl"
        pred_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z",
                            objects=[_obj(obj_id="A")]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.033Z",
                            objects=[_obj(obj_id="A")]),
        ]))
        pred_out = tmp_path / "pred.mot"
        prepare_prediction_file(
            str(pred_in), str(pred_out), fps=30, rtls_delay_sec=0.0,
        )
        rows = _read_mot_rows(pred_out)
        assert len(rows) == 2

    def test_empty_middle_frame_is_skipped(self, tmp_path):
        """Mid-stream empty-objects frames are skipped silently. (The
        empty-FIRST-frame case has a known bug — see
        :func:`test_empty_first_frame_raises_unbound_local_xfail` below.)"""
        pred_in = tmp_path / "pred.jsonl"
        pred_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z",
                            objects=[_obj(obj_id="A")]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.033Z", objects=[]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.066Z",
                            objects=[_obj(obj_id="A")]),
        ]))
        pred_out = tmp_path / "pred.mot"
        prepare_prediction_file(
            str(pred_in), str(pred_out), fps=30, rtls_delay_sec=0.0,
        )
        # Frames 1 and 3 written, frame 2 (empty) skipped.
        rows = _read_mot_rows(pred_out)
        assert len(rows) == 2

    def test_empty_first_frame_does_not_raise_unbound_local(self, tmp_path):
        """Regression test: previously, a JSONL line 0 with empty
        objects caused ``UnboundLocalError`` on the next non-empty
        line because ``base_timestamp`` was set inside the
        ``if line_number == 0:`` branch — which never ran when the
        loop ``continue``-d on empty objects first. Fixed by moving
        the timestamp-parsing block above the empty-objects skip (so
        ``base_timestamp`` is set on line 0 unconditionally,
        matching :func:`prepare_ground_truth_file`)."""
        pred_in = tmp_path / "pred.jsonl"
        pred_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z", objects=[]),
            _nvschema_line(timestamp="2025-01-01T12:00:00.033Z",
                            objects=[_obj(obj_id="A")]),
        ]))
        pred_out = tmp_path / "pred.mot"
        prepare_prediction_file(
            str(pred_in), str(pred_out), fps=30, rtls_delay_sec=0.0,
        )
        rows = _read_mot_rows(pred_out)
        # Line 0 was empty (no MOT row) but its timestamp was used as
        # the base; line 1 at +33ms with fps=30 → raw_frame_id 2.
        assert len(rows) == 1
        assert rows[0][0] == "2"

    def test_rtls_delay_shifts_frame_back_by_half_window(self, tmp_path):
        """``rtls_delay_sec`` advances raw frame back by ``delay/2 * fps``
        frames to align with the midpoint of the RTLS smoothing window."""
        pred_in = tmp_path / "pred.jsonl"
        pred_in.write_text("".join([
            _nvschema_line(timestamp="2025-01-01T12:00:00.000Z",
                            objects=[_obj(obj_id="A")]),
            _nvschema_line(timestamp="2025-01-01T12:00:01.000Z",
                            objects=[_obj(obj_id="A")]),
        ]))
        pred_out = tmp_path / "pred.mot"
        # 0.2 / 2 * 30 = 3 frames back; first frame (1) -> 1-3 = -2 -> dropped,
        # second frame (31) -> 31-3 = 28 -> kept.
        prepare_prediction_file(
            str(pred_in), str(pred_out), fps=30, rtls_delay_sec=0.2,
        )
        rows = _read_mot_rows(pred_out)
        assert len(rows) == 1
        assert rows[0][0] == "28"

    def test_handles_12_coord_bbox(self, tmp_path):
        """Some predictors emit 12 coords (extras post yaw); the
        loader must accept and truncate to the canonical 9."""
        pred_in = tmp_path / "pred.jsonl"
        coords_12 = [1.0, 2.0, 0.5, 0.6, 1.2, 1.8,
                      0.0, 0.0, 0.0, 99.0, 99.0, 99.0]
        pred_in.write_text(_nvschema_line(
            timestamp="2025-01-01T12:00:00.000Z",
            objects=[_obj(obj_id="A", coords=coords_12)],
        ))
        pred_out = tmp_path / "pred.mot"
        prepare_prediction_file(
            str(pred_in), str(pred_out), fps=30, rtls_delay_sec=0.0,
        )
        rows = _read_mot_rows(pred_out)
        assert len(rows) == 1
        # MOT row has 12 columns: frame, id, 1, x, y, z, w, l, h, pitch, roll, yaw
        assert len(rows[0]) == 12

    def test_invalid_bbox_coord_length_raises(self, tmp_path):
        pred_in = tmp_path / "pred.jsonl"
        # 7 coords (between the valid 9 and 12) -> ValueError
        pred_in.write_text(_nvschema_line(
            timestamp="2025-01-01T12:00:00.000Z",
            objects=[_obj(obj_id="A", coords=[0.0] * 7)],
        ))
        pred_out = tmp_path / "pred.mot"
        with pytest.raises(ValueError, match="Expected 9 or 12 coordinates"):
            prepare_prediction_file(
                str(pred_in), str(pred_out), fps=30, rtls_delay_sec=0.0,
            )


# ---------------------------------------------------------------------------
# make_seq_maps_file
# ---------------------------------------------------------------------------


def test_make_seq_maps_file_writes_header_and_one_row_per_sensor(tmp_path):
    seq_dir = tmp_path / "seqmaps"
    make_seq_maps_file(
        str(seq_dir), ["bev-sensor-1", "bev-sensor-2"],
        benchmark="MTMC", split_to_eval="all",
    )
    out = (seq_dir / "MTMC-all.txt").read_text().splitlines()
    assert out[0] == "name"
    assert out[1:] == ["bev-sensor-1", "bev-sensor-2"]


# ---------------------------------------------------------------------------
# setup_evaluation_configs
# ---------------------------------------------------------------------------


class TestSetupEvaluationConfigs:
    def test_bbox_branch_selects_3d_bbox_dataset(self, tmp_path):
        ds, ev = setup_evaluation_configs(
            str(tmp_path), eval_type="bbox", num_cores=1,
        )
        # Eval config is overridden for our usage
        assert ev["USE_PARALLEL"] is True
        assert ev["NUM_PARALLEL_CORES"] == 1
        # GT / TRACKERS folders are placed under the evaluation subtree
        assert ds["GT_FOLDER"].endswith("evaluation/gt")
        assert ds["TRACKERS_FOLDER"].endswith("evaluation/scores")
        assert ds["DO_PREPROC"] is False
        assert ds["SPLIT_TO_EVAL"] == "all"
        # The evaluation directory was created.
        assert os.path.isdir(os.path.join(str(tmp_path), "evaluation"))

    def test_location_branch_selects_3d_location_dataset(self, tmp_path):
        ds, ev = setup_evaluation_configs(
            str(tmp_path), eval_type="location", num_cores=2,
        )
        # The dataset config dict shape comes from the 3D location adapter;
        # easiest to assert: same eval_config shape, same GT path layout.
        assert ev["NUM_PARALLEL_CORES"] == 2
        assert ds["BENCHMARK"]  # both adapters populate this

    def test_unknown_eval_type_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown eval_type"):
            setup_evaluation_configs(str(tmp_path), eval_type="bogus", num_cores=1)


# ---------------------------------------------------------------------------
# make_seq_ini_file
# ---------------------------------------------------------------------------


def test_make_seq_ini_file_emits_valid_ini_with_seq_length_and_fps(tmp_path):
    make_seq_ini_file(str(tmp_path), camera="bev-sensor-1", seq_length=42, fps=25.0)
    ini_path = tmp_path / "seqinfo.ini"
    assert ini_path.is_file()
    ini = configparser.ConfigParser()
    ini.read(str(ini_path))
    seq = ini["Sequence"]
    assert seq["name"] == "bev-sensor-1"
    assert int(seq["seqLength"]) == 42
    assert float(seq["frameRate"]) == 25.0
    assert seq["imWidth"] == "1920"
    assert seq["imHeight"] == "1080"


def test_make_seq_ini_file_default_fps_is_thirty(tmp_path):
    make_seq_ini_file(str(tmp_path), camera="bev-sensor-1", seq_length=10)
    ini = configparser.ConfigParser()
    ini.read(str(tmp_path / "seqinfo.ini"))
    assert float(ini["Sequence"]["frameRate"]) == 30.0


# ---------------------------------------------------------------------------
# prepare_evaluation_folder
# ---------------------------------------------------------------------------


def test_prepare_evaluation_folder_returns_expected_paths(tmp_path):
    """Drive ``prepare_evaluation_folder`` end-to-end after
    ``setup_evaluation_configs`` and verify the GT + pred sub-tree
    layout TrackEval expects."""
    ds, _ = setup_evaluation_configs(str(tmp_path), eval_type="bbox", num_cores=1)
    pred_path, gt_path = prepare_evaluation_folder(
        ds, input_file_type="bev-sensor-1", fps=25.0, seq_length=200,
    )
    # GT lives under <gt_root>/<benchmark>-<split>/bev-sensor-1/gt/gt.txt
    assert gt_path.endswith("/bev-sensor-1/gt/gt.txt")
    # Pred lives under <trackers>/<benchmark>-<split>/data/data/bev-sensor-1.txt
    assert pred_path.endswith("/data/data/bev-sensor-1.txt")
    # ``seqinfo.ini`` was written next to the GT folder.
    ini_path = os.path.join(os.path.dirname(os.path.dirname(gt_path)), "seqinfo.ini")
    assert os.path.isfile(ini_path)


# ---------------------------------------------------------------------------
# _setup_tracking_output
# ---------------------------------------------------------------------------


def _write_calib_with_fps(path, fps=25.0):
    path.write_text(json.dumps({
        "sensors": [{
            "id": "Camera_01",
            "group": {"name": "bev-sensor-1"},
            "attributes": [{"name": "fps", "value": str(fps)}],
        }],
    }))


def test_setup_tracking_output_creates_subdir_and_returns_fps(tmp_path):
    calib = tmp_path / "calib.json"
    _write_calib_with_fps(calib, fps=15.0)
    out_dir, fps = _setup_tracking_output(
        str(calib), str(tmp_path / "out"), subdir_name="per_sensor",
    )
    assert os.path.isdir(out_dir)
    assert out_dir.endswith("per_sensor")
    assert fps == pytest.approx(15.0)


def test_setup_tracking_output_default_subdir_is_all_sensors(tmp_path):
    calib = tmp_path / "calib.json"
    _write_calib_with_fps(calib, fps=30.0)
    out_dir, fps = _setup_tracking_output(str(calib), str(tmp_path / "out"))
    assert out_dir.endswith("all_sensors")
    assert fps == pytest.approx(30.0)


# ===========================================================
# Coverage supplement (merged from test_trackeval_utils_coverage.py)
# ===========================================================

"""Coverage supplement for ``eval.tracking.hota.trackeval_utils`` —
pins the small bits the existing tests don't reach:

* single-quote repair branches in ``prepare_ground_truth_file`` /
  ``prepare_prediction_file``,
* ``empty bbox3d.coordinates`` skip in ``prepare_prediction_file``,
* ``run_evaluation`` validation raises (unknown ``eval_type``, no
  metric selected),
* per-sensor / all-sensors orchestrators with the heavy
  ``run_evaluation`` call stubbed out.
"""

import json
import logging
import os

import pytest

from spatialai_data_utils.eval.tracking.hota import trackeval_utils
from spatialai_data_utils.eval.tracking.hota.trackeval_utils import (
    evaluate_tracking_all_BEV_sensors,
    evaluate_tracking_per_BEV_sensor,
    prepare_ground_truth_file,
    prepare_prediction_file,
    run_evaluation,
    setup_evaluation_configs,
)


# ---------------------------------------------------------------------------
# Single-quote repair branches in prepare_ground_truth_file
# ---------------------------------------------------------------------------


def _gt_line(*, ts="2025-01-01T12:00:00.000Z", sensor_id="bev-1",
              object_id="A", coords=None, single_quoted=False):
    if coords is None:
        coords = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    payload = json.dumps({
        "timestamp": ts,
        "sensorId": f"some/path/{sensor_id}",
        "objects": [{
            "id": object_id,
            "type": "person",
            "bbox3d": {"coordinates": coords, "confidence": 0.9},
        }],
    })
    if single_quoted:
        payload = payload.replace('"', "'")
    return payload + "\n"


def _pred_line(*, ts="2025-01-01T12:00:00.000Z", object_id="A",
                coords=None, single_quoted=False):
    if coords is None:
        coords = [0.0, 0.0, 0.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0]
    payload = json.dumps({
        "timestamp": ts,
        "objects": [{
            "id": object_id,
            "type": "person",
            "bbox3d": {"coordinates": coords, "confidence": 0.9},
        }],
    })
    if single_quoted:
        payload = payload.replace('"', "'")
    return payload + "\n"


def test_prepare_gt_file_repairs_single_quoted_lines(tmp_path):
    gt = tmp_path / "gt.jsonl"
    gt.write_text(_gt_line(single_quoted=True))
    out = tmp_path / "out.txt"
    prepare_ground_truth_file(str(gt), str(out), fps=30, ground_truth_frame_offset_secs=0)
    # No raise -> repair branch fired and line parsed.
    assert out.is_file()


def test_prepare_pred_file_repairs_single_quoted_lines(tmp_path):
    pred = tmp_path / "pred.jsonl"
    pred.write_text(_pred_line(single_quoted=True))
    out = tmp_path / "out.txt"
    prepare_prediction_file(str(pred), str(out), fps=30, rtls_delay_sec=0)
    assert out.is_file()


def test_prepare_pred_file_skips_objects_with_empty_coordinates(tmp_path):
    """An object with an empty ``bbox3d.coordinates`` list is silently
    dropped (the ``if len(...) == 0: continue`` branch); the rest of
    the line still loads."""
    pred = tmp_path / "pred.jsonl"
    pred.write_text(json.dumps({
        "timestamp": "2025-01-01T12:00:00.000Z",
        "objects": [
            {"id": "X", "type": "person",
              "bbox3d": {"coordinates": [], "confidence": 0.9}},
            {"id": "Y", "type": "person",
              "bbox3d": {"coordinates": [0, 0, 0, 1, 1, 1, 0, 0, 0],
                          "confidence": 0.9}},
        ],
    }) + "\n")
    out = tmp_path / "out.txt"
    prepare_prediction_file(str(pred), str(out), fps=30, rtls_delay_sec=0)
    text = out.read_text()
    # Only the non-empty record made it through.
    assert text.count("\n") == 1


# ---------------------------------------------------------------------------
# run_evaluation — validation raises
# ---------------------------------------------------------------------------


def test_run_evaluation_unknown_eval_type_raises(tmp_path):
    dataset_config, eval_config = setup_evaluation_configs(
        str(tmp_path / "wd"), eval_type="bbox", num_cores=1,
    )
    with pytest.raises(ValueError, match="Unknown eval_type"):
        run_evaluation(
            gt_file="ignored", prediction_file="ignored",
            dataset_config=dataset_config, eval_config=eval_config,
            eval_type="not-a-real-type",
        )


# Note: the ``No metric selected`` branch in ``run_evaluation``
# (lines 376-377) is technically unreachable from a fresh
# ``run_evaluation`` call because the function hard-codes the
# ``METRICS=["HOTA","CLEAR","Identity"]`` list inside its own body
# (line 358) and merges it last, overriding any caller-supplied
# value. We don't attempt to cover those two lines via monkeypatching
# the function's own private metrics_config dict.


# ---------------------------------------------------------------------------
# Per-sensor / all-sensors orchestrators with run_evaluation stubbed
# ---------------------------------------------------------------------------


def _calibration_with_camera(tmp_path):
    """Minimal calibration JSON declaring one camera 'Camera_01' in
    BEV group 'bev-1' at 10 fps."""
    calib = tmp_path / "calib.json"
    calib.write_text(json.dumps({
        "sensors": [{
            "id": "Camera_01",
            "group": {"name": "bev-1"},
            "attributes": [{"name": "fps", "value": "10"}],
        }],
    }))
    return calib


def test_evaluate_tracking_per_bev_sensor_calls_orchestrator(tmp_path, monkeypatch):
    """End-to-end driver with ``run_evaluation`` stubbed to a no-op —
    proves the orchestrator can wire up the per-sensor split and
    invoke the runner."""
    calib = _calibration_with_camera(tmp_path)
    gt = tmp_path / "gt.jsonl"
    pred = tmp_path / "pred.jsonl"
    gt.write_text(
        json.dumps({
            "id": 0, "timestamp": "2025-01-01T12:00:00.000Z",
            "sensorId": "Camera_01", "objects": [{
                "id": "A", "type": "person",
                "bbox3d": {"coordinates": [0, 0, 0, 1, 1, 1, 0, 0, 0],
                            "confidence": 0.9},
            }],
        }) + "\n"
    )
    pred.write_text(
        json.dumps({
            "timestamp": "2025-01-01T12:00:00.000Z",
            "objects": [{
                "id": "A", "type": "person",
                "bbox3d": {"coordinates": [0, 0, 0, 1, 1, 1, 0, 0, 0],
                            "confidence": 0.9},
            }],
        }) + "\n"
    )

    # Stub the heavy TrackEval evaluator call to a no-op.
    monkeypatch.setattr(trackeval_utils, "run_evaluation",
                         lambda *args, **kwargs: ({}, {}))
    evaluate_tracking_per_BEV_sensor(
        ground_truth_file=str(gt), prediction_file=str(pred),
        calibration_file=str(calib),
        eval_options="bbox",
        output_root_dir=str(tmp_path / "out"),
        confidence_threshold=0.0, num_cores=1,
        input_file_type="bbox", num_frames_to_eval=10,
        ground_truth_frame_offset_secs=0.0,
        map_camera_name_to_bev_name={"Camera_01": ["bev-1"]},
    )
    # The orchestrator created the per_sensor split dir.
    assert (tmp_path / "out" / "per_sensor").is_dir()


def test_evaluate_tracking_all_bev_sensors_calls_orchestrator(tmp_path, monkeypatch):
    calib = _calibration_with_camera(tmp_path)
    gt = tmp_path / "gt.jsonl"
    pred = tmp_path / "pred.jsonl"
    gt.write_text(
        json.dumps({
            "id": 0, "timestamp": "2025-01-01T12:00:00.000Z",
            "sensorId": "Camera_01", "objects": [{
                "id": "A", "type": "person",
                "bbox3d": {"coordinates": [0, 0, 0, 1, 1, 1, 0, 0, 0],
                            "confidence": 0.9},
            }],
        }) + "\n"
    )
    pred.write_text(
        json.dumps({
            "id": 0, "timestamp": "2025-01-01T12:00:00.000Z",
            "objects": [{
                "id": "A", "type": "person",
                "bbox3d": {"coordinates": [0, 0, 0, 1, 1, 1, 0, 0, 0],
                            "confidence": 0.9},
            }],
        }) + "\n"
    )

    monkeypatch.setattr(trackeval_utils, "run_evaluation",
                         lambda *args, **kwargs: ({}, {}))
    evaluate_tracking_all_BEV_sensors(
        ground_truth_file=str(gt), prediction_file=str(pred),
        calibration_file=str(calib),
        eval_options="bbox",
        output_root_dir=str(tmp_path / "out"),
        confidence_threshold=0.0, num_cores=1,
        input_file_type="bbox", num_frames_to_eval=10,
        ground_truth_frame_offset_secs=0.0,
    )
    assert (tmp_path / "out" / "all_sensors").is_dir()


def test_run_tracking_per_sensor_warns_missing_class_folder(tmp_path, caplog):
    """``_run_tracking_per_sensor`` warns and skips sensor sub-dirs
    that lack a class sub-folder."""
    base_dir = tmp_path / "per_sensor"
    sensor_dir = base_dir / "bev-1"
    sensor_dir.mkdir(parents=True)
    # Add a stray file alongside the sensor dir to trigger the non-dir skip.
    (base_dir / "stray.txt").write_text("hello")
    # No class sub-folder under bev-1 -> warn for every CLASS_LIST entry
    with caplog.at_level(logging.WARNING):
        trackeval_utils._run_tracking_per_sensor(
            base_dir=str(base_dir), eval_options="bbox", num_cores=1,
            input_file_type="bbox",
            ground_truth_frame_offset_secs=0.0, fps=10.0,
            num_frames_to_eval=10,
        )
    assert "Class folder" in caplog.text


def test_run_tracking_all_sensors_warns_missing_class_folder(tmp_path, caplog):
    """Counterpart for the all-sensors orchestrator."""
    out_dir = tmp_path / "all_sensors"
    out_dir.mkdir()
    with caplog.at_level(logging.WARNING):
        trackeval_utils._run_tracking_all_sensors(
            output_directory=str(out_dir),
            eval_options="bbox", num_cores=1, input_file_type="bbox",
            ground_truth_frame_offset_secs=0.0, fps=10.0,
            num_frames_to_eval=10,
        )
    assert "Skipping class folder" in caplog.text


# ===================================================================
# HOTA tracking helper signatures (eval/tracking/hota/trackeval_utils.py)
# ===================================================================
#
# Commit f912fa2 simplified ``run_evaluation`` and ``_run_tracking_all_sensors``
# by removing the unused ``fps``, ``ground_truth_file`` and ``prediction_file``
# parameters.  These tests pin the new, narrower public surface.

class TestRunEvaluationSignature:
    """``run_evaluation`` should no longer take an ``fps`` parameter.

    The function only reads ``dataset_config`` / ``eval_config`` /
    ``eval_type`` after the simplification, so callers shouldn't be
    forced to plumb FPS through any more (it was passed but unused
    inside the body).  Pinning the signature here protects against an
    accidental re-introduction.
    """

    def test_run_evaluation_does_not_accept_fps_kw(self):
        import inspect
        from spatialai_data_utils.eval.tracking.hota import trackeval_utils
        sig = inspect.signature(trackeval_utils.run_evaluation)
        assert "fps" not in sig.parameters
        assert set(sig.parameters) == {
            "gt_file", "prediction_file", "dataset_config",
            "eval_config", "eval_type",
        }

    def test_run_tracking_all_sensors_does_not_accept_file_paths(self):
        """The all-sensors helper now reads files from the prepared output dir."""
        import inspect
        from spatialai_data_utils.eval.tracking.hota import trackeval_utils
        sig = inspect.signature(trackeval_utils._run_tracking_all_sensors)
        assert "ground_truth_file" not in sig.parameters
        assert "prediction_file" not in sig.parameters
        # The parameters we DO keep — the test pins the call surface so an
        # accidental re-add of the removed kwargs is caught at import time.
        assert "output_directory" in sig.parameters
        assert "fps" in sig.parameters

    def test_evaluate_tracking_all_bev_sensors_public_surface_unchanged(self):
        """The public entry point still takes the original 10-arg surface."""
        import inspect
        from spatialai_data_utils.eval.tracking.hota import trackeval_utils
        sig = inspect.signature(trackeval_utils.evaluate_tracking_all_BEV_sensors)
        assert {
            "ground_truth_file", "prediction_file", "calibration_file",
            "eval_options", "output_root_dir", "confidence_threshold",
            "num_cores", "input_file_type", "num_frames_to_eval",
            "ground_truth_frame_offset_secs",
        } == set(sig.parameters)

# ===================================================================
# prepare_evaluation_folder seq_length propagation
# ===================================================================
#
# ``seqinfo.ini`` previously hard-coded ``seqLength=20000`` regardless
# of the actual frame count.  TrackEval iterates ``range(seq_length)``
# in ``_load_raw_file``, so a too-small value silently truncates and a
# too-large value wastes per-timestep work.  These tests pin that the
# new ``seq_length`` parameter is honoured and that the legacy 20000
# default is preserved for callers that don't pass it.

class TestPrepareEvaluationFolder:
    """``seq_length`` flows from caller into the generated ``seqinfo.ini``."""

    def _cfg(self, tmp_path):
        return {
            "GT_FOLDER": os.path.join(str(tmp_path), "gt"),
            "TRACKERS_FOLDER": os.path.join(str(tmp_path), "trackers"),
            "BENCHMARK": "MOT17",
            "SPLIT_TO_EVAL": "all",
        }

    def _seqinfo_path(self, cfg, input_file_type):
        return os.path.join(
            cfg["GT_FOLDER"], "MOT17-all", input_file_type, "seqinfo.ini",
        )

    def test_seq_length_propagates_to_seqinfo_ini(self, tmp_path):
        from spatialai_data_utils.eval.tracking.hota.trackeval_utils import (
            prepare_evaluation_folder,
        )
        cfg = self._cfg(tmp_path)
        prepare_evaluation_folder(cfg, "RTLS", fps=20.0, seq_length=137)
        with open(self._seqinfo_path(cfg, "RTLS")) as f:
            text = f.read()
        assert "seqLength=137" in text
        assert "frameRate=20.0" in text

    def test_default_seq_length_is_20000(self, tmp_path):
        """Backwards-compat: callers that don't set ``seq_length`` keep 20000."""
        from spatialai_data_utils.eval.tracking.hota.trackeval_utils import (
            prepare_evaluation_folder,
        )
        cfg = self._cfg(tmp_path)
        prepare_evaluation_folder(cfg, "RTLS")
        with open(self._seqinfo_path(cfg, "RTLS")) as f:
            text = f.read()
        assert "seqLength=20000" in text

    def test_run_tracking_helpers_accept_num_frames_to_eval(self):
        """The internal helpers expose the parameter that feeds ``seq_length``."""
        import inspect
        from spatialai_data_utils.eval.tracking.hota import trackeval_utils
        for fn in (
            trackeval_utils._run_tracking_per_sensor,
            trackeval_utils._run_tracking_all_sensors,
        ):
            assert "num_frames_to_eval" in inspect.signature(fn).parameters, (
                f"{fn.__name__} should expose num_frames_to_eval to drive "
                f"prepare_evaluation_folder(seq_length=...)."
            )

# ===================================================================
# _setup_tracking_output subdir naming
# ===================================================================
#
# Pre-fix the helper hard-coded ``output_root_dir/all_sensors/`` for both
# the per-sensor and all-sensors flows, which made debugging confusing
# (per-sensor scaffolding ended up under a directory called
# "all_sensors").  The helper now takes a ``subdir_name`` parameter and
# the two entry points pass distinct values.

class TestSetupTrackingOutputSubdir:
    """``_setup_tracking_output`` exposes ``subdir_name`` and callers use it."""

    def test_helper_accepts_subdir_name_param(self):
        import inspect
        from spatialai_data_utils.eval.tracking.hota import trackeval_utils
        sig = inspect.signature(trackeval_utils._setup_tracking_output)
        assert "subdir_name" in sig.parameters
        assert sig.parameters["subdir_name"].default == "all_sensors"

    def test_per_sensor_entry_point_uses_per_sensor_subdir(self):
        """``evaluate_tracking_per_BEV_sensor`` should pass a non-default name.

        We don't run the full evaluation; just inspect the source to
        confirm the call site no longer falls back to the legacy
        ``"all_sensors"`` literal that's confusing for the per-sensor
        flow.
        """
        import inspect
        from spatialai_data_utils.eval.tracking.hota import trackeval_utils
        src = inspect.getsource(trackeval_utils.evaluate_tracking_per_BEV_sensor)
        assert 'subdir_name="per_sensor"' in src, (
            "evaluate_tracking_per_BEV_sensor should call "
            "_setup_tracking_output with subdir_name='per_sensor'."
        )

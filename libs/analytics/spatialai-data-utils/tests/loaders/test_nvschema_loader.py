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

"""Tests for ``spatialai_data_utils.loaders.nvschema``.

Covers:
    * :func:`nvschema_obj_to_gt_json_aicity` — per-object NVSchema->gt_json_aicity conversion
    * :func:`load_nvschema` — JSON-lines parsing, frame/sensor grouping,
      malformed-line fix-up, and the ``output_format`` switch
    * :func:`load_nvschemas` — directory-level multi-scene loading
    * :func:`iter_frame_rows` — per-row streaming variant of
      :func:`load_nvschema` (preserves rows + ``timestamp`` / ``info``)
"""

import json

import pytest

from spatialai_data_utils.loaders.nvschema import (
    GT_JSON_FORMAT,
    NVSCHEMA_FORMAT,
    iter_frame_rows,
    load_nvschema,
    load_nvschemas,
    nvschema_obj_to_gt_json_aicity,
)


def _make_nvschema_obj(obj_id="42", typ="Person", conf=0.9):
    """Build a minimal raw NVSchema object dict for tests."""
    return {
        "id": obj_id,
        "type": typ,
        "confidence": conf,
        "coordinate": {"x": 1.0, "y": 2.0, "z": 3.0},
        "bbox3d": {
            "coordinates": [1.0, 2.0, 3.0, 1.5, 3.0, 1.8, 0.1, 0.2, 0.3],
            "embedding": [{}],
            "confidence": conf,
        },
    }


def _write_jsonl(path, frames, use_single_quotes=False):
    """Write a list of frame dicts as NVSchema JSON-lines.

    When *use_single_quotes* is True, emit single-quoted Python repr to
    exercise the loader's malformed-JSON fix-up path.
    """
    with open(path, "w") as f:
        for frame in frames:
            line = json.dumps(frame)
            if use_single_quotes:
                line = line.replace('"', "'")
            f.write(line + "\n")


@pytest.fixture()
def sample_file(tmp_path):
    """Create a 2-frame, 2-sensor NVSchema JSON-lines file on disk."""
    path = tmp_path / "scene.json"
    frames = [
        {"id": 0, "sensorId": "cam0",
         "objects": [_make_nvschema_obj("1"), _make_nvschema_obj("2")]},
        {"id": 1, "sensorId": "cam1",
         "objects": [_make_nvschema_obj("3")]},
    ]
    _write_jsonl(path, frames)
    return str(path)


class TestNvschemaObjToGtJson:
    """Unit tests for the single-object converter helper."""

    def test_basic_conversion(self):
        """All required gt_json_aicity keys are populated from bbox3d.coordinates."""
        obj = _make_nvschema_obj("7", "Person", 0.8)
        gt = nvschema_obj_to_gt_json_aicity(obj)
        assert gt["object id"] == 7
        assert gt["3d location"] == [1.0, 2.0, 3.0]
        assert gt["3d bounding box scale"] == [1.5, 3.0, 1.8]
        assert gt["3d bounding box rotation"] == [0.1, 0.2, 0.3]
        assert gt["confidence"] == 0.8
        assert gt["type"] == "Person"

    def test_non_numeric_id_preserved(self):
        """Non-integer ids stay as-is (not silently coerced)."""
        obj = _make_nvschema_obj("track-abc")
        gt = nvschema_obj_to_gt_json_aicity(obj)
        assert gt["object id"] == "track-abc"

    def test_missing_id_becomes_sentinel(self):
        """Missing ``id`` field falls back to -1 sentinel."""
        obj = _make_nvschema_obj()
        del obj["id"]
        gt = nvschema_obj_to_gt_json_aicity(obj)
        assert gt["object id"] == -1

    def test_missing_confidence_defaults_to_one(self):
        """Missing top-level confidence defaults to 1.0."""
        obj = _make_nvschema_obj()
        del obj["confidence"]
        gt = nvschema_obj_to_gt_json_aicity(obj)
        assert gt["confidence"] == 1.0

    def test_missing_type_defaults_to_unknown(self):
        """Missing top-level type defaults to 'unknown'."""
        obj = _make_nvschema_obj()
        del obj["type"]
        gt = nvschema_obj_to_gt_json_aicity(obj)
        assert gt["type"] == "unknown"

    def test_missing_bbox3d_raises(self):
        """Converter errors clearly when the 3D block is missing."""
        with pytest.raises(KeyError, match="bbox3d"):
            nvschema_obj_to_gt_json_aicity({"id": "1", "type": "x"})

    def test_bbox3d_not_dict_raises(self):
        """Non-dict bbox3d value is caught and reported as KeyError."""
        obj = _make_nvschema_obj()
        obj["bbox3d"] = None
        with pytest.raises(KeyError, match="bbox3d"):
            nvschema_obj_to_gt_json_aicity(obj)

    def test_7dof_coordinates_rejected(self):
        """Legacy 7-value bbox3d.coordinates is rejected (NVSchema >= 9)."""
        obj = _make_nvschema_obj()
        # [x, y, z, w, l, h, yaw] — no roll/pitch
        obj["bbox3d"]["coordinates"] = [4.0, 5.0, 6.0, 0.5, 1.0, 1.8, 0.7]
        with pytest.raises(ValueError, match="at least 9 values"):
            nvschema_obj_to_gt_json_aicity(obj)

    def test_short_coordinate_length_raises(self):
        """bbox3d.coordinates shorter than 9 is rejected."""
        bad = _make_nvschema_obj()
        bad["bbox3d"]["coordinates"] = [1, 2, 3]
        with pytest.raises(ValueError, match="at least 9 values"):
            nvschema_obj_to_gt_json_aicity(bad)

    def test_extra_trailing_values_accepted(self):
        """bbox3d.coordinates with >=9 values (extras) is accepted."""
        obj = _make_nvschema_obj()
        # 9 canonical values + trailing velocity (vx, vy, vz) — common case.
        obj["bbox3d"]["coordinates"] = [
            4.0, 5.0, 6.0, 0.5, 1.0, 1.8, 0.0, 0.0, 0.7, 1.0, 0.0, 0.0,
        ]
        gt = nvschema_obj_to_gt_json_aicity(obj)
        # Trailing extras do not corrupt the extracted 9-DoF fields.
        assert gt["3d location"] == [4.0, 5.0, 6.0]
        assert gt["3d bounding box scale"] == [0.5, 1.0, 1.8]
        assert gt["3d bounding box rotation"] == [0.0, 0.0, 0.7]

    def test_extra_fields_preserved(self):
        """Extra top-level fields survive the conversion."""
        obj = _make_nvschema_obj()
        obj["visibility"] = 0.7
        obj["sensor_coverage"] = ["cam0", "cam1"]
        gt = nvschema_obj_to_gt_json_aicity(obj)
        assert gt["visibility"] == 0.7
        assert gt["sensor_coverage"] == ["cam0", "cam1"]
        # bbox3d is not carried forward - it has been flattened.
        assert "bbox3d" not in gt

    def test_does_not_mutate_input(self):
        """Conversion does not mutate the original NVSchema dict in place."""
        obj = _make_nvschema_obj()
        snapshot = json.dumps(obj, sort_keys=True)
        nvschema_obj_to_gt_json_aicity(obj)
        assert json.dumps(obj, sort_keys=True) == snapshot


class TestLoadNvschemaBasic:
    """Basic parsing and grouping behaviour of load_nvschema."""

    def test_frame_and_sensor_grouping(self, sample_file):
        """Top-level dict is {frame_id: {sensor_id: [obj, ...]}}."""
        result = load_nvschema(sample_file)
        assert set(result.keys()) == {0, 1}
        assert list(result[0].keys()) == ["cam0"]
        assert list(result[1].keys()) == ["cam1"]
        assert len(result[0]["cam0"]) == 2
        assert len(result[1]["cam1"]) == 1

    def test_frame_id_is_int(self, sample_file):
        """Frame ids are coerced from string/number to ``int`` keys."""
        result = load_nvschema(sample_file)
        assert all(isinstance(k, int) for k in result.keys())

    def test_sensor_id_is_string(self, sample_file):
        """Sensor ids remain as the raw string from NVSchema."""
        result = load_nvschema(sample_file)
        for frame in result.values():
            for sid in frame.keys():
                assert isinstance(sid, str)

    def test_empty_file(self, tmp_path):
        """An empty JSONL file yields an empty dict without error."""
        path = tmp_path / "empty.json"
        path.write_text("")
        result = load_nvschema(str(path))
        assert result == {}

    def test_blank_lines_skipped(self, tmp_path):
        """Blank lines between frames are silently skipped."""
        path = tmp_path / "blanks.json"
        frame = {"id": 0, "sensorId": "cam0", "objects": [_make_nvschema_obj()]}
        path.write_text("\n" + json.dumps(frame) + "\n\n\n")
        result = load_nvschema(str(path))
        assert list(result.keys()) == [0]

    def test_same_sensor_multiple_lines_accumulate(self, tmp_path):
        """Multiple lines sharing (frame_id, sensor_id) append their objects."""
        path = tmp_path / "dup.json"
        frames = [
            {"id": 0, "sensorId": "cam0", "objects": [_make_nvschema_obj("1")]},
            {"id": 0, "sensorId": "cam0", "objects": [_make_nvschema_obj("2")]},
        ]
        _write_jsonl(path, frames)
        result = load_nvschema(str(path))
        ids = [o["id"] for o in result[0]["cam0"]]
        assert ids == ["1", "2"]

    def test_multiple_sensors_same_frame(self, tmp_path):
        """Multiple sensors within one frame are recorded separately."""
        path = tmp_path / "multi_sensor.json"
        frames = [
            {"id": 0, "sensorId": "cam0", "objects": [_make_nvschema_obj("1")]},
            {"id": 0, "sensorId": "cam1", "objects": [_make_nvschema_obj("2")]},
        ]
        _write_jsonl(path, frames)
        result = load_nvschema(str(path))
        assert set(result[0].keys()) == {"cam0", "cam1"}

    def test_nonexistent_file_raises(self, tmp_path):
        """Missing file propagates FileNotFoundError to the caller."""
        with pytest.raises(FileNotFoundError):
            load_nvschema(str(tmp_path / "does_not_exist.json"))


class TestLoadNvschemaJsonRobustness:
    """Edge cases around the JSON-decoding fix-up."""

    def test_single_quote_fixup(self, tmp_path):
        """Single-quoted lines are repaired and parsed successfully."""
        path = tmp_path / "single_quoted.json"
        frames = [
            {"id": 0, "sensorId": "cam0", "objects": [_make_nvschema_obj("1")]},
        ]
        _write_jsonl(path, frames, use_single_quotes=True)
        result = load_nvschema(str(path))
        assert result[0]["cam0"][0]["id"] == "1"

    def test_truly_malformed_line_raises(self, tmp_path):
        """Lines that neither parse nor survive the quote fix-up raise."""
        path = tmp_path / "garbage.json"
        path.write_text("this is not json at all\n")
        with pytest.raises(json.JSONDecodeError):
            load_nvschema(str(path))


class TestLoadNvschemaOutputFormat:
    """Integration tests for the ``output_format`` kwarg on load_nvschema."""

    def test_default_returns_raw_nvschema(self, sample_file):
        """Default output preserves raw NVSchema nested shape."""
        result = load_nvschema(sample_file)
        obj = result[0]["cam0"][0]
        assert "bbox3d" in obj and "coordinates" in obj["bbox3d"]
        assert obj["id"] == "1"  # preserved as string
        assert "3d location" not in obj  # not flattened

    def test_explicit_nvschema_format(self, sample_file):
        """Explicit NVSCHEMA_FORMAT matches default behaviour."""
        default = load_nvschema(sample_file)
        explicit = load_nvschema(sample_file, output_format=NVSCHEMA_FORMAT)
        assert default == explicit

    def test_gt_json_aicity_format(self, sample_file):
        """gt_json_aicity format flattens every object in every frame/sensor."""
        result = load_nvschema(sample_file, output_format=GT_JSON_FORMAT)
        obj = result[0]["cam0"][0]
        assert obj["object id"] == 1
        assert obj["3d location"] == [1.0, 2.0, 3.0]
        assert obj["3d bounding box scale"] == [1.5, 3.0, 1.8]
        assert "bbox3d" not in obj

        # Second sensor / frame also flattened.
        assert result[1]["cam1"][0]["object id"] == 3

    def test_gt_json_aicity_frame_count_matches_nvschema(self, sample_file):
        """gt_json_aicity and nvschema formats return the same dict keys / counts."""
        raw = load_nvschema(sample_file, output_format=NVSCHEMA_FORMAT)
        flat = load_nvschema(sample_file, output_format=GT_JSON_FORMAT)
        assert set(raw.keys()) == set(flat.keys())
        for fid in raw.keys():
            assert set(raw[fid].keys()) == set(flat[fid].keys())
            for sid in raw[fid].keys():
                assert len(raw[fid][sid]) == len(flat[fid][sid])

    def test_invalid_format_raises(self, sample_file):
        """Unknown output_format values raise ValueError."""
        with pytest.raises(ValueError, match="output_format"):
            load_nvschema(sample_file, output_format="bogus")


class TestLoadNvschemas:
    """Tests for the multi-scene directory loader."""

    @pytest.fixture()
    def scenes_dir(self, tmp_path):
        """Create a directory with two well-formed scene JSONL files."""
        _write_jsonl(
            tmp_path / "scene_a",
            [{"id": 0, "sensorId": "cam0",
              "objects": [_make_nvschema_obj("1")]}],
        )
        _write_jsonl(
            tmp_path / "scene_b",
            [{"id": 0, "sensorId": "cam0",
              "objects": [_make_nvschema_obj("2")]}],
        )
        return str(tmp_path)

    def test_loads_requested_scenes(self, scenes_dir):
        """Every existing scene name in the request appears in the output."""
        result = load_nvschemas(scenes_dir, ["scene_a", "scene_b"])
        assert set(result.keys()) == {"scene_a", "scene_b"}
        assert result["scene_a"][0]["cam0"][0]["id"] == "1"
        assert result["scene_b"][0]["cam0"][0]["id"] == "2"

    def test_missing_scene_is_skipped(self, scenes_dir, caplog):
        """Missing files are logged (warning) but don't abort the remaining loads."""
        import logging

        caplog.set_level(logging.WARNING, logger="spatialai_data_utils.loaders.nvschema")
        result = load_nvschemas(scenes_dir, ["scene_a", "missing", "scene_b"])
        assert set(result.keys()) == {"scene_a", "scene_b"}

        # The missing-file path goes through a WARNING (no stacktrace needed).
        warn_records = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "missing" in r.getMessage()
        ]
        assert warn_records, (
            f"expected a WARNING log mentioning 'missing'; got {caplog.records}"
        )

    def test_malformed_json_is_logged_with_traceback(self, tmp_path, caplog):
        """A JSON-parse failure is skipped and logged with a stack trace."""
        import logging

        # Create one good scene and one scene whose bytes don't parse as JSON
        # (even after the single-quote fixup fallback).
        _write_jsonl(
            tmp_path / "good",
            [{"id": 0, "sensorId": "cam0", "objects": [_make_nvschema_obj("1")]}],
        )
        (tmp_path / "bad").write_text("this is definitely not json\n")

        caplog.set_level(logging.ERROR, logger="spatialai_data_utils.loaders.nvschema")
        result = load_nvschemas(str(tmp_path), ["good", "bad"])
        # The good scene still comes back; the bad one is dropped.
        assert set(result.keys()) == {"good"}
        # The bad scene triggers an ERROR record with exc_info attached.
        err_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("malformed JSON" in r.getMessage() for r in err_records)
        assert any(r.exc_info for r in err_records)

    def test_empty_scene_list_returns_empty(self, scenes_dir):
        """An empty scene list yields an empty result dict."""
        assert load_nvschemas(scenes_dir, []) == {}

    def test_output_format_is_passed_through(self, scenes_dir):
        """Passing ``output_format='gt_json_aicity'`` flattens every scene."""
        result = load_nvschemas(
            scenes_dir, ["scene_a"], output_format=GT_JSON_FORMAT,
        )
        obj = result["scene_a"][0]["cam0"][0]
        assert obj["object id"] == 1
        assert "bbox3d" not in obj

    def test_invalid_output_format_raises(self, scenes_dir, caplog):
        """Invalid output_format surfaces as a skipped-load (logged, not raised)."""
        import logging

        caplog.set_level(logging.ERROR, logger="spatialai_data_utils.loaders.nvschema")
        result = load_nvschemas(
            scenes_dir, ["scene_a"], output_format="bogus",
        )
        assert result == {}
        # The ValueError from load_nvschema lands in the generic "unexpected
        # error" handler and is logged with a stack trace.
        assert any(
            r.levelno >= logging.ERROR and "unexpected error" in r.getMessage()
            for r in caplog.records
        )


# ---------------------------------------------------------------------------
# iter_frame_rows
# ---------------------------------------------------------------------------


class TestIterFrameRows:
    """Tests for the per-row streamer.

    Sibling of :func:`load_nvschema` that preserves the one-row-per-
    yield mapping (no per-(frame, sensor) collapse, no
    ``output_format`` switch) and surfaces the optional top-level
    ``timestamp`` / ``info`` fields.  Drives the per-row CLI's
    timestamp-based image lookup; covered here as a library helper
    so other per-row NVSchema consumers (projection / evaluation
    tools) can reuse the same parsing + normalisation.
    """

    def _write_lines(self, path, rows):
        """Write each row dict (or raw string) on its own line."""
        with open(path, "w") as f:
            for r in rows:
                f.write(
                    json.dumps(r) if isinstance(r, dict) else str(r)
                )
                f.write("\n")
        return str(path)

    def test_single_row_round_trips_core_fields(self, tmp_path):
        """A single well-formed row yields the documented schema."""
        row = {
            "id": "6",
            "sensorId": "Camera_08",
            "timestamp": "2025-04-14T00:36:45.009Z",
            "objects": [{"id": "1", "type": "Person"}],
        }
        path = self._write_lines(tmp_path / "rows.jsonl", [row])
        out = list(iter_frame_rows(path))
        assert out == [{
            "frame_id": 6,
            "sensor_id": "Camera_08",
            "timestamp": "2025-04-14T00:36:45.009Z",
            "objects": [{"id": "1", "type": "Person"}],
            # Rows without an ``info`` field surface as an empty dict —
            # callers can treat that uniformly.
            "info": {},
        }]

    def test_info_field_round_trips(self, tmp_path):
        """Top-level ``info`` is preserved verbatim and shows up as a dict."""
        row = {
            "id": "9",
            "sensorId": "bev-sensor-1",
            "timestamp": "2025-04-14T00:36:45.109Z",
            "objects": [],
            "info": {
                "Camera_01": "2025-04-14T00:36:45.109Z",
                "Camera_02": "2025-04-14T00:36:45.209Z",
            },
        }
        path = self._write_lines(tmp_path / "rows.jsonl", [row])
        out = list(iter_frame_rows(path))
        assert out[0]["info"] == {
            "Camera_01": "2025-04-14T00:36:45.109Z",
            "Camera_02": "2025-04-14T00:36:45.209Z",
        }

    def test_null_info_normalised_to_empty_dict(self, tmp_path):
        """``"info": null`` (or an empty object) yields ``{}``, never ``None``."""
        rows = [
            {"id": "0", "sensorId": "cam", "timestamp": "t", "objects": [],
             "info": None},
            {"id": "1", "sensorId": "cam", "timestamp": "t", "objects": [],
             "info": {}},
        ]
        path = self._write_lines(tmp_path / "rows.jsonl", rows)
        out = list(iter_frame_rows(path))
        assert out[0]["info"] == {}
        assert out[1]["info"] == {}

    def test_multi_row_preserves_order(self, tmp_path):
        """Every line becomes its own yield; order preserved."""
        rows = [
            {"id": "0", "sensorId": "cam_a", "timestamp": "t0", "objects": []},
            {"id": "1", "sensorId": "cam_b", "timestamp": "t1", "objects": [{}]},
            {"id": "2", "sensorId": "cam_a", "timestamp": "t2", "objects": []},
        ]
        path = self._write_lines(tmp_path / "rows.jsonl", rows)
        out = list(iter_frame_rows(path))
        assert [r["frame_id"] for r in out] == [0, 1, 2]
        assert [r["sensor_id"] for r in out] == ["cam_a", "cam_b", "cam_a"]
        assert [r["timestamp"] for r in out] == ["t0", "t1", "t2"]
        # Rows with duplicate (frame, sensor) are NOT collapsed — the
        # streamer needs the row-level 1:1 mapping (unlike
        # load_nvschema which collapses to a per-(frame, sensor) list).
        assert len(out) == 3

    def test_missing_timestamp_becomes_none(self, tmp_path):
        """An absent ``timestamp`` field is surfaced as ``None`` (not missing)."""
        path = self._write_lines(tmp_path / "rows.jsonl", [{
            "id": "9", "sensorId": "cam", "objects": [],
        }])
        out = list(iter_frame_rows(path))
        assert out[0]["timestamp"] is None

    def test_missing_objects_becomes_empty_list(self, tmp_path):
        """An absent ``objects`` field defaults to ``[]`` (not KeyError)."""
        path = self._write_lines(tmp_path / "rows.jsonl", [{
            "id": "0", "sensorId": "cam", "timestamp": "t",
        }])
        out = list(iter_frame_rows(path))
        assert out[0]["objects"] == []

    def test_empty_lines_are_skipped(self, tmp_path):
        """Blank / whitespace-only lines in the JSONL don't produce yields."""
        path = tmp_path / "rows.jsonl"
        with open(path, "w") as f:
            f.write("\n")  # leading blank
            f.write(json.dumps({
                "id": "1", "sensorId": "cam", "timestamp": "t1", "objects": [],
            }) + "\n")
            f.write("   \n")  # whitespace-only
            f.write(json.dumps({
                "id": "2", "sensorId": "cam", "timestamp": "t2", "objects": [],
            }) + "\n")
            f.write("\n")  # trailing blank
        out = list(iter_frame_rows(str(path)))
        assert [r["frame_id"] for r in out] == [1, 2]

    def test_single_quote_fixup_parses_malformed_line(self, tmp_path):
        """A JSON line written with single quotes is repaired and parsed.

        Same defensive measure :func:`load_nvschema` carries — locks
        in the parity so users don't have to think about which loader
        they're using when their export tool produced single-quoted
        JSON.
        """
        path = tmp_path / "rows.jsonl"
        with open(path, "w") as f:
            f.write(
                "{'id': '3', 'sensorId': 'cam', 'timestamp': 't3', 'objects': []}\n"
            )
        out = list(iter_frame_rows(str(path)))
        assert out == [{
            "frame_id": 3, "sensor_id": "cam",
            "timestamp": "t3", "objects": [], "info": {},
        }]

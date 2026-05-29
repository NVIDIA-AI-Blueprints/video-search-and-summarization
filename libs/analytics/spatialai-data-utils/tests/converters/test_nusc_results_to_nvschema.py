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

"""Tests for ``converters.nusc_results_to_nvschema``.

Covers the fixed-precision ``FloatEncoder`` and the end-to-end
sparse4d -> NVSchema JSON-lines converter, including:

* per-scene + per-BEV-group output layout
  (``"<scene>+<group>"`` sample tokens),
* size axis swap from nuScenes ``[l, w, h]`` to NVSchema ``[w, l, h]``,
* the ``save_embedding`` toggle (drop vs. forward ``reid_embedding``).
"""

import json

import pytest

from spatialai_data_utils.converters.nusc_results_to_nvschema import (
    FloatEncoder,
    convert_sparse4d_to_nvschema,
)


# ---------------------------------------------------------------------------
# FloatEncoder
# ---------------------------------------------------------------------------


class TestFloatEncoder:
    def test_encodes_float_with_nine_decimal_places(self):
        assert FloatEncoder().encode(1.5) == "1.500000000"

    def test_encodes_list_of_floats_with_consistent_precision(self):
        out = FloatEncoder().encode([1.0, 2.5, 3.25])
        assert out == "[1.000000000, 2.500000000, 3.250000000]"

    def test_encodes_dict_with_string_keys_and_float_values(self):
        out = FloatEncoder().encode({"x": 1.0, "y": 2.5})
        # Dict iteration order is insertion order in Python 3.7+, so this is
        # deterministic for the test fixture.
        assert out == '{"x": 1.000000000, "y": 2.500000000}'

    def test_encodes_nested_structure(self):
        out = FloatEncoder().encode({"vec": [1.0, 2.0], "scalar": 3.14159})
        assert '"vec": [1.000000000, 2.000000000]' in out
        assert '"scalar": 3.141590000' in out

    def test_falls_back_to_super_for_non_float_scalars(self):
        """Ints / strings / bools / None take the default ``JSONEncoder``
        path (no fixed-precision formatting applied)."""
        assert FloatEncoder().encode(42) == "42"
        assert FloatEncoder().encode("hi") == '"hi"'
        assert FloatEncoder().encode(True) == "true"
        assert FloatEncoder().encode(None) == "null"


# ---------------------------------------------------------------------------
# convert_sparse4d_to_nvschema — fixture helpers
# ---------------------------------------------------------------------------


def _sparse4d_input(frame_token_to_objects):
    """Build the ``{results: {token: [obj, ...]}}`` envelope the
    converter consumes."""
    return {"results": frame_token_to_objects}


def _track_obj(*, translation=(1.0, 2.0, 0.5), size=(0.5, 1.0, 1.8),
               rotation=(1.0, 0.0, 0.0, 0.0), tracking_id="42",
               tracking_score=0.9, tracking_name="person",
               reid_embedding=None):
    """One Sparse4D tracking-result object. ``size`` is in nuScenes
    ``[l, w, h]`` convention; the converter swaps to NVSchema ``[w, l, h]``."""
    obj = {
        "translation": list(translation),
        "size": list(size),
        "rotation": list(rotation),
        "tracking_id": tracking_id,
        "tracking_score": tracking_score,
        "tracking_name": tracking_name,
    }
    if reid_embedding is not None:
        obj["reid_embedding"] = reid_embedding
    return obj


def _write_sparse4d_json(path, content):
    path.write_text(json.dumps(content))


def _read_jsonl(path):
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


MAP_CLASS_NAMES = {"person": "Person"}


# ---------------------------------------------------------------------------
# convert_sparse4d_to_nvschema — end-to-end
# ---------------------------------------------------------------------------


class TestConvertSparse4DToNVSchema:
    def test_single_scene_writes_one_jsonl_file(self, tmp_path):
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj()],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )

        produced = list(out.glob("*.json"))
        assert len(produced) == 1 and produced[0].name == "sceneA.json"
        records = _read_jsonl(produced[0])
        assert len(records) == 1

    def test_nvschema_envelope_fields_match_spec(self, tmp_path):
        """Each record must carry NVSchema v4.0 top-level fields:
        version / id / sensorId / timestamp / objects."""
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__7": [_track_obj()],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )
        rec = _read_jsonl(out / "sceneA.json")[0]
        assert rec["version"] == "4.0"
        assert rec["id"] == "7"
        # Default sensorId when no '+' in the token.
        assert rec["sensorId"] == "bev-sensor-1"
        # ISO-8601 with millisecond precision + trailing 'Z'.
        assert rec["timestamp"].endswith("Z")
        assert len(rec["objects"]) == 1

    def test_size_axes_are_swapped_to_nvschema_wlh_order(self, tmp_path):
        """Input ``size = [l, w, h]`` (nuScenes), NVSchema expects
        ``[w, l, h]``. Coordinates layout is
        ``[x, y, z, w, l, h, pitch, roll, yaw]``."""
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj(
                translation=(0.0, 0.0, 0.0),
                size=(2.0, 5.0, 1.8),  # [l=2, w=5, h=1.8]
                rotation=(1.0, 0.0, 0.0, 0.0),
            )],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )
        obj = _read_jsonl(out / "sceneA.json")[0]["objects"][0]
        coords = obj["bbox3d"]["coordinates"]
        # x, y, z then [w=5, l=2, h=1.8]
        assert coords[3] == pytest.approx(5.0)
        assert coords[4] == pytest.approx(2.0)
        assert coords[5] == pytest.approx(1.8)

    def test_object_metadata_fields_match_spec(self, tmp_path):
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj(
                translation=(1.0, 2.0, 0.5),
                tracking_id=99,
                tracking_score=0.7,
                tracking_name="person",
            )],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )
        obj = _read_jsonl(out / "sceneA.json")[0]["objects"][0]
        assert obj["id"] == "99"  # tracking_id stringified
        assert obj["type"] == "Person"
        assert obj["confidence"] == pytest.approx(0.7)
        assert obj["coordinate"] == {
            "x": pytest.approx(1.0),
            "y": pytest.approx(2.0),
            "z": pytest.approx(0.5),
        }
        assert obj["bbox3d"]["confidence"] == pytest.approx(0.7)

    def test_save_embedding_false_emits_empty_embedding_entry(self, tmp_path):
        """When ``save_embedding=False`` the converter still emits an
        ``embedding`` slot but it's a list containing one empty dict
        (NVSchema requires the field to be present)."""
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj(reid_embedding=[1.0, 2.0, 3.0])],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )
        emb = _read_jsonl(out / "sceneA.json")[0]["objects"][0]["bbox3d"]["embedding"]
        assert emb == [{}]

    def test_save_embedding_true_forwards_reid_embedding(self, tmp_path):
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj(reid_embedding=[0.1, 0.2, 0.3])],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=True,
        )
        emb = _read_jsonl(out / "sceneA.json")[0]["objects"][0]["bbox3d"]["embedding"]
        assert emb == [{"vector": [0.1, 0.2, 0.3]}]

    def test_save_embedding_true_without_reid_embedding_still_empty(self, tmp_path):
        """``save_embedding=True`` only fires when the object actually
        carries a ``reid_embedding`` field — otherwise it falls back
        to the empty-slot form."""
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj()],  # no reid_embedding
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=True,
        )
        emb = _read_jsonl(out / "sceneA.json")[0]["objects"][0]["bbox3d"]["embedding"]
        assert emb == [{}]

    def test_multiple_scenes_produce_one_file_each(self, tmp_path):
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj()],
            "sceneA__1": [_track_obj()],
            "sceneB__0": [_track_obj()],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )
        files = sorted(p.name for p in out.glob("*.json"))
        assert files == ["sceneA.json", "sceneB.json"]
        # sceneA has two frames -> two JSONL lines.
        assert len(_read_jsonl(out / "sceneA.json")) == 2
        assert len(_read_jsonl(out / "sceneB.json")) == 1

    def test_plus_separated_token_routes_to_named_bev_group(self, tmp_path):
        """Tokens like ``"sceneA+bev-3__0"`` carry the BEV group
        name in the token. The converter must emit it as ``sensorId``
        (rather than the default ``bev-sensor-1``) and route the
        record to a ``sceneA+bev-3.json`` file."""
        inp = tmp_path / "sparse4d.json"
        out = tmp_path / "nvschema"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA+bev-3__0": [_track_obj()],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(out), MAP_CLASS_NAMES, save_embedding=False,
        )
        produced = sorted(p.name for p in out.glob("*.json"))
        assert produced == ["sceneA+bev-3.json"]
        rec = _read_jsonl(out / "sceneA+bev-3.json")[0]
        assert rec["sensorId"] == "bev-3"

    def test_creates_output_directory_when_missing(self, tmp_path):
        """The converter should create its ``output_path`` if absent."""
        inp = tmp_path / "sparse4d.json"
        nested_out = tmp_path / "does" / "not" / "exist" / "yet"
        _write_sparse4d_json(inp, _sparse4d_input({
            "sceneA__0": [_track_obj()],
        }))

        convert_sparse4d_to_nvschema(
            str(inp), str(nested_out), MAP_CLASS_NAMES, save_embedding=False,
        )
        assert (nested_out / "sceneA.json").is_file()

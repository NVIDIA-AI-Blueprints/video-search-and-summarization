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

"""Tests for ``loaders.sparse4d``.

Covers all four public loaders against small JSON fixtures:

* ``load_sparse4d_raw_json`` in both detection and tracking modes
  (different score / id / name fields).
* ``load_sparse4d_det_3d_scene`` for the per-scene flattening path
  (with explicit ``scene_name`` and the single-scene assertion path).
* ``load_sparse4d_json`` for per-scene postprocessed files (including
  the missing-file branch).
* ``load_sparse4d_jsons`` for multi-scene loading from a directory.

Each test uses a hand-written JSON fixture in ``tmp_path`` so we
exercise the real ``json`` / ``pyquaternion`` plumbing without any
external Sparse4D artefact.
"""

import json

import pytest

from spatialai_data_utils.loaders.sparse4d import (
    load_sparse4d_det_3d_scene,
    load_sparse4d_json,
    load_sparse4d_jsons,
    load_sparse4d_raw_json,
)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _identity_quat():
    """Return a quaternion (w, x, y, z) representing zero rotation."""
    return [1.0, 0.0, 0.0, 0.0]


def _raw_record(*, detection=True, det_score=0.9, track_id="42"):
    """Build one entry in the Sparse4D ``results[sample_token]`` list."""
    base = {
        "translation": [1.0, 2.0, 0.5],
        "size": [0.5, 1.0, 1.8],   # (raw order; loader swaps [1], [0], [2])
        "rotation": _identity_quat(),
    }
    if detection:
        base.update({
            "detection_score": det_score,
            "detection_name": "Person",
        })
    else:
        base.update({
            "tracking_id": track_id,
            "tracking_score": det_score,
            "tracking_name": "Person",
        })
    return base


def _write_raw_sparse4d(path, sample_tokens_to_records):
    """Write the canonical {results: {sample_token: [...]}} shape."""
    path.write_text(json.dumps({"results": sample_tokens_to_records}))


# ---------------------------------------------------------------------------
# load_sparse4d_raw_json
# ---------------------------------------------------------------------------


def test_raw_json_detection_mode_assigns_sequential_ids(tmp_path):
    p = tmp_path / "raw_det.json"
    _write_raw_sparse4d(p, {
        "scene_A__0": [_raw_record(det_score=0.9), _raw_record(det_score=0.8)],
        "scene_A__1": [_raw_record(det_score=0.7)],
    })

    out = load_sparse4d_raw_json(str(p), tracking=False)

    assert set(out.keys()) == {"scene_A"}
    assert set(out["scene_A"].keys()) == {0, 1}
    frame0 = out["scene_A"][0]
    assert [d["person id"] for d in frame0] == [0, 1], (
        "Detection mode must assign sequential ids per frame."
    )
    assert frame0[0]["type"] == "Person"
    assert frame0[0]["confidence"] == 0.9


def test_raw_json_swaps_size_dimensions(tmp_path):
    """Loader swaps raw size ``[a, b, c]`` to ``[b, a, c]``."""
    p = tmp_path / "raw_det.json"
    _write_raw_sparse4d(p, {"scene_A__0": [_raw_record()]})

    out = load_sparse4d_raw_json(str(p), tracking=False)
    rec = out["scene_A"][0][0]
    assert rec["3d bounding box scale"] == [1.0, 0.5, 1.8]


def test_raw_json_tracking_mode_uses_tracking_fields(tmp_path):
    p = tmp_path / "raw_track.json"
    _write_raw_sparse4d(p, {
        "scene_A__0": [
            _raw_record(detection=False, det_score=0.99, track_id="7"),
            _raw_record(detection=False, det_score=0.5, track_id="99"),
        ],
    })

    out = load_sparse4d_raw_json(str(p), tracking=True)

    frame0 = out["scene_A"][0]
    assert [d["person id"] for d in frame0] == [7, 99], (
        "Tracking mode must use tracking_id (cast to int), not sequential ids."
    )
    assert [d["confidence"] for d in frame0] == [0.99, 0.5]


def test_raw_json_groups_frames_under_scene(tmp_path):
    p = tmp_path / "raw.json"
    _write_raw_sparse4d(p, {
        "sceneA__0": [_raw_record()],
        "sceneB__0": [_raw_record()],
        "sceneA__1": [_raw_record()],
    })
    out = load_sparse4d_raw_json(str(p), tracking=False)
    assert set(out.keys()) == {"sceneA", "sceneB"}
    assert set(out["sceneA"].keys()) == {0, 1}
    assert set(out["sceneB"].keys()) == {0}


# ---------------------------------------------------------------------------
# load_sparse4d_det_3d_scene
# ---------------------------------------------------------------------------


def test_det_3d_scene_returns_per_frame_list_of_class_box_conf_id(tmp_path):
    p = tmp_path / "raw_det.json"
    _write_raw_sparse4d(p, {
        "scene_A__0": [_raw_record(det_score=0.9)],
        "scene_A__1": [_raw_record(det_score=0.8)],
    })

    out = load_sparse4d_det_3d_scene(str(p), scene_name="scene_A")

    assert set(out.keys()) == {0, 1}
    entry = out[0][0]
    assert isinstance(entry, list) and len(entry) == 4
    cls, box, conf, oid = entry
    assert cls == "Person"
    assert len(box) == 7  # x, y, z, w, l, h, yaw_rad
    assert conf == 0.9
    assert oid == 0


def test_det_3d_scene_defaults_to_only_scene_when_unspecified(tmp_path):
    p = tmp_path / "raw_det.json"
    _write_raw_sparse4d(p, {"scene_A__0": [_raw_record()]})

    out = load_sparse4d_det_3d_scene(str(p))
    assert set(out.keys()) == {0}


def test_det_3d_scene_asserts_when_multi_scene_and_no_name(tmp_path):
    p = tmp_path / "raw_det.json"
    _write_raw_sparse4d(p, {
        "sceneA__0": [_raw_record()],
        "sceneB__0": [_raw_record()],
    })
    with pytest.raises(AssertionError):
        load_sparse4d_det_3d_scene(str(p), scene_name=None)


# ---------------------------------------------------------------------------
# load_sparse4d_json (per-scene postprocessed)
# ---------------------------------------------------------------------------


def test_load_sparse4d_json_casts_frame_ids_to_int(tmp_path):
    p = tmp_path / "scene_A.json"
    p.write_text(json.dumps({
        "0": [{"person id": 1, "type": "Person"}],
        "1": [{"person id": 2, "type": "Person"}],
    }))

    out = load_sparse4d_json(str(p))
    assert set(out.keys()) == {0, 1}
    assert all(isinstance(k, int) for k in out.keys())


def test_load_sparse4d_json_returns_none_for_missing_file(tmp_path):
    """The function intentionally swallows missing-file errors and
    returns ``None`` so callers (e.g. ``load_sparse4d_jsons``) can
    skip absent scenes."""
    assert load_sparse4d_json(str(tmp_path / "missing.json")) is None


# ---------------------------------------------------------------------------
# load_sparse4d_jsons (multi-scene)
# ---------------------------------------------------------------------------


def test_load_sparse4d_jsons_aggregates_present_scenes_and_skips_missing(tmp_path):
    (tmp_path / "scene_A.json").write_text(json.dumps({"0": [{"x": 1}]}))
    (tmp_path / "scene_B.json").write_text(json.dumps({"0": [{"x": 2}]}))
    # scene_C deliberately missing

    out = load_sparse4d_jsons(
        str(tmp_path), scene_names=["scene_A", "scene_B", "scene_C"]
    )

    assert set(out.keys()) == {"scene_A", "scene_B"}, (
        "Missing scenes must be silently skipped (load_sparse4d_json returns None)."
    )
    assert out["scene_A"] == {0: [{"x": 1}]}
    assert out["scene_B"] == {0: [{"x": 2}]}

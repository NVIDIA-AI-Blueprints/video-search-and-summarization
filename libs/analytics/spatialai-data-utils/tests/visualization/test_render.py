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

"""Coverage supplement for ``visualization.render`` — pins:

* ``process_frame_nvschema`` multi-sensor mapping branch (line 380),
* ``process_frame_nvschema`` ``load_image`` returns None branch (398),
* ``process_frame_gt_json_aicity`` ``load_image`` returns None (513),
* ``process_scene`` unknown viz_mode raise (632),
* ``process_scene`` ``n_frames > 0`` slice + ``pkl_info is None`` skip,
* ``visualize_3dbbox`` NVSchema dispatch logger lines + delegate call
  (876-880)."""

import json
import logging
import os

import numpy as np
import pytest

from spatialai_data_utils.visualization import render
from spatialai_data_utils.visualization.render import (
    process_frame_gt_json_aicity,
    process_frame_nvschema,
    process_scene,
    visualize_3dbbox,
)


# ---------------------------------------------------------------------------
# process_frame_nvschema / process_frame_gt_json_aicity — load_image None
# ---------------------------------------------------------------------------


def test_process_frame_nvschema_skips_when_load_image_returns_none(
    tmp_path, monkeypatch,
):
    """When ``load_image(frame_path)`` returns None (e.g. broken
    file), the per-frame driver hits the ``continue`` branch and
    leaves no annotated image on disk."""
    monkeypatch.setattr(render, "load_image", lambda p: None)
    # The function only iterates ``frame_paths`` and never reaches
    # save_viz, so any calib + det dict shape is fine.
    process_frame_nvschema(
        det_dicts_sensors={"cam_a": []},
        calib_dict={"cam_a": {}},
        frame_paths={"cam_a": "/nope.png"},
        vis_dir=str(tmp_path),
    )
    # No image was written.
    assert sorted(os.listdir(tmp_path)) == []


def test_process_frame_nvschema_multi_sensor_mapping_branch(
    tmp_path, monkeypatch,
):
    """When ``det_dicts_sensors`` has more than one entry, the
    per-camera lookup goes through the dict-get fallback at line 380.
    Stub everything downstream so we just verify the branch ran."""
    monkeypatch.setattr(render, "load_image", lambda p: None)  # short-circuit
    process_frame_nvschema(
        det_dicts_sensors={"cam_a": [], "cam_b": []},  # 2 sensors -> dict-get
        calib_dict={"cam_a": {}, "cam_b": {}},
        frame_paths={"cam_a": "/x", "cam_b": "/y"},
        vis_dir=str(tmp_path),
    )


def test_process_frame_gt_json_aicity_skips_when_load_image_returns_none(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(render, "load_image", lambda p: None)
    # Provide empty annotation list so the bbox-projection short-circuit
    # is reached only after the load-image continue (line 513).
    process_frame_gt_json_aicity(
        gt_frame=[],
        calib_dict={"cam_a": {}},
        frame_paths={"cam_a": "/nope.png"},
        vis_dir=str(tmp_path),
    )
    assert sorted(os.listdir(tmp_path)) == []


# ---------------------------------------------------------------------------
# process_scene — unknown viz_mode + n_frames slice + pkl_info None skip
# ---------------------------------------------------------------------------


def test_process_scene_raises_for_unknown_viz_mode(tmp_path, monkeypatch):
    """A viz_mode not in ``_VIZ_MODE_DRIVERS`` triggers the
    'Unknown viz_mode' raise (line 632)."""
    # Short-circuit calibration loading so we never need real files.
    monkeypatch.setattr(
        render, "resolve_scene_calib",
        lambda **kw: {"cam_a": {}},
    )
    with pytest.raises(ValueError, match="Unknown viz_mode"):
        process_scene(
            scene_name_full="sceneA",
            scene_root=str(tmp_path),
            scene_results={0: []},
            viz_root=str(tmp_path / "out"),
            viz_mode="bogus_mode",
        )


def test_process_scene_n_frames_slice_and_pkl_info_skip(
    tmp_path, monkeypatch,
):
    """``n_frames > 0`` slices the sorted frame-id list (line 649);
    when ``pkl_info`` for a given frame is None the per-frame loop
    skips that frame (line 655)."""
    monkeypatch.setattr(
        render, "resolve_scene_calib",
        lambda **kw: {"cam_a": {}},
    )
    # Replace the driver with a no-op so we don't need real images.
    invocations = []

    def _stub_driver(scene_results, calib, paths, vis_dir, **kw):
        invocations.append((scene_results, paths))

    monkeypatch.setitem(
        render._VIZ_MODE_DRIVERS, "nvschema", _stub_driver,
    )
    monkeypatch.setattr(
        render, "index_pkl_by_frame",
        # Two pkl entries: only frame 1 has info, frames 0 and 2 are None.
        lambda infos: {1: {"cams": {}}},
    )
    monkeypatch.setattr(
        render, "frame_paths_from_pkl_info",
        lambda info, cam_names: {"cam_a": "/fake.png"},
    )
    process_scene(
        scene_name_full="sceneA",
        scene_root=str(tmp_path),
        scene_results={0: [], 1: [], 2: []},
        viz_root=str(tmp_path / "out"),
        viz_mode="nvschema",
        n_frames=2,           # ← slices to {0, 1}
        pkl_infos=[{}, {}],   # ← drives the pkl_by_frame branch
    )
    # n_frames=2 + frame 0 having pkl_info=None -> only frame 1 dispatched.
    assert len(invocations) == 1


# ---------------------------------------------------------------------------
# visualize_3dbbox — NVSchema dispatch lines 876-880
# ---------------------------------------------------------------------------


def test_visualize_3dbbox_nvschema_branch_drives_dispatch_logs(
    tmp_path, monkeypatch, caplog,
):
    """``visualize_3dbbox(nvschema_path=..., calib_path=...,
    data_path=...)`` hits the NVSchema dispatch banner (lines
    876-880) and delegates to ``visualize_nvschema``."""
    # Stub the heavy delegate so we just trace the dispatch.
    captured = {}

    def _stub_visualize_nvschema(**kw):
        captured.update(kw)

    monkeypatch.setattr(
        render, "visualize_nvschema", _stub_visualize_nvschema,
    )

    # Minimal valid file fixtures (we only need them to exist as
    # path strings; the stubbed delegate never opens them).
    nv = tmp_path / "results.json"
    nv.write_text("{}")
    calib = tmp_path / "calib.json"
    calib.write_text("{}")
    data = tmp_path / "data"
    data.mkdir()

    with caplog.at_level(logging.INFO):
        visualize_3dbbox(
            nvschema_path=str(nv), calib_path=str(calib),
            data_path=str(data), output_dir=str(tmp_path / "out"),
        )

    # Banner + per-line dispatch logs landed.
    assert "3D BBox Visualization" in caplog.text
    assert "Results" in caplog.text
    assert "Calib" in caplog.text
    # Delegate received the forwarded kwargs.
    assert captured["nvschema_path"] == str(nv)
    assert captured["calib_path"] == str(calib)
    assert captured["data_path"] == str(data)


def test_visualize_3dbbox_nvschema_requires_calib_path(tmp_path):
    """``visualize_3dbbox(nvschema_path=...)`` without ``calib_path``
    raises ValueError (line 875)."""
    nv = tmp_path / "results.json"; nv.write_text("{}")
    data = tmp_path / "data"; data.mkdir()
    with pytest.raises(ValueError, match="calib_path"):
        visualize_3dbbox(
            nvschema_path=str(nv), data_path=str(data),
            output_dir=str(tmp_path / "out"),
        )

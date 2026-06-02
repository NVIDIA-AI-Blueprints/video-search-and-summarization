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

"""Tests for ``visualization.camera_placement.plotter`` private and
public helpers.

Covers:

* public ``render_camera_placement`` / ``render_camera_placement_sequence``
  validation raises, the ``close=True`` finally, footer/local-axes
  branches, group filtering, and the all-cameras fallback.
* ``_iter_polygons`` exhaustive shape branches (None / Polygon /
  MultiPolygon / GeometryCollection / empty parts).
* ``_clip_geometry_to_scene_bounds`` swallow-on-error + empty-after-clip.
* Private helpers covered by ``test_plotter_helpers_coverage.py`` in a
  previous iteration: ``_set_equal_axes`` zero-span, ``_auto_3d_view``
  single-camera + zero-forward branches, ``_compute_world_content_bounds``
  no-polygon fallback, ``_sensor_world_to_map_params``,
  ``_world_to_map_xy``, ``_resolve_fov_clip_bounds`` fallback paths,
  plus a full ``render_camera_placement`` call with a real map that
  exercises the ``_draw_bev_panel`` use_map branches.
"""

import json

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pytest  # noqa: E402
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401, E402 — registers projection
from shapely.geometry import (  # noqa: E402
    GeometryCollection,
    MultiPolygon,
    Polygon,
)

from spatialai_data_utils.visualization.camera_placement import plotter  # noqa: E402
from spatialai_data_utils.visualization.camera_placement.plotter import (  # noqa: E402
    CameraPlacementBevStyle,
    CameraPlacementStyle,
    _clip_geometry_to_scene_bounds,
    _iter_polygons,
    render_camera_placement,
    render_camera_placement_sequence,
)
from spatialai_data_utils.visualization.camera_placement.calibration_parser import (  # noqa: E402
    CameraPose,
    load_camera_poses_from_calibration,
)


# ---------------------------------------------------------------------------
# _iter_polygons — exhaustive shape branches
# ---------------------------------------------------------------------------


class TestIterPolygons:
    def test_none_yields_nothing(self):
        assert list(_iter_polygons(None)) == []

    def test_empty_polygon_yields_nothing(self):
        assert list(_iter_polygons(Polygon())) == []

    def test_polygon_yields_self(self):
        p = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        out = list(_iter_polygons(p))
        assert len(out) == 1
        assert out[0] is p

    def test_multipolygon_yields_each_part(self):
        a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        b = Polygon([(5, 5), (6, 5), (6, 6), (5, 6)])
        out = list(_iter_polygons(MultiPolygon([a, b])))
        assert len(out) == 2

    def test_multipolygon_skips_empty_parts(self):
        a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        # MultiPolygon with one empty part is treated as a single non-
        # empty polygon by shapely, so use the GeometryCollection path
        # instead to drive the empty-skip branch.
        coll = GeometryCollection([a, Polygon()])
        out = list(_iter_polygons(coll))
        assert len(out) == 1

    def test_geometry_collection_flattens_recursively(self):
        a = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        b = Polygon([(5, 5), (6, 5), (6, 6), (5, 6)])
        nested = GeometryCollection([
            a,
            GeometryCollection([b, MultiPolygon([a])]),
        ])
        out = list(_iter_polygons(nested))
        # Three polygons after flattening: a, b, a-inside-multipolygon.
        assert len(out) == 3


# ---------------------------------------------------------------------------
# _clip_geometry_to_scene_bounds — error swallow + empty-after-clip
# ---------------------------------------------------------------------------


def test_clip_swallows_exception_and_returns_input():
    """When ``geometry.intersection`` raises, the helper returns the
    input geometry unchanged (the wide try/except branch)."""
    class _BadGeom:
        is_empty = False

        def intersection(self, other):
            raise AttributeError("synthetic")

    bad = _BadGeom()
    out = _clip_geometry_to_scene_bounds(bad, (0.0, 0.0, 10.0, 10.0))
    assert out is bad


def test_clip_returns_none_when_clipped_is_empty():
    """A polygon entirely outside the scene-bounds rectangle clips
    down to an empty geometry -> returns ``None``."""
    far = Polygon([(1000, 1000), (1001, 1000), (1001, 1001), (1000, 1001)])
    out = _clip_geometry_to_scene_bounds(far, (0.0, 0.0, 10.0, 10.0))
    assert out is None


def test_clip_returns_none_geometry_unchanged_when_scene_bounds_none():
    p = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    assert _clip_geometry_to_scene_bounds(p, None) is p
    assert _clip_geometry_to_scene_bounds(None, (0, 0, 10, 10)) is None


# ---------------------------------------------------------------------------
# render_camera_placement — validation raises + footer + close branches
# ---------------------------------------------------------------------------


def _two_pose_calibration(path):
    """Tiny ungrouped calibration with two cameras."""
    path.write_text(json.dumps({
        "sensors": [
            {
                "id": "Camera_01",
                "intrinsicMatrix": [
                    [1000, 0, 960], [0, 1000, 540], [0, 0, 1],
                ],
                "extrinsicMatrix": [
                    [1, 0, 0, 1], [0, 1, 0, 2], [0, 0, 1, 3],
                ],
                "attributes": [
                    {"name": "frameWidth", "value": "1920"},
                    {"name": "frameHeight", "value": "1080"},
                ],
            },
            {
                "id": "Camera_02",
                "intrinsicMatrix": [
                    [900, 0, 640], [0, 900, 360], [0, 0, 1],
                ],
                "extrinsicMatrix": [
                    [1, 0, 0, -4], [0, 1, 0, 5], [0, 0, 1, -6],
                ],
                "attributes": [
                    {"name": "frameWidth", "value": "1280"},
                    {"name": "frameHeight", "value": "720"},
                ],
            },
        ],
    }))


def test_render_camera_placement_raises_on_empty_poses():
    with pytest.raises(ValueError, match="camera_poses is empty"):
        render_camera_placement([], output_path=None)


def test_render_camera_placement_raises_on_invalid_bev_source_mode(tmp_path):
    """``bev_style.source_mode`` must be one of the known modes; an
    unknown value triggers the validation raise (lines 937-940)."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    bad_style = CameraPlacementBevStyle(source_mode="bogus")
    with pytest.raises(ValueError, match="Unsupported bev source_mode"):
        render_camera_placement(poses, output_path=None,
                                  bev_style=bad_style, close=True)


def test_render_camera_placement_close_branch_releases_figure(tmp_path):
    """``close=True`` (no show, no error) calls ``plt.close(fig)`` in
    the finally block — verify the figure handle is no longer in
    the active manager after the call."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    out = tmp_path / "viz.png"
    fig, _ = render_camera_placement(
        poses, output_path=out, close=True,
    )
    assert out.is_file()
    # Figure was closed; querying its number should return False.
    assert not plt.fignum_exists(fig.number)


def test_render_camera_placement_with_footer_text(tmp_path):
    """``footer_text`` triggers the ``fig.text`` + adjusted
    tight_layout branch instead of the bare tight_layout fallback."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    out = tmp_path / "viz_footer.png"
    fig, _ = render_camera_placement(
        poses, output_path=out, footer_text="© 2026 NVIDIA",
        close=True,
    )
    assert out.is_file()


def test_render_camera_placement_with_draw_local_axes(tmp_path):
    """``style.draw_local_axes=True`` exercises the per-camera
    quiver branch in ``_draw_3d_panel`` (lines 662-678)."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    out = tmp_path / "viz_axes.png"
    custom_style = CameraPlacementStyle(draw_local_axes=True)
    fig, _ = render_camera_placement(
        poses, output_path=out, style=custom_style, close=True,
    )
    assert out.is_file()


# ---------------------------------------------------------------------------
# render_camera_placement_sequence — empty + filter + fallback branches
# ---------------------------------------------------------------------------


def test_render_camera_placement_sequence_raises_on_empty_poses(tmp_path):
    with pytest.raises(ValueError, match="camera_poses is empty"):
        render_camera_placement_sequence([], output_dir=str(tmp_path / "out"))


def test_render_camera_placement_sequence_filters_groups(tmp_path):
    """``group_names`` filters which groups are rendered — groups
    not in the set are skipped (line 1093). Pass a name that doesn't
    exist in ``cams_by_group`` so no per-group file is written."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    out_root = tmp_path / "out"
    outputs = render_camera_placement_sequence(
        poses, output_dir=str(out_root),
        cams_by_group={"some-group": [p.sensor_id for p in poses]},
        group_names={"different-group"},  # filter -> nothing matches
        include_all_cameras=True,
    )
    # Only the all_cameras frame got rendered; no group output.
    assert "all_cameras" in outputs
    assert len(outputs) == 1


def test_render_camera_placement_sequence_skips_empty_member_groups(tmp_path):
    """A group whose members aren't in the pose dict produces an
    empty subset and triggers the ``if not members: continue``
    branch (line 1098)."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    out_root = tmp_path / "out"
    outputs = render_camera_placement_sequence(
        poses, output_dir=str(out_root),
        cams_by_group={
            "ghost-group": ["NoSuchCamera"],  # all members filtered out
            "real-group": [poses[0].sensor_id],
        },
        include_all_cameras=False,
    )
    # ghost-group skipped; only real-group rendered.
    assert "ghost-group" not in outputs
    assert "real-group" in outputs


def test_render_camera_placement_sequence_fallback_when_no_groups_written(tmp_path):
    """``include_all_cameras=False`` + zero groups written hits the
    fallback branch (lines 1123-1143) that still emits one frame so
    callers always get *something*."""
    calib = tmp_path / "calib.json"
    _two_pose_calibration(calib)
    poses = load_camera_poses_from_calibration(str(calib))
    out_root = tmp_path / "out"
    outputs = render_camera_placement_sequence(
        poses, output_dir=str(out_root),
        cams_by_group=None,  # no groups -> nothing to filter on
        include_all_cameras=False,
    )
    assert "all_cameras" in outputs
    assert outputs["all_cameras"].is_file()


# ============================================================
# Private-helper coverage (merged from test_plotter_helpers_coverage.py)
# ============================================================

# ---------------------------------------------------------------------------
# _set_equal_axes — zero-span fallback (line 157)
# ---------------------------------------------------------------------------


def test_set_equal_axes_falls_back_when_max_span_near_zero():
    """When every point coincides (max_span < 1e-3) the helper
    substitutes a default span of 1.0 so the axes-limits math doesn't
    collapse to zero."""
    fig = plt.figure()
    ax = fig.add_subplot(111, projection="3d")
    try:
        pts = np.zeros((3, 3))  # identical points -> span=0
        plotter._set_equal_axes(ax, pts, padding_ratio=0.1, min_padding=0.5)
        x_lo, x_hi = ax.get_xlim()
        # Half-span (0.5 * 1.0) + padding (0.5) = 1.0 each side.
        assert x_hi - x_lo == pytest.approx(2.0)
    finally:
        plt.close(fig)


# ---------------------------------------------------------------------------
# _auto_3d_view — single-camera early-return + invalid-forward branch
# ---------------------------------------------------------------------------


def _make_pose(*, sensor_id, position, rotation=None):
    return CameraPose(
        sensor_id=sensor_id,
        position_xyz=np.asarray(position, dtype=np.float64),
        rotation_c2w=(rotation if rotation is not None else np.eye(3)),
        intrinsic_matrix=np.eye(3, dtype=np.float64),
        image_size=(1920, 1080),
    )


def test_auto_3d_view_returns_defaults_for_single_camera():
    poses = [_make_pose(sensor_id="X", position=(0, 0, 0))]
    elev, azim = plotter._auto_3d_view(poses)
    assert elev == pytest.approx(30.0)
    assert azim == pytest.approx(-60.0)


def test_auto_3d_view_handles_zero_forward_vectors():
    """Pose with rotation that yields a zero-XY forward (camera looks
    straight down) -> the ``valid`` mask is empty for that pose, so
    the normalize-branch ``forward_xy[valid] = ...`` (line 185) is
    skipped — covered by the surrounding ``np.any(valid)`` guard."""
    # Looking straight down (+z axis world == -z axis camera):
    #   rotation maps cam +z -> world -z. So forward_world = R @ [0,0,1] = [0,0,-1]
    #   forward_xy = [0, 0] -> norm = 0 -> mask is False.
    R_down = np.array([
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, -1],
    ], dtype=np.float64)
    poses = [
        _make_pose(sensor_id="A", position=(0, 0, 0), rotation=R_down),
        _make_pose(sensor_id="B", position=(10, 0, 0), rotation=R_down),
    ]
    elev, azim = plotter._auto_3d_view(poses)
    # No raise; returns numeric values.
    assert isinstance(elev, float)
    assert isinstance(azim, float)


# ---------------------------------------------------------------------------
# _compute_world_content_bounds — no-polygon fallback (lines 270, 277)
# ---------------------------------------------------------------------------


def test_compute_world_content_bounds_falls_back_to_camera_positions():
    """When ``polygon_by_id`` has only ``None`` values, the helper
    has no polygons to bound and falls back to
    ``_camera_positions_bounds`` over the camera positions alone."""
    poses = [
        _make_pose(sensor_id="A", position=(0, 0, 0)),
        _make_pose(sensor_id="B", position=(10, 5, 0)),
    ]
    out = plotter._compute_world_content_bounds(
        poses,
        polygon_by_id={"A": None, "B": None},
        padding_ratio=0.1, min_padding=1.0,
    )
    # Bounds include camera positions + at least min_padding.
    min_x, min_y, max_x, max_y = out
    assert min_x < 0
    assert max_x > 10
    assert max_y > 5


# ---------------------------------------------------------------------------
# _sensor_world_to_map_params — happy + missing cases (lines 532)
# ---------------------------------------------------------------------------


def test_sensor_world_to_map_params_returns_none_for_empty_calibration():
    assert plotter._sensor_world_to_map_params(None) == (None, None)
    assert plotter._sensor_world_to_map_params({}) == (None, None)


def test_sensor_world_to_map_params_returns_first_with_translation_and_scale():
    """Iterates sensors and returns the first ``(translation, scale)``
    pair found (line 532 is the early-return on the first match)."""
    calib = {"sensors": [
        {"id": "A"},
        {"id": "B", "translationToGlobalCoordinates": {"x": 1.5, "y": 2.5},
          "scaleFactor": 4.0},
        {"id": "C", "translationToGlobalCoordinates": {"x": 9.9},
          "scaleFactor": 99.0},
    ]}
    translation, scale = plotter._sensor_world_to_map_params(calib)
    assert translation == {"x": 1.5, "y": 2.5}
    assert scale == pytest.approx(4.0)


# ---------------------------------------------------------------------------
# _world_to_map_xy — direct formula check (line 543 — body)
# ---------------------------------------------------------------------------


def test_world_to_map_xy_applies_translation_scale_and_y_flip():
    translation = {"x": 100.0, "y": 50.0}
    out = plotter._world_to_map_xy(
        x=2.0, y=3.0, translation=translation, scale=10.0, map_height=1080,
    )
    # x = scale * (x_world + tx) = 10 * (2 + 100) = 1020
    # y = (H-1) - scale * (y_world + ty) = 1079 - 10*(3+50) = 549
    assert out[0] == pytest.approx(1020.0)
    assert out[1] == pytest.approx(549.0)


# ---------------------------------------------------------------------------
# _resolve_fov_clip_bounds — fallback paths (456, 462, 479, 482-483, 508-518)
# ---------------------------------------------------------------------------


def _write_calib(path, sensors):
    path.write_text(json.dumps({"sensors": sensors}))


def test_resolve_fov_clip_bounds_returns_none_for_empty_calibration():
    """Empty / None calibration -> early-return None."""
    poses = [_make_pose(sensor_id="X", position=(0, 0, 0))]
    assert plotter._resolve_fov_clip_bounds(
        camera_poses=poses, sensors_by_id={},
        calibration_data=None, map_width=None, map_height=None,
    ) is None
    assert plotter._resolve_fov_clip_bounds(
        camera_poses=poses, sensors_by_id={},
        calibration_data={"sensors": []},
        map_width=None, map_height=None,
    ) is None


def test_resolve_fov_clip_bounds_with_invalid_dimensions_falls_through(tmp_path):
    """Sensors whose ``group.dimensions`` is the wrong shape OR
    non-numeric trigger the ``continue`` branches (lines 479 + 482-483).
    With no usable group bounds and no FOV polygons, the function
    falls through to the map-pixel branch — which returns None because
    no ``translationToGlobalCoordinates`` / ``scaleFactor`` is set
    (line 516)."""
    # The upstream calibration loader requires "origin" on grouped
    # sensors; provide it. We build poses + sensors_by_id directly
    # so we don't depend on the loader's stricter group schema.
    sensors = [{
        "id": "Camera_01",
        "group": {"name": "g1", "type": "bev",
                   # Wrong shape (3 entries instead of 4):
                   "dimensions": [0, 0, 10]},
    }]
    poses = [_make_pose(sensor_id="Camera_01", position=(0, 0, 0))]
    out = plotter._resolve_fov_clip_bounds(
        camera_poses=poses, sensors_by_id={"Camera_01": sensors[0]},
        calibration_data={"sensors": sensors},
        map_width=None, map_height=None,
    )
    assert out is None


def test_resolve_fov_clip_bounds_with_non_numeric_dimensions_falls_through():
    """Dimensions that aren't float-coercible trigger the
    ``except (TypeError, ValueError): continue`` branch (482-483)."""
    sensors = [{
        "id": "Camera_01",
        "group": {"name": "g1", "type": "bev",
                   "dimensions": ["a", "b", "c", "d"]},
    }]
    poses = [_make_pose(sensor_id="Camera_01", position=(0, 0, 0))]
    out = plotter._resolve_fov_clip_bounds(
        camera_poses=poses, sensors_by_id={"Camera_01": sensors[0]},
        calibration_data={"sensors": sensors},
        map_width=None, map_height=None,
    )
    assert out is None


def test_resolve_fov_clip_bounds_falls_back_to_candidate_sensors_lookup(tmp_path):
    """When the requested sensor isn't in ``sensors_by_id``, the
    helper falls back to scanning ``calibration_data['sensors']``
    by id (lines 456-460), then again to using the full sensor list
    if even that fails (line 462)."""
    sensors = [{
        "id": "Camera_01",
        "intrinsicMatrix": [[1000, 0, 960], [0, 1000, 540], [0, 0, 1]],
        "extrinsicMatrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 1]],
        "attributes": [{"name": "fieldOfViewPolygon",
                         "value": "POLYGON((0 0, 5 0, 5 5, 0 5, 0 0))"}],
    }]
    calib_path = tmp_path / "calib.json"
    _write_calib(calib_path, sensors)
    poses = load_camera_poses_from_calibration(str(calib_path))
    # Empty sensors_by_id forces the fallback scan.
    out = plotter._resolve_fov_clip_bounds(
        camera_poses=poses, sensors_by_id={},
        calibration_data={"sensors": sensors},
        map_width=None, map_height=None,
    )
    # Returns the FOV polygon bounds from the calibration scan.
    assert out is not None
    assert out == (0.0, 0.0, 5.0, 5.0)


# ---------------------------------------------------------------------------
# Full render_camera_placement with map + translation/scale —
# drives _draw_bev_panel use_map branches (734-877) + _resolve_scene_bounds
# calibration branch (246-252).
# ---------------------------------------------------------------------------


def _make_tiny_map(path):
    """Tiny 256x256 black PNG written via PIL when available, or a
    1x1 transparent PNG bytestream as fallback."""
    try:
        from PIL import Image
        Image.new("RGB", (256, 256), color=(40, 40, 40)).save(path)
    except ImportError:
        path.write_bytes(
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
            b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
            b"\x00\x00\x00\rIDATx\x9cc\xf8\xcf\xc0\xf0\x1f\x00\x05"
            b"\x00\x01\xff\xa3\x9c\x9a\xf0\x00\x00\x00\x00IEND\xaeB`\x82"
        )


def _write_map_calibration(path, *, with_fov=True):
    """Calibration whose sensors carry ``translationToGlobalCoordinates``
    + ``scaleFactor`` so ``_sensor_world_to_map_params`` returns
    non-None and the ``use_map`` branches in ``_draw_bev_panel`` fire."""
    fov = (
        [{"name": "fieldOfViewPolygon",
          "value": "POLYGON((0 0, 4 0, 4 3, 0 3, 0 0))"}]
        if with_fov else []
    )
    path.write_text(json.dumps({
        "sensors": [
            {
                "id": "Camera_01",
                "intrinsicMatrix": [[1000, 0, 960],
                                      [0, 1000, 540],
                                      [0, 0, 1]],
                "extrinsicMatrix": [[1, 0, 0, 1],
                                      [0, 1, 0, 1],
                                      [0, 0, 1, 2]],
                "translationToGlobalCoordinates": {"x": 5.0, "y": 5.0},
                "scaleFactor": 10.0,
                "attributes": fov + [
                    {"name": "frameWidth", "value": "1920"},
                    {"name": "frameHeight", "value": "1080"},
                ],
            },
            {
                "id": "Camera_02",
                "intrinsicMatrix": [[900, 0, 640],
                                      [0, 900, 360],
                                      [0, 0, 1]],
                "extrinsicMatrix": [[1, 0, 0, -2],
                                      [0, 1, 0, 1],
                                      [0, 0, 1, 2]],
                "translationToGlobalCoordinates": {"x": 5.0, "y": 5.0},
                "scaleFactor": 10.0,
                "attributes": fov + [
                    {"name": "frameWidth", "value": "1280"},
                    {"name": "frameHeight", "value": "720"},
                ],
            },
        ],
    }))


def test_render_camera_placement_with_map_drives_use_map_branches(tmp_path):
    """Full ``render_camera_placement`` call with a real map file +
    calibration having translation/scale — drives the ``use_map``
    branches in ``_draw_bev_panel`` (lines 734-877) and the
    calibration-based ``_resolve_scene_bounds`` branch (246-252)."""
    from spatialai_data_utils.visualization.camera_placement.plotter import (
        render_camera_placement as _render,
    )

    calib_path = tmp_path / "calib.json"
    _write_map_calibration(calib_path)
    map_path = tmp_path / "Top.png"
    _make_tiny_map(map_path)

    poses = load_camera_poses_from_calibration(str(calib_path))
    # The calibration_data dict comes from json.load directly; reuse
    # ``load_camera_placement_context`` if available, else inline.
    calibration_data = json.loads(calib_path.read_text())

    out = tmp_path / "viz_map.png"
    fig, _ = _render(
        poses, output_path=out,
        calibration_data=calibration_data,
        map_file=str(map_path),
        close=True,
    )
    assert out.is_file()

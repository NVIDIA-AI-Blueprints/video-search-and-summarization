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

"""
Tests for the 3D bounding box visualization module.

Covers:
- spatialai_data_utils.visualization.box_3d
- spatialai_data_utils.visualization.draw_utils
- spatialai_data_utils.visualization.render (unit-level helpers)

All tests are self-contained — no external data files required.
"""

import json
import os
import shutil
import tempfile
from pathlib import Path

import cv2
import numpy as np
import pytest

from spatialai_data_utils.visualization.box_3d import (
    box3d_to_corners,
    draw_bbox3d_on_bev,
    draw_bbox3d_on_img,
    draw_bbox3d_multicam,
    draw_box3d_corners_on_img,
    draw_points3d_on_img,
)
from spatialai_data_utils.visualization.draw_utils import (
    build_world2img_from_calib,
    draw_camera_tag,
    generate_bbox_text,
    load_image,
    save_viz,
)
from spatialai_data_utils.core.boxes.box_3d import recenter_boxes, unrecenter_boxes
from spatialai_data_utils.loaders.calibration import (
    apply_recentering,
    get_calib_dict,
    get_calib_dict_from_cam_data,
    load_calib_into_dict,
    resolve_scene_calib,
)
from spatialai_data_utils.core.geometry.projection import project_bev_objects_bbox_in_image
from spatialai_data_utils.datasets.frame_paths import (
    frame_paths_from_pkl_info,
    index_pkl_by_frame,
    resolve_frame_root,
)
from spatialai_data_utils.visualization.render import (
    draw_bev_objects_bbox_in_image,
    process_frame_gt_json_aicity,
    process_frame_nvschema,
    visualize_3dbbox,
    visualize_nvschema,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_image(h=480, w=640):
    """Create a blank BGR test image."""
    return np.zeros((h, w, 3), dtype=np.uint8)


def _make_world2img():
    """Simple pinhole-like 4x4 world-to-image matrix (identity extrinsic)."""
    m = np.eye(4)
    m[0, 0] = 500.0
    m[1, 1] = 500.0
    m[0, 2] = 320.0
    m[1, 2] = 240.0
    return m


def _make_calib_dict(cam_names=("cam0",)):
    """Build a calib_dict in project convention for the given camera names."""
    d = {}
    for name in cam_names:
        d[name] = {
            "intrinsic_matrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
            "w2c_matrix": np.eye(4).tolist(),
        }
    return d


def _make_boxes(n=3, z=20.0):
    """Return ``(N, 9)`` boxes at depth *z* with small random yaw."""
    rng = np.random.RandomState(42)
    boxes = np.zeros((n, 9))
    boxes[:, 0] = rng.uniform(-2, 2, n)
    boxes[:, 1] = rng.uniform(-2, 2, n)
    boxes[:, 2] = z
    boxes[:, 3] = 1.5  # w
    boxes[:, 4] = 3.0  # l
    boxes[:, 5] = 1.8  # h
    # roll / pitch left at 0; yaw at index 8 (NVSchema 9-DoF layout).
    boxes[:, 8] = rng.uniform(-0.3, 0.3, n)
    return boxes


def _make_nvschema_dets(n=3):
    """Synthetic raw NVSchema detection dicts (native on-disk format)."""
    dets = []
    for i in range(n):
        conf = 0.5 + 0.1 * i
        dets.append({
            "id": str(i + 1),            # NVSchema: id is a string
            "type": "Person",
            "confidence": conf,
            "coordinate": {"x": float(i), "y": 0.0, "z": 20.0},
            "bbox3d": {
                "coordinates": [
                    float(i), 0.0, 20.0,   # x, y, z
                    1.5, 3.0, 1.8,          # w, l, h
                    0.0, 0.0, 0.1 * i,      # pitch, roll, yaw
                ],
                "embedding": [{}],
                "confidence": conf,
            },
        })
    return dets


def _make_gt_dicts(n=2):
    """Synthetic ground-truth dicts matching process_bbox3d_gt expectations."""
    gts = []
    for i in range(n):
        gts.append({
            "object id": 100 + i,
            "3d location": [float(i), 1.0, 20.0],
            "3d bounding box scale": [1.0, 2.0, 1.5],
            "3d bounding box rotation": [0.0, 0.0, 0.0],
        })
    return gts


@pytest.fixture()
def tmp_dir():
    d = tempfile.mkdtemp()
    yield d
    shutil.rmtree(d)


# =====================================================================
# Tests for box_3d.py
# =====================================================================

class TestBox3dToCorners:
    """Tests for the 3D box to 8-corner conversion (9-DoF canonical layout)."""

    def test_output_shape_single(self):
        """A single 9-DoF box yields corners of shape (1, 8, 3)."""
        box = np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box)
        assert corners.shape == (1, 8, 3)

    def test_output_shape_batch(self):
        """A batch of N 9-DoF boxes yields corners of shape (N, 8, 3)."""
        boxes = np.zeros((5, 9))
        corners = box3d_to_corners(boxes)
        assert corners.shape == (5, 8, 3)

    def test_1d_input(self):
        """A 1-D 9-DoF input is auto-promoted to a batch of one box."""
        box = np.array([0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0])
        corners = box3d_to_corners(box)
        assert corners.shape == (1, 8, 3)

    def test_center_at_origin_no_rotation(self):
        """Zero-center, zero-rotation box has corners centred on origin with expected extents."""
        box = np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box)
        assert np.allclose(corners[0].mean(axis=0), [0, 0, 0], atol=1e-10)
        assert np.isclose(np.ptp(corners[0, :, 0]), 2.0)
        assert np.isclose(np.ptp(corners[0, :, 1]), 4.0)
        assert np.isclose(np.ptp(corners[0, :, 2]), 1.0)

    def test_translation(self):
        """Box centre position is applied to the corner mean."""
        box = np.array([[10, 20, 30, 2, 4, 1, 0.0, 0.0, 0.0]])
        corners = box3d_to_corners(box)
        assert np.allclose(corners[0].mean(axis=0), [10, 20, 30], atol=1e-10)

    def test_rotation_90deg(self):
        """A 90-degree yaw swaps X and Y extents while preserving Z."""
        box_0 = np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0]])
        box_90 = np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, np.pi / 2]])
        c0 = box3d_to_corners(box_0)
        c90 = box3d_to_corners(box_90)
        assert np.isclose(np.ptp(c90[0, :, 0]), 4.0, atol=1e-6)
        assert np.isclose(np.ptp(c90[0, :, 1]), 2.0, atol=1e-6)
        assert np.isclose(np.ptp(c0[0, :, 2]), np.ptp(c90[0, :, 2]))

    def test_extra_columns_ignored(self):
        """Columns beyond index 8 (e.g. velocity) are ignored."""
        box = np.array([[0, 0, 0, 2, 4, 1, 0.0, 0.0, 0.0, 99.0]])
        np.testing.assert_allclose(
            box3d_to_corners(box), box3d_to_corners(box[:, :9]),
        )

    def test_rejects_7_dof_input(self):
        """Legacy 7-DoF inputs must raise ValueError (no implicit padding)."""
        with pytest.raises(ValueError, match="requires 9-DoF"):
            box3d_to_corners(np.array([[0, 0, 0, 2, 4, 1, 0.3]]))


class TestPlotRect3dOnImg:
    """Tests for drawing 3D wireframe boxes from pre-projected 2D corners."""

    def test_basic_draw(self):
        """Drawing a single box produces a non-empty output image."""
        img = _make_image()
        corners = np.array([[[100, 100], [200, 100], [200, 200], [100, 200],
                             [100, 50], [200, 50], [200, 150], [100, 150]]], dtype=np.float64)
        result = draw_box3d_corners_on_img(img, 1, corners)
        assert result.shape == img.shape
        assert result.dtype == np.uint8
        assert result.sum() > 0

    def test_with_text(self):
        """Passing text labels draws them on the image alongside the box."""
        img = _make_image()
        corners = np.array([[[100, 100], [200, 100], [200, 200], [100, 200],
                             [100, 50], [200, 50], [200, 150], [100, 150]]], dtype=np.float64)
        result = draw_box3d_corners_on_img(img, 1, corners, box_texts=["hello"])
        assert result.sum() > 0

    def test_no_heading(self):
        """Disabling heading shading still draws the wireframe."""
        img = _make_image()
        corners = np.array([[[100, 100], [200, 100], [200, 200], [100, 200],
                             [100, 50], [200, 50], [200, 150], [100, 150]]], dtype=np.float64)
        result = draw_box3d_corners_on_img(img, 1, corners, shade_heading=False)
        assert result.sum() > 0

    def test_per_box_color(self):
        """A per-box list of colours draws each box in its own colour."""
        img = _make_image()
        corners = np.zeros((2, 8, 2))
        corners[0] = [[100, 100], [200, 100], [200, 200], [100, 200],
                       [100, 50], [200, 50], [200, 150], [100, 150]]
        corners[1] = corners[0] + 50
        colors = [(255, 0, 0), (0, 0, 255)]
        result = draw_box3d_corners_on_img(img, 2, corners, color=colors)
        assert result.sum() > 0


class TestDrawBbox3dOnImg:
    """Tests for projecting and drawing 3D boxes onto a single camera image."""

    def test_with_world2img(self):
        """Passing a 4x4 world-to-image matrix renders boxes correctly."""
        img = _make_image()
        boxes = _make_boxes(2)
        result = draw_bbox3d_on_img(boxes, img, world2img=_make_world2img())
        assert result.shape == img.shape
        assert result.dtype == np.uint8

    def test_with_calib_info(self):
        """Passing a calib_info (intrinsic + w2c) produces the same-shape output."""
        img = _make_image()
        boxes = _make_boxes(2)
        cd = _make_calib_dict(["cam0"])["cam0"]
        calib = {
            "intrinsic_matrix": np.array(cd["intrinsic_matrix"]),
            "w2c_matrix": np.array(cd["w2c_matrix"]),
        }
        result = draw_bbox3d_on_img(boxes, img, calib_info=calib)
        assert result.shape == img.shape

    def test_error_both_args(self):
        """Passing both world2img and calib_info raises ValueError."""
        img = _make_image()
        boxes = _make_boxes(1)
        with pytest.raises(ValueError, match="only one"):
            draw_bbox3d_on_img(
                boxes, img, world2img=_make_world2img(),
                calib_info={"intrinsic_matrix": np.eye(3),
                            "w2c_matrix": np.eye(4)},
            )

    def test_error_no_args(self):
        """Passing neither world2img nor calib_info raises ValueError."""
        img = _make_image()
        boxes = _make_boxes(1)
        with pytest.raises(ValueError, match="Exactly one"):
            draw_bbox3d_on_img(boxes, img)

    def test_empty_boxes(self):
        """An empty box array returns the original image unchanged."""
        img = _make_image()
        empty = np.empty((0, 9))
        result = draw_bbox3d_on_img(empty, img, world2img=_make_world2img())
        assert np.array_equal(result, img)

    def test_single_box_1d(self):
        """A 1-D (single-box) input is handled correctly."""
        img = _make_image()
        box = np.array([0, 0, 20, 2, 4, 1, 0.0, 0.0, 0.0])
        result = draw_bbox3d_on_img(box, img, world2img=_make_world2img())
        assert result.shape == img.shape

    def test_with_text_labels(self):
        """Per-box text labels are accepted and drawn."""
        img = _make_image()
        boxes = _make_boxes(2)
        result = draw_bbox3d_on_img(
            boxes, img, world2img=_make_world2img(),
            bboxes3d_text=["car 0.9", "truck 0.8"],
        )
        assert result.shape == img.shape

    def test_does_not_mutate_input(self):
        """The input image array is not modified in place."""
        img = _make_image()
        original = img.copy()
        boxes = _make_boxes(2)
        draw_bbox3d_on_img(boxes, img, world2img=_make_world2img())
        assert np.array_equal(img, original)


class TestDrawPoints3dOnImg:
    """Tests for projecting and drawing 3D points on a camera image."""

    def test_with_world2img(self):
        """Points are projected and rendered when given a world2img matrix."""
        img = _make_image()
        pts = np.array([[[0, 0, 20], [1, 1, 20]]], dtype=np.float64)
        result = draw_points3d_on_img(pts, img, world2img=_make_world2img())
        assert result.shape == img.shape
        assert result.sum() > 0

    def test_with_calib_info(self):
        """Points are projected and rendered when given a calib_info."""
        img = _make_image()
        pts = np.array([[[0, 0, 20]]], dtype=np.float64)
        cd = _make_calib_dict(["cam0"])["cam0"]
        calib = {
            "intrinsic_matrix": np.array(cd["intrinsic_matrix"]),
            "w2c_matrix": np.array(cd["w2c_matrix"]),
        }
        result = draw_points3d_on_img(pts, img, calib_info=calib)
        assert result.shape == img.shape

    def test_raises_when_both_calibration_sources_given(self):
        """Passing both world2img and calib_info raises ValueError."""
        img = _make_image()
        pts = np.array([[[0, 0, 20]]], dtype=np.float64)
        cd = _make_calib_dict(["cam0"])["cam0"]
        calib = {
            "intrinsic_matrix": np.array(cd["intrinsic_matrix"]),
            "w2c_matrix": np.array(cd["w2c_matrix"]),
        }
        with pytest.raises(ValueError, match="only one"):
            draw_points3d_on_img(
                pts, img, world2img=_make_world2img(), calib_info=calib,
            )

    def test_raises_when_no_calibration_source_given(self):
        """Passing neither world2img nor calib_info raises ValueError."""
        img = _make_image()
        pts = np.array([[[0, 0, 20]]], dtype=np.float64)
        with pytest.raises(ValueError, match="one of"):
            draw_points3d_on_img(pts, img)

    def test_per_object_color_list(self):
        """A list of BGR tuples (one per object) is accepted."""
        img = _make_image()
        pts = np.array(
            [[[0.0, 0.0, 20.0]], [[1.0, 0.0, 20.0]]], dtype=np.float64,
        )
        colors = [(255, 0, 0), (0, 0, 255)]
        result = draw_points3d_on_img(
            pts, img, world2img=_make_world2img(), color=colors,
        )
        assert result.shape == img.shape
        assert result.sum() > 0


class TestDrawBbox3dOnBev:
    """Tests for drawing 3D boxes on a bird's-eye-view canvas."""

    def test_square_canvas(self):
        """A scalar size argument produces a square canvas."""
        boxes = _make_boxes(3, z=0)
        bev = draw_bbox3d_on_bev(boxes, 400)
        assert bev.shape == (400, 400, 3)
        assert bev.dtype == np.uint8

    def test_rectangular_canvas(self):
        """A (h, w) tuple produces a rectangular canvas."""
        boxes = _make_boxes(2, z=0)
        bev = draw_bbox3d_on_bev(boxes, (300, 500))
        assert bev.shape == (300, 500, 3)

    def test_empty_boxes(self):
        """Empty box input still draws the range-circle background."""
        bev = draw_bbox3d_on_bev(np.empty((0, 9)), 200)
        assert bev.shape == (200, 200, 3)
        assert bev.sum() > 0  # range circles still drawn

    def test_per_box_color(self):
        """A per-box list of colours is accepted."""
        boxes = _make_boxes(2, z=0)
        colors = [(255, 0, 0), (0, 255, 0)]
        bev = draw_bbox3d_on_bev(boxes, 200, color=colors)
        assert bev.shape == (200, 200, 3)


class TestDrawBbox3dMulticam:
    """Tests for the multi-camera composite drawing helper."""

    def test_with_world2imgs(self):
        """A list of world2img matrices (one per camera) renders all views."""
        boxes = _make_boxes(2)
        imgs = [_make_image() for _ in range(4)]
        w2i = [_make_world2img() for _ in range(4)]
        result = draw_bbox3d_multicam(boxes, imgs, world2imgs=w2i)
        assert result.ndim == 3
        assert result.shape[2] == 3

    def test_with_calib_info_list(self):
        """A list of calib_info dicts (one per camera) renders all views."""
        boxes = _make_boxes(2)
        imgs = [_make_image() for _ in range(4)]
        cd = _make_calib_dict(["cam0"])["cam0"]
        calib = {
            "intrinsic_matrix": np.array(cd["intrinsic_matrix"]),
            "w2c_matrix": np.array(cd["w2c_matrix"]),
        }
        result = draw_bbox3d_multicam(boxes, imgs, calib_info_list=[calib] * 4)
        assert result.ndim == 3

    def test_odd_camera_count(self):
        """Odd number of cameras is handled (no grid symmetry required)."""
        boxes = _make_boxes(1)
        imgs = [_make_image() for _ in range(3)]
        w2i = [_make_world2img() for _ in range(3)]
        result = draw_bbox3d_multicam(boxes, imgs, world2imgs=w2i)
        assert result.ndim == 3

    def test_error_both_args(self):
        """Passing both world2imgs and calib_info_list raises ValueError."""
        boxes = _make_boxes(1)
        imgs = [_make_image()]
        w2i = [_make_world2img()]
        cd = _make_calib_dict(["cam0"])["cam0"]
        calib = {
            "intrinsic_matrix": np.array(cd["intrinsic_matrix"]),
            "w2c_matrix": np.array(cd["w2c_matrix"]),
        }
        with pytest.raises(ValueError):
            draw_bbox3d_multicam(boxes, imgs, world2imgs=w2i, calib_info_list=[calib])


# =====================================================================
# Tests for draw_utils.py
# =====================================================================

class TestDrawCameraTag:
    """Tests for the camera-name badge overlay helper."""

    def test_modifies_image(self):
        """Drawing a tag increases pixel sum on a blank image."""
        img = _make_image()
        original_sum = img.sum()
        draw_camera_tag(img, "Camera_01")
        assert img.sum() > original_sum

    def test_returns_same_array(self):
        """The function returns the same array it was given (in-place)."""
        img = _make_image()
        result = draw_camera_tag(img, "cam_name")
        assert result is img

    def test_timestamp_none_is_pixel_identical_to_no_timestamp(self):
        """``timestamp=None`` must reproduce the original single-line badge.

        Backward-compat guard: existing callers in ``render.py`` and
        the test suite continue to pass only ``cam_name``.  Adding the
        new keyword should not shift a single pixel for them.
        """
        img_a = _make_image()
        img_b = _make_image()
        draw_camera_tag(img_a, "Camera_05")
        draw_camera_tag(img_b, "Camera_05", timestamp=None)
        np.testing.assert_array_equal(img_a, img_b)

    def test_timestamp_renders_extra_pixels(self):
        """Passing a non-empty timestamp must paint **strictly more**
        pixels than the single-line badge.

        The two-line layout extends the bg rectangle vertically (and
        possibly horizontally if the timestamp is wider), so the
        diff between the two renders is non-empty.

        The samples-below-badge geometry is computed lazily from the
        current ``draw_utils`` layout constants instead of hard-coded
        pixel coordinates — that way the assertion stays meaningful
        if the badge is later thinned / thickened (it just needs to
        sample a band that's *below* the single-line bottom but
        *inside* the two-line bottom).
        """
        from spatialai_data_utils.visualization import draw_utils

        img_no_ts = _make_image()
        img_with_ts = _make_image()
        draw_camera_tag(img_no_ts, "Camera_05")
        draw_camera_tag(img_with_ts, "Camera_05",
                        timestamp="2025-04-14T00:36:45.009Z")
        # Recover the single-line badge's bottom-y from the rendered
        # output: it's the lowest row that has any non-zero pixel.
        nz_y = np.where(img_no_ts.any(axis=2).any(axis=1))[0]
        assert nz_y.size, "single-line badge must produce some pixels"
        single_bottom = int(nz_y.max()) + 1
        # Sample a thin band right below it.  The two-line badge is
        # taller than the single-line one (a constant is the timestamp
        # font size + the inter-line gap), so this band lives inside
        # the with-timestamp rectangle.
        sample_h = max(4, draw_utils._CAM_TAG_BOTTOM_PAD // 2)
        below_no_ts = img_no_ts[single_bottom:single_bottom + sample_h, :100]
        below_with_ts = img_with_ts[single_bottom:single_bottom + sample_h, :100]
        assert not np.array_equal(below_no_ts, below_with_ts)

    def test_empty_timestamp_treated_as_none(self):
        """``timestamp=""`` collapses to the single-line layout.

        NVSchema rows occasionally arrive with empty-string timestamps;
        the helper should not paint a phantom subtitle line for them.
        """
        img_empty = _make_image()
        img_none = _make_image()
        draw_camera_tag(img_empty, "Camera_05", timestamp="")
        draw_camera_tag(img_none, "Camera_05", timestamp=None)
        np.testing.assert_array_equal(img_empty, img_none)

    def test_bg_alpha_default_blends_with_image(self):
        """Default ``bg_alpha`` produces a translucent badge background.

        At ``bg_alpha=0.6`` the rectangle interior is
        ``0.6 * bg_color + 0.4 * underlying`` — visibly different from
        both an opaque ``bg_color`` fill *and* the original underlying
        pixels.  Picking a non-zero, non-bg pixel value for the source
        image lets the test assert both inequalities crisply.
        """
        # Underlying pixels are pure red so the blend with grey
        # bg_color (66, 66, 66) lands at a deterministic value.
        underlying = np.full((1080, 1920, 3), 200, dtype=np.uint8)
        underlying[:, :, 2] = 0  # pure cyan-ish, just != bg
        img = underlying.copy()
        draw_camera_tag(img, "Camera_05")  # default bg_alpha=0.6

        # Sample a point well inside the badge (avoid the text region).
        y_inside, x_inside = 5, 5
        rect_pixel = img[y_inside, x_inside]

        # 1) Not the original pixel value (something was blended in).
        assert not np.array_equal(rect_pixel, underlying[y_inside, x_inside])

        # 2) Not the solid bg_color either — meaning the underlying
        #    image still shows through, which is the whole point of
        #    making the badge "more transparent".
        assert not np.array_equal(rect_pixel, np.array([66, 66, 66]))

    def test_bg_alpha_one_matches_legacy_opaque_fill(self):
        """``bg_alpha=1.0`` reproduces the pre-alpha (fully-opaque) badge.

        Backward-compat escape hatch: callers that explicitly request
        ``bg_alpha=1`` should get an opaque grey rectangle exactly as
        the original implementation produced — useful when the badge
        sits over noisy / busy regions and readability matters more
        than scene visibility.
        """
        underlying = np.full((1080, 1920, 3), 200, dtype=np.uint8)
        img = underlying.copy()
        draw_camera_tag(img, "Camera_05", bg_alpha=1.0)
        # Sample a non-text interior pixel; it must be pure bg_color.
        np.testing.assert_array_equal(img[5, 5], np.array([66, 66, 66]))

    def test_bg_alpha_zero_skips_rectangle(self):
        """``bg_alpha=0`` renders text-only, leaving non-text pixels untouched.

        Useful for callers that want a transparent badge for headless
        compositing pipelines, where the rectangle would interfere
        with downstream alpha-channel processing.
        """
        underlying = np.full((1080, 1920, 3), 123, dtype=np.uint8)
        img = underlying.copy()
        draw_camera_tag(img, "Camera_05", bg_alpha=0.0)
        # Background-region pixel (outside any glyph stroke) should be
        # untouched: ``cv2.putText`` only paints glyph pixels.  Sample
        # the very top-left, well above the text baseline.
        np.testing.assert_array_equal(img[0, 0], np.array([123, 123, 123]))


class TestGenerateBboxText:
    """Tests for the per-box label string generator."""

    def test_basic(self):
        """Labels, IDs and scores are formatted as 'ClassName(id) score'."""
        labels = np.array([0, 1, 0])
        scores = np.array([0.9, 0.8, 0.7])
        ids = np.array([1, 2, 3])
        texts = generate_bbox_text(3, labels, scores, ids, ["car", "truck"])
        assert len(texts) == 3
        assert texts[0] == "car(1) 0.90"
        assert texts[1] == "truck(2) 0.80"
        assert texts[2] == "car(3) 0.70"

    def test_out_of_range_label(self):
        """A label index outside class_names falls back to 'unknown'."""
        labels = np.array([5])
        scores = np.array([0.5])
        ids = np.array([1])
        texts = generate_bbox_text(1, labels, scores, ids, ["car"])
        assert "unknown" in texts[0]

    def test_empty(self):
        """Zero boxes produces an empty list."""
        texts = generate_bbox_text(0, np.array([]), np.array([]), np.array([]), [])
        assert texts == []


class TestLoadImage:
    """Tests for the image loading helper."""

    def test_load_valid_image(self, tmp_dir):
        """An existing image file is loaded with its original shape."""
        path = os.path.join(tmp_dir, "test.jpg")
        cv2.imwrite(path, _make_image(100, 100))
        img = load_image(path)
        assert img is not None
        assert img.shape[:2] == (100, 100)

    def test_load_missing_image(self):
        """A non-existent path returns None (logs a warning)."""
        img = load_image("/nonexistent/path.jpg")
        assert img is None


class TestSaveViz:
    """Tests for saving annotated images under cam-name subdirectories."""

    def test_creates_file(self, tmp_dir):
        """Saving writes the file to vis_dir/cam_name/basename.jpg."""
        img = _make_image(50, 50)
        save_viz(img, tmp_dir, "cam0", "/some/path/frame_001.jpg")
        out = os.path.join(tmp_dir, "cam0", "frame_001.jpg")
        assert os.path.isfile(out)
        loaded = cv2.imread(out)
        assert loaded is not None
        assert loaded.shape == (50, 50, 3)

    def test_h5_basename(self, tmp_dir):
        """H5 tuple frame_path derives basename from the inner dataset key."""
        img = _make_image(50, 50)
        save_viz(img, tmp_dir, "cam1", ("/data/file.h5", "rgb/rgb_00001.jpg"), h5_file=True)
        out = os.path.join(tmp_dir, "cam1", "rgb_00001.jpg")
        assert os.path.isfile(out)


class TestBuildWorld2imgFromCalib:
    """Tests for constructing a 4x4 world-to-image matrix from a calib dict."""

    def test_identity_extrinsic(self):
        """Identity extrinsic produces intrinsic-only projection."""
        calib = _make_calib_dict(["cam0"])
        w2i = build_world2img_from_calib(calib, "cam0")
        assert w2i.shape == (4, 4)
        expected = np.eye(4)
        expected[:3, :3] = np.array(calib["cam0"]["intrinsic_matrix"])
        np.testing.assert_allclose(w2i, expected)

    def test_non_identity_extrinsic(self):
        """A rotated extrinsic is composed with the intrinsic (K @ w2c)."""
        calib = _make_calib_dict(["cam0"])
        rot = np.eye(4)
        rot[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]
        calib["cam0"]["w2c_matrix"] = rot.tolist()
        w2i = build_world2img_from_calib(calib, "cam0")
        intrin = np.eye(4)
        intrin[:3, :3] = np.array(calib["cam0"]["intrinsic_matrix"])
        np.testing.assert_allclose(w2i, intrin @ rot)


# =====================================================================
# Tests for render.py helpers
# =====================================================================

class TestApplyRecentering:
    """Tests for shifting calibration extrinsics by a camera-group origin."""

    def test_shifts_origin(self):
        """After recentering, the group origin maps to (0, 0) in the new frame."""
        calib = {
            "group_a": {
                "cam0": {
                    "intrinsic_matrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                    "w2c_matrix": np.eye(4).tolist(),
                },
            },
        }
        group_area = {
            "group_a": {"origin": [10.0, 20.0], "dimensions": [100, 100]},
        }
        result = apply_recentering(calib, group_area)
        w2c_new = np.array(result["group_a"]["cam0"]["w2c_matrix"])
        c2w_new = np.linalg.inv(w2c_new)
        assert np.isclose(c2w_new[0, 3], -10.0)
        assert np.isclose(c2w_new[1, 3], -20.0)

    def test_skips_none_group(self):
        """Groups whose metadata is None are left untouched."""
        calib = {
            "group_a": {
                "cam0": {
                    "intrinsic_matrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                    "w2c_matrix": np.eye(4).tolist(),
                },
            },
        }
        group_area = {"group_a": None}
        result = apply_recentering(calib, group_area)
        w2c = np.array(result["group_a"]["cam0"]["w2c_matrix"])
        np.testing.assert_allclose(w2c, np.eye(4))

    def test_w2p_is_rebuilt_from_new_w2c(self):
        """After recentering, w2p must equal intrinsic_4x4 @ new_w2c (not stale)."""
        K = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1]], dtype=np.float64)
        w2c_old = np.eye(4)
        intrin_4x4 = np.eye(4)
        intrin_4x4[:3, :3] = K

        calib = {
            "group_a": {
                "cam0": {
                    "intrinsic_matrix": K.tolist(),
                    "w2c_matrix": w2c_old.tolist(),
                    # Deliberately start with a *stale* w2p — a zero matrix —
                    # so any code path that fails to rebuild it would leave
                    # this garbage in place and trip the assertion below.
                    "w2p_matrix": np.zeros((4, 4)).tolist(),
                },
            },
        }
        group_area = {"group_a": {"origin": [10.0, 20.0]}}
        result = apply_recentering(calib, group_area)

        new_w2c = np.array(result["group_a"]["cam0"]["w2c_matrix"])
        new_w2p = np.array(result["group_a"]["cam0"]["w2p_matrix"])
        # 1. w2c actually moved.
        assert not np.allclose(new_w2c, w2c_old)
        # 2. w2p is the fresh composition (no stale zeros).
        np.testing.assert_allclose(new_w2p, intrin_4x4 @ new_w2c)


class TestProcessFrameNvschema:
    """Tests for the per-frame NVSchema rendering function."""

    def test_produces_output(self, tmp_dir):
        """End-to-end: write a test image, run process_frame, check output."""
        cam_name = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_000.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam_name])
        dets = {"sensor_bev": _make_nvschema_dets(2)}
        frame_paths = {cam_name: img_path}
        vis_dir = os.path.join(tmp_dir, "vis")

        process_frame_nvschema(dets, calib, frame_paths, vis_dir, conf_thresh=0.0)

        out = os.path.join(vis_dir, cam_name, "frame_000.jpg")
        assert os.path.isfile(out)
        saved = cv2.imread(out)
        assert saved is not None
        assert saved.shape == (480, 640, 3)


class TestProcessFrameGtJson:
    """Tests for the per-frame ground-truth JSON rendering function."""

    def test_produces_output(self, tmp_dir):
        """End-to-end: write a test image, run process_frame_gt_json_aicity, check output."""
        cam_name = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_001.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam_name])
        gt_frame = _make_gt_dicts(2)
        frame_paths = {cam_name: img_path}
        vis_dir = os.path.join(tmp_dir, "vis_gt")

        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_dir)

        out = os.path.join(vis_dir, cam_name, "frame_001.jpg")
        assert os.path.isfile(out)

    def test_empty_gt(self, tmp_dir):
        """Empty GT list should still produce an image (just with camera tag)."""
        cam_name = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_002.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam_name])
        frame_paths = {cam_name: img_path}
        vis_dir = os.path.join(tmp_dir, "vis_empty")

        process_frame_gt_json_aicity([], calib, frame_paths, vis_dir)

        out = os.path.join(vis_dir, cam_name, "frame_002.jpg")
        assert os.path.isfile(out)

    # ----- color_by kwarg -----

    def _mixed_type_gt(self):
        """Four GT dicts spanning 2 object types × 2 object ids per type."""
        out = []
        for i, (obj_type, obj_id) in enumerate([
            ("Person",      201),
            ("Person",      202),
            ("Transporter", 203),
            ("Transporter", 204),
        ]):
            out.append({
                "object type": obj_type,
                "object id": obj_id,
                "3d location": [float(i), 1.0, 20.0],
                "3d bounding box scale": [1.0, 2.0, 1.5],
                "3d bounding box rotation": [0.0, 0.0, 0.0],
            })
        return out

    def test_color_by_class_differs_from_track_id(self, tmp_dir):
        """class-mode and track_id-mode produce different renders.

        Mirror of the NVSchema ``draw_bev_objects_bbox_in_image`` test but for
        the gt_json_aicity path: 2 types × 2 ids gives 4 unique colours in
        track mode vs 2 unique colours in class mode.
        """
        cam = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_010.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam])
        gt_frame = self._mixed_type_gt()
        frame_paths = {cam: img_path}
        vis_track = os.path.join(tmp_dir, "vis_by_track")
        vis_class = os.path.join(tmp_dir, "vis_by_class")

        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_track,
                              color_by="track_id")
        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_class,
                              color_by="class")

        a = cv2.imread(os.path.join(vis_track, cam, "frame_010.jpg"))
        b = cv2.imread(os.path.join(vis_class, cam, "frame_010.jpg"))
        assert not np.array_equal(a, b)

    def test_color_by_default_is_track_id(self, tmp_dir):
        """Omitted kwarg matches explicit color_by='track_id' byte-for-byte."""
        cam = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_011.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam])
        gt_frame = self._mixed_type_gt()
        frame_paths = {cam: img_path}
        vis_default = os.path.join(tmp_dir, "vis_default")
        vis_track = os.path.join(tmp_dir, "vis_track")

        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_default)
        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_track,
                              color_by="track_id")

        a = cv2.imread(os.path.join(vis_default, cam, "frame_011.jpg"))
        b = cv2.imread(os.path.join(vis_track, cam, "frame_011.jpg"))
        np.testing.assert_array_equal(a, b)

    def test_color_by_invalid_raises(self, tmp_dir):
        """Unknown color_by values are rejected with a clear ValueError."""
        cam = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_012.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam])
        gt_frame = self._mixed_type_gt()
        frame_paths = {cam: img_path}
        vis_dir = os.path.join(tmp_dir, "vis_bad")

        with pytest.raises(ValueError, match="color_by must be one of"):
            process_frame_gt_json_aicity(
                gt_frame, calib, frame_paths, vis_dir, color_by="rainbow",
            )

    # ---- object_class_tag filter -----------------------------------

    def _mixed_known_unknown_gt(self):
        """Three GT dicts: 2 known (Person + Transporter) + 1 unknown (Spaceship).

        Mirrors the NVSchema-side ``_make_mixed_known_unknown_frame``
        fixture but for the gt_json_aicity schema (``"object type"`` key,
        full coordinate / scale / rotation block).
        """
        out = []
        for i, obj_type in enumerate(["Person", "Transporter", "Spaceship"]):
            out.append({
                "object type": obj_type,
                "object id": 300 + i,
                "3d location": [float(i), 1.0, 20.0],
                "3d bounding box scale": [1.0, 2.0, 1.5],
                "3d bounding box rotation": [0.0, 0.0, 0.0],
            })
        return out

    def test_object_class_tag_filters_unknown_classes(self, tmp_dir):
        """Filtering drops the unknown ``Spaceship`` annotation.

        Compare a render with the full mixed-GT frame
        (``object_class_tag="warehouse"``) to a render with only the
        known annotations and *no* tag set: the two should match
        byte-for-byte, proving the filter dropped exactly the third
        annotation and nothing else.
        """
        cam = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_020.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam])
        mixed = self._mixed_known_unknown_gt()
        known_only = mixed[:2]
        frame_paths = {cam: img_path}
        vis_filtered = os.path.join(tmp_dir, "vis_filtered")
        vis_known_only = os.path.join(tmp_dir, "vis_known_only")

        process_frame_gt_json_aicity(
            mixed, calib, frame_paths, vis_filtered,
            object_class_tag="warehouse",
        )
        process_frame_gt_json_aicity(
            known_only, calib, frame_paths, vis_known_only,
        )

        a = cv2.imread(os.path.join(vis_filtered, cam, "frame_020.jpg"))
        b = cv2.imread(os.path.join(vis_known_only, cam, "frame_020.jpg"))
        np.testing.assert_array_equal(a, b)

    def test_object_class_tag_none_keeps_everything(self, tmp_dir):
        """``object_class_tag=None`` (default) renders all annotations.

        Backward-compat guard: this is the pre-filter behaviour that
        existing gt_json_aicity callers rely on.  Without a tag, the unknown
        Spaceship annotation must still appear in the render.
        """
        cam = "cam0"
        img_dir = os.path.join(tmp_dir, "images")
        os.makedirs(img_dir, exist_ok=True)
        img_path = os.path.join(img_dir, "frame_021.jpg")
        cv2.imwrite(img_path, _make_image(480, 640))

        calib = _make_calib_dict([cam])
        mixed = self._mixed_known_unknown_gt()
        frame_paths = {cam: img_path}
        vis_no_filter = os.path.join(tmp_dir, "vis_no_filter")
        vis_filtered = os.path.join(tmp_dir, "vis_filtered_only")

        process_frame_gt_json_aicity(mixed, calib, frame_paths, vis_no_filter)
        process_frame_gt_json_aicity(
            mixed, calib, frame_paths, vis_filtered,
            object_class_tag="warehouse",
        )

        a = cv2.imread(os.path.join(vis_no_filter, cam, "frame_021.jpg"))
        b = cv2.imread(os.path.join(vis_filtered, cam, "frame_021.jpg"))
        # No-filter renders all 3 annotations; filtered renders 2 →
        # the images must differ in the Spaceship region (and overall).
        assert not np.array_equal(a, b)


# =====================================================================
# Tests for process_frame_* drawing parameters (sensor filtering, shading, etc.)
# =====================================================================

def _setup_multi_cam_frame(tmp_dir, cam_names):
    """Write a test image per camera and return (calib_dict, frame_paths)."""
    img_dir = os.path.join(tmp_dir, "images")
    os.makedirs(img_dir, exist_ok=True)
    frame_paths = {}
    for cam in cam_names:
        cam_dir = os.path.join(img_dir, cam)
        os.makedirs(cam_dir, exist_ok=True)
        path = os.path.join(cam_dir, "frame_000.jpg")
        cv2.imwrite(path, _make_image(480, 640))
        frame_paths[cam] = path
    calib = _make_calib_dict(cam_names)
    return calib, frame_paths


class TestProcessFrameDrawingParams:
    """Tests for the per-frame driver parameters (sensor filtering, shading, etc.)."""

    def test_all_sensors_default(self, tmp_dir):
        """Without sensor_ids, all cameras produce output."""
        cams = ["cam0", "cam1", "cam2"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        dets = {"sensor_bev": _make_nvschema_dets(2)}
        vis_dir = os.path.join(tmp_dir, "vis_all")

        process_frame_nvschema(dets, calib, frame_paths, vis_dir, conf_thresh=0.0)

        for cam in cams:
            assert os.path.isfile(os.path.join(vis_dir, cam, "frame_000.jpg"))

    def test_single_sensor_filter(self, tmp_dir):
        """Only the selected sensor produces output."""
        cams = ["cam0", "cam1", "cam2"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        dets = {"sensor_bev": _make_nvschema_dets(2)}
        vis_dir = os.path.join(tmp_dir, "vis_single")

        filtered_paths = {c: p for c, p in frame_paths.items() if c == "cam1"}
        process_frame_nvschema(dets, calib, filtered_paths, vis_dir, conf_thresh=0.0)

        assert os.path.isfile(os.path.join(vis_dir, "cam1", "frame_000.jpg"))
        assert not os.path.exists(os.path.join(vis_dir, "cam0"))
        assert not os.path.exists(os.path.join(vis_dir, "cam2"))

    def test_multi_sensor_subset(self, tmp_dir):
        """A subset of sensors produces output only for those cameras."""
        cams = ["cam0", "cam1", "cam2"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        dets = {"sensor_bev": _make_nvschema_dets(2)}
        vis_dir = os.path.join(tmp_dir, "vis_subset")

        subset = ["cam0", "cam2"]
        filtered_paths = {c: p for c, p in frame_paths.items() if c in subset}
        process_frame_nvschema(dets, calib, filtered_paths, vis_dir, conf_thresh=0.0)

        assert os.path.isfile(os.path.join(vis_dir, "cam0", "frame_000.jpg"))
        assert os.path.isfile(os.path.join(vis_dir, "cam2", "frame_000.jpg"))
        assert not os.path.exists(os.path.join(vis_dir, "cam1"))

    def test_draw_camera_label_off(self, tmp_dir):
        """draw_camera_label=False should produce different output than True."""
        cams = ["cam0"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        dets = {"sensor_bev": _make_nvschema_dets(1)}

        vis_with = os.path.join(tmp_dir, "vis_label_on")
        vis_without = os.path.join(tmp_dir, "vis_label_off")

        process_frame_nvschema(
            dets, calib, frame_paths, vis_with,
            conf_thresh=0.0, draw_camera_label=True,
        )
        process_frame_nvschema(
            dets, calib, frame_paths, vis_without,
            conf_thresh=0.0, draw_camera_label=False,
        )

        img_with = cv2.imread(os.path.join(vis_with, "cam0", "frame_000.jpg"))
        img_without = cv2.imread(os.path.join(vis_without, "cam0", "frame_000.jpg"))
        assert not np.array_equal(img_with, img_without)

    def test_shade_heading_forwarded(self, tmp_dir):
        """shade_heading=False produces different output than True."""
        cams = ["cam0"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        dets = {"sensor_bev": _make_nvschema_dets(2)}

        vis_on = os.path.join(tmp_dir, "vis_shade_on")
        vis_off = os.path.join(tmp_dir, "vis_shade_off")

        process_frame_nvschema(
            dets, calib, frame_paths, vis_on,
            conf_thresh=0.0, shade_heading=True,
        )
        process_frame_nvschema(
            dets, calib, frame_paths, vis_off,
            conf_thresh=0.0, shade_heading=False,
        )

        img_on = cv2.imread(os.path.join(vis_on, "cam0", "frame_000.jpg"))
        img_off = cv2.imread(os.path.join(vis_off, "cam0", "frame_000.jpg"))
        assert not np.array_equal(img_on, img_off)

    def test_gt_json_aicity_mode(self, tmp_dir):
        """process_frame_gt_json_aicity produces output with new params."""
        cams = ["cam0"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        gt_frame = _make_gt_dicts(2)
        vis_dir = os.path.join(tmp_dir, "vis_gt_unified")

        process_frame_gt_json_aicity(
            gt_frame, calib, frame_paths, vis_dir,
            draw_camera_label=True, shade_heading=False,
        )

        assert os.path.isfile(os.path.join(vis_dir, "cam0", "frame_000.jpg"))

    def test_gt_json_aicity_no_camera_label(self, tmp_dir):
        """process_frame_gt_json_aicity respects draw_camera_label=False."""
        cams = ["cam0"]
        calib, frame_paths = _setup_multi_cam_frame(tmp_dir, cams)
        gt_frame = _make_gt_dicts(2)

        vis_with = os.path.join(tmp_dir, "vis_gt_label_on")
        vis_without = os.path.join(tmp_dir, "vis_gt_label_off")

        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_with, draw_camera_label=True)
        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_without, draw_camera_label=False)

        img_with = cv2.imread(os.path.join(vis_with, "cam0", "frame_000.jpg"))
        img_without = cv2.imread(os.path.join(vis_without, "cam0", "frame_000.jpg"))
        assert not np.array_equal(img_with, img_without)


# =====================================================================
# Tests for top-level public API: visualize_nvschema / visualize_3dbbox
# =====================================================================

def _write_nvschema_jsonl(path, frames):
    """Dump a list of NVSchema frame dicts as JSON-lines."""
    with open(path, "w") as f:
        for frame in frames:
            f.write(json.dumps(frame) + "\n")


def _make_scene_on_disk(tmp_dir, cam_names, frame_ids=(0,)):
    """Create a mock scene with per-camera image folders + calibration.

    Lays out::

        tmp_dir/scene/
          Camera_A/images/000000000.jpg
          Camera_B/images/000000000.jpg
          calibration.json  (not auto-detected; callers pass --calib_path)
    """
    scene_dir = os.path.join(tmp_dir, "scene")
    os.makedirs(scene_dir, exist_ok=True)
    for cam in cam_names:
        cam_dir = os.path.join(scene_dir, cam, "images")
        os.makedirs(cam_dir, exist_ok=True)
        for fid in frame_ids:
            cv2.imwrite(
                os.path.join(cam_dir, f"{fid:09d}.jpg"),
                _make_image(480, 640),
            )

    calib_payload = {
        "sensors": [
            {
                "type": "camera",
                "id": cam,
                "intrinsicMatrix": np.eye(3).tolist(),
                "extrinsicMatrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 5]],
            }
            for cam in cam_names
        ],
    }
    calib_path = os.path.join(scene_dir, "calibration.json")
    with open(calib_path, "w") as f:
        json.dump(calib_payload, f)
    return scene_dir, calib_path


def _write_nvschema_frame(path, frame_idx, sensor_id, objs):
    """Write a single NVSchema frame line (for visualize_nvschema tests)."""
    payload = {"id": str(frame_idx), "sensorId": sensor_id, "objects": objs}
    with open(path, "w") as f:
        f.write(json.dumps(payload) + "\n")


class TestVisualizeNvschema:
    """Smoke tests for the customer-facing visualize_nvschema wrapper."""

    def test_end_to_end_single_sensor(self, tmp_dir):
        """A minimal nvschema + calib + image round-trips to a saved image."""
        scene_dir, calib_path = _make_scene_on_disk(tmp_dir, ["Camera_A"])
        nvschema_path = os.path.join(tmp_dir, "results.json")
        obj = {
            "id": "1",
            "type": "Person",
            "confidence": 0.9,
            "coordinate": {"x": 0.0, "y": 0.0, "z": 1.0},
            "bbox3d": {
                "coordinates": [0.0, 0.0, 1.0, 0.5, 0.5, 1.8, 0.0, 0.0, 0.0],
                "embedding": [{}],
                "confidence": 0.9,
            },
        }
        _write_nvschema_frame(nvschema_path, 0, "Camera_A", [obj])

        out_dir = os.path.join(tmp_dir, "viz")
        visualize_nvschema(
            nvschema_path=nvschema_path,
            calib_path=calib_path,
            data_path=scene_dir,
            output_dir=out_dir,
            sensor_ids=["Camera_A"],
            conf_thresh=0.0,
        )
        # Output filename follows the nvschema stem as the scene name.
        scene_name = "results"
        out_img = os.path.join(out_dir, scene_name, "Camera_A", "000000000.jpg")
        assert os.path.isfile(out_img)


class TestVisualize3dbbox:
    """Argument-validation tests for the general visualize_3dbbox dispatcher."""

    def test_no_source_raises(self, tmp_dir):
        """Passing zero result sources raises ValueError."""
        with pytest.raises(ValueError, match="Exactly one of"):
            visualize_3dbbox(output_dir=os.path.join(tmp_dir, "viz"))

    def test_multiple_sources_raises(self, tmp_dir):
        """Passing >1 result source raises ValueError."""
        with pytest.raises(ValueError, match="Exactly one of"):
            visualize_3dbbox(
                output_dir=os.path.join(tmp_dir, "viz"),
                nvschema_path="a.json",
                gt_json_aicity_path="b.json",
            )

    def test_data_pkl_with_calib_path_raises(self, tmp_dir):
        """data_pkl + calib_path is rejected (pkl has calib embedded)."""
        with pytest.raises(ValueError, match="calib_path"):
            visualize_3dbbox(
                output_dir=os.path.join(tmp_dir, "viz"),
                data_pkl="x.pkl",
                calib_path="y.json",
            )

    def test_nvschema_requires_data_path(self, tmp_dir):
        """nvschema mode without data_path raises ValueError."""
        with pytest.raises(ValueError, match="data_path"):
            visualize_3dbbox(
                output_dir=os.path.join(tmp_dir, "viz"),
                nvschema_path="a.json",
                calib_path="b.json",
            )

    def test_nvschema_requires_calib_path(self, tmp_dir):
        """nvschema mode without calib_path raises ValueError."""
        with pytest.raises(ValueError, match="calib_path"):
            visualize_3dbbox(
                output_dir=os.path.join(tmp_dir, "viz"),
                nvschema_path="a.json",
                data_path=tmp_dir,
            )


def _write_scene_ground_truth_json(path, frame_ids, obj_template):
    """Dump a minimal scene ground_truth.json dict-of-dicts to *path*.

    Mirrors the on-disk shape of real scene GT:
    ``{"0": [{...}, {...}], "1": [...], ...}``.
    """
    doc = {str(fid): [dict(obj_template)] for fid in frame_ids}
    with open(path, "w") as f:
        json.dump(doc, f)


class TestVisualize3dbboxGtJsonEndToEnd:
    """End-to-end smoke tests for ``visualize_3dbbox(gt_json_aicity_path=...)``.

    Regression coverage for the fix that makes gt_json_aicity mode load the raw
    scene JSON directly (dict-of-dicts shape) instead of going through
    ``load_det_3d_from_gt_scene`` (which flattens to tuples and dropped
    all annotations silently).
    """

    def test_renders_annotated_image(self, tmp_dir):
        """gt_json_aicity mode writes an annotated image that differs from the raw input."""
        scene_dir, calib_path = _make_scene_on_disk(
            tmp_dir, ["Camera_A"], frame_ids=[0],
        )
        gt_path = os.path.join(scene_dir, "ground_truth.json")
        _write_scene_ground_truth_json(
            gt_path,
            frame_ids=[0],
            obj_template={
                "object id": 42,
                "object type": "Person",
                "3d location": [0.0, 0.0, 1.0],
                "3d bounding box scale": [0.5, 0.5, 1.8],
                "3d bounding box rotation": [0.0, 0.0, 0.0],
                "confidence": 1.0,
            },
        )

        out_dir = os.path.join(tmp_dir, "viz")
        visualize_3dbbox(
            output_dir=out_dir,
            gt_json_aicity_path=gt_path,
            calib_path=calib_path,
            data_path=scene_dir,
        )
        # Scene name comes from data_path's basename.
        scene_name = os.path.basename(os.path.normpath(scene_dir))
        out_img_path = os.path.join(out_dir, scene_name, "Camera_A", "000000000.jpg")
        assert os.path.isfile(out_img_path)

        # Something was drawn: the output differs from the blank input.
        out_img = cv2.imread(out_img_path)
        raw_img = cv2.imread(
            os.path.join(scene_dir, "Camera_A", "images", "000000000.jpg")
        )
        assert not np.array_equal(out_img, raw_img)


class TestVisualize3dbboxPklEndToEnd:
    """End-to-end smoke test for ``visualize_3dbbox(data_pkl=...)``.

    Covers the sparse4d-style pkl viz path: synthesises a minimal pkl
    carrying calibration, an H5 tuple image path, and one GT box, then
    checks that an annotated image is written.
    """

    def test_renders_annotated_image(self, tmp_dir):
        """Full pkl pipeline (load calib+GT+paths, project, draw, save)."""
        import pickle
        import h5py

        cam_name = "Camera_A"
        img = _make_image(480, 640)

        # Put the image inside an H5 file, addressed as (h5_path, key).
        h5_path = os.path.join(tmp_dir, f"{cam_name}.h5")
        with h5py.File(h5_path, "w") as h5:
            h5.create_dataset("rgb/rgb_00000.jpg", data=img)

        intrinsic = np.array(
            [[500.0, 0.0, 320.0], [0.0, 500.0, 240.0], [0.0, 0.0, 1.0]],
            dtype=np.float64,
        )
        w2c = np.eye(4, dtype=np.float64)

        infos = [{
            "frame_idx": 0,
            "cams": {
                cam_name: {
                    "data_path": (h5_path, "rgb/rgb_00000.jpg"),
                    "cam_intrinsic": intrinsic,
                    "sensor2world_transform": w2c,
                },
            },
            "gt_boxes": np.array(
                [[0.0, 0.0, 20.0, 0.5, 0.5, 1.8, 0.0]], dtype=np.float32,
            ),
            "gt_names": np.array(["Person"], dtype=object),
            "instance_inds": np.array([42], dtype=np.int64),
            "valid_flag": np.array([True]),
        }]
        pkl_path = os.path.join(tmp_dir, "scene_infos.pkl")
        with open(pkl_path, "wb") as f:
            pickle.dump({"infos": infos, "metadata": {"version": "test"}}, f)

        out_dir = os.path.join(tmp_dir, "viz")
        visualize_3dbbox(output_dir=out_dir, data_pkl=pkl_path)

        # Scene name comes from the pkl stem.
        out_img_path = os.path.join(
            out_dir, "scene_infos", cam_name, "rgb_00000.jpg",
        )
        assert os.path.isfile(out_img_path)
        out_img = cv2.imread(out_img_path)
        assert not np.array_equal(out_img, img)  # a box was drawn


class TestProcessFrameH5TuplePath:
    """Regression coverage for the H5-tuple auto-detect fix.

    Before the fix, ``process_frame_nvschema`` forwarded its default
    ``h5_file=False`` down to ``load_image`` / ``save_viz``, which
    forced the non-H5 path and hit ``cv2.imread(tuple)`` on pkl-produced
    paths.  The fix stopped forwarding the flag so the loaders can
    auto-detect tuple vs string paths.
    """

    def test_nvschema_tuple_path_does_not_crash(self, tmp_dir):
        """A tuple (h5_path, key) frame_path round-trips without raising."""
        import h5py

        cam_name = "cam0"
        img = _make_image(240, 320)
        h5_path = os.path.join(tmp_dir, f"{cam_name}.h5")
        with h5py.File(h5_path, "w") as h5:
            h5.create_dataset("rgb/rgb_00000.jpg", data=img)

        calib = _make_calib_dict([cam_name])
        dets = {"sensor_bev": _make_nvschema_dets(1)}
        frame_paths = {cam_name: (h5_path, "rgb/rgb_00000.jpg")}
        vis_dir = os.path.join(tmp_dir, "vis_h5")

        process_frame_nvschema(
            dets, calib, frame_paths, vis_dir, conf_thresh=0.0,
        )

        # save_viz strips the directory from the tuple's key and writes
        # under vis_dir/<cam>/<basename>.
        out_img_path = os.path.join(vis_dir, cam_name, "rgb_00000.jpg")
        assert os.path.isfile(out_img_path)

    def test_gt_json_aicity_tuple_path_does_not_crash(self, tmp_dir):
        """Same regression coverage for ``process_frame_gt_json_aicity``."""
        import h5py

        cam_name = "cam0"
        img = _make_image(240, 320)
        h5_path = os.path.join(tmp_dir, f"{cam_name}.h5")
        with h5py.File(h5_path, "w") as h5:
            h5.create_dataset("rgb/rgb_00000.jpg", data=img)

        calib = _make_calib_dict([cam_name])
        gt_frame = _make_gt_dicts(1)
        frame_paths = {cam_name: (h5_path, "rgb/rgb_00000.jpg")}
        vis_dir = os.path.join(tmp_dir, "vis_gt_h5")

        process_frame_gt_json_aicity(gt_frame, calib, frame_paths, vis_dir)

        out_img_path = os.path.join(vis_dir, cam_name, "rgb_00000.jpg")
        assert os.path.isfile(out_img_path)


# =====================================================================
# Tests for recenter_boxes / unrecenter_boxes
# =====================================================================

class TestRecenterBoxes:
    """Tests for the recenter_boxes utility (shift 3D box centres by origin)."""

    def test_shifts_xy(self):
        """Box x and y are reduced by the origin offset."""
        boxes = np.array([[10.0, 20.0, 1.0, 0.5, 0.5, 1.8, 0.1]])
        origin = [3.0, 7.0]
        result = recenter_boxes(boxes, origin)
        assert result[0, 0] == pytest.approx(7.0)
        assert result[0, 1] == pytest.approx(13.0)

    def test_z_and_dims_unchanged(self):
        """Only x/y are shifted; z, dimensions, yaw remain identical."""
        boxes = np.array([[10.0, 20.0, 5.0, 1.0, 2.0, 3.0, 0.5]])
        origin = [3.0, 7.0]
        result = recenter_boxes(boxes, origin)
        np.testing.assert_allclose(result[0, 2:], boxes[0, 2:])

    def test_does_not_mutate_input(self):
        """The input array is not modified in place."""
        boxes = np.array([[10.0, 20.0, 1.0, 0.5, 0.5, 1.8, 0.1]])
        original = boxes.copy()
        recenter_boxes(boxes, [1.0, 2.0])
        np.testing.assert_array_equal(boxes, original)

    def test_roundtrip(self):
        """recenter followed by unrecenter restores the original boxes."""
        boxes = np.array([
            [10.0, 20.0, 1.0, 0.5, 0.5, 1.8, 0.1],
            [15.0, 25.0, 0.9, 0.6, 0.6, 1.7, 0.2],
        ])
        origin = [-23.14, -2.44]
        roundtrip = unrecenter_boxes(recenter_boxes(boxes, origin), origin)
        np.testing.assert_allclose(roundtrip, boxes)

    def test_negative_origin(self):
        """Negative origins subtract correctly (double negative)."""
        boxes = np.array([[0.0, 0.0, 0.0, 1.0, 1.0, 1.0, 0.0]])
        origin = [-5.0, -10.0]
        result = recenter_boxes(boxes, origin)
        assert result[0, 0] == pytest.approx(5.0)
        assert result[0, 1] == pytest.approx(10.0)

    def test_batch(self):
        """Shifting is applied element-wise across a batch of 100 boxes."""
        boxes = np.zeros((100, 7))
        boxes[:, 0] = np.arange(100)
        boxes[:, 1] = np.arange(100) * 2
        origin = [10.0, 20.0]
        result = recenter_boxes(boxes, origin)
        np.testing.assert_allclose(result[:, 0], np.arange(100) - 10.0)
        np.testing.assert_allclose(result[:, 1], np.arange(100) * 2 - 20.0)


class TestUnrecenterBoxes:
    """Tests for the inverse recenter operation."""

    def test_shifts_xy_back(self):
        """Box x and y are increased by the origin offset."""
        boxes = np.array([[7.0, 13.0, 1.0, 0.5, 0.5, 1.8, 0.1]])
        origin = [3.0, 7.0]
        result = unrecenter_boxes(boxes, origin)
        assert result[0, 0] == pytest.approx(10.0)
        assert result[0, 1] == pytest.approx(20.0)

    def test_does_not_mutate_input(self):
        """The input array is not modified in place."""
        boxes = np.array([[7.0, 13.0, 1.0, 0.5, 0.5, 1.8, 0.1]])
        original = boxes.copy()
        unrecenter_boxes(boxes, [1.0, 2.0])
        np.testing.assert_array_equal(boxes, original)


# =====================================================================
# Tests for resolve_scene_calib
# =====================================================================

class TestResolveSceneCalib:
    """Tests for the scene-level calibration resolver."""

    def _make_prebuilt(self):
        """Build a minimal flat calib dict with one camera."""
        return {
            "cam0": {
                "intrinsic_matrix": np.eye(3).tolist(),
                "w2c_matrix": np.eye(4).tolist(),
            },
        }

    def test_prebuilt_calib_passthrough(self):
        """A flat prebuilt_calib dict is returned as-is, no disk access."""
        calib = self._make_prebuilt()
        out = resolve_scene_calib(
            scene_root="/nonexistent", prebuilt_calib=calib,
        )
        assert out is calib

    def test_prebuilt_calib_nested_raises(self):
        """A nested {group: {cam: ...}} prebuilt_calib raises ValueError."""
        nested = {"group_a": self._make_prebuilt()}
        with pytest.raises(ValueError, match="flat dict"):
            resolve_scene_calib(
                scene_root="/nonexistent", prebuilt_calib=nested,
            )

    def test_missing_group_raises_with_helpful_message(self, monkeypatch):
        """Looking up an unknown group name raises KeyError listing available groups."""
        # Bypass the on-disk calibration loader — stub it to return a grouped
        # calib dict that doesn't contain "ghost".
        def fake_load_calib(scene_root, calib_mode, camera_group_config):
            return {
                "group_a": {"cam0": {"intrinsic_matrix": np.eye(3).tolist(),
                                     "w2c_matrix": np.eye(4).tolist()}},
                "group_b": {"cam1": {"intrinsic_matrix": np.eye(3).tolist(),
                                     "w2c_matrix": np.eye(4).tolist()}},
            }, None  # no group_area_dict

        from spatialai_data_utils.loaders import calibration as calib_mod
        monkeypatch.setattr(calib_mod, "load_calib", fake_load_calib)

        with pytest.raises(KeyError) as excinfo:
            resolve_scene_calib(
                scene_root="/fake/scene",
                group_name="ghost",
            )
        # Error message includes the requested name and available options.
        msg = str(excinfo.value)
        assert "ghost" in msg
        assert "group_a" in msg and "group_b" in msg


# =====================================================================
# Tests for index_pkl_by_frame
# =====================================================================

class TestIndexPklByFrame:
    """Tests for indexing pkl info entries by their frame_idx."""

    def test_builds_mapping(self):
        """Each info entry is keyed by its (int-coerced) frame_idx."""
        pkl_infos = [
            {"frame_idx": 0, "cams": {}},
            {"frame_idx": "1", "cams": {}},
            {"frame_idx": 5, "cams": {}},
        ]
        result = index_pkl_by_frame(pkl_infos)
        assert set(result.keys()) == {0, 1, 5}
        assert result[0] is pkl_infos[0]
        assert result[1] is pkl_infos[1]
        assert result[5] is pkl_infos[2]

    def test_empty_list(self):
        """An empty input yields an empty dict."""
        assert index_pkl_by_frame([]) == {}

    def test_missing_frame_idx_raises(self):
        """An entry without ``frame_idx`` raises KeyError mentioning the index."""
        pkl_infos = [{"frame_idx": 0}, {"cams": {}}]
        with pytest.raises(KeyError, match="index 1"):
            index_pkl_by_frame(pkl_infos)


# =====================================================================
# Tests for resolve_frame_root
# =====================================================================

class TestResolveFrameRoot:
    """Tests for detecting a scene's per-camera image root directory."""

    def test_frames_subdir_preferred(self, tmp_dir):
        """``scene_root/frames`` is returned when it exists."""
        frames = os.path.join(tmp_dir, "frames")
        os.makedirs(frames)
        assert resolve_frame_root(tmp_dir) == frames

    def test_fallback_to_scene_root(self, tmp_dir):
        """Without a ``frames/`` subdir, the scene root itself is returned."""
        assert resolve_frame_root(tmp_dir) == tmp_dir


# =====================================================================
# Tests for get_calib_dict_from_cam_data
# =====================================================================

class TestGetCalibDictFromCamData:
    """Tests for building a standard calib dict from intrinsic + w2c matrices."""

    def test_identity(self):
        """All three expected keys are produced; w2c is preserved."""
        intrinsic = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1]], dtype=np.float64)
        w2c = np.eye(4)
        result = get_calib_dict_from_cam_data(intrinsic, w2c)
        assert "intrinsic_matrix" in result
        assert "w2c_matrix" in result
        assert "w2p_matrix" in result
        np.testing.assert_allclose(result["w2c_matrix"], np.eye(4).tolist())

    def test_w2p_equals_intrin_times_w2c(self):
        """The w2p entry equals (intrinsic @ w2c) as a 4x4 matrix."""
        intrinsic = np.array([[600, 0, 400], [0, 600, 300], [0, 0, 1]], dtype=np.float64)
        w2c = np.eye(4)
        w2c[0, 3] = 5.0
        w2c[1, 3] = -3.0
        result = get_calib_dict_from_cam_data(intrinsic, w2c)
        intrin_4x4 = np.eye(4)
        intrin_4x4[:3, :3] = intrinsic
        expected_w2p = intrin_4x4 @ w2c
        np.testing.assert_allclose(result["w2p_matrix"], expected_w2p.tolist())

    def test_does_not_mutate_input(self):
        """The input extrinsic array is not modified in place."""
        w2c = np.eye(4)
        w2c_copy = w2c.copy()
        intrinsic = np.eye(3) * 500
        get_calib_dict_from_cam_data(intrinsic, w2c)
        np.testing.assert_array_equal(w2c, w2c_copy)


# =====================================================================
# Tests for get_calib_dict (auto-detects synthetic vs real-world schemas)
# =====================================================================

class TestGetCalibFieldLegacyFallback:
    """Tests for :func:`spatialai_data_utils.core.cameras.utils.get_calib_field`.

    The helper is the **only** point where calibration-field keys are
    read in the visualization / projection stack — every direct
    ``calib_info[KEY_INTRINSIC_MATRIX]`` access was migrated to
    ``get_calib_field(calib_info, KEY_INTRINSIC_MATRIX)`` so that
    pre-rename calibration files / pickled dicts continue to load
    without a one-shot data migration.  These tests lock in that
    contract: every legacy key documented in
    ``_LEGACY_CALIB_KEYS_TO_NEW`` is read transparently when the
    canonical key is absent.
    """

    def test_returns_canonical_when_present(self):
        """Canonical key takes precedence over the legacy alternative."""
        from spatialai_data_utils.constants import KEY_INTRINSIC_MATRIX
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        # Both keys present — canonical wins (so a partially-migrated
        # dict that still has the legacy key alongside the new one
        # doesn't return stale data).
        info = {
            KEY_INTRINSIC_MATRIX: "new",
            "intrinsic matrix": "legacy",
        }
        assert get_calib_field(info, KEY_INTRINSIC_MATRIX) == "new"

    def test_falls_back_to_legacy_intrinsic_matrix(self):
        """Old ``"intrinsic matrix"`` key still readable via the new constant."""
        from spatialai_data_utils.constants import KEY_INTRINSIC_MATRIX
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        info = {"intrinsic matrix": [[1, 0, 0], [0, 1, 0], [0, 0, 1]]}
        assert get_calib_field(info, KEY_INTRINSIC_MATRIX) == [
            [1, 0, 0], [0, 1, 0], [0, 0, 1],
        ]

    def test_falls_back_to_legacy_w2c_matrix(self):
        """Old ``"projection matrix w2c"`` key still readable via the new constant."""
        from spatialai_data_utils.constants import KEY_W2C_MATRIX
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        info = {"projection matrix w2c": np.eye(4).tolist()}
        np.testing.assert_array_equal(
            get_calib_field(info, KEY_W2C_MATRIX), np.eye(4).tolist(),
        )

    def test_falls_back_to_legacy_w2p_matrix(self):
        """Old ``"projection matrix w2p"`` key still readable via the new constant."""
        from spatialai_data_utils.constants import KEY_W2P_MATRIX
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        info = {"projection matrix w2p": "legacy_w2p"}
        assert get_calib_field(info, KEY_W2P_MATRIX) == "legacy_w2p"

    def test_falls_back_to_legacy_image_size(self):
        """Old ``"image size"`` key still readable via the new constant."""
        from spatialai_data_utils.constants import KEY_IMAGE_SIZE
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        info = {"image size": [1920, 1080]}
        assert get_calib_field(info, KEY_IMAGE_SIZE) == [1920, 1080]

    def test_missing_field_raises_keyerror(self):
        """No canonical, no legacy → ``KeyError`` with a helpful message."""
        from spatialai_data_utils.constants import KEY_INTRINSIC_MATRIX
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        with pytest.raises(KeyError, match="not found"):
            get_calib_field({}, KEY_INTRINSIC_MATRIX)

    def test_missing_field_with_default_returns_default(self):
        """``default=`` honoured the same way ``dict.get(key, default)`` would."""
        from spatialai_data_utils.constants import KEY_INTRINSIC_MATRIX
        from spatialai_data_utils.core.cameras.utils import get_calib_field
        sentinel = object()
        assert get_calib_field({}, KEY_INTRINSIC_MATRIX, default=sentinel) is sentinel
        assert get_calib_field({}, KEY_INTRINSIC_MATRIX, default=None) is None

    def test_legacy_only_calib_info_renders_via_draw_bbox3d_on_img(self):
        """End-to-end: a hand-built old-key calib_info still drives drawing.

        Catches regressions where some downstream module bypasses
        ``get_calib_field`` and indexes ``calib_info[KEY_*]`` directly,
        which would silently break legacy calibrations.  Covers the
        actual code path:
        ``draw_bbox3d_on_img`` → ``_build_world2img`` →
        ``build_world2img_from_calib_info`` → ``get_calib_field``.
        """
        img = _make_image()
        boxes = _make_boxes(1)
        # Hand-built dict using ONLY the legacy keys (with spaces).
        legacy_calib_info = {
            "intrinsic matrix": np.array(
                [[500, 0, 320], [0, 500, 240], [0, 0, 1]], dtype=np.float64,
            ),
            "projection matrix w2c": np.eye(4),
        }
        result = draw_bbox3d_on_img(boxes, img, calib_info=legacy_calib_info)
        # Drawing must complete without KeyError + produce a same-shape
        # output (proves the legacy path didn't short-circuit).
        assert result.shape == img.shape


class TestGetCalibDict:
    """Tests for :func:`get_calib_dict`'s schema auto-detection."""

    def test_synthetic_schema(self):
        """'intrinsicMatrix' + 'extrinsicMatrix' are used as-is."""
        K = np.array([[500, 0, 320], [0, 500, 240], [0, 0, 1]], dtype=np.float64)
        extrinsic = np.array(
            [[1.0, 0.0, 0.0, 2.0],
             [0.0, 1.0, 0.0, -1.0],
             [0.0, 0.0, 1.0, 5.0]],
            dtype=np.float64,
        )
        sensor = {
            "id": "cam0",
            "intrinsicMatrix": K.tolist(),
            "extrinsicMatrix": extrinsic.tolist(),
        }
        out = get_calib_dict(sensor)

        np.testing.assert_allclose(out["intrinsic_matrix"], K)
        expected_w2c = np.eye(4)
        expected_w2c[:3] = extrinsic
        np.testing.assert_allclose(out["w2c_matrix"], expected_w2c)

    def test_real_world_schema(self):
        """A 3x4 'cameraMatrix' is stored as the w2c (padded); K is identity."""
        # Build a synthetic real-world camera: P = K_true @ [R | t]
        K_true = np.array([[800, 0, 960], [0, 800, 540], [0, 0, 1]], dtype=np.float64)
        R = np.eye(3)
        t = np.array([1.0, 2.0, 3.0])
        P = K_true @ np.hstack([R, t[:, None]])   # 3x4 world-to-pixel

        sensor = {"id": "camA", "cameraMatrix": P.tolist()}
        out = get_calib_dict(sensor)

        # Real-world schema: K is identity, w2c IS the padded cameraMatrix.
        np.testing.assert_allclose(out["intrinsic_matrix"], np.eye(3))
        w2c = np.array(out["w2c_matrix"])
        np.testing.assert_allclose(w2c[:3], P)
        np.testing.assert_allclose(w2c[3], [0, 0, 0, 1])
        # And the downstream-friendly w2p = intrinsic @ w2c equals the padded P.
        np.testing.assert_allclose(out["w2p_matrix"], w2c)

    def test_real_world_pipeline_projects_correctly(self):
        """The real-world branch still produces correct pixel coordinates.

        Verifies that using ``K = I`` + ``w2c = cameraMatrix_padded`` yields
        the same pixels as applying the original ``cameraMatrix`` directly.
        """
        from spatialai_data_utils.core.geometry.projection import (
            project_points_3d_to_image,
        )
        K_true = np.array([[800, 0, 960], [0, 800, 540], [0, 0, 1]], dtype=np.float64)
        R = np.eye(3)
        t = np.array([0.0, 0.0, 20.0])                       # camera 20m along +Z
        P = K_true @ np.hstack([R, t[:, None]])

        sensor = {"id": "camB", "cameraMatrix": P.tolist()}
        out = get_calib_dict(sensor)
        w2p = np.array(out["w2p_matrix"])

        # A world point in front of the camera.
        world = np.array([0.5, -0.25, 0.0])
        pix, front_mask = project_points_3d_to_image(world, w2p)

        # Reference: apply P directly.
        homog = P @ np.append(world, 1.0)
        pix_ref = homog[:2] / homog[2]

        np.testing.assert_allclose(pix, pix_ref, atol=1e-9)
        assert bool(front_mask)

    def test_missing_schema_raises(self):
        """A sensor dict with neither schema's keys raises a clear KeyError."""
        with pytest.raises(KeyError, match="missing both"):
            get_calib_dict({"id": "camX"})

    def test_image_size_from_attributes(self):
        """``frameWidth`` / ``frameHeight`` attributes become ``image size``."""
        K = np.eye(3).tolist()
        extrinsic = np.array(
            [[1.0, 0.0, 0.0, 0.0],
             [0.0, 1.0, 0.0, 0.0],
             [0.0, 0.0, 1.0, 0.0]],
            dtype=np.float64,
        ).tolist()
        sensor = {
            "id": "camA",
            "intrinsicMatrix": K,
            "extrinsicMatrix": extrinsic,
            "attributes": [
                {"name": "fps", "value": "30"},
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
            ],
        }
        out = get_calib_dict(sensor)
        assert out["image_size"] == [1920, 1080]

    def test_image_size_missing_attributes_is_omitted(self):
        """No ``frameWidth`` / ``frameHeight`` means no ``image size`` key."""
        sensor = {
            "id": "camA",
            "intrinsicMatrix": np.eye(3).tolist(),
            "extrinsicMatrix": np.zeros((3, 4)).tolist(),
            # attributes lists fps only.
            "attributes": [{"name": "fps", "value": "30"}],
        }
        out = get_calib_dict(sensor)
        assert "image_size" not in out

    def test_image_size_malformed_attributes_is_omitted(self):
        """Non-integer ``frameWidth`` / ``frameHeight`` values are ignored."""
        sensor = {
            "id": "camA",
            "cameraMatrix": np.hstack([np.eye(3), np.zeros((3, 1))]).tolist(),
            "attributes": [
                {"name": "frameWidth", "value": ""},          # empty
                {"name": "frameHeight", "value": "not-a-number"},
            ],
        }
        out = get_calib_dict(sensor)
        assert "image_size" not in out


# =====================================================================
# Tests for load_calib_into_dict
# =====================================================================

class TestLoadCalibFromJsonPath:
    """Tests for loading calibration from a JSON path and flattening groups."""

    def _write_calib_json(self, path, grouped=True):
        """Write a minimal calibration JSON for testing."""
        import json
        sensors = []
        for cam_id in ["Camera_01", "Camera_02"]:
            sensor = {
                "id": cam_id,
                "intrinsicMatrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                "extrinsicMatrix": np.eye(4)[:3].tolist(),
            }
            if grouped:
                sensor["group"] = {
                    "type": "bev",
                    "name": "bev-sensor-1",
                    "origin": [10.0, 20.0],
                    "dimensions": [-50, -50, 50, 50],
                }
            sensors.append(sensor)
        with open(path, "w") as f:
            json.dump({"version": "1.0", "sensors": sensors}, f)

    def test_loads_flat_dict(self, tmp_dir):
        """Grouped calibration is flattened to {cam_name: calib_info}."""
        path = os.path.join(tmp_dir, "calib.json")
        self._write_calib_json(path, grouped=True)
        result = load_calib_into_dict(path)
        assert "Camera_01" in result
        assert "Camera_02" in result
        assert "intrinsic_matrix" in result["Camera_01"]

    def test_sensor_id_filter(self, tmp_dir):
        """Only the requested sensor IDs are included in the result."""
        path = os.path.join(tmp_dir, "calib.json")
        self._write_calib_json(path, grouped=True)
        result = load_calib_into_dict(path, sensor_ids=["Camera_01"])
        assert "Camera_01" in result
        assert "Camera_02" not in result

    def test_recentering_shifts_extrinsics(self, tmp_dir):
        """recentering=True shifts the w2c matrix by the group origin."""
        path = os.path.join(tmp_dir, "calib.json")
        self._write_calib_json(path, grouped=True)
        result_no = load_calib_into_dict(path, recentering=False)
        result_yes = load_calib_into_dict(path, recentering=True)
        w2c_no = np.array(result_no["Camera_01"]["w2c_matrix"])
        w2c_yes = np.array(result_yes["Camera_01"]["w2c_matrix"])
        assert not np.allclose(w2c_no, w2c_yes)
        c2w_yes = np.linalg.inv(w2c_yes)
        assert c2w_yes[0, 3] == pytest.approx(-10.0)
        assert c2w_yes[1, 3] == pytest.approx(-20.0)

    def test_no_group_file(self, tmp_dir):
        """Ungrouped calibration file works without recentering."""
        path = os.path.join(tmp_dir, "calib.json")
        self._write_calib_json(path, grouped=False)
        result = load_calib_into_dict(path)
        assert "Camera_01" in result


# =====================================================================
# Tests for load_calib_into_dict_with_group_memberships
# =====================================================================

class TestLoadCalibAndGroupsFromJsonPath:
    """Tests for the group-aware calibration loader (returns flat + inverse map)."""

    def _write_grouped_calib_json(
        self, path, group_name="bev-sensor-1", cams=("Camera_01", "Camera_02"),
    ):
        """Write a grouped calibration JSON with a single BEV group."""
        import json
        sensors = []
        for cam_id in cams:
            sensors.append({
                "id": cam_id,
                "intrinsicMatrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                "extrinsicMatrix": np.eye(4)[:3].tolist(),
                "group": {
                    "type": "bev",
                    "name": group_name,
                    "origin": [10.0, 20.0],
                    "dimensions": [-50, -50, 50, 50],
                },
            })
        with open(path, "w") as f:
            json.dump({"version": "1.0", "sensors": sensors}, f)

    def _write_multi_group_calib_json(self, path):
        """Write a calibration JSON with two distinct BEV groups."""
        import json
        sensors = []
        for cam_id in ("Camera_01", "Camera_02"):
            sensors.append({
                "id": cam_id,
                "intrinsicMatrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                "extrinsicMatrix": np.eye(4)[:3].tolist(),
                "group": {
                    "type": "bev", "name": "bev-sensor-1",
                    "origin": [10.0, 20.0], "dimensions": [-50, -50, 50, 50],
                },
            })
        for cam_id in ("Camera_03", "Camera_04"):
            sensors.append({
                "id": cam_id,
                "intrinsicMatrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
                "extrinsicMatrix": np.eye(4)[:3].tolist(),
                "group": {
                    "type": "bev", "name": "bev-sensor-2",
                    "origin": [30.0, 40.0], "dimensions": [-50, -50, 50, 50],
                },
            })
        with open(path, "w") as f:
            json.dump({"version": "1.0", "sensors": sensors}, f)

    def _write_ungrouped_calib_json(self, path, cams=("Camera_01", "Camera_02")):
        """Write a calibration JSON with no ``group`` block on any sensor."""
        import json
        sensors = [{
            "id": cam_id,
            "intrinsicMatrix": [[500, 0, 320], [0, 500, 240], [0, 0, 1]],
            "extrinsicMatrix": np.eye(4)[:3].tolist(),
        } for cam_id in cams]
        with open(path, "w") as f:
            json.dump({"version": "1.0", "sensors": sensors}, f)

    def test_returns_flat_and_group_dicts(self, tmp_dir):
        """Grouped calib yields both a flat dict and an inverse group→cams map."""
        from spatialai_data_utils.loaders.calibration import (
            load_calib_into_dict_with_group_memberships,
        )
        path = os.path.join(tmp_dir, "calib.json")
        self._write_grouped_calib_json(path)
        flat, groups = load_calib_into_dict_with_group_memberships(path)
        assert set(flat) == {"Camera_01", "Camera_02"}
        assert "intrinsic_matrix" in flat["Camera_01"]
        assert groups == {"bev-sensor-1": ["Camera_01", "Camera_02"]}

    def test_multiple_groups_preserved(self, tmp_dir):
        """Each BEV group gets its own entry, members preserve insertion order."""
        from spatialai_data_utils.loaders.calibration import (
            load_calib_into_dict_with_group_memberships,
        )
        path = os.path.join(tmp_dir, "calib.json")
        self._write_multi_group_calib_json(path)
        flat, groups = load_calib_into_dict_with_group_memberships(path)
        assert set(flat) == {"Camera_01", "Camera_02", "Camera_03", "Camera_04"}
        assert groups == {
            "bev-sensor-1": ["Camera_01", "Camera_02"],
            "bev-sensor-2": ["Camera_03", "Camera_04"],
        }

    def test_ungrouped_calib_empty_group_map(self, tmp_dir):
        """Flat/ungrouped calib produces an empty group dict (no BEV sensors)."""
        from spatialai_data_utils.loaders.calibration import (
            load_calib_into_dict_with_group_memberships,
        )
        path = os.path.join(tmp_dir, "calib.json")
        self._write_ungrouped_calib_json(path)
        flat, groups = load_calib_into_dict_with_group_memberships(path)
        assert set(flat) == {"Camera_01", "Camera_02"}
        assert groups == {}

    def test_recentering_shifts_only_when_grouped(self, tmp_dir):
        """recentering=True shifts extrinsics by group origin (same as flat loader)."""
        from spatialai_data_utils.loaders.calibration import (
            load_calib_into_dict_with_group_memberships,
        )
        path = os.path.join(tmp_dir, "calib.json")
        self._write_grouped_calib_json(path)
        flat_no, _ = load_calib_into_dict_with_group_memberships(path, recentering=False)
        flat_yes, _ = load_calib_into_dict_with_group_memberships(path, recentering=True)
        w2c_no = np.array(flat_no["Camera_01"]["w2c_matrix"])
        w2c_yes = np.array(flat_yes["Camera_01"]["w2c_matrix"])
        assert not np.allclose(w2c_no, w2c_yes)
        c2w_yes = np.linalg.inv(w2c_yes)
        assert c2w_yes[0, 3] == pytest.approx(-10.0)
        assert c2w_yes[1, 3] == pytest.approx(-20.0)

    def test_matches_flat_loader_for_flat_calib(self, tmp_dir):
        """Flat-dict half is byte-identical to load_calib_into_dict output."""
        from spatialai_data_utils.loaders.calibration import (
            load_calib_into_dict_with_group_memberships,
        )
        path = os.path.join(tmp_dir, "calib.json")
        self._write_grouped_calib_json(path)
        flat_new, _ = load_calib_into_dict_with_group_memberships(path, recentering=True)
        flat_old = load_calib_into_dict(path, recentering=True)
        assert set(flat_new) == set(flat_old)
        for cam in flat_old:
            # calib entries are nested dicts of arrays — compare structure.
            for key in flat_old[cam]:
                np.testing.assert_allclose(
                    np.asarray(flat_new[cam][key]),
                    np.asarray(flat_old[cam][key]),
                )


# =====================================================================
# Tests for resolve_frame_path (single-camera non-raising resolver)
# =====================================================================

class TestResolveFramePath:
    """Tests for the canonical single-camera image-path resolver.

    Every pattern here is shared with
    :func:`spatialai_data_utils.datasets.frame_paths.get_frame_paths_of_multi_cameras`
    so if a layout shows up in this test class it must be honoured by
    the batch CLI too.  The helper is non-raising — missing images
    return ``None`` and the caller decides what to do.
    """

    def _touch(self, *parts):
        """Create an empty file at os.path.join(*parts) + mkdir -p."""
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_returns_none_when_nothing_matches(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) is None

    def test_images_subfolder_9digit(self, tmp_dir):
        """<scene>/<cam>/images/<09d>.jpg (AIC standard)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "images", "000000006.jpg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_rgb_subfolder_rgb_prefix_jpg(self, tmp_dir):
        """<scene>/<cam>/rgb/rgb_<05d>.jpg (Isaac / h5-mirror layout)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_rgb_subfolder_rgb_prefix_png(self, tmp_dir):
        """<scene>/<cam>/rgb/rgb_<05d>.png — PNG twin of the jpg layout."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "rgb", "rgb_00006.png")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_rgb_subfolder_9digit(self, tmp_dir):
        """<scene>/<cam>/rgb/<09d>.jpg (hybrid)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "rgb", "000000006.jpg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_scout_layout(self, tmp_dir):
        """<scene>/<cam>/image_<frame_id>.jpg (scout dataset, non-padded)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "image_6.jpg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_frames_prefix_layout(self, tmp_dir):
        """<scene>/frames/<cam>/images/<09d>.jpg (less-common BEV variant)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "frames", "Camera_08", "images", "000000006.jpg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_bare_frame_id_jpg(self, tmp_dir):
        """<scene>/<cam>/<frame_id>.jpg — bare filename fallback."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "6.jpg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_bare_frame_id_png(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "6.png")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_bare_frame_id_jpeg(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        expected = self._touch(tmp_dir, "Camera_08", "6.jpeg")
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == expected

    def test_prefers_canonical_over_bare(self, tmp_dir):
        """When multiple layouts coexist, canonical (images/, rgb/) wins."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        # Bare fallback (pattern 7)
        self._touch(tmp_dir, "Camera_08", "6.jpg")
        # Canonical AIC layout (pattern 1 — first on the list)
        preferred = self._touch(
            tmp_dir, "Camera_08", "images", "000000006.jpg",
        )
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == preferred

    # ----- timestamp kwarg -----

    def test_timestamp_substring_match_wins_over_canonical(self, tmp_dir):
        """Timestamp branch runs FIRST, ahead of every canonical pattern."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        # Canonical pattern #1 would otherwise win.
        self._touch(tmp_dir, "Camera_08", "images", "000000006.jpg")
        # Timestamp-bearing file directly under the cam folder.
        ts_match = self._touch(
            tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.009Z.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14T00-36-45.009Z",
        ) == ts_match

    def test_timestamp_partial_substring_matches(self, tmp_dir):
        """A substring of the filename's timestamp is enough to trigger match."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        ts_match = self._touch(
            tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.009Z.jpg",
        )
        # Partial timestamp (no trailing millis) should still land the file.
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14",
        ) == ts_match

    def test_timestamp_no_match_falls_through_to_canonical(self, tmp_dir):
        """When no filename contains the timestamp, canonical patterns still apply."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        canonical = self._touch(
            tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg",
        )
        # Timestamp present but no file contains it.
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="1999-01-01",
        ) == canonical

    def test_empty_timestamp_is_noop(self, tmp_dir):
        """timestamp='' / None takes the same path as no kwarg at all."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        canonical = self._touch(tmp_dir, "Camera_08", "6.jpg")
        # None (default), empty string, and explicit None all behave identically.
        assert resolve_frame_path(tmp_dir, "Camera_08", 6) == canonical
        assert resolve_frame_path(tmp_dir, "Camera_08", 6, timestamp="") == canonical
        assert resolve_frame_path(tmp_dir, "Camera_08", 6, timestamp=None) == canonical

    def test_timestamp_honours_image_extension_filter(self, tmp_dir):
        """Non-image files with the timestamp in their name are ignored."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        # Red herring: a .txt / .json that contains the timestamp.
        self._touch(tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.009Z.txt")
        # And the real image at the canonical location.
        canonical = self._touch(
            tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14T00-36-45.009Z",
        ) == canonical

    def test_timestamp_picks_lex_smallest_among_multiple(self, tmp_dir):
        """Multiple timestamp-bearing files: sorted order wins
        (ISO timestamps sort chronologically)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        # Two candidates, both with the row's timestamp substring, but
        # different leading frame ids.  sorted() returns '006_*' before
        # '007_*' so the first one wins.
        early = self._touch(tmp_dir, "Camera_08", "006_2025-04-14.jpg")
        self._touch(tmp_dir, "Camera_08", "007_2025-04-14.jpg")
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14",
        ) == early

    # ----- :↔- normalisation in the timestamp branch -----

    def test_timestamp_colon_input_matches_dashed_filename(self, tmp_dir):
        """JSON-form ISO ``:`` timestamp matches the dashed filesystem form.

        ``2025-04-14T00:36:45.109Z`` (NVSchema row's ``"timestamp"`` /
        per-camera ``info`` value) must land on
        ``42_2025-04-14T00-36-45.109Z.jpg`` (filesystem-safe form) —
        otherwise consumers carrying JSON ISO timestamps would silently
        fall through to canonical patterns and miss the per-row image.
        """
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        match = self._touch(
            tmp_dir, "Camera_08", "042_2025-04-14T00-36-45.109Z.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 42,
            timestamp="2025-04-14T00:36:45.109Z",
        ) == match

    def test_timestamp_colon_filename_matches_colon_input(self, tmp_dir):
        """Both sides ``:`` (rare on Linux but legal) — match still works.

        Locks the symmetric direction of the normalisation: when the
        on-disk filename already happens to embed the JSON form,
        normalising both sides still yields a match (no regression
        from the pre-normalisation literal-substring behaviour).
        """
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        match = self._touch(
            tmp_dir, "Camera_08", "042_2025-04-14T00:36:45.109Z.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 42,
            timestamp="2025-04-14T00:36:45.109Z",
        ) == match

    def test_timestamp_dashed_input_matches_colon_filename(self, tmp_dir):
        """Symmetric: dashed input + colon-filename also matches."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        match = self._touch(
            tmp_dir, "Camera_08", "042_2025-04-14T00:36:45.109Z.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 42,
            timestamp="2025-04-14T00-36-45.109Z",
        ) == match

    def test_timestamp_short_subsec_matches_long_subsec_filename(self, tmp_dir):
        """Sub-second precision mismatch: ``.1Z`` (100 ms) input matches
        ``.100Z`` filename (also 100 ms).

        Without sub-second padding the substring branch would fail
        because ``"...45.1Z"`` is not a literal substring of
        ``"...45.100Z"``.  Both sides are now padded to
        ``"...45.100000000Z"`` so the substring match succeeds even
        when the caller and the filesystem encode the same instant
        at different widths.  Critical for NVSchema rows that emit
        sub-millisecond ``info`` timestamps without trailing zeros.
        """
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        match = self._touch(
            tmp_dir, "Camera_08", "042_2025-04-14T00-36-45.100Z.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 42,
            timestamp="2025-04-14T00:36:45.1Z",
        ) == match

    def test_timestamp_long_subsec_matches_short_subsec_filename(self, tmp_dir):
        """Symmetric: ``.100Z`` input matches ``.1Z`` filename (same instant)."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        match = self._touch(
            tmp_dir, "Camera_08", "042_2025-04-14T00-36-45.1Z.jpg",
        )
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 42,
            timestamp="2025-04-14T00:36:45.100Z",
        ) == match


# =====================================================================
# Tests for _ts_to_fs_safe (private library helper)
# =====================================================================

class TestTsToFsSafe:
    """Tests for the ``:`` → ``-`` ISO timestamp normaliser.

    Bridges the two ISO conventions the toolkit has to reconcile:
    JSON rows / their per-camera ``info`` map use ``HH:MM:SS`` (legal
    in JSON strings) while image filenames typically use ``HH-MM-SS``
    because most filesystems disallow ``:`` (FAT, Windows, ext4 with
    portability constraints).  Used internally by
    :func:`resolve_frame_path` (substring match normalisation) and
    :func:`find_frame_path_in_ts_range` (range-bound canonicalisation).
    """

    def test_iso_with_colons_is_dasherised(self):
        from spatialai_data_utils.datasets.frame_paths import _ts_to_fs_safe
        assert _ts_to_fs_safe("2025-04-14T00:36:45.109Z") == \
            "2025-04-14T00-36-45.109Z"

    def test_already_dashed_is_idempotent(self):
        """No double-conversion — already-fs-safe inputs pass through."""
        from spatialai_data_utils.datasets.frame_paths import _ts_to_fs_safe
        assert _ts_to_fs_safe("2025-04-14T00-36-45.109Z") == \
            "2025-04-14T00-36-45.109Z"

    def test_empty_string_passes_through(self):
        from spatialai_data_utils.datasets.frame_paths import _ts_to_fs_safe
        assert _ts_to_fs_safe("") == ""

    def test_only_colons_replaced(self):
        """Other punctuation (``-``, ``T``, ``.``, ``Z``) is preserved."""
        from spatialai_data_utils.datasets.frame_paths import _ts_to_fs_safe
        assert _ts_to_fs_safe("2025:04:14T00:36:45.109Z") == \
            "2025-04-14T00-36-45.109Z"

    def test_runs_of_colons_all_replaced(self):
        """``replace`` covers every occurrence — defensive against multi-``:`` inputs."""
        from spatialai_data_utils.datasets.frame_paths import _ts_to_fs_safe
        assert _ts_to_fs_safe("a:b:c:d") == "a-b-c-d"


# =====================================================================
# Tests for _normalize_subsec_precision (private library helper)
# =====================================================================

class TestNormalizeSubsecPrecision:
    """Tests for the sub-second-width canonicaliser.

    Sister to :class:`TestTsToFsSafe`.  Right-pads (or truncates) the
    first ``\\.\\d+`` segment to a fixed width so lex comparison
    aligns with chronological order across mixed-precision inputs.
    Without this, ``Z`` (``0x5A``) sorts above ``0``-``9``
    (``0x30``-``0x39``) and turns sub-second-width mismatches into
    silent off-by-range bugs in
    :func:`find_frame_path_in_ts_range` and the CLI's
    ``info.values()`` sort.
    """

    def test_short_sub_second_is_right_padded(self):
        """``.1Z`` → ``.100000000Z`` (zero-pad to 9 nanosecond digits)."""
        from spatialai_data_utils.datasets.frame_paths import (
            _normalize_subsec_precision,
        )
        assert _normalize_subsec_precision("2025-04-14T00-36-45.1Z") == \
            "2025-04-14T00-36-45.100000000Z"

    def test_three_digit_sub_second_padded_to_nine(self):
        """``.109Z`` → ``.109000000Z`` — the typical NVSchema case."""
        from spatialai_data_utils.datasets.frame_paths import (
            _normalize_subsec_precision,
        )
        assert _normalize_subsec_precision("2025-04-14T00-36-45.109Z") == \
            "2025-04-14T00-36-45.109000000Z"

    def test_already_canonical_width_is_idempotent(self):
        """Inputs already at the canonical width pass through unchanged."""
        from spatialai_data_utils.datasets.frame_paths import (
            _normalize_subsec_precision,
        )
        ts = "2025-04-14T00-36-45.123456789Z"
        assert _normalize_subsec_precision(ts) == ts

    def test_over_canonical_width_is_truncated(self):
        """Inputs with > 9 fractional digits are truncated (precision loss
        is acceptable; lex order remains chronologically correct after
        truncation since all bounds normalise to the same width)."""
        from spatialai_data_utils.datasets.frame_paths import (
            _normalize_subsec_precision,
        )
        # 12 digits → keeps the leading 9, drops the trailing 3.
        assert _normalize_subsec_precision("2025-04-14T00-36-45.123456789012Z") == \
            "2025-04-14T00-36-45.123456789Z"

    def test_no_subsec_segment_passes_through(self):
        """Date-only / time-only inputs without ``.\\d+`` are unchanged."""
        from spatialai_data_utils.datasets.frame_paths import (
            _normalize_subsec_precision,
        )
        assert _normalize_subsec_precision("2025-04-14") == "2025-04-14"
        assert _normalize_subsec_precision("2025-04-14T00-36-45Z") == \
            "2025-04-14T00-36-45Z"

    def test_padded_range_is_chronologically_correct(self):
        """End-to-end: the very mismatch the helper exists to fix.

        Pre-fix lex compare: ``".5Z" > ".500Z"`` (``Z`` > ``0``); the
        fix pads both to ``".500000000Z"`` so they compare equal.
        Lock that here so future refactors don't silently regress
        the chronological ordering guarantee.
        """
        from spatialai_data_utils.datasets.frame_paths import (
            _normalize_subsec_precision,
        )
        a = _normalize_subsec_precision("2025-04-14T00-36-45.5Z")
        b = _normalize_subsec_precision("2025-04-14T00-36-45.500Z")
        c = _normalize_subsec_precision("2025-04-14T00-36-45.150Z")
        assert a == b
        assert c < a  # 150 ms < 500 ms — chronological order preserved.


# =====================================================================
# Tests for _parse_iso_ts (private library helper)
# =====================================================================

class TestParseIsoTs:
    """Tests for the ISO-timestamp → :class:`datetime` parser used by
    :func:`find_nearest_frame_path` to compute absolute deltas.

    Accepts both ISO conventions the toolkit deals with (``:`` and
    ``-`` time separators), tolerates ``Z`` / ``+HH:MM`` /
    no-offset suffixes, and returns ``None`` for unparsable inputs
    so callers can ``continue`` without raising.
    """

    def test_parses_dashed_z_form(self):
        """Filesystem-safe form (`-` time separators, `Z` suffix) parses."""
        from datetime import datetime, timezone
        from spatialai_data_utils.datasets.frame_paths import _parse_iso_ts
        dt = _parse_iso_ts("2025-04-14T00-36-45.109Z")
        assert dt == datetime(2025, 4, 14, 0, 36, 45, 109000, tzinfo=timezone.utc)

    def test_parses_colon_z_form(self):
        """JSON ISO form (`:` time separators, `Z` suffix) parses."""
        from datetime import datetime, timezone
        from spatialai_data_utils.datasets.frame_paths import _parse_iso_ts
        dt = _parse_iso_ts("2025-04-14T00:36:45.109Z")
        assert dt == datetime(2025, 4, 14, 0, 36, 45, 109000, tzinfo=timezone.utc)

    def test_short_subsec_is_padded_to_microseconds(self):
        """``.1Z`` (1 digit) is padded to microseconds before parsing."""
        from datetime import datetime, timezone
        from spatialai_data_utils.datasets.frame_paths import _parse_iso_ts
        dt = _parse_iso_ts("2025-04-14T00-36-45.1Z")
        # 1 digit ".1" → ".100000" microseconds → 100 000 us = 100 ms.
        assert dt == datetime(2025, 4, 14, 0, 36, 45, 100000, tzinfo=timezone.utc)

    def test_long_subsec_is_truncated_to_microseconds(self):
        """``.123456789Z`` (nanoseconds) truncates to microseconds."""
        from datetime import datetime, timezone
        from spatialai_data_utils.datasets.frame_paths import _parse_iso_ts
        dt = _parse_iso_ts("2025-04-14T00-36-45.123456789Z")
        assert dt == datetime(2025, 4, 14, 0, 36, 45, 123456, tzinfo=timezone.utc)

    def test_no_offset_assumed_utc(self):
        """A naive timestamp (no offset) is treated as UTC."""
        from datetime import timezone
        from spatialai_data_utils.datasets.frame_paths import _parse_iso_ts
        dt = _parse_iso_ts("2025-04-14T00:36:45.109")
        assert dt is not None
        assert dt.tzinfo is timezone.utc

    def test_returns_none_for_unparsable(self):
        """Garbage input returns None instead of raising."""
        from spatialai_data_utils.datasets.frame_paths import _parse_iso_ts
        assert _parse_iso_ts("not-a-timestamp") is None
        assert _parse_iso_ts("1999-99-99T99-99-99.999Z") is None


# =====================================================================
# Tests for find_frame_path_in_ts_range (public library helper)
# =====================================================================

class TestFindFramePathInTsRange:
    """Tests for the timestamp-range bracket scan.

    Sibling of :func:`resolve_frame_path` for the range-bracket case:
    when the caller has a ``[ts_min, ts_max]`` window (e.g. derived
    from an NVSchema row's per-camera ``info`` map) and wants the
    first image whose embedded ISO timestamp falls inside it.  Both
    bounds and each candidate's extracted timestamp are
    canonicalised via :func:`_ts_to_fs_safe` before comparison so
    JSON-form (``:``) and filesystem-safe (``-``) inputs interoperate.
    No canonical-pattern fallback — the bracket scan is the only
    rule.
    """

    def _touch(self, *parts):
        path = Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_returns_none_when_dir_missing(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "missing",
            "2025-04-14T00-36-45.000Z", "2025-04-14T00-36-45.999Z",
        ) is None

    def test_in_range_match_returned(self, tmp_dir):
        """A file whose embedded timestamp is in [min, max] wins."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.150Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) == match

    def test_inclusive_lower_bound(self, tmp_dir):
        """``ts_min`` is inclusive (file timestamp == ts_min counts)."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.100Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) == match

    def test_inclusive_upper_bound(self, tmp_dir):
        """``ts_max`` is inclusive (file timestamp == ts_max counts)."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.200Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) == match

    def test_out_of_range_skipped(self, tmp_dir):
        """Files whose embedded timestamp falls outside [min, max] don't match."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        self._touch(tmp_dir, "Camera_05", "006_2025-04-14T00-36-44.999Z.jpg")
        self._touch(tmp_dir, "Camera_05", "007_2025-04-14T00-36-45.300Z.jpg")
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) is None

    def test_files_without_embedded_timestamp_skipped(self, tmp_dir):
        """A canonical-named file (no ISO substring) is silently skipped."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        # No embedded ISO substring — must NOT match.
        self._touch(tmp_dir, "Camera_05", "rgb_00006.jpg")
        # Real in-range candidate.
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.150Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) == match

    def test_non_image_extensions_ignored(self, tmp_dir):
        """Non-image basenames (.txt / .json) don't satisfy the scan."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        self._touch(tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.150Z.txt")
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) is None

    def test_lex_smallest_wins_among_multiple_in_range(self, tmp_dir):
        """Multiple in-range candidates: sorted order wins (deterministic)."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        early = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.110Z.jpg",
        )
        self._touch(tmp_dir, "Camera_05", "007_2025-04-14T00-36-45.190Z.jpg")
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.200Z",
        ) == early

    def test_colon_bounds_match_dashed_filename(self, tmp_dir):
        """JSON-form ``:`` bounds match dashed filenames after canonicalisation."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.150Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00:36:45.100Z", "2025-04-14T00:36:45.200Z",
        ) == match

    def test_colon_filename_matches_colon_bounds(self, tmp_dir):
        """Symmetric: ``:``-bearing filename matches ``:``-bound input."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00:36:45.150Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00:36:45.100Z", "2025-04-14T00:36:45.200Z",
        ) == match

    # ----- Mixed sub-second precision (regression for the lex-compare bug) -----

    def test_short_sub_second_file_in_three_digit_range(self, tmp_dir):
        """``.5Z`` (500 ms) in range ``[.100Z, .500Z]`` (3-digit bounds).

        Pre-normalisation lex would say ``.5Z`` > ``.500Z`` because
        ``Z`` (0x5A) > ``0`` (0x30) at the same position, so the file
        was silently dropped from the upper-inclusive boundary.  The
        sub-second pad fixes both bounds and the file's extracted ts
        to width 9 (``.500000000Z`` on both sides) so the file
        compares equal to the upper bound.
        """
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.5Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.500Z",
        ) == match

    def test_short_sub_second_file_genuinely_below_range_excluded(self, tmp_dir):
        """``.0Z`` (0 ms) is < lower bound ``.100Z`` even after padding —
        verify the chronological-correctness guarantee doesn't go too far
        the other way and admit out-of-range files."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        # File at .0Z (0 ms) — strictly below the lower bound.
        self._touch(tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.0Z.jpg")
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.500Z",
        ) is None

    def test_one_digit_bound_matches_three_digit_filename(self, tmp_dir):
        """Inverse direction: bounds use short form, filename uses long.

        File ``.150Z`` (150 ms) in range ``[.1Z, .2Z]`` (i.e.
        100 ms-200 ms after padding to ``.100000000Z`` /
        ``.200000000Z``).  Symmetric to the previous test: ensures
        the helper handles either side carrying the shorter form.
        """
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.150Z.jpg",
        )
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.1Z", "2025-04-14T00-36-45.2Z",
        ) == match

    def test_mixed_precision_files_all_evaluated_correctly(self, tmp_dir):
        """A directory full of mixed-precision files compares correctly:
        only the in-range ones survive, and the lex-smallest among
        survivors wins (sorted iteration is preserved)."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_frame_path_in_ts_range,
        )
        # Decoy: 0 ms — below range.
        self._touch(tmp_dir, "Camera_05", "001_2025-04-14T00-36-45.0Z.jpg")
        # In range, lex-smallest filename — should win.
        winner = self._touch(
            tmp_dir, "Camera_05", "002_2025-04-14T00-36-45.100Z.jpg",
        )
        # Also in range, but lex-larger filename.
        self._touch(tmp_dir, "Camera_05", "003_2025-04-14T00-36-45.5Z.jpg")
        # Decoy: 999 ms — above range.
        self._touch(tmp_dir, "Camera_05", "004_2025-04-14T00-36-45.999Z.jpg")
        assert find_frame_path_in_ts_range(
            tmp_dir, "Camera_05",
            "2025-04-14T00-36-45.100Z", "2025-04-14T00-36-45.500Z",
        ) == winner


# =====================================================================
# Tests for find_nearest_frame_path (public library helper)
# =====================================================================

class TestFindNearestFramePath:
    """Tests for the timestamp-driven nearest-within-window resolver.

    Recovers from sub-millisecond acquisition skew between an
    NVSchema row's nominal timestamp (from ``info[cam_name]`` or the
    row-level ``timestamp``) and the actual frame the camera
    captured — the per-row CLI's mid-tier between exact substring
    match and strict-skip / canonical fallback.  Returns the file
    with the smallest absolute delta, provided ``|delta| <=
    window_ms``.
    """

    def _touch(self, *parts):
        path = Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_returns_none_when_dir_missing(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        assert find_nearest_frame_path(
            tmp_dir, "missing", "2025-04-14T00:36:45.109Z",
        ) is None

    def test_picks_in_window_file_one_ms_away(self, tmp_dir):
        """File 1 ms late (``.110Z`` vs target ``.109Z``) wins the scan."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.110Z.jpg",
        )
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
        ) == match

    def test_skips_out_of_window_file(self, tmp_dir):
        """File 600 ms away (> default 500 ms window) returns None."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.709Z.jpg",
        )
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
        ) is None

    def test_window_kwarg_widens_acceptable_delta(self, tmp_dir):
        """Same file as the previous test, but ``window_ms=1000``
        admits it (delta = 600 ms ≤ 1000 ms)."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.709Z.jpg",
        )
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
            window_ms=1000,
        ) == match

    def test_picks_smallest_delta_among_in_window_candidates(self, tmp_dir):
        """Multiple in-window files: smallest absolute delta wins."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        # 100 ms early.
        self._touch(tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.009Z.jpg")
        # 1 ms late — closest to target.
        winner = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.110Z.jpg",
        )
        # 200 ms late.
        self._touch(tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.309Z.jpg")
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
        ) == winner

    def test_files_without_embedded_timestamp_skipped(self, tmp_dir):
        """A canonical-named file (no ISO substring) doesn't satisfy."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        self._touch(tmp_dir, "Camera_05", "rgb_00006.jpg")
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.110Z.jpg",
        )
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
        ) == match

    def test_only_canonical_files_returns_none(self, tmp_dir):
        """All files canonical (no embedded timestamps) → None."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        self._touch(tmp_dir, "Camera_05", "rgb_00006.jpg")
        self._touch(tmp_dir, "Camera_05", "rgb_00007.jpg")
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
        ) is None

    def test_unparsable_target_returns_none(self, tmp_dir):
        """A target_ts that doesn't parse as ISO returns None."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.110Z.jpg",
        )
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "not-a-timestamp",
        ) is None

    def test_colon_target_matches_dashed_filename(self, tmp_dir):
        """JSON-form ``:`` target matches dashed filename via _parse_iso_ts."""
        from spatialai_data_utils.datasets.frame_paths import (
            find_nearest_frame_path,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "006_2025-04-14T00-36-45.110Z.jpg",
        )
        assert find_nearest_frame_path(
            tmp_dir, "Camera_05", "2025-04-14T00:36:45.109Z",
        ) == match


# =====================================================================
# Tests for resolve_frame_path_with_window (public library helper)
# =====================================================================


class TestResolveFramePathWithWindow:
    """Tests for the three-tier composition.

    Public sibling of :func:`resolve_frame_path` that orchestrates
    the substring → nearest-window → strict-skip-or-canonical flow
    used by the per-row CLI's timestamp-driven lookup.  Promoted
    from ``tools/visualization/draw_3dbbox.py``'s former CLI-private
    ``_resolve_with_target_ts`` so other timestamp-aware consumers
    share the same semantics; covered here as a library-level
    contract.
    """

    def _touch(self, *parts):
        path = Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_tier1_substring_exact_wins(self, tmp_dir):
        """T1: a file whose basename contains the target ts is returned."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "6_2025-04-14T00-36-45.109Z.jpg",
        )
        # Decoy file 1 ms away: T1 substring still wins because it's
        # tried first.
        self._touch(tmp_dir, "Camera_05", "6_2025-04-14T00-36-45.110Z.jpg")
        assert resolve_frame_path_with_window(
            tmp_dir, "Camera_05", 6, "2025-04-14T00:36:45.109Z",
        ) == match

    def test_tier2_nearest_within_window(self, tmp_dir):
        """T2: substring miss, but a file is within ±window_ms."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        # Only file is 1 ms after target — substring miss, T2 hit.
        match = self._touch(
            tmp_dir, "Camera_05", "6_2025-04-14T00-36-45.110Z.jpg",
        )
        assert resolve_frame_path_with_window(
            tmp_dir, "Camera_05", 6, "2025-04-14T00:36:45.109Z",
        ) == match

    def test_tier3a_strict_skip_for_timestamped_dir(self, tmp_dir):
        """T3a: timestamped dir + no in-window file → ``None`` (skip)."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        # File 600 ms away — outside default 500 ms window.
        self._touch(tmp_dir, "Camera_05", "6_2025-04-14T00-36-45.709Z.jpg")
        # Canonical file present but should NOT be returned (strict skip).
        self._touch(tmp_dir, "Camera_05", "rgb", "rgb_00006.jpg")
        assert resolve_frame_path_with_window(
            tmp_dir, "Camera_05", 6, "2025-04-14T00:36:45.109Z",
        ) is None

    def test_tier3b_canonical_fallback_for_legacy_dir(self, tmp_dir):
        """T3b: dir has no embedded-ts files → canonical pattern wins."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        canonical = self._touch(
            tmp_dir, "Camera_05", "rgb", "rgb_00006.jpg",
        )
        assert resolve_frame_path_with_window(
            tmp_dir, "Camera_05", 6, "2025-04-14T00:36:45.109Z",
        ) == canonical

    def test_window_ms_kwarg_widens_acceptance(self, tmp_dir):
        """``window_ms=1000`` admits a 600 ms-away file the default
        500 ms window would have skipped."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        match = self._touch(
            tmp_dir, "Camera_05", "6_2025-04-14T00-36-45.709Z.jpg",
        )
        assert resolve_frame_path_with_window(
            tmp_dir, "Camera_05", 6, "2025-04-14T00:36:45.109Z",
            window_ms=1000,
        ) == match

    def test_window_ms_zero_disables_tier2(self, tmp_dir):
        """``window_ms=0`` disables T2 — a 1 ms-away file no longer
        recovers; falls through to T3 (here: T3a strict skip since
        the dir is timestamped)."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        # File 1 ms away — only T2 would catch it, T1 misses.
        self._touch(tmp_dir, "Camera_05", "6_2025-04-14T00-36-45.110Z.jpg")
        # Canonical decoy that mustn't be returned (strict skip).
        self._touch(tmp_dir, "Camera_05", "rgb", "rgb_00006.jpg")
        assert resolve_frame_path_with_window(
            tmp_dir, "Camera_05", 6, "2025-04-14T00:36:45.109Z",
            window_ms=0,
        ) is None

    def test_returns_none_when_dir_missing(self, tmp_dir):
        """Missing camera folder → ``None`` (consistent with siblings)."""
        from spatialai_data_utils.datasets.frame_paths import (
            resolve_frame_path_with_window,
        )
        assert resolve_frame_path_with_window(
            tmp_dir, "missing", 6, "2025-04-14T00:36:45.109Z",
        ) is None


# =====================================================================
# Tests for cam_dir_has_ts_encoded_frame (public library helper)
# =====================================================================

class TestCamDirHasTsEncodedFrame:
    """Tests for the lightweight 'is this dataset timestamped?'
    checker.

    Used by the per-row CLI to decide between **strict skip** (the
    dataset embeds timestamps in filenames; the bracket / nearest
    scans were authoritative) and **legacy canonical-pattern
    fallback** (the dataset is purely frame-id-keyed; no
    timestamps to drive a strict-skip rule).
    """

    def _touch(self, *parts):
        path = Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_returns_false_when_dir_missing(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import (
            cam_dir_has_ts_encoded_frame,
        )
        assert cam_dir_has_ts_encoded_frame(tmp_dir, "missing") is False

    def test_returns_false_when_only_canonical_files(self, tmp_dir):
        """Legacy frame-id-keyed dataset → False (caller falls to canonical)."""
        from spatialai_data_utils.datasets.frame_paths import (
            cam_dir_has_ts_encoded_frame,
        )
        self._touch(tmp_dir, "Camera_08", "rgb_00006.jpg")
        self._touch(tmp_dir, "Camera_08", "rgb_00007.jpg")
        assert cam_dir_has_ts_encoded_frame(tmp_dir, "Camera_08") is False

    def test_returns_true_when_any_file_has_embedded_timestamp(self, tmp_dir):
        """One timestamped file is enough — caller treats as timestamped dataset."""
        from spatialai_data_utils.datasets.frame_paths import (
            cam_dir_has_ts_encoded_frame,
        )
        self._touch(tmp_dir, "Camera_08", "rgb_00006.jpg")
        # Even one timestamp-encoded image flips the verdict.
        self._touch(tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.109Z.jpg")
        assert cam_dir_has_ts_encoded_frame(tmp_dir, "Camera_08") is True

    def test_ignores_non_image_files_with_timestamps(self, tmp_dir):
        """A `.txt` / `.json` with a timestamp doesn't flip the verdict."""
        from spatialai_data_utils.datasets.frame_paths import (
            cam_dir_has_ts_encoded_frame,
        )
        self._touch(tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.109Z.txt")
        self._touch(tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.109Z.json")
        assert cam_dir_has_ts_encoded_frame(tmp_dir, "Camera_08") is False

    def test_returns_false_for_empty_dir(self, tmp_dir):
        from spatialai_data_utils.datasets.frame_paths import (
            cam_dir_has_ts_encoded_frame,
        )
        os.makedirs(os.path.join(tmp_dir, "Camera_08"))
        assert cam_dir_has_ts_encoded_frame(tmp_dir, "Camera_08") is False


# =====================================================================
# Tests for resolve_frame_path's canonical_fallback kwarg
# =====================================================================

class TestResolveFramePathCanonicalFallbackKwarg:
    """Tests for the ``canonical_fallback`` keyword argument on
    :func:`resolve_frame_path`.

    When ``False`` the resolver returns ``None`` if the substring
    branch misses, instead of falling through to the canonical
    filename patterns.  Used by the per-row CLI's timestamp-driven
    mode where a substring miss is supposed to cascade to
    :func:`find_nearest_frame_path` (and ultimately a strict-skip
    decision via :func:`cam_dir_has_ts_encoded_frame`), not to a
    frame-id-keyed canonical guess that might point at a different
    instant.
    """

    def _touch(self, *parts):
        path = Path(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
        return str(path)

    def test_default_true_preserves_legacy_fallback(self, tmp_dir):
        """Default (no kwarg) still falls through to canonical patterns."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        canonical = self._touch(
            tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg",
        )
        # No timestamped file; substring branch misses; canonical wins.
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14T00:36:45.109Z",
        ) == canonical

    def test_explicit_false_suppresses_canonical_fallback(self, tmp_dir):
        """``canonical_fallback=False``: substring miss → None, no canonical."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        # Canonical file exists but should NOT be returned.
        self._touch(tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg")
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14T00:36:45.109Z",
            canonical_fallback=False,
        ) is None

    def test_canonical_fallback_false_with_substring_hit(self, tmp_dir):
        """Substring hit still returns the file even when canonical_fallback=False."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        match = self._touch(
            tmp_dir, "Camera_08", "006_2025-04-14T00-36-45.109Z.jpg",
        )
        # Canonical also exists but substring wins.
        self._touch(tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg")
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, timestamp="2025-04-14T00:36:45.109Z",
            canonical_fallback=False,
        ) == match

    def test_canonical_fallback_false_no_timestamp_returns_none(self, tmp_dir):
        """No timestamp arg + canonical_fallback=False → always None."""
        from spatialai_data_utils.datasets.frame_paths import resolve_frame_path
        # Canonical file exists; never reached.
        self._touch(tmp_dir, "Camera_08", "rgb", "rgb_00006.jpg")
        assert resolve_frame_path(
            tmp_dir, "Camera_08", 6, canonical_fallback=False,
        ) is None


# =====================================================================
# Tests for get_frame_paths_of_multi_cameras (post-refactor: delegates
# the non-H5 branch to resolve_frame_path; preserves the historical
# assertion-on-miss contract its callers rely on).
# =====================================================================

class TestGetFramePathsOfMultiCameras:
    """Regression coverage for the multi-camera scene-wide image resolver."""

    def _touch(self, *parts):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def test_non_h5_resolves_every_camera_via_library_helper(self, tmp_dir):
        """Each requested camera is resolved through resolve_frame_path."""
        from spatialai_data_utils.datasets.frame_paths import (
            get_frame_paths_of_multi_cameras,
        )
        # Mixed layouts across three cameras — every one a valid pattern.
        a = self._touch(tmp_dir, "Camera_01", "images", "000000006.jpg")
        b = self._touch(tmp_dir, "Camera_02", "rgb", "rgb_00006.jpg")
        c = self._touch(tmp_dir, "Camera_03", "6.jpg")  # bare fallback
        result = get_frame_paths_of_multi_cameras(
            tmp_dir, 6, cam_names=["Camera_01", "Camera_02", "Camera_03"],
        )
        assert result == {
            "Camera_01": a, "Camera_02": b, "Camera_03": c,
        }

    def test_non_h5_raises_filenotfounderror_with_listed_candidates(self, tmp_dir):
        """Missing image → FileNotFoundError whose message lists every pattern."""
        from spatialai_data_utils.datasets.frame_paths import (
            get_frame_paths_of_multi_cameras,
        )
        # Camera_04 sub-folder exists but contains no valid image.
        os.makedirs(os.path.join(tmp_dir, "Camera_04"), exist_ok=True)
        with pytest.raises(FileNotFoundError) as excinfo:
            get_frame_paths_of_multi_cameras(
                tmp_dir, 6, cam_names=["Camera_04"],
            )
        msg = str(excinfo.value)
        # Error includes the camera/frame context + every candidate path
        # so a human can eyeball why nothing matched.
        assert "Camera_04" in msg and "frame 6" in msg
        assert "Camera_04/images/000000006.jpg" in msg
        assert "Camera_04/rgb/rgb_00006.jpg" in msg
        assert "Camera_04/6.jpg" in msg

    def test_h5_branch_returns_tuple_path(self, tmp_dir):
        """H5 branch is untouched by the library delegation: returns ``(h5, key)``."""
        from spatialai_data_utils.datasets.frame_paths import (
            get_frame_paths_of_multi_cameras,
        )
        # Touch an empty file at <cam>.h5 so os.path.exists() passes.
        h5_path = os.path.join(tmp_dir, "Camera_05.h5")
        open(h5_path, "w").close()
        result = get_frame_paths_of_multi_cameras(
            tmp_dir, 6, cam_names=["Camera_05"], h5_file=True,
        )
        # Signature: (<full h5 path>, <internal key>).
        assert result == {
            "Camera_05": (h5_path, os.path.join("rgb", "rgb_00006.jpg")),
        }


# =====================================================================
# Tests for frame_paths_from_pkl_info
# =====================================================================

class TestFramePathsFromPklInfo:
    """Tests for extracting per-camera frame paths from a pkl info entry."""

    def test_extracts_paths(self):
        """Requested cameras are returned with their data paths."""
        info = {
            "cams": {
                "Camera_01": {"data_path": ("/data/scene/Camera_01.h5", "rgb/frame_0.jpg")},
                "Camera_02": {"data_path": ("/data/scene/Camera_02.h5", "rgb/frame_0.jpg")},
                "Camera_03": {"data_path": "/data/scene/Camera_03/frame_0.jpg"},
            },
        }
        result = frame_paths_from_pkl_info(info, ["Camera_01", "Camera_02"])
        assert len(result) == 2
        assert result["Camera_01"] == ("/data/scene/Camera_01.h5", "rgb/frame_0.jpg")
        assert result["Camera_02"] == ("/data/scene/Camera_02.h5", "rgb/frame_0.jpg")

    def test_missing_camera_raises(self):
        """Requesting a camera missing from the pkl raises KeyError."""
        info = {"cams": {"Camera_01": {"data_path": ("a.h5", "b.jpg")}}}
        with pytest.raises(KeyError, match="Camera_99"):
            frame_paths_from_pkl_info(info, ["Camera_01", "Camera_99"])

    def test_missing_cams_field_raises(self):
        """An info entry without a 'cams' field raises KeyError."""
        info = {"frame_idx": 0}
        with pytest.raises(KeyError, match="cams"):
            frame_paths_from_pkl_info(info, ["Camera_01"])

    def test_missing_data_path_raises(self):
        """A cam entry without a 'data_path' field raises KeyError."""
        info = {"cams": {"Camera_01": {}}}
        with pytest.raises(KeyError, match="data_path"):
            frame_paths_from_pkl_info(info, ["Camera_01"])

    def test_string_path_passthrough(self):
        """A plain string data_path is returned unchanged."""
        info = {"cams": {"cam0": {"data_path": "/some/path.jpg"}}}
        result = frame_paths_from_pkl_info(info, ["cam0"])
        assert result["cam0"] == "/some/path.jpg"

    def test_list_path_to_tuple(self):
        """A list-type data_path is converted to a tuple (h5_path, key)."""
        info = {"cams": {"cam0": {"data_path": ["/data/cam.h5", "rgb/img.jpg"]}}}
        result = frame_paths_from_pkl_info(info, ["cam0"])
        assert isinstance(result["cam0"], tuple)
        assert result["cam0"] == ("/data/cam.h5", "rgb/img.jpg")


# =====================================================================
# Tests for project_bev_objects_bbox_in_image (stage 1 of split pipeline)
# =====================================================================

class TestProjectFrameToImage:
    """Tests for the world-to-image frame projection helper."""

    def test_enriches_visible_boxes(self):
        """Visible boxes get a projection written into bbox3d.info."""
        calib = _make_calib_dict(["cam0"])
        frame = _make_nvschema_dets(2)  # all visible
        result = project_bev_objects_bbox_in_image("cam0", calib, frame)
        assert len(result) == 2
        for det in result:
            # Projection metadata lives inside the native Bbox3d.info map.
            info = det["bbox3d"]["info"]
            assert isinstance(info, dict)
            assert "sensorId" in info and isinstance(info["sensorId"], str)
            assert "vertices" in info
            assert isinstance(info["vertices"], str)
            verts = json.loads(info["vertices"])
            assert len(verts) == 8 and len(verts[0]) == 2

    def test_rejects_7dof_coordinates(self):
        """Stage 1 rejects the legacy 7-value bbox3d.coordinates format.

        NVSchema requires at least 9 values
        ``[x, y, z, w, l, h, pitch, roll, yaw]``; trailing extras are
        permitted but anything shorter than 9 raises.  Real-world
        datasets that still emit the 7-value form must be re-exported
        before consumption.
        """
        calib = _make_calib_dict(["cam0"])
        det_7dof = {
            "id": "42",
            "type": "Person",
            "confidence": 0.9,
            "coordinate": {"x": 0.0, "y": 0.0, "z": 20.0},
            "bbox3d": {
                "coordinates": [0.0, 0.0, 20.0, 0.5, 0.5, 1.8, 0.0],  # 7 values
                "embedding": [{}],
                "confidence": 0.9,
            },
        }
        with pytest.raises(ValueError, match="at least 9 values"):
            project_bev_objects_bbox_in_image("cam0", calib, [det_7dof])

    def test_sensor_id_stamped_on_projection(self):
        """bbox3d.info.sensorId matches the sensor this was projected to."""
        calib = _make_calib_dict(["cam0", "cam1"])
        frame = _make_nvschema_dets(1)
        for sid in ("cam0", "cam1"):
            result = project_bev_objects_bbox_in_image(sid, calib, frame)
            assert result
            assert result[0]["bbox3d"]["info"]["sensorId"] == sid

    def test_reprojection_overwrites_bbox3d_info(self):
        """Calling stage-1 twice overwrites the earlier projection in info."""
        calib = _make_calib_dict(["cam0", "cam1"])
        frame = _make_nvschema_dets(1)
        first = project_bev_objects_bbox_in_image("cam0", calib, frame)
        # Feed the enriched output back into a second projection.
        second = project_bev_objects_bbox_in_image("cam1", calib, first)
        assert len(second) == 1
        # The second call replaces the sensorId / vertices keys rather
        # than nesting them inside an older info dict.
        assert second[0]["bbox3d"]["info"]["sensorId"] == "cam1"

    def test_preserves_input_fields(self):
        """Top-level NVSchema fields are preserved; bbox3d is carried over."""
        calib = _make_calib_dict(["cam0"])
        frame = _make_nvschema_dets(1)
        original_keys = set(frame[0].keys())
        result = project_bev_objects_bbox_in_image("cam0", calib, frame)
        assert original_keys.issubset(set(result[0].keys()))
        assert result[0]["id"] == frame[0]["id"]
        assert result[0]["type"] == frame[0]["type"]
        assert result[0]["confidence"] == frame[0]["confidence"]
        # bbox3d sub-fields other than the newly-added info are intact.
        in_bbox, out_bbox = frame[0]["bbox3d"], result[0]["bbox3d"]
        for key in ("coordinates", "embedding", "confidence"):
            assert out_bbox[key] == in_bbox[key]

    def test_does_not_mutate_input(self):
        """The enrichment does not modify the caller's input bbox3d block."""
        calib = _make_calib_dict(["cam0"])
        frame = _make_nvschema_dets(1)
        # Snapshot both the outer dict and the nested bbox3d dict.
        outer_snapshot = dict(frame[0])
        bbox3d_snapshot = dict(frame[0]["bbox3d"])
        project_bev_objects_bbox_in_image("cam0", calib, frame)
        assert frame[0] == outer_snapshot
        assert frame[0]["bbox3d"] == bbox3d_snapshot
        # Specifically: the projection MUST NOT plant its info on the input.
        assert "info" not in frame[0]["bbox3d"]

    def test_preserves_existing_bbox3d_info_keys(self):
        """Pre-existing bbox3d.info entries on the input are merged, not replaced."""
        calib = _make_calib_dict(["cam0"])
        frame = _make_nvschema_dets(1)
        # Caller pre-populates info with a vendor tag.
        frame[0]["bbox3d"]["info"] = {"vendor_tag": "abc123"}
        result = project_bev_objects_bbox_in_image("cam0", calib, frame)
        merged = result[0]["bbox3d"]["info"]
        assert merged["vendor_tag"] == "abc123"      # preserved
        assert "sensorId" in merged                  # added
        assert "vertices" in merged                  # added

    def test_invisible_boxes_dropped(self):
        """Boxes behind the camera or off-screen are dropped from the output."""
        def _make_det(oid, x, y, z, typ):
            return {
                "id": str(oid),
                "type": typ,
                "confidence": 1.0,
                "coordinate": {"x": x, "y": y, "z": z},
                "bbox3d": {
                    "coordinates": [x, y, z, 1.5, 3.0, 1.8, 0.0, 0.0, 0.0],
                    "embedding": [{}],
                    "confidence": 1.0,
                },
            }
        calib = _make_calib_dict(["cam0"])
        frame = [
            _make_det(1, 0.0, 0.0, 20.0, "visible"),
            _make_det(2, 0.0, 0.0, -10.0, "behind"),
            _make_det(3, 1e5, 0.0, 20.0, "offscreen"),
        ]
        result = project_bev_objects_bbox_in_image("cam0", calib, frame)
        assert len(result) == 1
        assert result[0]["id"] == "1"

    def test_image_size_filters(self):
        """Custom image_size narrows the visibility window."""
        calib = _make_calib_dict(["cam0"])
        frame = _make_nvschema_dets(1)  # projects near the (320, 240) centre
        default_result = project_bev_objects_bbox_in_image("cam0", calib, frame)
        tiny_result = project_bev_objects_bbox_in_image(
            "cam0", calib, frame, image_size=(10, 10),
        )
        assert len(default_result) == 1
        assert len(tiny_result) == 0

    def test_empty_frame(self):
        """An empty input frame returns an empty list."""
        calib = _make_calib_dict(["cam0"])
        result = project_bev_objects_bbox_in_image("cam0", calib, [])
        assert result == []

    def test_missing_sensor_raises(self):
        """Unknown sensor_id raises KeyError with a helpful message."""
        calib = _make_calib_dict(["cam0"])
        frame = _make_nvschema_dets(1)
        with pytest.raises(KeyError, match="nonexistent"):
            project_bev_objects_bbox_in_image("nonexistent", calib, frame)

    def test_missing_bbox3d_raises(self):
        """Input dict without bbox3d.coordinates raises a clear KeyError."""
        calib = _make_calib_dict(["cam0"])
        bad_frame = [{"id": "1", "type": "Person", "confidence": 1.0}]
        with pytest.raises(KeyError, match="bbox3d"):
            project_bev_objects_bbox_in_image("cam0", calib, bad_frame)

    def test_roll_pitch_applied(self):
        """Non-zero pitch/roll affect the projected vertices (9-DoF path).

        A zero-pitch, zero-roll 9-DoF input must match the 7-DoF path;
        a perturbed-pitch/roll input must produce *different* vertices.
        """
        calib = _make_calib_dict(["cam0"])

        def _det(oid, pitch, roll, yaw):
            return {
                "id": str(oid), "type": "Person", "confidence": 1.0,
                "coordinate": {"x": 0.0, "y": 0.0, "z": 20.0},
                "bbox3d": {
                    "coordinates": [
                        0.0, 0.0, 20.0,  # x, y, z
                        1.5, 3.0, 1.8,   # w, l, h
                        pitch, roll, yaw,
                    ],
                    "embedding": [{}],
                    "confidence": 1.0,
                },
            }

        ref = project_bev_objects_bbox_in_image(
            "cam0", calib, [_det(1, 0.0, 0.0, 0.3)],
        )
        perturbed = project_bev_objects_bbox_in_image(
            "cam0", calib, [_det(2, 1.1, -0.7, 0.3)],
        )
        ref_verts = np.array(
            json.loads(ref[0]["bbox3d"]["info"]["vertices"])
        )
        perturbed_verts = np.array(
            json.loads(perturbed[0]["bbox3d"]["info"]["vertices"])
        )
        # Non-zero roll/pitch must shift at least one corner meaningfully.
        max_diff = np.max(np.abs(perturbed_verts - ref_verts))
        assert max_diff > 1.0, (
            f"Expected visible roll/pitch effect, got max pixel delta "
            f"{max_diff:.3e}"
        )


# =====================================================================
# Tests for draw_bev_objects_bbox_in_image (stage 2 of split pipeline)
# =====================================================================

class TestDrawFrameOnImage:
    """Tests for the frame-to-image rendering helper."""

    def _make_enriched_frame(self, n=2):
        """Project a fresh frame so it has the 2D fields."""
        calib = _make_calib_dict(["cam0"])
        return project_bev_objects_bbox_in_image("cam0", calib, _make_nvschema_dets(n))

    def test_accepts_numpy_image(self):
        """Image can be passed as a pre-loaded numpy array."""
        img = _make_image()
        frame = self._make_enriched_frame(1)
        result = draw_bev_objects_bbox_in_image(frame, img)
        assert result.shape == img.shape
        assert result.sum() > 0  # something was drawn

    def test_accepts_file_path(self, tmp_dir):
        """Image can be passed as a file path (loaded with cv2.imread)."""
        path = os.path.join(tmp_dir, "frame.jpg")
        cv2.imwrite(path, _make_image(480, 640))
        frame = self._make_enriched_frame(1)
        result = draw_bev_objects_bbox_in_image(frame, path)
        assert result.shape == (480, 640, 3)

    def test_empty_frame_returns_image_unchanged(self):
        """Empty detection list returns the input image copy untouched."""
        img = _make_image()
        result = draw_bev_objects_bbox_in_image([], img)
        np.testing.assert_array_equal(result, img)

    def test_missing_bbox3d_raises(self):
        """A dict missing the 'bbox3d' block entirely raises KeyError."""
        img = _make_image()
        # Raw NVSchema object without the bbox3d field at all.
        bad_frame = [{"id": "1", "type": "Person", "confidence": 1.0}]
        with pytest.raises(KeyError, match="'bbox3d'"):
            draw_bev_objects_bbox_in_image(bad_frame, img)

    def test_bbox3d_missing_info_raises(self):
        """A bbox3d block missing the ``info`` map raises KeyError.

        This is the state of a raw NVSchema object that hasn't yet been
        fed through :func:`project_bev_objects_bbox_in_image`.
        """
        img = _make_image()
        bad_frame = [{
            "id": "1", "type": "Person", "confidence": 1.0,
            "bbox3d": {
                "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                "embedding": [{}],
                "confidence": 1.0,
            },
        }]
        with pytest.raises(KeyError, match="info"):
            draw_bev_objects_bbox_in_image(bad_frame, img)

    def test_bbox3d_info_without_vertices_raises(self):
        """A bbox3d.info map missing ``vertices`` raises KeyError.

        Covers the partial-construction case (someone builds the dict
        manually but forgets to populate the corners).
        """
        img = _make_image()
        bad_frame = [{
            "id": "1", "type": "Person", "confidence": 1.0,
            "bbox3d": {
                "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                "embedding": [{}],
                "confidence": 1.0,
                "info": {"sensorId": "cam0"},
            },
        }]
        with pytest.raises(KeyError, match="vertices"):
            draw_bev_objects_bbox_in_image(bad_frame, img)

    def test_vertices_accepts_json_string(self):
        """bbox3d.info.vertices can be a json.dumps'd string (canonical wire form)."""
        img = _make_image()
        good_frame = [{
            "id": "1", "type": "Person", "confidence": 1.0,
            "bbox3d": {
                "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                "embedding": [{}],
                "confidence": 1.0,
                "info": {
                    "sensorId": "cam0",
                    "vertices": json.dumps([[0, 0]] * 8),
                },
            },
        }]
        result = draw_bev_objects_bbox_in_image(good_frame, img)
        assert result.shape == img.shape

    def test_vertices_invalid_json_raises(self):
        """Malformed JSON in bbox3d.info.vertices raises ValueError."""
        img = _make_image()
        bad_frame = [{
            "id": "1", "type": "Person", "confidence": 1.0,
            "bbox3d": {
                "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                "embedding": [{}],
                "confidence": 1.0,
                "info": {"sensorId": "cam0", "vertices": "not-json"},
            },
        }]
        with pytest.raises(ValueError, match="invalid JSON"):
            draw_bev_objects_bbox_in_image(bad_frame, img)

    def test_bbox3d_info_non_dict_raises(self):
        """bbox3d.info with a non-dict value (e.g. string) raises KeyError."""
        img = _make_image()
        bad_frame = [{
            "id": "1", "type": "Person", "confidence": 1.0,
            "bbox3d": {
                "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                "embedding": [{}],
                "confidence": 1.0,
                "info": "not-a-dict",  # must be a map, not a string
            },
        }]
        with pytest.raises(KeyError, match="expected a dict"):
            draw_bev_objects_bbox_in_image(bad_frame, img)

    def test_missing_file_raises(self):
        """An image path that doesn't exist raises FileNotFoundError."""
        frame = self._make_enriched_frame(1)
        with pytest.raises(FileNotFoundError):
            draw_bev_objects_bbox_in_image(frame, "/nonexistent/path.jpg")

    def test_auto_color_differs_per_track(self):
        """Auto-coloring yields different colors for distinct track IDs."""
        img = _make_image()
        frame = self._make_enriched_frame(3)
        # Raw NVSchema uses string ids; enriched frame carries distinct ones.
        ids = [det["id"] for det in frame]
        assert len(set(ids)) == 3
        # The output should be a valid annotated image.
        result = draw_bev_objects_bbox_in_image(frame, img)
        assert result.shape == img.shape
        assert result.sum() > 0

    def test_explicit_color_accepted(self):
        """A single BGR tuple overrides auto-coloring."""
        img = _make_image()
        frame = self._make_enriched_frame(1)
        result = draw_bev_objects_bbox_in_image(frame, img, color=(255, 0, 255))
        assert result.shape == img.shape

    def test_sensor_id_matches_draws(self):
        """``sensor_id`` matching the projection's sensor draws normally."""
        calib = _make_calib_dict(["cam0"])
        frame = project_bev_objects_bbox_in_image(
            "cam0", calib, _make_nvschema_dets(1),
        )
        img = _make_image()
        result = draw_bev_objects_bbox_in_image(frame, img, sensor_id="cam0")
        assert result.shape == img.shape
        assert result.sum() > 0

    def test_sensor_id_mismatch_skips_detection(self):
        """A mismatching ``sensor_id`` filter silently skips the detection."""
        calib = _make_calib_dict(["cam0"])
        frame = project_bev_objects_bbox_in_image(
            "cam0", calib, _make_nvschema_dets(1),
        )
        img = _make_image()
        # Filter for a sensor that the projected frame wasn't built for.
        result = draw_bev_objects_bbox_in_image(frame, img, sensor_id="not_cam0")
        np.testing.assert_array_equal(result, img)

    def test_no_text_labels(self):
        """draw_text_labels=False suppresses per-box labels."""
        img = _make_image()
        frame = self._make_enriched_frame(1)
        result_with = draw_bev_objects_bbox_in_image(frame, img, draw_text_labels=True)
        result_without = draw_bev_objects_bbox_in_image(frame, img, draw_text_labels=False)
        assert not np.array_equal(result_with, result_without)

    # ----- color_by kwarg -----

    def _make_mixed_type_enriched_frame(self):
        """Build a 4-dict enriched frame spanning 2 types × 2 ids per type.

        Shape is hand-crafted so the class-mode colour key (CRC of the
        raw ``type``) and the track-id-mode colour key (``id`` as int)
        necessarily fall on different ``COLOR_MAP`` slots — keeps the
        differential-rendering tests deterministic regardless of the
        50-entry palette's ordering.
        """
        # Four boxes at four non-overlapping (N, 8, 2) corners so drawing
        # never clips into the same pixels and per-box colours matter.
        frame = []
        verts_tpl = [
            [[100, 100], [200, 100], [200, 200], [100, 200],
             [100,  50], [200,  50], [200, 150], [100, 150]],
            [[300, 100], [400, 100], [400, 200], [300, 200],
             [300,  50], [400,  50], [400, 150], [300, 150]],
            [[100, 300], [200, 300], [200, 400], [100, 400],
             [100, 250], [200, 250], [200, 350], [100, 350]],
            [[300, 300], [400, 300], [400, 400], [300, 400],
             [300, 250], [400, 250], [400, 350], [300, 350]],
        ]
        for i, (tp, tid, verts) in enumerate([
            ("Person",      "10", verts_tpl[0]),
            ("Person",      "11", verts_tpl[1]),
            ("Transporter", "12", verts_tpl[2]),
            ("Transporter", "13", verts_tpl[3]),
        ]):
            frame.append({
                "id": tid, "type": tp, "confidence": 1.0,
                "bbox3d": {
                    "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                    "embedding": [{}],
                    "confidence": 1.0,
                    "info": {"sensorId": "cam0",
                             "vertices": json.dumps(verts)},
                },
            })
        return frame

    def test_color_by_class_differs_from_track_id(self):
        """Class-mode and track_id-mode produce different annotations.

        Fixture has 2 classes × 2 ids per class — track_id mode gives
        4 unique colours, class mode gives 2 (one per type) — so the
        rendered pixels are guaranteed to differ.
        """
        img = _make_image()
        frame = self._make_mixed_type_enriched_frame()
        by_id = draw_bev_objects_bbox_in_image(
            frame, img, color_by="track_id", draw_text_labels=False,
        )
        by_class = draw_bev_objects_bbox_in_image(
            frame, img, color_by="class", draw_text_labels=False,
        )
        assert not np.array_equal(by_id, by_class)

    def test_color_by_class_fifo_palette_walk(self):
        """Class-mode assigns palette slots in first-seen order.

        The underlying helper walks :data:`COLOR_MAP` in FIFO order —
        the first unique ``type`` encountered claims slot 0, the
        next distinct type claims slot 1, and so on.  Repeats reuse
        the already-assigned slot.
        """
        from spatialai_data_utils.visualization.coloring import (
            _fifo_palette_slots,
        )
        # Inline sequence: "Person" first (→0), "Transporter" next
        # (→1), a repeat Person (→0 again), and an unseen "Box" (→2).
        assert _fifo_palette_slots([
            "Person", "Transporter", "Person", "Box",
        ]) == [0, 1, 0, 2]

    def test_color_by_class_palette_wrap(self):
        """A 51st unique type reuses slot 0 (palette length == 50)."""
        from spatialai_data_utils.visualization.coloring import (
            _fifo_palette_slots,
        )
        from spatialai_data_utils.visualization import COLOR_MAP
        # All distinct so each claims its own slot; the palette has 50
        # entries, so the 51st unique type wraps back to 0.
        names = [f"Class_{i}" for i in range(len(COLOR_MAP) + 1)]
        slots = _fifo_palette_slots(names)
        assert slots[:len(COLOR_MAP)] == list(range(len(COLOR_MAP)))
        assert slots[-1] == 0, "51st unique class should wrap to slot 0"

    def test_color_by_class_same_type_same_color(self):
        """Two boxes sharing a type render with identical colour.

        Uses the library function end-to-end instead of prodding the
        helper directly — verifies the class-mode wiring preserves
        the "same type → same colour" invariant (even though the
        underlying palette slot is now FIFO rather than CRC-hashed).
        """
        img = _make_image()
        frame = self._make_mixed_type_enriched_frame()  # 2 types × 2 ids
        result_class = draw_bev_objects_bbox_in_image(
            frame, img, color_by="class", draw_text_labels=False,
        )
        result_track = draw_bev_objects_bbox_in_image(
            frame, img, color_by="track_id", draw_text_labels=False,
        )
        # class-mode renders with 2 distinct colours (2 types);
        # track-mode renders with 4 distinct colours (4 ids) — so the
        # total non-background pixel count differs even though both
        # outputs have the same number of boxes drawn.  A looser but
        # robust-to-palette-tweaks check: the two renders must differ.
        assert not np.array_equal(result_class, result_track)

    def test_color_by_default_is_track_id(self):
        """Omitted color_by behaves identically to color_by='track_id'."""
        img = _make_image()
        frame = self._make_mixed_type_enriched_frame()
        default = draw_bev_objects_bbox_in_image(frame, img, draw_text_labels=False)
        by_id = draw_bev_objects_bbox_in_image(
            frame, img, color_by="track_id", draw_text_labels=False,
        )
        np.testing.assert_array_equal(default, by_id)

    def test_color_by_invalid_raises(self):
        """Unknown color_by values are rejected with a clear ValueError."""
        img = _make_image()
        frame = self._make_enriched_frame(1)
        with pytest.raises(ValueError, match="color_by must be one of"):
            draw_bev_objects_bbox_in_image(frame, img, color_by="rainbow")

    def test_color_by_ignored_when_color_is_explicit(self):
        """Explicit ``color`` wins; ``color_by`` is never consulted then.

        Concretely: passing a bogus color_by alongside an explicit
        color must NOT raise — the validation only runs in the
        auto-color branch.
        """
        img = _make_image()
        frame = self._make_enriched_frame(1)
        result = draw_bev_objects_bbox_in_image(
            frame, img, color=(255, 0, 255), color_by="not_a_mode",
            draw_text_labels=False,
        )
        assert result.shape == img.shape

    # ---- object_class_tag filter -----------------------------------

    def _make_mixed_known_unknown_frame(self):
        """Two enriched dets: one ``"Person"`` (in warehouse) + one
        ``"Spaceship"`` (not in any config).

        The verts are placed in disjoint image quadrants so the visual
        diff between filtered / unfiltered output is unambiguous.
        """
        verts_person = [
            [50, 50], [150, 50], [150, 150], [50, 150],
            [50, 0],  [150, 0],  [150, 100], [50, 100],
        ]
        verts_spaceship = [
            [400, 300], [500, 300], [500, 400], [400, 400],
            [400, 250], [500, 250], [500, 350], [400, 350],
        ]
        return [
            {
                "id": "1", "type": "Person", "confidence": 1.0,
                "bbox3d": {
                    "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                    "embedding": [{}], "confidence": 1.0,
                    "info": {"sensorId": "cam0",
                             "vertices": json.dumps(verts_person)},
                },
            },
            {
                "id": "2", "type": "Spaceship", "confidence": 1.0,
                "bbox3d": {
                    "coordinates": [0, 0, 20, 1, 1, 1, 0, 0, 0],
                    "embedding": [{}], "confidence": 1.0,
                    "info": {"sensorId": "cam0",
                             "vertices": json.dumps(verts_spaceship)},
                },
            },
        ]

    def test_object_class_tag_filters_unknown_classes(self):
        """``object_class_tag`` drops dets whose ``type`` isn't in the config.

        Mixed frame: 1 ``"Person"`` (known to ``warehouse``) + 1
        ``"Spaceship"`` (unknown).  With the filter on, only Person
        survives — the Spaceship region of the image must therefore
        be byte-identical to a render produced from a Person-only
        frame.
        """
        img = _make_image()
        mixed_frame = self._make_mixed_known_unknown_frame()
        person_only = [mixed_frame[0]]

        result_filtered = draw_bev_objects_bbox_in_image(
            mixed_frame, img, object_class_tag="warehouse",
            draw_text_labels=False,
        )
        result_person_only = draw_bev_objects_bbox_in_image(
            person_only, img, draw_text_labels=False,
        )
        # The Spaceship verts live in y=[250, 400] / x=[400, 500];
        # sample that region only — it must be untouched by the filter
        # (i.e. match the no-Spaceship render).
        np.testing.assert_array_equal(
            result_filtered[250:400, 400:500],
            result_person_only[250:400, 400:500],
        )

    def test_object_class_tag_none_keeps_everything(self):
        """``object_class_tag=None`` is the legacy "draw every box" path.

        Backward-compat guard: existing callers (and tests) that omit
        the kwarg must still get every detection rendered, even those
        whose type would be filtered out by a config.
        """
        img = _make_image()
        mixed_frame = self._make_mixed_known_unknown_frame()
        result_no_filter = draw_bev_objects_bbox_in_image(
            mixed_frame, img, object_class_tag=None, draw_text_labels=False,
        )
        result_filtered = draw_bev_objects_bbox_in_image(
            mixed_frame, img, object_class_tag="warehouse",
            draw_text_labels=False,
        )
        # No-filter must have *strictly more* drawn pixels in the
        # Spaceship region, since the filter zeroes that detection.
        assert not np.array_equal(
            result_no_filter[250:400, 400:500],
            result_filtered[250:400, 400:500],
        )

    def test_object_class_tag_all_unknown_returns_image_unchanged(self):
        """When every det's type is unknown, the image is returned unchanged.

        Edge case: the full frame gets filtered to an empty list.
        ``draw_bev_objects_bbox_in_image`` short-circuits on an empty
        ``kept_dets`` and returns an unmodified copy of the input.
        """
        img = _make_image()
        unknown_only = [self._make_mixed_known_unknown_frame()[1]]
        result = draw_bev_objects_bbox_in_image(
            unknown_only, img, object_class_tag="warehouse",
            draw_text_labels=False,
        )
        np.testing.assert_array_equal(result, img)

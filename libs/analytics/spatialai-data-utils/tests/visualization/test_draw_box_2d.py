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

"""Tests for ``visualization.box_2d.draw_box_2d`` / ``draw_boxes_2d``.

The two helpers wrap ``cv2.rectangle`` and are used to overlay
detection rectangles on debug imagery. Coverage focuses on observable
effects on the pixel buffer, not on exercising every OpenCV path:

* drawing somewhere actually changes pixels;
* drawing nothing (empty list) leaves the buffer untouched;
* the returned object is the same canvas (in-place draw, not a copy);
* float box coords are honoured (int-cast inside the helper).
"""

import numpy as np

from spatialai_data_utils.visualization.box_2d import (
    draw_box_2d,
    draw_boxes_2d,
)


def _blank_canvas(h=100, w=200):
    return np.zeros((h, w, 3), dtype=np.uint8)


def test_draw_box_2d_writes_to_image_and_returns_same_canvas():
    img = _blank_canvas()
    out = draw_box_2d(img, [50, 30, 150, 70], color=(0, 255, 0), thinkness=2)
    assert out is img
    assert out.sum() > 0, "drawing a non-empty rectangle should change pixels"


def test_draw_box_2d_paints_with_requested_color():
    """The pixels along the rectangle outline should carry the requested
    BGR colour (allowing for slight anti-aliasing tolerance — we use
    exact compare since the helper uses default cv2.LINE_8)."""
    img = _blank_canvas()
    color = (10, 200, 30)
    out = draw_box_2d(img, [50, 30, 150, 70], color=color, thinkness=2)
    drawn_pixels = out[out.any(axis=-1)]
    assert drawn_pixels.size > 0
    unique_colors = {tuple(int(c) for c in px) for px in drawn_pixels}
    assert color in unique_colors


def test_draw_box_2d_accepts_float_coordinates():
    """Box coordinates are int-cast inside the helper — float inputs
    from upstream numpy-driven projections must work without errors."""
    img = _blank_canvas()
    out = draw_box_2d(img, [50.7, 30.2, 150.9, 70.4])
    assert out.sum() > 0


def test_draw_boxes_2d_no_op_on_empty_list():
    img = _blank_canvas()
    pristine = img.copy()
    out = draw_boxes_2d(img, [])
    np.testing.assert_array_equal(out, pristine)


def test_draw_boxes_2d_draws_each_box():
    img = _blank_canvas()
    boxes = [
        [10, 10, 40, 40],
        [60, 50, 90, 80],
        [120, 20, 180, 60],
    ]
    out = draw_boxes_2d(img, boxes, color=(255, 0, 0), thinkness=1)
    # Each box should have visible outline pixels in its sub-region.
    for x1, y1, x2, y2 in boxes:
        region = out[y1:y2 + 1, x1:x2 + 1]
        assert region.sum() > 0, f"no pixels drawn in box ({x1},{y1},{x2},{y2})"

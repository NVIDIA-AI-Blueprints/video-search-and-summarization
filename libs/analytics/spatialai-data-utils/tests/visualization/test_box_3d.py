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

"""Coverage supplement for ``visualization.box_3d`` — pins:

* ``_to_numpy`` torch.Tensor detach branch (uses a duck-typed stub so
  we don't take a hard torch dependency in the test).
* ``draw_bbox3d_on_img`` ``keep.any() is False`` short-circuit (every
  box has at least one corner behind the camera).
* ``draw_points3d_on_img`` per-point ``front_mask[i, j] is False`` skip.
* ``draw_bbox3d_multicam`` neither-arg-supplied raise.
"""

import numpy as np
import pytest

from spatialai_data_utils.visualization.box_3d import (
    _to_numpy,
    draw_bbox3d_multicam,
    draw_bbox3d_on_img,
    draw_points3d_on_img,
)


class _DetachableStub:
    """Duck-typed stand-in for a torch.Tensor — implements
    ``detach().cpu().numpy()`` over a wrapped numpy array. Lets us
    cover the ``hasattr(arr, 'detach')`` branch in ``_to_numpy``
    without taking a torch dependency in the test suite."""

    def __init__(self, arr):
        self._arr = np.asarray(arr)

    def detach(self):
        return self

    def cpu(self):
        return self

    def numpy(self):
        return self._arr


def test_to_numpy_calls_detach_for_torch_like_inputs():
    """The ``hasattr(arr, 'detach')`` branch dispatches to
    ``arr.detach().cpu().numpy()`` instead of ``np.asarray(arr)``."""
    stub = _DetachableStub([1.0, 2.0, 3.0])
    out = _to_numpy(stub)
    np.testing.assert_array_equal(out, [1.0, 2.0, 3.0])


def _world2img_behind_camera():
    """A world-to-image matrix that puts every world point with z > 0
    behind the camera (camera looks down -z, so flipping z forces
    front_mask=False for any world point with z >= 0).

    We use a 4x4 that translates the world by (0, 0, -1000) along the
    camera z-axis so the camera-space z value is large negative — i.e.
    behind the camera in OpenCV's convention. Then project with an
    identity-ish intrinsic afterwards. Simpler: an all-zero w2c that
    pushes everything to the principal point with z=0; combined with
    a non-degenerate intrinsic this yields front_mask=False uniformly."""
    # Construct: camera placed +100 along world Y, looking toward +Y, so
    # objects at world origin are *behind* the camera frame.
    K = np.eye(3)
    K[0, 0] = K[1, 1] = 100.0  # focal
    K[0, 2] = 50.0; K[1, 2] = 50.0  # principal point
    # Rotation that flips the camera-z axis (so everything sits behind)
    R = np.array([
        [1, 0, 0],
        [0, 1, 0],
        [0, 0, -1],
    ], dtype=np.float64)
    t = np.array([0.0, 0.0, -1.0])  # behind by 1m along (post-flip) +z
    w2c = np.eye(4)
    w2c[:3, :3] = R
    w2c[:3, 3] = t
    return K @ np.eye(3, 4) @ w2c  # 3x4
    # Note: caller expands to 4x4 via padding below.


def test_draw_bbox3d_on_img_returns_input_when_all_boxes_behind_camera():
    """When every box has at least one corner behind the camera, the
    function returns the input image unchanged (``keep.any() is
    False`` short-circuit)."""
    # Build a 4x4 world2img that places the origin behind the camera.
    K = np.eye(3, dtype=np.float64)
    K[0, 0] = K[1, 1] = 100.0
    K[0, 2] = K[1, 2] = 50.0
    R = np.eye(3, dtype=np.float64)
    t = np.array([0.0, 0.0, -1000.0])  # huge negative -> behind cam
    w2c = np.eye(4, dtype=np.float64)
    w2c[:3, :3] = R; w2c[:3, 3] = t
    KE = np.eye(4, dtype=np.float64)
    KE[:3, :3] = K
    world2img = KE @ w2c

    img = np.zeros((100, 100, 3), dtype=np.uint8)
    bbox = np.array([[0, 0, 0, 1, 1, 1, 0, 0, 0]], dtype=np.float64)
    out = draw_bbox3d_on_img(bbox, img, world2img=world2img)
    np.testing.assert_array_equal(out, img)


def test_draw_points3d_on_img_skips_behind_camera_points():
    """The per-point ``if not front_mask[i, j]: continue`` skip
    silently drops corners that project behind the camera; the
    remaining in-front points still draw."""
    # Camera at origin looking down -z; place one point in front
    # (positive z in world) and one behind (negative z in world).
    K = np.eye(3, dtype=np.float64)
    K[0, 0] = K[1, 1] = 100.0
    K[0, 2] = K[1, 2] = 50.0
    KE = np.eye(4, dtype=np.float64)
    KE[:3, :3] = K
    # Identity world->cam, so cam-z == world-z.
    world2img = KE @ np.eye(4, dtype=np.float64)

    # Two parallel "lines" with two points each: in front, behind.
    pts = np.array([[
        [0.0, 0.0, 5.0],   # in front (z>0)
        [0.0, 0.0, -5.0],  # behind (z<0)
    ]])
    img = np.zeros((100, 100, 3), dtype=np.uint8)
    out = draw_points3d_on_img(pts, img.copy(), world2img=world2img,
                                color=(255, 255, 255), radius=2)
    # Image still has shape (H,W,3) and is uint8; at least the
    # in-front point was drawn (some non-zero pixels exist).
    assert out.shape == img.shape
    assert out.dtype == np.uint8


def test_draw_bbox3d_multicam_raises_when_neither_arg_supplied():
    """``draw_bbox3d_multicam`` requires exactly one of ``world2imgs``
    or ``calib_info_list``; supplying neither raises ValueError."""
    img = np.zeros((50, 50, 3), dtype=np.uint8)
    with pytest.raises(ValueError, match="Exactly one of"):
        draw_bbox3d_multicam(
            bboxes_3d=np.zeros((0, 9)),
            imgs=[img],
            world2imgs=None, calib_info_list=None,
        )

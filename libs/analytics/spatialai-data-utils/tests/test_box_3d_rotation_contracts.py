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

"""Regression tests for the API-shape contracts established by the
``[roll, pitch, yaw]`` → ``[pitch, roll, yaw]`` rename and the
``process_bbox3d_gt`` move.  Also locks in the removal of the BEVFormer
model-output loader (its restoration requires implementation + test
coverage of the nuScenes → NVSchema normalization first).

These are pure documentation/sanity guards.  Numeric correctness of
:func:`spatialai_data_utils.core.boxes.box_3d.box3d_to_corners` is covered
by ``test_projection.py`` (especially
``test_9dof_composite_rotation_matches_hand_computed`` and the bottom-face
/ origin tests around it); this file deliberately does not duplicate that.
"""

import numpy as np
import pytest


def test_euler_to_quaternion_positional_only():
    """Old-style kwargs on euler_to_quaternion must raise, not silently swap axes.

    After the ``[roll, pitch, yaw]`` → ``[pitch, roll, yaw]`` parameter
    rename, a caller using the old keyword form
    ``euler_to_quaternion(roll=θ, pitch=φ, yaw=ψ)`` would silently swap
    which axis each value rotates about — Python's keyword binding
    matches the names, not the positions.  The function is therefore
    declared positional-only via the trailing ``/`` in its signature.
    Removing that ``/`` would re-open the silent miscomputation hazard.
    """
    from spatialai_data_utils.core.geometry.rotation import euler_to_quaternion

    # Positional still works unchanged.
    q = euler_to_quaternion(0.1, 0.2, 0.3)
    assert len(q) == 4

    # Both old and new keyword names are rejected — no caller should
    # be relying on either kwargs form.
    with pytest.raises(TypeError, match="positional-only"):
        euler_to_quaternion(roll=0.1, pitch=0.2, yaw=0.3)
    with pytest.raises(TypeError, match="positional-only"):
        euler_to_quaternion(pitch=0.1, roll=0.2, yaw=0.3)


def test_euler_quat_round_trip_machine_precision():
    """quat → euler → quat must round-trip to machine precision.

    Locks in the renamed implementations of
    :func:`euler_from_quaternion` and :func:`euler_to_quaternion`
    against any future refactor that reorders axes or flips signs.
    """
    from spatialai_data_utils.core.geometry.rotation import (
        euler_from_quaternion,
        euler_to_quaternion,
    )

    rng = np.random.default_rng(1234)
    for _ in range(20):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        eul = euler_from_quaternion(*q)
        q2 = euler_to_quaternion(*eul)
        # ``q`` and ``-q`` encode the same rotation, so accept either.
        d = min(
            max(abs(q[i] - q2[i]) for i in range(4)),
            max(abs(q[i] + q2[i]) for i in range(4)),
        )
        assert d < 1e-10, d


def test_box3d_heading_face_at_negative_y_at_yaw_zero():
    """BOX3D_HEADING_FACE corner indices must lie on ``y = -l / 2`` at ``yaw = 0``.

    The post-rename docstring of
    :data:`spatialai_data_utils.core.boxes.box_3d.BOX3D_HEADING_FACE`
    declares "front in -Y at yaw = 0" as a public contract; the
    renderers (heading-face shading in ``draw_box3d_corners_on_img``,
    BEV bottom-face quad in ``draw_bbox3d_on_bev``) all rely on this.
    A future tidy-up of the corner ordering must keep this invariant.
    """
    from spatialai_data_utils.core.boxes.box_3d import (
        BOX3D_HEADING_FACE,
        box3d_to_corners,
    )

    box = np.array([[0, 0, 0, 1.0, 4.0, 1.0, 0, 0, 0]])  # w=1, l=4, h=1, all rot=0
    corners = box3d_to_corners(box)[0]
    ys = corners[list(BOX3D_HEADING_FACE), 1]
    assert np.allclose(ys, -2.0), ys  # all four corners on y = -l/2


def test_box3d_layout_columns_match_renamed_constants():
    """PITCH=6, ROLL=7, YAW=8 (post-rename) is a public-API contract."""
    from spatialai_data_utils.core.boxes import box_3d

    assert (box_3d.PITCH, box_3d.ROLL, box_3d.YAW) == (6, 7, 8)


def test_bevformer_loader_removed():
    """The BEVFormer model-output loader module must stay removed.

    ``spatialai_data_utils.loaders.bevformer`` (and the dependent
    ``spatialai_data_utils.converters.nusc_results_pp``) were removed
    in this version pending implementation + test coverage of the
    nuScenes → NVSchema normalization (size swap + heading-axis
    offset).  Restoring either module without adding fixtures that
    validate both transforms would silently re-introduce the latent
    width/length transposition and 90° heading offset; this guard
    forces a contributor to update the test suite at the same time.

    Note: this does **not** restrict the active BEVFormer-format
    calibration reader
    (:func:`spatialai_data_utils.loaders.calibration.load_calib_into_dict_from_bevformer`)
    or the AICity'24 ``ground_truth_bevformer.json`` GT-format reader
    — those are on-disk file-format concerns, not model-output
    converters.
    """
    with pytest.raises(ModuleNotFoundError):
        import spatialai_data_utils.loaders.bevformer  # noqa: F401
    with pytest.raises(ModuleNotFoundError):
        import spatialai_data_utils.converters.nusc_results_pp  # noqa: F401


def test_back_compat_reexport_is_identical():
    """Old import path in ``core.boxes.box_3d`` for ``process_bbox3d_gt`` must still resolve.

    ``process_bbox3d_gt`` now lives in ``loaders.ground_truth`` but
    the original import site is kept working via a re-export in
    ``core/boxes/box_3d.py``.  An ``is``-comparison guards against a
    future refactor accidentally shadowing the symbol (which would
    silently break any external user still importing from the old
    path).
    """
    from spatialai_data_utils.core.boxes.box_3d import (
        process_bbox3d_gt as gt_via_old,
    )
    from spatialai_data_utils.loaders.ground_truth import (
        process_bbox3d_gt as gt_via_new,
    )

    assert gt_via_old is gt_via_new

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

"""Tests for ``core.boxes.aicity_box.AICityBox``.

The class is a thin ``nuscenes.utils.data_classes.Box`` subclass that
attaches three MTMC-tracking-only metadata fields. The tests pin:

* the base ``Box`` geometry kwargs are passed through unchanged
  (center / size / orientation / label / score / velocity / name /
  token), and
* the new fields default to ``None`` and round-trip when supplied.

Box geometry semantics are owned by upstream nuScenes — we don't
re-test those here.
"""

import numpy as np
from nuscenes.utils.data_classes import Box as NuScenesBox
from pyquaternion import Quaternion

from spatialai_data_utils.core.boxes.aicity_box import AICityBox


def _basic_box(**overrides):
    kwargs = dict(
        center=[1.0, 2.0, 3.0],
        size=[0.5, 1.0, 1.8],
        orientation=Quaternion(axis=[0, 0, 1], angle=0.5),
    )
    kwargs.update(overrides)
    return AICityBox(**kwargs)


def test_inherits_from_nuscenes_box():
    """Downstream nuScenes utilities (corner derivation, render helpers)
    rely on ``isinstance(box, NuScenesBox)`` — pin that subclass tie."""
    box = _basic_box()
    assert isinstance(box, NuScenesBox)


def test_geometry_kwargs_pass_through_unchanged():
    box = _basic_box()
    np.testing.assert_allclose(box.center, [1.0, 2.0, 3.0])
    # NuScenes Box stores size as [w, l, h] in the .wlh attribute.
    np.testing.assert_allclose(box.wlh, [0.5, 1.0, 1.8])
    assert np.isnan(box.label)
    assert np.isnan(box.score)
    assert all(np.isnan(v) for v in box.velocity)


def test_extras_default_to_none():
    """A box constructed with only the base kwargs must leave all three
    MTMC metadata fields as ``None`` so callers can detect 'unset'."""
    box = _basic_box()
    assert box.embed is None
    assert box.reid_embed is None
    assert box.visibility_scores is None


def test_extras_round_trip_when_provided():
    embed = np.array([0.1, 0.2, 0.3])
    reid = np.array([1.0, 2.0])
    vis = np.array([0.9, 0.8, 0.7])
    box = _basic_box(embed=embed, reid_embed=reid, visibility_scores=vis)
    assert box.embed is embed
    assert box.reid_embed is reid
    assert box.visibility_scores is vis


def test_name_and_token_are_stored():
    box = _basic_box(name="Person", token="track-42")
    assert box.name == "Person"
    assert box.token == "track-42"

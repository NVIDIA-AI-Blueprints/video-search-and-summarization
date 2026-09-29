# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import numpy as np
import pytest
from .segmenter import BottleInstance, BottleSegmenter, mask_to_polygons


def test_mask_contour_keeps_nonrectangular_shape():
    mask = np.zeros((100, 200), bool)
    mask[10:80, 20:60] = True
    mask[60:80, 60:120] = True
    rings = mask_to_polygons(mask)
    assert len(rings) == 1
    assert len(rings[0]) >= 6  # actual L contour, not a fabricated bounding rectangle
    assert all(0 <= value <= 1 for point in rings[0] for value in point)
    xs = [point[0] for point in rings[0]]
    assert min(xs) == .1
    assert max(xs) == .595


def test_disconnected_real_mask_components_preserved():
    mask = np.zeros((60, 80), bool)
    mask[5:20, 4:15] = True
    mask[35:50, 55:70] = True
    assert len(mask_to_polygons(mask)) == 2


def test_empty_mask_has_no_fake_polygon():
    assert mask_to_polygons(np.zeros((60, 80), bool)) == []


def test_pixel_box_is_original_frame_and_mask_not_serialized():
    mask = np.zeros((720, 1280), bool)
    mask[144:504, 256:512] = True
    instance = BottleInstance("bottle-1", [.2, .2, .2, .5],
                              mask_to_polygons(mask), .9, mask, {"checkpoint_sha256": "actual"})
    assert instance.box_pixels == [256, 144, 256, 360]
    data = instance.to_json()
    assert data["box_pixels"] == [256, 144, 256, 360]
    assert data["mask_area_pixels"] == 256 * 360
    assert "raw_mask" not in data


def test_invalid_frame_rejected_before_inference():
    segmenter = object.__new__(BottleSegmenter)
    with pytest.raises(ValueError, match="uint8"):
        segmenter.predict(np.zeros((10, 10), np.float32))


def test_invalid_mask_shape_rejected():
    with pytest.raises(ValueError, match="shape"):
        mask_to_polygons(np.zeros((3, 4, 5), bool))

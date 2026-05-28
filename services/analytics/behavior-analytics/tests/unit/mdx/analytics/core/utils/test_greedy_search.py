# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import pytest
from shapely.geometry import Polygon

from mdx.analytics.core.utils.greedy_search import (
    two_pallets_cross,
    can_place_pallet,
    place_pallet,
    convert_pallet_bottom_left_loc_to_polygon,
    greedy_search,
)


class TestTwoPalletsCross:
    """Tests for two_pallets_cross function."""

    def test_non_overlapping_pallets(self):
        """Non-overlapping pallets should return 0."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly2 = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
        result = two_pallets_cross(poly1, poly2)
        assert result == 0

    def test_overlapping_pallets(self):
        """Overlapping pallets should return 1."""
        poly1 = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        poly2 = Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])
        result = two_pallets_cross(poly1, poly2)
        assert result == 1

    def test_touching_pallets(self):
        """Touching (but not overlapping) pallets should return 0."""
        poly1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        poly2 = Polygon([(1, 0), (2, 0), (2, 1), (1, 1)])
        result = two_pallets_cross(poly1, poly2)
        assert result == 0


class TestCanPlacePallet:
    """Tests for can_place_pallet function."""

    def test_can_place_in_empty_area(self):
        """Should be able to place pallet in empty area."""
        pallet = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        existing_pallets = []
        result = can_place_pallet(pallet, existing_pallets)
        assert result is True

    def test_can_place_next_to_existing(self):
        """Should be able to place pallet next to existing one."""
        pallet = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        existing = [Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])]
        result = can_place_pallet(pallet, existing)
        assert result is True

    def test_cannot_place_overlapping(self):
        """Should not be able to place overlapping pallet."""
        pallet = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
        existing = [Polygon([(1, 1), (3, 1), (3, 3), (1, 3)])]
        result = can_place_pallet(pallet, existing)
        assert result is False


class TestPlacePallet:
    """Tests for place_pallet function."""

    def test_place_pallet_adds_to_lists(self):
        """Placing a pallet should add it to both lists."""
        pallet = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
        all_pallets = []
        placed_pallets = []
        
        result_all, result_placed = place_pallet(pallet, all_pallets, placed_pallets)
        
        assert len(result_all) == 1
        assert len(result_placed) == 1
        assert result_all[0] == pallet
        assert result_placed[0] == pallet


class TestConvertPalletBottomLeftLocToPolygon:
    """Tests for convert_pallet_bottom_left_loc_to_polygon function."""

    def test_unit_pallet(self):
        """Test creating a unit-sized pallet."""
        loc = (0, 0)
        result = convert_pallet_bottom_left_loc_to_polygon(loc, pallet_width=1)
        
        expected_coords = [(0, 0), (1, 0), (1, 1), (0, 1), (0, 0)]
        actual_coords = list(result.exterior.coords)
        
        assert len(actual_coords) == 5  # Including closing point
        for actual, expected in zip(actual_coords, expected_coords):
            assert pytest.approx(actual[0]) == expected[0]
            assert pytest.approx(actual[1]) == expected[1]

    def test_custom_width_pallet(self):
        """Test creating a pallet with custom width."""
        loc = (2, 3)
        result = convert_pallet_bottom_left_loc_to_polygon(loc, pallet_width=2.5)
        
        # Bottom-left should be (2, 3), top-right should be (4.5, 5.5)
        bounds = result.bounds
        assert pytest.approx(bounds[0]) == 2.0  # min x
        assert pytest.approx(bounds[1]) == 3.0  # min y
        assert pytest.approx(bounds[2]) == 4.5  # max x
        assert pytest.approx(bounds[3]) == 5.5  # max y


class TestGreedySearch:
    """Tests for greedy_search function."""

    def test_empty_area(self):
        """Test greedy search on empty area."""
        area_width = 5.0
        area_height = 5.0
        pallet_width = 1.0
        existing_pallets = []
        grid_size = 1.0
        
        result = greedy_search(area_width, area_height, pallet_width, existing_pallets, grid_size)
        
        # Should be able to place multiple pallets
        assert len(result) > 0
        # All results should be Polygon objects
        for pallet in result:
            assert isinstance(pallet, Polygon)

    def test_with_existing_pallets(self):
        """Test greedy search with existing pallets."""
        area_width = 5.0
        area_height = 5.0
        pallet_width = 1.0
        # Place an existing pallet at (0, 0)
        existing_pallets = [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])]
        grid_size = 1.0
        
        result = greedy_search(area_width, area_height, pallet_width, existing_pallets, grid_size)
        
        # Result should not overlap with existing pallet
        for placed in result:
            for existing in existing_pallets:
                assert not (placed.intersects(existing) and not placed.touches(existing))

    def test_small_area(self):
        """Test greedy search on area that fits one pallet."""
        area_width = 1.0
        area_height = 1.0
        pallet_width = 1.0
        existing_pallets = []
        grid_size = 0.5
        
        result = greedy_search(area_width, area_height, pallet_width, existing_pallets, grid_size)
        
        # Should fit exactly 1 pallet
        assert len(result) == 1

    def test_no_room(self):
        """Test greedy search when no room for new pallets."""
        area_width = 2.0
        area_height = 2.0
        pallet_width = 1.0
        # Fill the area completely
        existing_pallets = [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(1, 0), (2, 0), (2, 1), (1, 1)]),
            Polygon([(0, 1), (1, 1), (1, 2), (0, 2)]),
            Polygon([(1, 1), (2, 1), (2, 2), (1, 2)]),
        ]
        grid_size = 0.5
        
        result = greedy_search(area_width, area_height, pallet_width, existing_pallets, grid_size)
        
        # Should not be able to place any more pallets
        assert len(result) == 0


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


import numpy as np
from shapely.geometry import Polygon


def two_pallets_cross(poly1: Polygon, poly2: Polygon) -> int:
    """
    Check if two pallets (represented as polygons) intersect.

    :param Polygon poly1: First pallet polygon.
    :param Polygon poly2: Second pallet polygon.
    :return int: 1 if pallets intersect, 0 otherwise.

    Examples::
        >>> poly1 = Polygon([(0,0), (1,0), (1,1), (0,1)])
        >>> poly2 = Polygon([(0.5,0.5), (1.5,0.5), (1.5,1.5), (0.5,1.5)])
        >>> result = two_pallets_cross(poly1, poly2)
        >>> print(f"Pallets cross: {result}")
    """
    flag_cross = 0
    if poly1.intersects(poly2) and not poly1.touches(poly2):
        flag_cross = 1

    return flag_cross


def can_place_pallet(pallet: Polygon, existing_pallets: list[Polygon]) -> bool:
    """
    Check if a pallet can be placed without intersecting any existing pallets.

    :param Polygon pallet: Polygon representing the pallet to place.
    :param list existing_pallets: List of Polygon objects representing existing pallets.
    :return bool: True if pallet can be placed, False otherwise.

    Examples::
        >>> new_pallet = Polygon([(0,0), (1,0), (1,1), (0,1)])
        >>> existing = [Polygon([(2,2), (3,2), (3,3), (2,3)])]
        >>> can_place = can_place_pallet(new_pallet, existing)
        >>> print(f"Can place pallet: {can_place}")
    """
    poly1 = pallet
    for poly2 in existing_pallets:
        if two_pallets_cross(poly1, poly2):
            return False
    return True


def place_pallet(pallet: Polygon, all_pallets: list[Polygon], placed_pallets: list[Polygon]) -> tuple[list[Polygon], list[Polygon]]:
    """
    Add a pallet to both the list of all pallets and the list of newly placed pallets.

    :param Polygon pallet: Polygon representing the pallet to place.
    :param list all_pallets: List of all pallet polygons.
    :param list placed_pallets: List of newly placed pallet polygons.
    :return tuple: Tuple of updated (all_pallets, placed_pallets).

    Examples::
        >>> new_pallet = Polygon([(0,0), (1,0), (1,1), (0,1)])
        >>> all_pallets = []
        >>> placed_pallets = []
        >>> all_pallets, placed_pallets = place_pallet(new_pallet, all_pallets, placed_pallets)
        >>> print(f"Number of placed pallets: {len(placed_pallets)}")
    """
    all_pallets.append(pallet)
    placed_pallets.append(pallet)

    return all_pallets, placed_pallets


def convert_pallet_bottom_left_loc_to_polygon(loc: tuple[float, float], pallet_width: float = 1) -> Polygon:
    """
    Convert a pallet's bottom-left location to a Polygon object.

    :param tuple loc: (x,y) coordinates of the bottom-left corner.
    :param float pallet_width: Width of the pallet (default: 1).
    :return Polygon: Polygon object representing the pallet.

    Examples::
        >>> loc = (0, 0)
        >>> pallet = convert_pallet_bottom_left_loc_to_polygon(loc, pallet_width=1.0)
        >>> print(f"Pallet vertices: {list(pallet.exterior.coords)}")
    """
    x, y = loc
    x_min = x
    x_max = x + pallet_width
    y_min = y
    y_max = y + pallet_width

    polygon = Polygon([(x_min, y_min), (x_max, y_min), (x_max, y_max), (x_min, y_max)])

    return polygon


def greedy_search(area_width: float, area_height: float, pallet_width: float, existing_pallets: list[Polygon], grid_size: float) -> list[Polygon]:
    """
    Perform a greedy search to find optimal pallet placement in a given area.
    The algorithm tries to place pallets from top-left to bottom-right,
    placing each pallet in the first valid position it finds.

    :param float area_width: Width of the available area.
    :param float area_height: Height of the available area.
    :param float pallet_width: Width of each pallet.
    :param list existing_pallets: List of Polygon objects representing existing pallets.
    :param float grid_size: Size of the grid for discretizing the space.
    :return list: List of Polygon objects representing the placed pallets.

    Examples::
        >>> area_width = 10.0
        >>> area_height = 10.0
        >>> pallet_width = 1.0
        >>> existing_pallets = []
        >>> grid_size = 0.1
        >>> placed_pallets = greedy_search(area_width, area_height, pallet_width, existing_pallets, grid_size)
        >>> print(f"Number of pallets placed: {len(placed_pallets)}")
    """
    num_grid_per_pallet = int(pallet_width / grid_size)
    num_grid_width = int(area_width / grid_size)
    num_grid_height = int(area_height / grid_size)
    x_candidate_list: list[float] = [round(v, 1) for v in np.linspace(0, area_width - pallet_width, num_grid_width - num_grid_per_pallet + 1)]
    y_candidate_list: list[float] = [round(v, 1) for v in np.linspace(0, area_height - pallet_width, num_grid_height - num_grid_per_pallet + 1)]

    placed_pallets = []
    all_pallets = existing_pallets.copy()
    # Greedily try to place pallets from top-left to bottom-right
    for y in y_candidate_list:
        for x in x_candidate_list:
            loc = (x, y)
            pallet = convert_pallet_bottom_left_loc_to_polygon(loc, pallet_width)
            if can_place_pallet(pallet, all_pallets):
                all_pallets, placed_pallets = place_pallet(pallet, all_pallets, placed_pallets)
    return placed_pallets

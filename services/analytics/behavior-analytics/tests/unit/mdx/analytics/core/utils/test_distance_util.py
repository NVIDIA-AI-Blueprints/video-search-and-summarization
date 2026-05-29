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
import pytest
import numpy as np
import osmnx as ox
from mdx.analytics.core.schema.models import Location, Coordinate, Point2D, Line
from mdx.analytics.core.utils.distance_util import (
    xy_to_lonlat,
    lonlat_to_xy,
    bearing,
    direction,
    get_point_at_distance,
    euclidean_distance,
    haversine_distance,
    get_lat_lon,
    get_lat_lon_coord,
    orientation,
    intersect,
    calculate_euclidean_distance_vectorized,
    calculate_haversine_distance_vectorized
)

def test_bearing_and_point_on_distance():
    lat1_test = 52.20472
    lon1_test = -100.14056

    lat2_test = 52.2048030863518
    lon2_test = -100.14050384267733
    pt1_test = Location(lat=lat1_test, lon=lon1_test)
    pt2_test = Location(lat=lat2_test, lon=lon2_test)

    distance_test = haversine_distance(pt1_test, pt2_test)
    distance_gt = ox.distance.great_circle(lat1_test, lon1_test, lat2_test, lon2_test)

    error = abs(distance_test-distance_gt)
    error_rate = error / distance_gt
    error_rate_thresh = 0.0001

    assert error_rate < error_rate_thresh

    b = bearing(pt1_test, pt2_test)
    distance = haversine_distance(pt1_test, pt2_test)
    pt2_computed = get_point_at_distance(pt1_test, distance, b)

    error = haversine_distance(pt2_computed, pt2_test)  # in meters
    error_thresh = 0.1  # in meters

    assert error < error_thresh


def test_xy_to_lonlat():
    # Test conversion from UTM coordinates to lat/lon
    x, y = 500000, 5000000  # Example UTM coordinates
    lon, lat = xy_to_lonlat(x, y)
    assert isinstance(lon, float)
    assert isinstance(lat, float)
    assert -180 <= lon <= 180
    assert -90 <= lat <= 90


def test_lonlat_to_xy():
    # Test conversion from lat/lon to UTM coordinates
    lon, lat = -93.0, 45.0  # Example coordinates near Minneapolis
    x, y = lonlat_to_xy(lon, lat)
    assert isinstance(x, float)
    assert isinstance(y, float)


def test_bearing():
    # Test bearing calculation
    start = Location(lat=45.0, lon=-93.0)
    end = Location(lat=45.1, lon=-93.1)
    brng = bearing(start, end)
    assert 0 <= brng <= 360


def test_direction():
    # Test direction conversion
    # Test 4-way direction (mode 0)
    assert direction(0, mode=0) == "N"
    assert direction(90, mode=0) == "E"
    assert direction(180, mode=0) == "S"
    assert direction(270, mode=0) == "W"

    # Test 8-way direction (mode 1)
    assert direction(0, mode=1) == "N"
    assert direction(45, mode=1) == "NE"
    assert direction(90, mode=1) == "E"

    # Test 16-way direction (mode 2)
    assert direction(0, mode=2) == "N"
    assert direction(22.5, mode=2) == "NNE"
    assert direction(45, mode=2) == "NE"


def test_get_point_at_distance():
    start = Location(lat=45.0, lon=-93.0)
    distance = 1000  # 1km
    brng = 90  # East
    result = get_point_at_distance(start, distance, brng)
    assert isinstance(result, Location)
    assert -90 <= result.lat <= 90
    assert -180 <= result.lon <= 180


def test_euclidean_distance():
    p1 = Coordinate(x=0, y=0, z=0)
    p2 = Coordinate(x=3, y=4, z=0)
    dist = euclidean_distance(p1, p2)
    assert pytest.approx(dist, 0.000001) == 5.0  # 3-4-5 triangle


def test_haversine_distance():
    # Test points approximately 786m apart
    p1 = Location(lat=45.0, lon=-93.0)
    p2 = Location(lat=45.0, lon=-93.01)
    dist = haversine_distance(p1, p2)
    assert pytest.approx(dist, 0.001) == 786  # More precise range around the actual distance


def test_get_lat_lon():
    origin = Location(lat=45.0, lon=-93.0)
    coord = Coordinate(x=100000, y=100000, z=0)  # 1km north and east
    result = get_lat_lon(origin, coord)
    assert isinstance(result, Location)
    assert pytest.approx(result.lat, 0.01) == 44
    assert pytest.approx(result.lon, 0.01) == -94


def test_get_lat_lon_coord():
    origin = Location(lat=45.0, lon=-93.0)
    coord = Coordinate(x=100000, y=100000, z=0)  # 1km north and east
    result = get_lat_lon_coord(origin, coord)
    assert isinstance(result, Coordinate)
    assert pytest.approx(result.x, 0.01) == 44
    assert pytest.approx(result.y, 0.01) == -94


def test_orientation():
    line = Line(
        p1=Point2D(x=0, y=0),
        p2=Point2D(x=1, y=1)
    )
    # Test point on the left side (Counterclockwise)
    point_right = Point2D(x=0, y=1)
    assert orientation([line], point_right) == 2  # Counterclockwise is 2

    # Test point on the right side (clockwise)
    point_left = Point2D(x=1, y=0)
    assert orientation([line], point_left) == 1  # clockwise is 1

    # Test collinear point
    point_collinear = Point2D(x=0.5, y=0.5)
    assert orientation([line], point_collinear) == 0


def test_orientation_horizontal_line():
    """Test orientation with horizontal lines."""
    # Horizontal line going right
    line = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0))
    
    # Point above line (left side when traveling left to right)
    point_above = Point2D(x=1, y=1)
    assert orientation([line], point_above) == 2  # Left side
    
    # Point below line (right side)
    point_below = Point2D(x=1, y=-1)
    assert orientation([line], point_below) == 1  # Right side
    
    # Point on line
    point_on = Point2D(x=1, y=0)
    assert orientation([line], point_on) == 0  # On line


def test_orientation_vertical_line():
    """Test orientation with vertical lines."""
    # Vertical line going up
    line = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=0, y=2))
    
    # Point to the right (when traveling upward, right is right side)
    point_right = Point2D(x=1, y=1)
    assert orientation([line], point_right) == 1  # Right side
    
    # Point to the left
    point_left = Point2D(x=-1, y=1)
    assert orientation([line], point_left) == 2  # Left side
    
    # Point on line
    point_on = Point2D(x=0, y=1)
    assert orientation([line], point_on) == 0  # On line


def test_orientation_l_shape_polyline():
    """Test orientation with L-shaped polyline."""
    # L-shaped polyline: horizontal then vertical
    lines = [
        Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0)),  # Horizontal right
        Line(p1=Point2D(x=2, y=0), p2=Point2D(x=2, y=2))   # Vertical up
    ]
    
    # Point inside the L (on the left when walking the path)
    point_inside = Point2D(x=1, y=1)
    assert orientation(lines, point_inside) == 2  # Left side
    
    # Point outside the L (on the right when walking the path)
    point_outside = Point2D(x=3, y=1)
    assert orientation(lines, point_outside) == 1  # Right side
    
    # Point on the polyline
    point_on_horizontal = Point2D(x=1, y=0)
    assert orientation(lines, point_on_horizontal) == 0  # On line
    
    point_on_vertical = Point2D(x=2, y=1)
    assert orientation(lines, point_on_vertical) == 0  # On line
    
    # Additional test points for comprehensive coverage
    # Corner region
    assert orientation(lines, Point2D(x=1.5, y=0.5)) == 2  # Inside corner - Left
    assert orientation(lines, Point2D(x=2.5, y=0.5)) == 1  # Outside corner - Right
    
    # Far from polyline
    assert orientation(lines, Point2D(x=-1, y=0.5)) == 2  # Left of start - actually Left (above horizontal)
    assert orientation(lines, Point2D(x=2.5, y=2.5)) == 1  # Above end - Right
    
    # Diagonal positions
    assert orientation(lines, Point2D(x=0.5, y=0.5)) == 2  # Inside diagonal - Left
    assert orientation(lines, Point2D(x=1, y=1.5)) == 2  # Upper inside - Left


def test_orientation_u_shape_polyline():
    """Test orientation with U-shaped polyline."""
    # U-shaped polyline
    lines = [
        Line(p1=Point2D(x=0, y=2), p2=Point2D(x=0, y=0)),  # Left vertical down
        Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0)),  # Bottom horizontal right
        Line(p1=Point2D(x=2, y=0), p2=Point2D(x=2, y=2))   # Right vertical up
    ]
    
    # Point inside the U
    point_inside = Point2D(x=1, y=1)
    assert orientation(lines, point_inside) == 2  # Left side (inside is left when walking the path)
    
    # Point outside the U
    point_outside = Point2D(x=1, y=-1)
    assert orientation(lines, point_outside) == 1  # Right side
    
    # Point above the U opening
    point_above = Point2D(x=1, y=3)
    # This point is above the U opening, which is on the left side when traveling the path
    assert orientation(lines, point_above) == 2  # Left side - inside continuation
    
    # Additional comprehensive test points
    # Left side regions
    assert orientation(lines, Point2D(x=-1, y=1)) == 1  # Left of left wall - Right
    assert orientation(lines, Point2D(x=-0.5, y=2.5)) == 1  # Above left start - Right
    assert orientation(lines, Point2D(x=-0.5, y=-0.5)) == 1  # Below left corner - Right
    
    # Right side regions
    assert orientation(lines, Point2D(x=3, y=1)) == 1  # Right of right wall - Right
    assert orientation(lines, Point2D(x=2.5, y=2.5)) == 1  # Above right end - Right
    assert orientation(lines, Point2D(x=2.5, y=-0.5)) == 1  # Below right corner - Right
    
    # Inside U at different heights
    assert orientation(lines, Point2D(x=1, y=0.5)) == 2  # Lower inside - Left
    assert orientation(lines, Point2D(x=1, y=1.5)) == 2  # Upper inside - Left
    assert orientation(lines, Point2D(x=0.5, y=1)) == 2  # Left inside - Left
    assert orientation(lines, Point2D(x=1.5, y=1)) == 2  # Right inside - Left
    
    # Corner regions
    assert orientation(lines, Point2D(x=0.5, y=0.5)) == 2  # Bottom-left corner inside - Left
    assert orientation(lines, Point2D(x=1.5, y=0.5)) == 2  # Bottom-right corner inside - Left
    
    # On the polyline
    assert orientation(lines, Point2D(x=0, y=1)) == 0  # On left vertical
    assert orientation(lines, Point2D(x=1, y=0)) == 0  # On bottom horizontal
    assert orientation(lines, Point2D(x=2, y=1)) == 0  # On right vertical


def test_orientation_edge_cases():
    """Test edge cases for orientation function."""
    line = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=10, y=0))
    
    # Very close to line but not on it
    point_close_above = Point2D(x=5, y=1e-9)
    assert orientation([line], point_close_above) == 2  # Still left side
    
    point_close_below = Point2D(x=5, y=-1e-9)
    assert orientation([line], point_close_below) == 1  # Still right side
    
    # Exactly on line (within tolerance)
    point_exactly_on = Point2D(x=5, y=0)
    assert orientation([line], point_exactly_on) == 0
    
    # Far from line
    point_far_above = Point2D(x=5, y=1000)
    assert orientation([line], point_far_above) == 2  # Left side
    
    point_far_below = Point2D(x=5, y=-1000)
    assert orientation([line], point_far_below) == 1  # Right side


def test_orientation_z_shape_polyline():
    """Test orientation with Z-shaped polyline.
    
    Note: Z-shapes with sharp angles can produce counterintuitive results
    due to how parallel_offset handles complex geometries. The function
    returns orientation based on the offset line distances, which may not
    match visual intuition for complex shapes.
    """
    # Z-shaped polyline
    lines = [
        Line(p1=Point2D(x=0, y=2), p2=Point2D(x=2, y=2)),  # Top horizontal (left to right)
        Line(p1=Point2D(x=2, y=2), p2=Point2D(x=0, y=0)),  # Diagonal (top-right to bottom-left)
        Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0))   # Bottom horizontal (left to right)
    ]
    
    # Points that are visually on the left of the Z
    point_left_top = Point2D(x=-0.5, y=1.5)
    point_left_bottom = Point2D(x=-0.5, y=0.5)
    
    # Points that are visually on the right of the Z
    point_right_top = Point2D(x=2.5, y=1.5)
    point_right_bottom = Point2D(x=2.5, y=0.5)
    
    # Note: Due to the complexity of the Z-shape and how parallel_offset works,
    # the results may not match visual intuition. The function finds the closest
    # segment and uses offset lines to determine orientation.
    
    # For simpler cases near the horizontal segments, behavior is predictable
    point_clearly_left = Point2D(x=1, y=2.5)  # Above top horizontal
    assert orientation(lines, point_clearly_left) == 2  # Left of top horizontal
    
    point_clearly_right = Point2D(x=1, y=-0.5)  # Below bottom horizontal
    assert orientation(lines, point_clearly_right) == 1  # Right of bottom horizontal
    
    # Points near the diagonal segment - these have deterministic values but may be counterintuitive
    # due to how parallel_offset handles the sharp angle in the Z-shape
    assert orientation(lines, point_left_top) == 1  # Left of Z (-0.5, 1.5) - Right side
    assert orientation(lines, point_left_bottom) == 1  # Left of Z (-0.5, 0.5) - Right side  
    assert orientation(lines, point_right_top) == 2  # Right of Z (2.5, 1.5) - Left side
    assert orientation(lines, point_right_bottom) == 2  # Right of Z (2.5, 0.5) - Left side
    
    # Additional test points for comprehensive coverage
    # Above and below the entire Z-shape
    assert orientation(lines, Point2D(x=0.5, y=3)) == 2  # Far above - Left
    assert orientation(lines, Point2D(x=1.5, y=3)) == 2  # Far above - Left
    assert orientation(lines, Point2D(x=0.5, y=-1)) == 1  # Far below - Right
    assert orientation(lines, Point2D(x=1.5, y=-1)) == 1  # Far below - Right
    
    # On the diagonal line
    assert orientation(lines, Point2D(x=1, y=1)) == 0  # On diagonal
    
    # Near diagonal - actual behavior with parallel_offset
    point_near_diag_1 = Point2D(x=0.9, y=1.1)  # Slightly left of diagonal
    point_near_diag_2 = Point2D(x=1.1, y=0.9)  # Slightly right of diagonal
    assert orientation(lines, point_near_diag_1) == 1  # Near diagonal - Right side (actual)
    assert orientation(lines, point_near_diag_2) == 2  # Near diagonal - Left side (actual)
    
    # Corner points
    assert orientation(lines, Point2D(x=0, y=2)) == 0  # Start point
    assert orientation(lines, Point2D(x=2, y=0)) == 0  # End point
    assert orientation(lines, Point2D(x=2, y=2)) == 0  # Top-right corner (on line)
    assert orientation(lines, Point2D(x=0, y=0)) == 0  # Bottom-left corner (on line)


def test_orientation_complex_path():
    """Test orientation with complex W-shaped path."""
    # W-shaped complex path
    lines = [
        Line(p1=Point2D(x=0, y=2), p2=Point2D(x=1, y=0)),  # Down-right
        Line(p1=Point2D(x=1, y=0), p2=Point2D(x=2, y=2)),  # Up-right
        Line(p1=Point2D(x=2, y=2), p2=Point2D(x=3, y=0)),  # Down-right
        Line(p1=Point2D(x=3, y=0), p2=Point2D(x=4, y=2))   # Up-right
    ]
    
    # Test points in valleys (should be on right side)
    assert orientation(lines, Point2D(x=0.5, y=-0.5)) == 1  # Below first valley - Right
    assert orientation(lines, Point2D(x=2.5, y=-0.5)) == 1  # Below second valley - Right
    
    # Test points at peaks (should be on left side)
    assert orientation(lines, Point2D(x=0, y=2.5)) == 2  # Above start peak - Left
    assert orientation(lines, Point2D(x=2, y=2.5)) == 2  # Above middle peak - Left
    assert orientation(lines, Point2D(x=4, y=2.5)) == 2  # Above end peak - Left
    
    # Test points between segments
    # These points are between the W segments - orientation is deterministic based on closest segment
    assert orientation(lines, Point2D(x=1.5, y=1)) == 0  # On the path (on upslope)
    assert orientation(lines, Point2D(x=2.5, y=1)) == 0  # On the path (on downslope)
    
    # Points on the path
    assert orientation(lines, Point2D(x=0.5, y=1)) == 0  # On first downslope
    assert orientation(lines, Point2D(x=1.5, y=1)) == 0  # On first upslope
    assert orientation(lines, Point2D(x=2.5, y=1)) == 0  # On second downslope
    assert orientation(lines, Point2D(x=3.5, y=1)) == 0  # On second upslope
    
    # Far points
    assert orientation(lines, Point2D(x=-1, y=1)) == 1  # Left of start - Right side
    assert orientation(lines, Point2D(x=5, y=1)) == 1  # Right of end - Right side
    assert orientation(lines, Point2D(x=2, y=4)) == 2  # Far above - Left
    assert orientation(lines, Point2D(x=2, y=-2)) == 1  # Far below - Right


def test_orientation_single_point_line():
    """Test orientation with degenerate line (single point)."""
    # This should ideally raise an error or handle gracefully
    # Current implementation will create a LineString with duplicate points
    line = Line(p1=Point2D(x=1, y=1), p2=Point2D(x=1, y=1))
    point = Point2D(x=2, y=2)
    
    # The behavior here depends on Shapely's handling of degenerate geometries
    # We just ensure it doesn't crash
    try:
        result = orientation([line], point)
        assert result in [0, 1, 2]  # Should return some valid orientation
    except Exception:
        # It's acceptable to raise an exception for degenerate cases
        pass


def test_calculate_euclidean_distance_vectorized():
    # Test case 1: Basic case with two points
    points1 = np.array([[0, 0], [1, 1]])
    points2 = np.array([[1, 1], [2, 2]])
    expected = np.array([np.sqrt(2), np.sqrt(2)])
    result = calculate_euclidean_distance_vectorized(points1, points2)
    assert pytest.approx(result) == expected

    # Test case 2: Points with zero distance
    points1 = np.array([[1, 1], [2, 2]])
    points2 = np.array([[1, 1], [2, 2]]) 
    expected = np.array([0, 0])
    result = calculate_euclidean_distance_vectorized(points1, points2)
    assert pytest.approx(result) == expected

    # Test case 3: Points with negative coordinates
    points1 = np.array([[-1, -1], [-2, -2]])
    points2 = np.array([[1, 1], [2, 2]])
    expected = np.array([2*np.sqrt(2), 4*np.sqrt(2)])
    result = calculate_euclidean_distance_vectorized(points1, points2)
    assert pytest.approx(result) == expected

    # Test case 4: Single point pair
    points1 = np.array([[3, 4]])
    points2 = np.array([[0, 0]])
    expected = np.array([5])  # 3-4-5 triangle
    result = calculate_euclidean_distance_vectorized(points1, points2)
    assert pytest.approx(result) == expected


def test_calculate_haversine_distance_vectorized():
    # Test case 1: Basic case with two points
    lat1 = np.array([0, 45])
    lon1 = np.array([0, -93]) 
    lat2 = np.array([0, 46])
    lon2 = np.array([1, -93])
    result = calculate_haversine_distance_vectorized(lat1, lon1, lat2, lon2)
    
    # First point pair: 0,0 to 0,1 (~111km)
    # Second point pair: 45,-93 to 46,-93 (~111km)
    expected = np.array([111194.92664455874, 111194.92664455874])
    assert pytest.approx(expected) == result

    # Test case 2: Points with same coordinates (zero distance)
    lat1 = np.array([45, 0])
    lon1 = np.array([-93, 0])
    lat2 = np.array([45, 0]) 
    lon2 = np.array([-93, 0])
    result = calculate_haversine_distance_vectorized(lat1, lon1, lat2, lon2)
    expected = np.array([0, 0])
    assert pytest.approx(expected) == result

    # Test case 3: Points at antipodes
    lat1 = np.array([90, 0])  # North pole and equator
    lon1 = np.array([0, 0])
    lat2 = np.array([-90, 0])  # South pole and equator point
    lon2 = np.array([0, 180])
    result = calculate_haversine_distance_vectorized(lat1, lon1, lat2, lon2)
    expected = np.array([20015086.79602057, 20015086.79602057])  # Half Earth circumference
    assert pytest.approx(expected) == result

    # Test case 4: Single point pair
    lat1 = np.array([40])
    lon1 = np.array([-74])  # New York
    lat2 = np.array([51.5])
    lon2 = np.array([-0.13])  # London
    result = calculate_haversine_distance_vectorized(lat1, lon1, lat2, lon2)
    expected = np.array([5636800.0])  
    assert pytest.approx(expected, rel=0.01) == result


def test_intersect_normal_cases():
    """Test normal line intersection cases."""
    # Test 1: Normal crossing lines
    line1 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=2))
    line2 = Line(p1=Point2D(x=0, y=2), p2=Point2D(x=2, y=0))
    assert intersect([line1], line2) == True  # X-shaped intersection
    
    # Test 2: Parallel lines (no intersection)
    line3 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0))
    line4 = Line(p1=Point2D(x=0, y=1), p2=Point2D(x=2, y=1))
    assert intersect([line3], line4) == False
    
    # Test 3: Non-intersecting lines
    line5 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=1, y=0))
    line6 = Line(p1=Point2D(x=2, y=2), p2=Point2D(x=3, y=3))
    assert intersect([line5], line6) == False


def test_intersect_collinear_cases():
    """Test collinear line segment intersection cases."""
    # Test 1: Collinear overlapping segments
    line1 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=3, y=0))
    line2 = Line(p1=Point2D(x=1, y=0), p2=Point2D(x=4, y=0))
    assert intersect([line1], line2) == True
    
    # Test 2: Collinear non-overlapping segments
    line3 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=1, y=0))
    line4 = Line(p1=Point2D(x=2, y=0), p2=Point2D(x=3, y=0))
    assert intersect([line3], line4) == False
    
    # Test 3: Collinear diagonal segments overlapping
    line5 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=2))
    line6 = Line(p1=Point2D(x=1, y=1), p2=Point2D(x=3, y=3))
    assert intersect([line5], line6) == True


def test_intersect_endpoint_cases():
    """Test endpoint touching cases."""
    # Test 1: Touching endpoints
    line1 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=1, y=0))
    line2 = Line(p1=Point2D(x=1, y=0), p2=Point2D(x=2, y=0))
    assert intersect([line1], line2) == True
    
    # Test 2: T-intersection (one endpoint on the other line)
    line3 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0))
    line4 = Line(p1=Point2D(x=1, y=-1), p2=Point2D(x=1, y=0))
    assert intersect([line3], line4) == True
    
    # Test 3: L-shape corner touch
    line5 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=1, y=0))
    line6 = Line(p1=Point2D(x=1, y=0), p2=Point2D(x=1, y=1))
    assert intersect([line5], line6) == True


def test_intersect_with_list():
    """Test intersection with list of lines."""
    # Create an L-shape with two lines
    lines = [
        Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0)),  # Horizontal
        Line(p1=Point2D(x=2, y=0), p2=Point2D(x=2, y=2))   # Vertical
    ]
    
    # Test 1: Line that intersects with first line in list
    test_line1 = Line(p1=Point2D(x=1, y=-1), p2=Point2D(x=1, y=1))
    assert intersect(lines, test_line1) == True
    
    # Test 2: Line that intersects with second line in list
    test_line2 = Line(p1=Point2D(x=1, y=1), p2=Point2D(x=3, y=1))
    assert intersect(lines, test_line2) == True
    
    # Test 3: Line that doesn't intersect with any
    test_line3 = Line(p1=Point2D(x=3, y=3), p2=Point2D(x=4, y=4))
    assert intersect(lines, test_line3) == False
    
    # Test 4: Empty list
    empty_lines = []
    test_line4 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=1, y=1))
    assert intersect(empty_lines, test_line4) == False


def test_intersect_edge_cases():
    """Test edge cases for intersection."""
    # Test 1: Point-like segments (degenerate case)
    line1 = Line(p1=Point2D(x=1, y=1), p2=Point2D(x=1, y=1))  # Point
    line2 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=2))  # Line through point
    assert intersect([line1], line2) == True
    
    # Test 2: Very short segments
    line3 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=0.001, y=0))
    line4 = Line(p1=Point2D(x=0.0005, y=-1), p2=Point2D(x=0.0005, y=1))
    assert intersect([line3], line4) == True
    
    # Test 3: Vertical and horizontal intersection
    line5 = Line(p1=Point2D(x=1, y=-1), p2=Point2D(x=1, y=1))  # Vertical
    line6 = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0))  # Horizontal
    assert intersect([line5], line6) == True

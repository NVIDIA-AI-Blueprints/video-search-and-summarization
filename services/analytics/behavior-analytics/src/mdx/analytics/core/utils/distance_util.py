# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import math
from math import asin, atan2, cos, degrees, radians, sin, sqrt

import numpy as np
import pyproj
from shapely.geometry import LineString, Point

from mdx.analytics.core.schema.models import Coordinate, Line, Location, Point2D
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema

DEGREES_TO_RADIANS = math.pi / 180
RADIANS_TO_DEGREES = 1 / DEGREES_TO_RADIANS
KMPH_TO_MPH = 0.621371
MPS_TO_MPH = 2.23694

# Constants for orientation function
ORIENTATION_OFFSET_RATIO = 0.1  # 10% of distance for parallel offset calculation
ORIENTATION_MIN_OFFSET = 1e-6  # Minimum offset to avoid numerical issues
ORIENTATION_ON_LINE_TOLERANCE = 1e-10  # Tolerance for determining if a point is on the line


def xy_to_lonlat(x: float, y: float, crs_lonlat: str = "EPSG:4326", crs_xy: str = "EPSG:26915") -> tuple[float, float]:
    """
    Convert XY coordinates to longitude/latitude using coordinate reference system (CRS) transformation.
    This function uses pyproj to perform the coordinate transformation between different CRS systems.
    EPSG codes are standardized identifiers for coordinate reference systems (CRS) maintained by the European Petroleum Survey Group (now part of IOGP).

    :param float x: X coordinate in the source CRS.
    :param float y: Y coordinate in the source CRS.
    :param str crs_lonlat: Target coordinate reference system for longitude/latitude
                          (default: EPSG:4326).
    :param str crs_xy: Source coordinate reference system for XY coordinates
                      (default: EPSG:26915).
    :return tuple[float, float]: Tuple containing (longitude, latitude) in the target CRS.

    Examples::
        >>> x, y = 500000, 5000000  # UTM coordinates
        >>> lon, lat = xy_to_lonlat(x, y)
        >>> print(f"Longitude: {lon}, Latitude: {lat}")
    """
    tr_xy2lonlat = pyproj.Transformer.from_crs(crs_xy, crs_lonlat, always_xy=True)
    lon, lat = tr_xy2lonlat.transform(x, y)
    return lon, lat


def lonlat_to_xy(
    lon: float, lat: float, crs_lonlat: str = "EPSG:4326", crs_xy: str = "EPSG:26915"
) -> tuple[float, float]:
    """
    Convert longitude/latitude to XY coordinates using coordinate reference system (CRS) transformation.
    This function uses pyproj to perform the coordinate transformation between different CRS systems.
    EPSG codes are standardized identifiers for coordinate reference systems (CRS) maintained by the European Petroleum Survey Group (now part of IOGP).

    :param float lon: Longitude in the source CRS.
    :param float lat: Latitude in the source CRS.
    :param str crs_lonlat: Source coordinate reference system for longitude/latitude
                          (default: EPSG:4326).
    :param str crs_xy: Target coordinate reference system for XY coordinates
                      (default: EPSG:26915).
    :return tuple[float, float]: Tuple containing (x, y) coordinates in the target CRS.

    Examples::
        >>> lon, lat = -122.4194, 37.7749  # San Francisco coordinates
        >>> x, y = lonlat_to_xy(lon, lat)
        >>> print(f"X: {x}, Y: {y}")
    """
    tr_lonlat2xy = pyproj.Transformer.from_crs(crs_lonlat, crs_xy, always_xy=True)
    x, y = tr_lonlat2xy.transform(lon, lat)
    return x, y


def bearing(from_point: Location, to_point: Location) -> float:    
    """
    Calculate the bearing (heading) between two geographic points.
    The bearing is the angle between the line from the start point to the end point
    and the line from the start point to true north.

    :param Location from_point: The starting location with latitude and longitude.
    :param Location to_point: The destination location with latitude and longitude.
    :return float: Bearing in degrees (0-360) from the starting point to the destination point.

    Examples::
        >>> start = Location(lat=37.7749, lon=-122.4194)
        >>> end = Location(lat=34.0522, lon=-118.2437)
        >>> heading = bearing(start, end)
        >>> print(f"Heading: {heading} degrees")
    """
    p1_lat_rad = from_point.lat * DEGREES_TO_RADIANS
    p1_lon_rad = from_point.lon * DEGREES_TO_RADIANS
    p2_lat_rad = to_point.lat * DEGREES_TO_RADIANS
    p2_lon_rad = to_point.lon * DEGREES_TO_RADIANS

    dLon = p2_lon_rad - p1_lon_rad

    y = math.sin(dLon) * math.cos(p2_lat_rad)
    x = math.cos(p1_lat_rad) * math.sin(p2_lat_rad) - math.sin(p1_lat_rad) * math.cos(p2_lat_rad) * math.cos(dLon)

    brng = math.atan2(y, x) * RADIANS_TO_DEGREES

    return (brng + 360) % 360


def direction(bearing: float, mode: int = 2) -> str:
    """
    Convert a bearing angle to a cardinal or intercardinal direction.
    Supports three modes of direction granularity: 4-way, 8-way, or 16-way directions.

    :param float bearing: Bearing angle in degrees (0-360).
    :param int mode: Direction granularity mode:
                    - 0: 4-way direction (N, E, S, W)
                    - 1: 8-way direction (N, NE, E, SE, S, SW, W, NW)
                    - 2: 16-way direction (N, NNE, NE, ENE, E, ESE, SE, SSE, S, SSW, SW, WSW, W, WNW, NW, NNW)
    :return str: String representing the cardinal or intercardinal direction.
    :raises ValueError: If the direction mode is invalid.

    Examples::
        >>> # Get 4-way direction
        >>> dir_4 = direction(45, mode=0)  # Returns "E"
        >>> # Get 8-way direction
        >>> dir_8 = direction(45, mode=1)  # Returns "NE"
        >>> # Get 16-way direction
        >>> dir_16 = direction(45, mode=2)  # Returns "NE"
    """
    if mode == 0:
        idx = int(((bearing + 45) % 360) // 90)
        arr = ["N", "E", "S", "W"]
        return arr[idx]
    if mode == 1:
        idx = int(((bearing + 22.5) % 360) // 45)
        arr = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        return arr[idx]
    if mode == 2:
        idx = int(((bearing + 11.25) % 360) // 22.5)
        arr = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        return arr[idx]
    raise ValueError(f"Invalid direction mode {mode}; expected 0, 1, or 2.")


def get_point_at_distance(from_point: Location, d: float, bearing: float, R: float = 6371009) -> Location:
    """
    Calculate a new location at a specified distance and bearing from a starting point.
    Uses the haversine formula to compute the new location on a spherical Earth.

    :param Location from_point: Initial point with latitude and longitude in degrees.
    :param float d: Target distance from initial point in meters.
    :param float bearing: True heading in degrees (0-360).
    :param float R: Optional radius of sphere in meters (defaults to mean radius of Earth: 6371009).
    :return Location: New Location object with latitude and longitude in degrees.

    Examples::
        >>> start = Location(lat=37.7749, lon=-122.4194)
        >>> # Get point 1000 meters north of start
        >>> new_point = get_point_at_distance(start, 1000, 0)
        >>> print(f"New location: {new_point.lat}, {new_point.lon}")
    """
    lat1 = radians(from_point.lat)
    lon1 = radians(from_point.lon)
    a = radians(bearing)
    lat2 = asin(sin(lat1) * cos(d / R) + cos(lat1) * sin(d / R) * cos(a))
    lon2 = lon1 + atan2(sin(a) * sin(d / R) * cos(lat1), cos(d / R) - sin(lat1) * sin(lat2))
    return Location(lat=degrees(lat2), lon=degrees(lon2))


def euclidean_distance(p1: Coordinate | nvSchema.Coordinate, p2: Coordinate | nvSchema.Coordinate) -> float:
    """
    Compute the Euclidean distance between two points in 3D space.
    This is the straight-line distance between the points.

    :param Coordinate | nvSchema.Coordinate p1: The first point with x, y, z coordinates.
    :param Coordinate | nvSchema.Coordinate p2: The second point with x, y, z coordinates.
    :return float: Euclidean distance between p1 and p2.

    Examples::
        >>> p1 = Coordinate(x=1, y=2, z=3)
        >>> p2 = Coordinate(x=4, y=5, z=6)
        >>> dist = euclidean_distance(p1, p2)
        >>> print(f"Distance: {dist}")
    """
    return math.sqrt(math.pow(p1.x - p2.x, 2) + math.pow(p1.y - p2.y, 2) + math.pow(p1.z - p2.z, 2))


def haversine_distance(p1: Location, p2: Location, radius: float = 6371009) -> float:
    """
    Compute the great circle distance between two points on the Earth's surface.
    Uses the haversine formula to account for the Earth's spherical shape.

    :param Location p1: The first point with latitude and longitude in degrees.
    :param Location p2: The second point with latitude and longitude in degrees.
    :param float radius: Optional mean radius of Earth in meters (default: 6371009).
    :return float: Haversine distance between p1 and p2 in meters.

    Examples::
        >>> p1 = Location(lat=37.7749, lon=-122.4194)  # San Francisco
        >>> p2 = Location(lat=34.0522, lon=-118.2437)  # Los Angeles
        >>> dist = haversine_distance(p1, p2)
        >>> print(f"Distance: {dist} meters")
    """
    lat1 = radians(p1.lat)
    lon1 = radians(p1.lon)
    lat2 = radians(p2.lat)
    lon2 = radians(p2.lon)

    lat = lat2 - lat1
    lon = lon2 - lon1
    a = sin(lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(lon / 2) ** 2
    dist = 2 * radius * atan2(sqrt(a), sqrt(1 - a))

    return dist


def haversine_distance_coords(
    p1: Coordinate | nvSchema.Coordinate, p2: Coordinate | nvSchema.Coordinate, radius: float = 6371009
) -> float:
    """
    Compute the great circle distance between two points using coordinate objects.
    Similar to haversine_distance but works with Coordinate objects where x is longitude and y is latitude.

    :param Coordinate | nvSchema.Coordinate p1: The first point with x (longitude) and y (latitude) coordinates.
    :param Coordinate | nvSchema.Coordinate p2: The second point with x (longitude) and y (latitude) coordinates.
    :param float radius: Optional mean radius of Earth in meters (default: 6371009).
    :return float: Haversine distance between p1 and p2 in meters.

    Examples::
        >>> p1 = Coordinate(x=-122.4194, y=37.7749)  # San Francisco
        >>> p2 = Coordinate(x=-118.2437, y=34.0522)  # Los Angeles
        >>> dist = haversine_distance_coords(p1, p2)
        >>> print(f"Distance: {dist} meters")
    """
    lat1 = radians(p1.y)
    lon1 = radians(p1.x)
    lat2 = radians(p2.y)
    lon2 = radians(p2.x)

    lat = lat2 - lat1
    lon = lon2 - lon1
    a = sin(lat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(lon / 2) ** 2
    dist = 2 * radius * atan2(sqrt(a), sqrt(1 - a))

    return dist


def get_lat_lon(origin: Location, coor: Coordinate) -> Location:
    """
    Convert local coordinates to geographic coordinates (latitude and longitude).
    This function assumes the local coordinates are relative to the origin point.

    :param Location origin: The origin location with known latitude and longitude.
    :param Coordinate coor: The local coordinate to convert, relative to the origin.
    :return Location: The geographic location with latitude and longitude.

    Examples::
        >>> origin = Location(lat=37.7749, lon=-122.4194)
        >>> local_coord = Coordinate(x=100, y=200)
        >>> geo_location = get_lat_lon(origin, local_coord)
        >>> print(f"Latitude: {geo_location.lat}, Longitude: {geo_location.lon}")
    """
    x = coor.x / 1000.0  # meter to km
    y = coor.y / 1000.0  # meter to km

    lat = origin.lat - y * 360.0 / 40000.0
    lon = origin.lon - x * 360.0 / (40000.0 * math.cos((origin.lat + lat) * math.pi / 360.0))

    return Location(lat=lat, lon=lon)


def get_lat_lon_coord(origin: Location, coor: Coordinate) -> Coordinate:
    """
    Convert local coordinates to geographic coordinates (latitude and longitude).
    This function assumes the local coordinates are relative to the origin point.

    :param Location origin: The origin location with known latitude and longitude.
    :param Coordinate coor: The local coordinate to convert, relative to the origin.
    :return Coordinate: The geographic coordinate with latitude and longitude.

    Examples::
        >>> origin = Location(lat=37.7749, lon=-122.4194)
        >>> local_coord = Coordinate(x=100, y=200)
        >>> geo_coord = get_lat_lon_coord(origin, local_coord)
        >>> print(f"Latitude: {geo_coord.x}, Longitude: {geo_coord.y}")
    """
    x = coor.x / 1000.0  # meter to km
    y = coor.y / 1000.0  # meter to km

    lat = origin.lat - y * 360.0 / 40000.0
    lon = origin.lon - x * 360.0 / (40000.0 * math.cos((origin.lat + lat) * math.pi / 360.0))

    return Coordinate(x=lat, y=lon)


def orientation(lines: list[Line], r: Coordinate | Point2D | nvSchema.Coordinate | nvSchema.Point2D) -> int:
    """
    Determines the orientation of a point relative to a line or polyline using Shapely.
    
    Uses Shapely's parallel_offset to create offset lines and determines which side
    the point is closer to.
    
    :param list[Line] lines: A single line or list of connected line segments.
    :param Coordinate | Point2D, nvSchema.Coordinate, nvSchema.Point2D r: A point to check.
    :return int: 0 for on the line, 1 for right/clockwise, 2 for left/counterclockwise.
    
    Examples::
        >>> # Single line
        >>> line = Line(p1=Point2D(x=0, y=0), p2=Point2D(x=1, y=1))
        >>> result = orientation([line], Point2D(x=2, y=2))  # Returns 0 (collinear)
        
        >>> # Polyline
        >>> lines = [
        ...     Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0)),
        ...     Line(p1=Point2D(x=2, y=0), p2=Point2D(x=2, y=2))
        ... ]
        >>> result = orientation(lines, Point2D(x=1, y=1))  # Returns consistent side
    """
    if not lines:
        raise ValueError("lines must contain at least one line segment")
    
    # Convert to Shapely objects
    coords = []
    coords.append((lines[0].p1.x, lines[0].p1.y))
    for i, line in enumerate(lines):
        # Check if the end of the current line is the same as the start of the next line
        if i < len(lines) - 1 and line.p2 != lines[i + 1].p1:
            raise ValueError(f"Line segments are not connected on index {i}: {line.p2} != {lines[i + 1].p1}")
        coords.append((line.p2.x, line.p2.y))
    linestring = LineString(coords)
    point = Point(r.x, r.y)
    
    # Check if point is on the line (within tolerance)
    distance = linestring.distance(point)
    if distance < ORIENTATION_ON_LINE_TOLERANCE:
        return 0  # On the line
    
    # Use parallel_offset to determine which side the point is on
    offset_distance = max(distance * ORIENTATION_OFFSET_RATIO, ORIENTATION_MIN_OFFSET)
    left_offset = linestring.parallel_offset(offset_distance, 'left')
    right_offset = linestring.parallel_offset(offset_distance, 'right')
    
    # Check which offset line is closer to the point
    if hasattr(left_offset, 'distance'):
        left_distance = left_offset.distance(point)
    else:
        left_distance = float('inf')
        
    if hasattr(right_offset, 'distance'):
        right_distance = right_offset.distance(point)
    else:
        right_distance = float('inf')
    
    # The side with smaller distance to offset line is the side the point is on
    if left_distance < right_distance:
        return 2  # Left side (counterclockwise)
    else:
        return 1  # Right side (clockwise)


def intersect(lines: list[Line], line: Line) -> bool:
    """
    Determines if a line segment intersects with a list of line segments.

    :param list[Line] lines: A list of line segments to check against.
    :param Line line: The line segment to check for intersection.
    :return bool: True if the line segments intersect, False otherwise.

    Examples::
        >>> lines = [Line(p1=Point2D(x=0, y=0), p2=Point2D(x=2, y=0)), Line(p1=Point2D(x=2, y=0), p2=Point2D(x=2, y=2))]
        >>> line = Line(p1=Point2D(x=1, y=1), p2=Point2D(x=3, y=1))
        >>> result = intersect_lines(lines, line)  # Returns True (intersects with line1)
    """
    def line_intersect(line1: Line, line2: Line) -> bool:
        """Determines if a line segment intersects with another line segment."""
        def on_segment(seg: Line, point: Point2D) -> bool:
            # Check if point is on the line segment
            return (min(seg.p1.x, seg.p2.x) <= point.x <= max(seg.p1.x, seg.p2.x) and
                    min(seg.p1.y, seg.p2.y) <= point.y <= max(seg.p1.y, seg.p2.y))

        o1 = orientation([line1], line2.p1)
        o2 = orientation([line1], line2.p2)
        o3 = orientation([line2], line1.p1)
        o4 = orientation([line2], line1.p2)
        
        # General case: segments intersect if points are on opposite sides
        if o1 != o2 and o3 != o4:
            return True
        
        # Special cases: handle collinear points (orientation = 0)
        # Check if endpoints of one segment lie on the other segment
        if o1 == 0 and on_segment(line1, line2.p1):
            return True
        if o2 == 0 and on_segment(line1, line2.p2):
            return True
        if o3 == 0 and on_segment(line2, line1.p1):
            return True
        if o4 == 0 and on_segment(line2, line1.p2):
            return True

        return False
    # List of lines case - recursively check each line
    for l in lines:
        if line_intersect(l, line):
            return True
    return False


def calculate_haversine_distance_vectorized(
    lat1: np.ndarray, lon1: np.ndarray, lat2: np.ndarray, lon2: np.ndarray
) -> np.ndarray:
    """
    Calculate the Haversine distances between two sets of points on a sphere.
    This is a vectorized version of the haversine_distance function that operates on numpy arrays.
    Uses the haversine formula to account for the Earth's spherical shape.

    :param np.ndarray lat1: Array of latitudes for the first set of points in degrees.
    :param np.ndarray lon1: Array of longitudes for the first set of points in degrees.
    :param np.ndarray lat2: Array of latitudes for the second set of points in degrees.
    :param np.ndarray lon2: Array of longitudes for the second set of points in degrees.
    :return np.ndarray: Array of distances in meters between corresponding points.

    Examples::
        >>> import numpy as np
        >>> # Calculate distances between multiple points
        >>> lat1 = np.array([37.7749, 34.0522])  # San Francisco, Los Angeles
        >>> lon1 = np.array([-122.4194, -118.2437])
        >>> lat2 = np.array([40.7128, 40.7128])  # New York City
        >>> lon2 = np.array([-74.0060, -74.0060])
        >>> distances = calculate_haversine_distance_vectorized(lat1, lon1, lat2, lon2)
        >>> print(f"Distances: {distances} meters")
    """
    R = 6371 * 1000  # Earth radius in kilometers
    lat1 = np.radians(lat1)
    lon1 = np.radians(lon1)
    lat2 = np.radians(lat2)
    lon2 = np.radians(lon2)
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) * np.sin(dlat / 2) + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) * np.sin(dlon / 2)
    c = 2 * np.arctan2(np.sqrt(a), np.sqrt(1 - a))
    d = R * c

    return d


def calculate_euclidean_distance_vectorized(points1: np.ndarray, points2: np.ndarray) -> np.ndarray:
    """
    Calculate the Euclidean distances between two sets of points in N-dimensional space.
    This is a vectorized version of the euclidean_distance function that operates on numpy arrays.
    Computes the straight-line distance between corresponding points in the input arrays.

    :param np.ndarray points1: Array of points with shape (N, D) where N is number of points and D is dimensions.
    :param np.ndarray points2: Array of points with shape (N, D) matching points1.
    :return np.ndarray: Array of distances between corresponding points.

    Examples::
        >>> import numpy as np
        >>> # Calculate distances between multiple 3D points
        >>> points1 = np.array([[1, 2, 3], [4, 5, 6]])
        >>> points2 = np.array([[7, 8, 9], [10, 11, 12]])
        >>> distances = calculate_euclidean_distance_vectorized(points1, points2)
        >>> print(f"Distances: {distances}")
    """
    # Calculate the differences between corresponding points
    differences = points1 - points2

    # Calculate the squared differences
    squared_differences = differences**2

    # Sum the squared differences along the second axis (axis=1)
    sum_squared_differences = np.sum(squared_differences, axis=1)

    # Calculate the square root of the sum of squared differences
    return np.sqrt(sum_squared_differences)

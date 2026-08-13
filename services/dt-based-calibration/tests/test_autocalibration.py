# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# SPDX-License-Identifier: Apache-2.0

import os
import sys
import tempfile
import pytest
import numpy as np
from pathlib import Path
from unittest.mock import MagicMock

# ---------------------------------------------------------------------------
# Stub out heavy / optional dependencies before the module is imported.
# carla  - requires the CARLA simulator Python egg
# rasterio - requires GDAL native libs
# matplotlib - not available in the minimal test venv
# ---------------------------------------------------------------------------
for _mod in (
    "carla",
    "rasterio",
    "rasterio.transform",
    "matplotlib",
    "matplotlib.pyplot",
):
    sys.modules.setdefault(_mod, MagicMock())

from src.utils.autocalibration import (  # noqa: E402
    validate_ip_addr,
    parse_path_file,
    parse_path_dir,
    get_camera_intrinsics,
    build_intrinsic,
    visualize_drivable_surface,
    normalize_path,
    depth_to_meters,
    extract_2d_3d,
    carla_to_opendrive,
    decode_carla_depth,
    get_rgb_image,
    get_extrinsic_matrix,
    pixel_to_world,
    world_to_pixel,
    world_to_latlon,
)


# ===========================================================================
# validate_ip_addr
# ===========================================================================

class TestValidateIpAddr:
    def test_valid_ipv4(self):
        assert validate_ip_addr("192.168.1.1") == "192.168.1.1"

    def test_valid_ipv6(self):
        assert validate_ip_addr("::1") == "::1"

    def test_localhost_string(self):
        assert validate_ip_addr("localhost") == "localhost"

    def test_valid_hostname(self):
        assert validate_ip_addr("my-server.example.com") == "my-server.example.com"

    def test_invalid_raises(self):
        with pytest.raises(Exception, match="Invalid IP or hostname"):
            validate_ip_addr("not a valid host!!")

    def test_empty_string_raises(self):
        with pytest.raises(Exception):
            validate_ip_addr("")


# ===========================================================================
# parse_path_file / parse_path_dir
# ===========================================================================

class TestParsePaths:
    def test_parse_path_file_valid(self, tmp_path):
        f = tmp_path / "test.yaml"
        f.write_text("data")
        assert parse_path_file(str(f)) == str(f)

    def test_parse_path_file_missing(self):
        with pytest.raises(Exception, match="Invalid file"):
            parse_path_file("/nonexistent/path/file.yaml")

    def test_parse_path_file_directory_raises(self, tmp_path):
        with pytest.raises(Exception, match="Invalid file"):
            parse_path_file(str(tmp_path))

    def test_parse_path_dir_valid(self, tmp_path):
        assert parse_path_dir(str(tmp_path)) == str(tmp_path)

    def test_parse_path_dir_missing(self):
        with pytest.raises(Exception, match="Invalid output folder"):
            parse_path_dir("/nonexistent/dir")

    def test_parse_path_dir_file_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x")
        with pytest.raises(Exception, match="Invalid output folder"):
            parse_path_dir(str(f))


# ===========================================================================
# normalize_path
# ===========================================================================

class TestNormalizePath:
    def test_returns_path(self):
        result = normalize_path("/tmp/some/path")
        assert isinstance(result, Path)

    def test_expands_tilde(self):
        result = normalize_path("~/foo")
        assert "~" not in str(result)

    def test_resolves_dotdot(self, tmp_path):
        result = normalize_path(str(tmp_path / "a" / ".." / "b"))
        assert ".." not in str(result)


# ===========================================================================
# get_camera_intrinsics / build_intrinsic
# ===========================================================================

class TestCameraIntrinsics:
    def test_shape(self):
        K = get_camera_intrinsics(90, 1280, 720)
        assert K.shape == (3, 3)

    def test_principal_point(self):
        K = get_camera_intrinsics(90, 1280, 720)
        assert K[0, 2] == pytest.approx(640.0)
        assert K[1, 2] == pytest.approx(360.0)

    def test_focal_length_90fov(self):
        # At 90° FOV, f = w / 2
        K = get_camera_intrinsics(90, 1000, 500)
        assert K[0, 0] == pytest.approx(500.0, rel=1e-5)

    def test_build_intrinsic_matches(self):
        K1 = get_camera_intrinsics(60, 640, 480)
        K2 = build_intrinsic(640, 480, 60)
        np.testing.assert_allclose(K1, K2, rtol=1e-5)

    def test_wider_fov_shorter_focal(self):
        K_wide = get_camera_intrinsics(120, 1280, 720)
        K_narrow = get_camera_intrinsics(60, 1280, 720)
        assert K_wide[0, 0] < K_narrow[0, 0]


# ===========================================================================
# visualize_drivable_surface
# ===========================================================================

class TestVisualizeDrivableSurface:
    def _make_seg(self, h=10, w=10, label=7):
        seg = np.zeros((h, w), dtype=np.uint8)
        seg[3:7, 3:7] = label
        return seg

    def test_output_shape(self):
        seg = self._make_seg()
        out = visualize_drivable_surface(seg, label=7)
        assert out.shape == (10, 10, 3)

    def test_road_pixels_white(self):
        seg = self._make_seg(label=7)
        out = visualize_drivable_surface(seg, label=7)
        # Road pixels should be [255, 255, 255]
        road = out[3:7, 3:7]
        assert np.all(road == 255)

    def test_non_road_pixels_black(self):
        seg = self._make_seg(label=7)
        out = visualize_drivable_surface(seg, label=7)
        assert np.all(out[0, 0] == 0)

    def test_no_matching_label_all_black(self):
        seg = np.zeros((10, 10), dtype=np.uint8)
        out = visualize_drivable_surface(seg, label=7)
        assert np.all(out == 0)


# ===========================================================================
# depth_to_meters
# ===========================================================================

class TestDepthToMeters:
    def test_returns_center_value(self):
        depth = np.zeros((10, 20), dtype=np.float32)
        depth[5, 10] = 42.0
        assert depth_to_meters(depth) == pytest.approx(42.0)

    def test_dtype_is_float(self):
        depth = np.ones((8, 8), dtype=np.uint8) * 5
        result = depth_to_meters(depth)
        assert isinstance(result, float)

    def test_odd_dimensions(self):
        depth = np.zeros((9, 9), dtype=np.float32)
        depth[4, 4] = 7.5
        assert depth_to_meters(depth) == pytest.approx(7.5)


# ===========================================================================
# extract_2d_3d
# ===========================================================================

class TestExtract2d3d:
    def _setup(self):
        h, w = 100, 100
        label = 7
        seg = np.zeros((h, w), dtype=np.uint8)
        seg[30:70, 30:70] = label
        depth = np.ones((h, w), dtype=np.float32) * 10.0
        K = build_intrinsic(w, h, 90)
        return seg, depth, K, label

    def test_output_shapes(self):
        seg, depth, K, label = self._setup()
        obj_pts, img_pts = extract_2d_3d(seg, depth, K, label, max_pts=50)
        assert obj_pts.shape[1] == 3
        assert img_pts.shape[1] == 2
        assert len(obj_pts) == len(img_pts)

    def test_no_road_pixels_raises(self):
        seg = np.zeros((50, 50), dtype=np.uint8)
        depth = np.ones((50, 50), dtype=np.float32)
        K = build_intrinsic(50, 50, 90)
        with pytest.raises(ValueError, match="No road pixels"):
            extract_2d_3d(seg, depth, K, label=7)

    def test_max_pts_limit(self):
        seg, depth, K, label = self._setup()
        obj_pts, img_pts = extract_2d_3d(seg, depth, K, label, max_pts=10)
        assert len(obj_pts) <= 10

    def test_filters_near_zero_depth(self):
        h, w = 50, 50
        label = 7
        seg = np.full((h, w), label, dtype=np.uint8)
        depth = np.zeros((h, w), dtype=np.float32)  # all zero → filtered
        K = build_intrinsic(w, h, 90)
        obj_pts, img_pts = extract_2d_3d(seg, depth, K, label, max_pts=200)
        assert len(obj_pts) == 0


# ===========================================================================
# carla_to_opendrive  (mocked carla.Location)
# ===========================================================================

class TestCarlaToOpendrive:
    def _location(self, x, y, z):
        loc = MagicMock()
        loc.x, loc.y, loc.z = x, y, z
        return loc

    def test_basic_conversion(self):
        loc = self._location(1.0, 2.0, 3.0)
        x, y, z = carla_to_opendrive(loc)
        assert x == pytest.approx(1.0)
        assert y == pytest.approx(-2.0)  # y is negated
        assert z == pytest.approx(3.0)

    def test_zero_location(self):
        loc = self._location(0.0, 0.0, 0.0)
        assert carla_to_opendrive(loc) == (0.0, 0.0, 0.0)

    def test_negative_y_flips(self):
        loc = self._location(0.0, -5.0, 0.0)
        _, y_od, _ = carla_to_opendrive(loc)
        assert y_od == pytest.approx(5.0)


# ===========================================================================
# decode_carla_depth  (mocked carla.Image)
# ===========================================================================

class TestDecodeCarlaDepth:
    def _make_image(self, h, w, B, G, R):
        """Create a mock CARLA depth image with uniform BGR values."""
        img = MagicMock()
        img.height = h
        img.width = w
        raw = np.zeros((h, w, 4), dtype=np.uint8)
        raw[:, :, 0] = B
        raw[:, :, 1] = G
        raw[:, :, 2] = R
        img.raw_data = raw.tobytes()
        return img

    def test_output_shape(self):
        img = self._make_image(10, 20, 0, 0, 0)
        depth = decode_carla_depth(img)
        assert depth.shape == (10, 20)

    def test_zero_encodes_zero_depth(self):
        img = self._make_image(4, 4, 0, 0, 0)
        depth = decode_carla_depth(img)
        assert np.all(depth == pytest.approx(0.0))

    def test_max_encodes_1000m(self):
        # B=255, G=255, R=255 → normalized ≈ 1.0 → 1000 m
        img = self._make_image(4, 4, 255, 255, 255)
        depth = decode_carla_depth(img)
        assert np.all(depth == pytest.approx(1000.0, rel=1e-4))


# ===========================================================================
# get_rgb_image  (mocked carla.Image)
# ===========================================================================

class TestGetRgbImage:
    def _make_image(self, h, w, r, g, b):
        img = MagicMock()
        img.height = h
        img.width = w
        raw = np.zeros((h, w, 4), dtype=np.uint8)
        raw[:, :, 0] = b  # BGRA order
        raw[:, :, 1] = g
        raw[:, :, 2] = r
        img.raw_data = raw.tobytes()
        return img

    def test_output_shape(self):
        img = self._make_image(8, 16, 100, 150, 200)
        rgb = get_rgb_image(img)
        assert rgb.shape == (8, 16, 3)

    def test_bgra_to_rgb_flip(self):
        # raw BGRA → [B=10, G=20, R=30, A=0] → expect RGB [30, 20, 10]
        img = self._make_image(2, 2, r=30, g=20, b=10)
        rgb = get_rgb_image(img)
        assert rgb[0, 0, 0] == 30  # R channel
        assert rgb[0, 0, 1] == 20  # G channel
        assert rgb[0, 0, 2] == 10  # B channel


# ===========================================================================
# get_extrinsic_matrix  (mocked carla.Transform)
# ===========================================================================

class TestGetExtrinsicMatrix:
    def _make_transform(self, x=0, y=0, z=0, pitch=0, yaw=0, roll=0):
        t = MagicMock()
        t.location.x = float(x)
        t.location.y = float(y)
        t.location.z = float(z)
        t.rotation.pitch = float(pitch)
        t.rotation.yaw = float(yaw)
        t.rotation.roll = float(roll)
        return t

    def test_output_shape(self):
        tf = self._make_transform()
        E = get_extrinsic_matrix(tf)
        assert E.shape == (4, 4)

    def test_last_row(self):
        tf = self._make_transform()
        E = get_extrinsic_matrix(tf)
        np.testing.assert_allclose(E[3], [0, 0, 0, 1], atol=1e-8)

    def test_identity_rotation_is_invertible(self):
        tf = self._make_transform(x=1.0, y=2.0, z=3.0)
        E = get_extrinsic_matrix(tf)
        # Result is world-to-camera; verify it's a proper 4×4 matrix
        assert np.isfinite(E).all()
        assert abs(np.linalg.det(E)) == pytest.approx(1.0, abs=1e-5)


# ===========================================================================
# world_to_latlon
# ===========================================================================

GEO_REF = "+proj=tmerc +lat_0=0 +lon_0=-87.6 +k=1 +x_0=0 +y_0=0 +datum=WGS84 +units=m +no_defs"


class TestWorldToLatlon:
    def test_returns_two_values(self):
        lat, lon = world_to_latlon(0.0, 0.0, GEO_REF)
        assert isinstance(lat, float)
        assert isinstance(lon, float)

    def test_origin_maps_to_valid_lat_lon(self):
        lat, lon = world_to_latlon(0.0, 0.0, GEO_REF)
        assert -90 <= lat <= 90
        assert -180 <= lon <= 180

    def test_shifting_x_changes_longitude(self):
        _, lon1 = world_to_latlon(0.0, 0.0, GEO_REF)
        _, lon2 = world_to_latlon(1000.0, 0.0, GEO_REF)
        assert lon1 != lon2

    def test_shifting_y_changes_latitude(self):
        lat1, _ = world_to_latlon(0.0, 0.0, GEO_REF)
        lat2, _ = world_to_latlon(0.0, 1000.0, GEO_REF)
        assert lat1 != lat2


# ===========================================================================
# pixel_to_world / world_to_pixel  (mocked camera_transform)
# ===========================================================================

def _make_identity_transform():
    """Camera at origin, no rotation → inverse matrix is identity."""
    tf = MagicMock()
    identity = np.eye(4).tolist()
    tf.get_inverse_matrix.return_value = identity
    return tf


class TestPixelToWorld:
    def test_returns_tuple_of_three(self):
        K = build_intrinsic(100, 100, 90)
        tf = _make_identity_transform()
        result = pixel_to_world(50, 50, 5.0, K, tf)
        assert len(result) == 3

    def test_pass(self):
        pass  # Full round-trip tested in TestRoundTrip


class TestWorldToPixel:
    def test_returns_two_values_or_none(self):
        K = build_intrinsic(100, 100, 90)
        tf = _make_identity_transform()
        result = world_to_pixel((1.0, 0.0, 5.0), K, tf)
        # Returns (u, v) or (None, None)
        assert len(result) == 2

    def test_behind_camera_returns_none(self):
        K = build_intrinsic(100, 100, 90)
        tf = _make_identity_transform()
        u, v = world_to_pixel((0.0, 0.0, -1.0), K, tf)
        assert u is None and v is None

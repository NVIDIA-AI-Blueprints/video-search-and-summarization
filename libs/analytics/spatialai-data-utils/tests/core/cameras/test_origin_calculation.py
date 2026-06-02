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

"""
Test cases for origin calculation with group information.

This module tests the calculate_group_origins_from_calibration function with:
1. Calibration data that already has group information
2. Calibration data without groups (n_sensor_groups parameter)

Uses synthetic calibration data for all tests - no external data files required.
"""

import json
import logging
import os
import tempfile
import shutil
from pathlib import Path

try:
    import pytest
except ImportError:
    pytest = None

logger = logging.getLogger(__name__)

from spatialai_data_utils.core.cameras.bev import (
    calculate_group_origins_from_calibration,
)
from spatialai_data_utils.core.cameras.origin import (
    calculate_and_update_group_origins,
)

MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\nIDATx\x9cc``\x00\x00\x00"
    b"\x02\x00\x01E-\xb4\xdc\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _make_synthetic_sensors(n_cameras=6, with_groups=False):
    """Build synthetic sensor dicts with valid intrinsics, extrinsics and FOV."""
    sensors = []
    for idx in range(n_cameras):
        sensor = {
            "id": f"Camera_{idx:02d}",
            "type": "camera",
            "intrinsicMatrix": [
                [1000.0, 0.0, 960.0],
                [0.0, 1000.0, 540.0],
                [0.0, 0.0, 1.0],
            ],
            "extrinsicMatrix": [
                [1.0, 0.0, 0.0, float(idx * 5)],
                [0.0, 0.866, -0.5, 2.0],
                [0.0, 0.5, 0.866, 5.0],
                [0.0, 0.0, 0.0, 1.0],
            ],
            "translationToGlobalCoordinates": {"x": 0.0, "y": 0.0},
            "scaleFactor": 3.0,
            "attributes": [
                {"name": "frameWidth", "value": "1920"},
                {"name": "frameHeight", "value": "1080"},
                {
                    "name": "fieldOfViewPolygon",
                    "value": (
                        f"POLYGON(({idx * 10} 0, {idx * 10 + 10} 0, "
                        f"{idx * 10 + 10} 10, {idx * 10} 10, {idx * 10} 0))"
                    ),
                },
            ],
        }
        if with_groups:
            group_id = 1 if idx < n_cameras // 2 else 2
            sensor["group"] = {
                "name": f"bev-sensor-{group_id}",
                "alias": f"area-{group_id}",
                "type": "bev",
            }
        sensors.append(sensor)
    return sensors


def _ensure_map_file(directory: Path) -> Path:
    """Create a minimal map image in *directory* and return its path."""
    map_path = directory / "Top.png"
    if not map_path.exists():
        try:
            from PIL import Image

            Image.new("RGB", (200, 200), color=(0, 0, 0)).save(map_path)
        except Exception:
            map_path.write_bytes(MINIMAL_PNG)
    return map_path


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files under spatialai_data_utils/tmp/."""
    project_root = Path(__file__).resolve().parents[3]
    tmp_base = project_root / "tmp"
    tmp_base.mkdir(exist_ok=True)

    temp_path = tempfile.mkdtemp(dir=tmp_base, prefix="test_origin_")
    preserve_tmp = os.getenv("PRESERVE_TEST_TMP", "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    logger.debug("=" * 80)
    logger.debug("Test temp directory: %s", temp_path)
    logger.debug("=" * 80)
    yield temp_path

    if preserve_tmp:
        logger.debug("Preserving temp directory for inspection: %s", temp_path)
    else:
        shutil.rmtree(temp_path, ignore_errors=True)


@pytest.fixture
def calibration_with_groups_file(temp_dir):
    """Synthetic calibration file WITH pre-assigned group information."""
    sensors = _make_synthetic_sensors(n_cameras=6, with_groups=True)
    temp_file = Path(temp_dir) / "calibration_with_groups.json"
    with open(temp_file, "w") as f:
        json.dump({"sensors": sensors}, f)
    return str(temp_file)


@pytest.fixture
def map_file(temp_dir):
    """Synthetic map image for visualization tests."""
    return str(_ensure_map_file(Path(temp_dir)))


@pytest.fixture
def calibration_without_groups_file(temp_dir):
    """Synthetic calibration file WITHOUT group information."""
    sensors = _make_synthetic_sensors(n_cameras=6, with_groups=False)
    temp_file = Path(temp_dir) / "calibration_without_groups.json"
    with open(temp_file, "w") as f:
        json.dump({"sensors": sensors}, f)
    return str(temp_file)


class TestOriginCalculationWithGroups:
    """Test suite for origin calculation when groups already exist."""

    def test_calculate_origins_with_existing_groups(
        self, calibration_with_groups_file, temp_dir
    ):
        """
        Test that origins are calculated correctly when groups already exist.

        Expected behavior:
        - Groups remain unchanged
        - Origins and dimensions are updated based on FOV polygons
        - Each group has valid origin and dimensions
        """
        input_file = calibration_with_groups_file

        # Load original data to compare
        with open(input_file, "r") as f:
            original_data = json.load(f)
        original_sensor_count = len(original_data["sensors"])

        # Run origin calculation
        output_file = calculate_group_origins_from_calibration(
            input_calibration=input_file,
            output=str(Path(temp_dir) / "output_with_origins.json"),
            n_sensor_groups=2,
            dilation=1.0,
            prefer_existing_fov=True,  # Use FOV from attributes
        )

        logger.debug("Output file created: %s", output_file)

        # Load result
        with open(output_file, "r") as f:
            result = json.load(f)

        # Assertions
        assert "sensors" in result
        assert len(result["sensors"]) == original_sensor_count, (
            "Number of sensors should remain the same when groups already exist"
        )

        # Check that groups exist and have valid origins
        groups_found = set()
        for sensor in result["sensors"]:
            assert "group" in sensor, f"Sensor {sensor['id']} should have group info"

            group_name = sensor["group"]["name"]
            groups_found.add(group_name)

            # Check origin is calculated
            origin = sensor["group"]["origin"]
            assert len(origin) == 2, "Origin should be [x, y]"
            assert isinstance(origin[0], (int, float)), "Origin x should be numeric"
            assert isinstance(origin[1], (int, float)), "Origin y should be numeric"

            # Check dimensions are calculated
            dimensions = sensor["group"]["dimensions"]
            assert len(dimensions) == 4, (
                "Dimensions should be [x_min, y_min, x_max, y_max]"
            )
            assert all(isinstance(d, (int, float)) for d in dimensions), (
                "All dimensions should be numeric"
            )

            # Verify dimensions make sense (max > min)
            assert dimensions[2] > dimensions[0], "x_max should be > x_min"
            assert dimensions[3] > dimensions[1], "y_max should be > y_min"

        # Verify we have at least one group
        assert len(groups_found) >= 1, "Should have at least one group"
        assert all(name.startswith("bev-sensor-") for name in groups_found), (
            "All group names should follow bev-sensor-N pattern"
        )

    def test_origins_calculated_from_frustum(
        self, calibration_with_groups_file, map_file, temp_dir
    ):
        """
        Test origin calculation using frustum-based FOV generation.

        Expected behavior:
        - Origins calculated from camera frustum instead of FOV attributes
        - Valid origins and dimensions generated
        """
        # Run origin calculation with frustum mode
        output_file = calculate_group_origins_from_calibration(
            input_calibration=calibration_with_groups_file,
            output=str(Path(temp_dir) / "output_frustum.json"),
            n_sensor_groups=2,
            dilation=1.0,
            prefer_existing_fov=False,  # Use frustum calculation
            height_range=(1.0, 3.0),
            map_file=map_file,
        )

        logger.debug("Output file (frustum mode): %s", output_file)

        # Load result
        with open(output_file, "r") as f:
            result = json.load(f)

        # Verify all sensors have valid origins
        for sensor in result["sensors"]:
            origin = sensor["group"]["origin"]
            dimensions = sensor["group"]["dimensions"]

            assert len(origin) == 2
            assert len(dimensions) == 4
            assert all(isinstance(v, (int, float)) for v in origin)
            assert all(isinstance(v, (int, float)) for v in dimensions)

    def test_sensor_filtering_with_groups(self, calibration_with_groups_file, temp_dir):
        """
        Test that sensor name filtering works correctly.

        Expected behavior:
        - Only specified sensors are processed
        - Origins calculated only for filtered sensors
        """
        # Load original to find available sensors
        with open(calibration_with_groups_file, "r") as f:
            original_data = json.load(f)
        available_sensors = [s["id"] for s in original_data["sensors"]]

        # Filter to first 2 available sensors
        sensors_to_filter = available_sensors[:2]

        output_file = calculate_group_origins_from_calibration(
            input_calibration=calibration_with_groups_file,
            output=str(Path(temp_dir) / "output_filtered.json"),
            sensor_names=sensors_to_filter,
            dilation=1.0,
        )

        logger.debug("Output file (filtered sensors %s): %s", sensors_to_filter, output_file)

        with open(output_file, "r") as f:
            result = json.load(f)

        # Should only have the filtered sensors
        result_sensor_ids = {s["id"] for s in result["sensors"]}
        assert len(result["sensors"]) <= len(sensors_to_filter), (
            "Should have at most the filtered sensor count"
        )
        assert result_sensor_ids.issubset(set(sensors_to_filter)), (
            "All result sensors should be from the filtered list"
        )


class TestVisualization:
    """Test visualization functionality."""

    def test_visualize_without_map_file(self, calibration_with_groups_file, temp_dir):
        """
        Test that visualization works without a map file (black background).

        Expected behavior:
        - Visualization generates successfully with black background
        - Output map PNG file is created in a _vis subfolder
        """
        output_json = str(Path(temp_dir) / "output_black_bg.json")

        output_file = calculate_group_origins_from_calibration(
            input_calibration=calibration_with_groups_file,
            output=output_json,
            n_sensor_groups=2,
            dilation=1.0,
            visualize=True,  # Enable visualization
            map_file=None,  # No map file - should use black background
        )

        logger.debug("Output file created: %s", output_file)

        # Verify output JSON exists
        assert Path(output_file).exists()

        # Visualization creates files in a _vis subfolder: <base>_vis/map.png
        expected_vis_folder = Path(temp_dir) / "output_black_bg_vis"
        expected_map_file = expected_vis_folder / "map.png"
        assert expected_vis_folder.exists(), (
            f"Visualization folder should be created: {expected_vis_folder}"
        )
        assert expected_map_file.exists(), (
            f"Visualization map file should be created: {expected_map_file}"
        )
        logger.debug("Visualization map created: %s", expected_map_file)

    def test_visualize_with_map_file(
        self, calibration_with_groups_file, map_file, temp_dir
    ):
        """
        Test that visualization works with a map file.

        Expected behavior:
        - Visualization generates successfully with map background
        - Output map PNG file is created in a _vis subfolder
        """
        output_json = str(Path(temp_dir) / "output_with_map_bg.json")

        output_file = calculate_group_origins_from_calibration(
            input_calibration=calibration_with_groups_file,
            output=output_json,
            n_sensor_groups=2,
            dilation=1.0,
            visualize=True,  # Enable visualization
            map_file=map_file,  # Use provided map file
        )

        logger.debug("Output file created: %s", output_file)

        # Verify output JSON exists
        assert Path(output_file).exists()

        # Visualization creates files in a _vis subfolder: <base>_vis/map.png
        expected_vis_folder = Path(temp_dir) / "output_with_map_bg_vis"
        expected_map_file = expected_vis_folder / "map.png"
        assert expected_vis_folder.exists(), (
            f"Visualization folder should be created: {expected_vis_folder}"
        )
        assert expected_map_file.exists(), (
            f"Visualization map file should be created: {expected_map_file}"
        )
        logger.debug("Visualization map created: %s", expected_map_file)


class TestOriginCalculationEdgeCases:
    """Test edge cases and error handling."""

    def test_invalid_sensor_names(self, calibration_with_groups_file):
        """
        Test that invalid sensor names are rejected.

        Expected behavior:
        - Should exit with error if sensor names don't exist
        """
        with pytest.raises(SystemExit):
            calculate_group_origins_from_calibration(
                input_calibration=calibration_with_groups_file,
                sensor_names=["NonExistentCamera_999"],
            )

    def test_output_file_created(self, calibration_with_groups_file, temp_dir):
        """
        Test that output file is created correctly.

        Expected behavior:
        - Output file path returned
        - File exists and contains valid JSON
        """
        output_file = calculate_group_origins_from_calibration(
            input_calibration=calibration_with_groups_file,
            output=str(Path(temp_dir) / "custom_output.json"),
            n_sensor_groups=2,
            dilation=1.0,
        )

        logger.debug("Output file created: %s", output_file)

        # Verify output file exists
        assert Path(output_file).exists()

        # Verify it contains valid JSON
        with open(output_file, "r") as f:
            result = json.load(f)
            assert "sensors" in result

    def test_overwrite_flag(self, calibration_with_groups_file, temp_dir):
        """
        Test that overwrite flag works correctly.

        Expected behavior:
        - Input file is overwritten when overwrite=True
        """
        # Copy file to temp since we'll overwrite it
        temp_input = Path(temp_dir) / "calibration_to_overwrite.json"
        shutil.copy(calibration_with_groups_file, temp_input)

        output_file = calculate_group_origins_from_calibration(
            input_calibration=str(temp_input),
            overwrite=True,
            n_sensor_groups=2,
            dilation=1.0,
        )

        logger.debug("File overwritten: %s", output_file)

        # Output should be same as input when overwrite=True
        assert Path(output_file) == temp_input

        # File should be updated with calculated origins
        with open(output_file, "r") as f:
            result = json.load(f)
            # Verify file was actually updated
            assert "sensors" in result
            assert all("group" in sensor for sensor in result["sensors"])


class TestDirectFunctionCalls:
    """Test direct calls to underlying functions."""

    def test_calculate_and_update_group_origins_direct(
        self, calibration_with_groups_file
    ):
        """
        Test direct call to calculate_and_update_group_origins.

        Expected behavior:
        - Function updates calibration data in place
        - Returns updated calibration data
        """
        # Load synthetic calibration data
        with open(calibration_with_groups_file, "r") as f:
            calibration_data = json.load(f)

        # Call function directly
        result = calculate_and_update_group_origins(
            calibration_data,
            dilation_distance=1.0,
            height_range=(1.0, 3.0),
            use_frustum=False,
        )

        # Verify origins were updated
        for sensor in result["sensors"]:
            origin = sensor["group"]["origin"]
            dimensions = sensor["group"]["dimensions"]

            assert len(origin) == 2
            assert len(dimensions) == 4
            assert all(isinstance(v, (int, float)) for v in origin)
            assert all(isinstance(v, (int, float)) for v in dimensions)


class TestSensorGroupingWithNSensorGroups:
    """Test suite for n_sensor_groups parameter when group information is missing."""

    def test_n_sensor_groups_equals_1(self, calibration_without_groups_file, temp_dir):
        """
        Test that n_sensor_groups=1 assigns all sensors to 'bev-sensor-1'.

        Expected behavior:
        - All sensors assigned to 'bev-sensor-1'
        - FOV polygons extracted
        - Overlap ratio calculated
        - Origins and dimensions calculated
        """
        input_file = calibration_without_groups_file

        # Load original data to verify sensor count
        with open(input_file, "r") as f:
            original_data = json.load(f)
        original_sensor_count = len(original_data["sensors"])

        logger.debug("Testing n_sensor_groups=1 with %d sensors", original_sensor_count)

        # Run origin calculation with n_sensor_groups=1 (default)
        output_file = calculate_group_origins_from_calibration(
            input_calibration=input_file,
            output=str(Path(temp_dir) / "output_n_sensor_groups_1.json"),
            n_sensor_groups=1,
            max_sensors_per_group=None,  # No limit for this test
            dilation=1.0,
            prefer_existing_fov=False,  # Use frustum calculation
            height_range=(1.0, 3.0),
        )

        logger.debug("Output file created: %s", output_file)

        # Load result
        with open(output_file, "r") as f:
            result = json.load(f)

        # Assertions
        assert "sensors" in result
        assert len(result["sensors"]) == original_sensor_count, (
            "All sensors should be present in output"
        )

        # Check that all sensors are assigned to a group
        group_names = set()
        for sensor in result["sensors"]:
            assert "group" in sensor, f"Sensor {sensor['id']} should have group info"

            # Verify group has required fields
            assert "name" in sensor["group"], (
                f"Sensor {sensor['id']} group should have 'name' field"
            )
            assert "type" in sensor["group"], (
                f"Sensor {sensor['id']} group should have 'type' field"
            )
            assert "alias" in sensor["group"], (
                f"Sensor {sensor['id']} group should have 'alias' field"
            )

            group_name = sensor["group"]["name"]
            group_names.add(group_name)

            # Check origin is calculated
            origin = sensor["group"]["origin"]
            assert len(origin) == 2, "Origin should be [x, y]"
            assert isinstance(origin[0], (int, float)), "Origin x should be numeric"
            assert isinstance(origin[1], (int, float)), "Origin y should be numeric"

            # Check dimensions are calculated
            dimensions = sensor["group"]["dimensions"]
            assert len(dimensions) == 4, (
                "Dimensions should be [x_min, y_min, x_max, y_max]"
            )
            assert all(isinstance(d, (int, float)) for d in dimensions), (
                "All dimensions should be numeric"
            )

            # Verify dimensions make sense (max > min)
            assert dimensions[2] > dimensions[0], "x_max should be > x_min"
            assert dimensions[3] > dimensions[1], "y_max should be > y_min"

        # Verify only one group exists (for n_sensor_groups=1)
        assert len(group_names) == 1, (
            "Should have exactly one group when n_sensor_groups=1"
        )

        logger.debug("All %d sensors assigned to 'bev-sensor-1'", original_sensor_count)

    def test_n_sensor_groups_default_value(
        self, calibration_without_groups_file, temp_dir
    ):
        """
        Test that default n_sensor_groups value (1) works correctly.

        Expected behavior:
        - When n_sensor_groups is not specified, defaults to 1
        - All sensors assigned to 'bev-sensor-1'
        """
        input_file = calibration_without_groups_file

        # Run without specifying n_sensor_groups (should default to 1)
        output_file = calculate_group_origins_from_calibration(
            input_calibration=input_file,
            output=str(Path(temp_dir) / "output_default_n_sensor_groups.json"),
            max_sensors_per_group=None,  # No limit for this test
            dilation=1.0,
            prefer_existing_fov=False,
            height_range=(1.0, 3.0),
        )

        logger.debug("Output file (default n_sensor_groups): %s", output_file)

        # Load result
        with open(output_file, "r") as f:
            result = json.load(f)

        # Verify all sensors have required group fields
        group_names = {sensor["group"]["name"] for sensor in result["sensors"]}
        assert len(group_names) == 1, (
            "Default n_sensor_groups should assign all sensors to a single group"
        )

        # Verify group has required fields
        for sensor in result["sensors"]:
            assert "name" in sensor["group"], "Group should have 'name' field"
            assert "alias" in sensor["group"], "Group should have 'alias' field"
            assert "type" in sensor["group"], "Group should have 'type' field"

    def test_n_sensor_groups_greater_than_1_raises_error(
        self, calibration_without_groups_file
    ):
        """
        Test that n_sensor_groups > 1 raises an appropriate error.

        Expected behavior:
        - Should exit with error message about clustering not implemented
        """
        input_file = calibration_without_groups_file

        logger.debug("Testing n_sensor_groups > 1 (should raise error)")

        # Should raise SystemExit because clustering is not yet implemented
        with pytest.raises(SystemExit):
            calculate_group_origins_from_calibration(
                input_calibration=input_file,
                n_sensor_groups=3,  # Not yet supported
                dilation=1.0,
            )

        logger.debug("Correctly raised error for n_sensor_groups > 1")

    def test_overlap_ratio_calculation_with_prefer_existing_fov(
        self, calibration_without_groups_file, temp_dir
    ):
        """
        Test that overlap ratio is calculated when prefer_existing_fov=True.

        Expected behavior:
        - FOV polygons extracted from calibration attributes if available
        - Overlap ratio calculated and logged
        - Warning shown if overlap is low
        """
        input_file = calibration_without_groups_file

        # Run with prefer_existing_fov=True
        output_file = calculate_group_origins_from_calibration(
            input_calibration=input_file,
            output=str(Path(temp_dir) / "output_with_fov_attributes.json"),
            n_sensor_groups=1,
            max_sensors_per_group=None,  # No limit for this test
            dilation=1.0,
            prefer_existing_fov=True,  # Try to use FOV from attributes
            height_range=(1.0, 3.0),
        )

        logger.debug("Output file (with FOV attributes): %s", output_file)

        # Load result
        with open(output_file, "r") as f:
            result = json.load(f)

        # Verify all sensors have valid origins
        for sensor in result["sensors"]:
            assert "group" in sensor
            assert "name" in sensor["group"], "Group should have 'name' field"
            assert "alias" in sensor["group"], "Group should have 'alias' field"
            assert "type" in sensor["group"], "Group should have 'type' field"
            assert len(sensor["group"]["origin"]) == 2
            assert len(sensor["group"]["dimensions"]) == 4

    def test_sensor_filtering_with_n_sensor_groups(
        self, calibration_without_groups_file, temp_dir
    ):
        """
        Test that sensor filtering works with n_sensor_groups=1.

        Expected behavior:
        - Only filtered sensors are processed
        - All filtered sensors assigned to 'bev-sensor-1'
        - Origins calculated only for filtered sensors
        """
        # Load original to find available sensors
        with open(calibration_without_groups_file, "r") as f:
            original_data = json.load(f)
        available_sensors = [s["id"] for s in original_data["sensors"]]

        # Filter to first 2 available sensors
        sensors_to_filter = available_sensors[:2]

        logger.debug("Testing sensor filtering with n_sensor_groups=1, sensors: %s", sensors_to_filter)

        output_file = calculate_group_origins_from_calibration(
            input_calibration=calibration_without_groups_file,
            output=str(Path(temp_dir) / "output_filtered_n_sensor_groups.json"),
            n_sensor_groups=1,
            sensor_names=sensors_to_filter,
            dilation=1.0,
        )

        logger.debug("Output file (filtered): %s", output_file)

        with open(output_file, "r") as f:
            result = json.load(f)

        # Should only have the filtered sensors
        result_sensor_ids = {s["id"] for s in result["sensors"]}
        assert len(result["sensors"]) == len(sensors_to_filter), (
            "Should have exactly the filtered sensor count"
        )
        assert result_sensor_ids == set(sensors_to_filter), (
            "Result sensors should match filtered list"
        )

        # All sensors should have required group fields
        for sensor in result["sensors"]:
            assert "name" in sensor["group"], "Group should have 'name' field"
            assert "alias" in sensor["group"], "Group should have 'alias' field"
            assert "type" in sensor["group"], "Group should have 'type' field"


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v", "--tb=short"])

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
from unittest.mock import Mock, patch
from datetime import datetime

from mdx.analytics.core.utils.anomaly_util import AnomalyDetector
from mdx.analytics.core.schema.config import (
    AbnormalMovementConfig,
    AppConfig,
    CollisionDetectionConfig,
    SpeedViolationConfig,
    UnexpectedStopConfig,
)
from mdx.analytics.core.schema.models import (
    Behavior, 
    Sensor, 
    Object, 
    GeoLocation, 
    Point, 
    Place,
    Coordinate,
)
from mdx.analytics.core.schema.trajectory.trajectory import Trajectory
from mdx.analytics.core.transform.calibration.calibration_dynamic import CalibrationType


class TestAnomalyDetector:
    @pytest.fixture
    def anomaly_detector(self):
        # Create a real AppConfig instance
        config = AppConfig()
        
        # Set up anomaly-related configurations in the app config
        config.set_app_config("anomalyClasses", '["vehicle", "person", "truck"]')
        config.set_app_config("trajGeoCoordEnable", "false")
        
        collision_detection_config = CollisionDetectionConfig(
            enable=True,
            stopTimeByDistanceThreshold=5,
            stopSpeedThreshold=5
        )
        config.set_sensor_config("anomalyCollisionDetection", collision_detection_config.model_dump_json())
        
        return AnomalyDetector(config, CalibrationType.GEO)

    @pytest.fixture
    def sample_behavior(self):
        """Create a sample behavior object for testing"""
        sensor = Sensor(id="sensor1", type="camera")
        obj = Object(id="obj1", type="vehicle")
        
        # Create trajectory points
        geo_location = GeoLocation(type="linestring")
        geo_location.coordinates = [
            Point(point=[0.0, 0.0]),
            Point(point=[1.0, 1.0]),
            Point(point=[2.0, 2.0]),
            Point(point=[3.0, 3.0])
        ]
        
        behavior = Behavior(
            id="test_behavior #-# obj1",
            timestamp=datetime.now(),
            end=datetime.now(),
            sensor=sensor,
            object=obj,
            timeInterval=10.0,
            distance=5.0,
            speed=15.0,
            locations=geo_location,
            smoothLocations=geo_location,
            place=Place(id="place1", name="test_place"),
            videoPath="frameId-test_frame",
            embeddings=[]
        )
        return behavior

    @pytest.fixture 
    def sample_configs(self):
        """Create sample configuration objects"""
        return {
            'speed_violation': SpeedViolationConfig(
                enable=True,
                mphThreshold=25.0,
                timeIntervalSecThreshold=5.0
            ),
            'unexpected_stop': UnexpectedStopConfig(
                enable=True,
                mphThreshold=1.0,
                timeIntervalSecThreshold=20.0
            ),
            'abnormal_movement': AbnormalMovementConfig(
                enable=True,
                distanceMetersThreshold=10.0,
                timeIntervalSecThreshold=5.0,
                useLinearDistance=False,
                strideDeviationComputation=5,
                distanceMetersThresholdDeviationComputation=10.0,
                abnormalRelativeThreshold=2.0,
                abnormalRelativeMaxThreshold=10.0,
                abnormalRelativeDirectionChangeEnable=True,
                changeInDirectionDegree=30.0
            ),
            'collision_detection': CollisionDetectionConfig(
                enable=True,
                useSpeedStopCondition=True,
                stopSpeedThreshold=5.0,
                stopTimeBySpeedThreshold=5.0
            )
        }

    def test_collision_detection_speed_stop(self, anomaly_detector):
        # Speed-based stop should trigger collision
        behavior = Mock(spec=Behavior)
        behavior.speed = 2
        behavior.timeInterval = 6
        behavior.smoothLocations = Mock()
        behavior.smoothLocations.coordinates = []
        behavior.distance = 0
        behavior.timestamp = datetime.now()
        behavior.end = datetime.now()
        behavior.id = "test_id"
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        behavior.object = Mock()
        behavior.object.id = "obj1"
        behavior.object.type = "Vehicle"
        behavior.place = "test_place"
        behavior.videoPath = "frameId-test_frame"
        behavior.embeddings = []

        config = CollisionDetectionConfig(
            enable=True,
            useDistanceStopCondition=False,
            useSpeedStopCondition=True,
            stopSpeedThreshold=5.0,
            stopTimeBySpeedThreshold=5.0,
        )

        with patch.object(anomaly_detector.stop_detection, 'is_vehicle_stopped_by_speed', return_value=True):
            result, trigger_modules = anomaly_detector.collision_detection(behavior, False, config)

        assert result is True
        assert 'Stop by speed' in trigger_modules

    def test_collision_detection_abnormal_movement(self, anomaly_detector):
        # Abnormal movement should trigger collision when configured
        behavior = Mock(spec=Behavior)
        behavior.speed = 10
        behavior.timeInterval = 3
        behavior.smoothLocations = Mock()
        behavior.smoothLocations.coordinates = []
        behavior.distance = 0
        behavior.timestamp = datetime.now()
        behavior.end = datetime.now()
        behavior.id = "test_id"
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        behavior.object = Mock()
        behavior.object.id = "obj1"
        behavior.object.type = "Vehicle"
        behavior.place = "test_place"
        behavior.videoPath = "frameId-test_frame"
        behavior.embeddings = []

        config = CollisionDetectionConfig(
            enable=True,
            useDistanceStopCondition=False,
            useSpeedStopCondition=False,
        )

        result, trigger_modules = anomaly_detector.collision_detection(behavior, True, config)

        assert result is True
        assert 'Abnormal Movement' in trigger_modules

    def test_collision_detection_distance_stop(self, anomaly_detector):
        # Distance-based stop should trigger collision
        behavior = Mock(spec=Behavior)
        behavior.speed = 2
        behavior.timeInterval = 3
        behavior.smoothLocations = Mock()
        behavior.smoothLocations.coordinates = []
        behavior.distance = 0
        behavior.timestamp = datetime.now()
        behavior.end = datetime.now()
        behavior.id = "test_id"
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        behavior.object = Mock()
        behavior.object.id = "obj1"
        behavior.object.type = "Vehicle"
        behavior.place = "test_place"
        behavior.videoPath = "frameId-test_frame"
        behavior.embeddings = []

        config = CollisionDetectionConfig(
            enable=True,
            useDistanceStopCondition=True,
            useSpeedStopCondition=False,
        )

        with patch.object(anomaly_detector.stop_detection, 'is_vehicle_stopped_by_distance', return_value=True):
            result, trigger_modules = anomaly_detector.collision_detection(behavior, False, config)

        assert result is True
        assert 'Stop by distance' in trigger_modules

    def test_collision_detection_no_collision(self, anomaly_detector):
        # No triggers should result in no collision
        behavior = Mock(spec=Behavior)
        behavior.speed = 20
        behavior.timeInterval = 3
        behavior.smoothLocations = Mock()
        behavior.smoothLocations.coordinates = []
        behavior.distance = 0
        behavior.timestamp = datetime.now()
        behavior.end = datetime.now()
        behavior.id = "test_id"
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        behavior.object = Mock()
        behavior.object.id = "obj1"
        behavior.object.type = "Vehicle"
        behavior.place = "test_place"
        behavior.videoPath = "frameId-test_frame"
        behavior.embeddings = []

        config = CollisionDetectionConfig(
            enable=True,
            useDistanceStopCondition=True,
            useSpeedStopCondition=True,
            stopSpeedThreshold=5.0,
            stopTimeBySpeedThreshold=5.0,
        )

        with patch.object(anomaly_detector.stop_detection, 'is_vehicle_stopped_by_distance', return_value=False), \
             patch.object(anomaly_detector.stop_detection, 'is_vehicle_stopped_by_speed', return_value=False):
            result, trigger_modules = anomaly_detector.collision_detection(behavior, False, config)

        assert result is False
        assert len(trigger_modules) == 0

    def test_collision_detection_disabled(self, anomaly_detector):
        # Disabled config should never trigger collision
        behavior = Mock(spec=Behavior)
        behavior.speed = 2
        behavior.timeInterval = 6
        behavior.smoothLocations = Mock()
        behavior.smoothLocations.coordinates = []
        behavior.distance = 0
        behavior.timestamp = datetime.now()
        behavior.end = datetime.now()
        behavior.id = "test_id"
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        behavior.object = Mock()
        behavior.object.id = "obj1"
        behavior.object.type = "Vehicle"
        behavior.place = "test_place"
        behavior.videoPath = "frameId-test_frame"
        behavior.embeddings = []

        config = CollisionDetectionConfig(enable=False)

        result, trigger_modules = anomaly_detector.collision_detection(behavior, True, config)

        assert result is False

    # Additional comprehensive tests for all methods

    def test_speed_violation_enabled_above_threshold(self, anomaly_detector, sample_configs):
        """Test speed violation when enabled and speed is above threshold"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 30.0  # Above threshold of 25
        behavior.timeInterval = 10.0  # Above threshold of 5
        
        violation, description = anomaly_detector.speed_violation(behavior, sample_configs['speed_violation'])
        
        assert violation is True
        assert "Speed Violation" in description
        assert "30" in description

    def test_speed_violation_enabled_below_threshold(self, anomaly_detector, sample_configs):
        """Test speed violation when enabled but speed is below threshold"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 20.0  # Below threshold of 25
        behavior.timeInterval = 10.0  # Above threshold of 5
        
        violation, description = anomaly_detector.speed_violation(behavior, sample_configs['speed_violation'])
        
        assert violation is False
        assert description == ""

    def test_speed_violation_enabled_short_duration(self, anomaly_detector, sample_configs):
        """Test speed violation when speed is high but duration is too short"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 30.0  # Above threshold of 25
        behavior.timeInterval = 3.0  # Below threshold of 5
        
        violation, description = anomaly_detector.speed_violation(behavior, sample_configs['speed_violation'])
        
        assert violation is False
        assert description == ""

    def test_speed_violation_disabled(self, anomaly_detector):
        """Test speed violation when disabled"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 30.0
        behavior.timeInterval = 10.0
        
        config = SpeedViolationConfig(enable=False, mphThreshold=25.0, timeIntervalSecThreshold=5.0)
        violation, description = anomaly_detector.speed_violation(behavior, config)
        
        assert violation is False
        assert description == ""

    def test_unexpected_stop_enabled_below_threshold(self, anomaly_detector, sample_configs):
        """Test unexpected stop when enabled and speed is below threshold"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 0.5  # Below threshold of 1
        behavior.timeInterval = 30.0  # Above threshold of 20
        
        stopped, description = anomaly_detector.unexpected_stop(behavior, sample_configs['unexpected_stop'])
        
        assert stopped is True
        assert "Unexpected Stop" in description
        assert "30" in description

    def test_unexpected_stop_enabled_above_threshold(self, anomaly_detector, sample_configs):
        """Test unexpected stop when enabled but speed is above threshold"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 2.0  # Above threshold of 1
        behavior.timeInterval = 30.0  # Above threshold of 20
        
        stopped, description = anomaly_detector.unexpected_stop(behavior, sample_configs['unexpected_stop'])
        
        assert stopped is False
        assert description == ""

    def test_unexpected_stop_enabled_short_duration(self, anomaly_detector, sample_configs):
        """Test unexpected stop when speed is low but duration is too short"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 0.5  # Below threshold of 1
        behavior.timeInterval = 10.0  # Below threshold of 20
        
        stopped, description = anomaly_detector.unexpected_stop(behavior, sample_configs['unexpected_stop'])
        
        assert stopped is False
        assert description == ""

    def test_unexpected_stop_disabled(self, anomaly_detector):
        """Test unexpected stop when disabled"""
        behavior = Mock(spec=Behavior)
        behavior.id = "test_obj"
        behavior.speed = 0.5
        behavior.timeInterval = 30.0
        
        config = UnexpectedStopConfig(enable=False, mphThreshold=1.0, timeIntervalSecThreshold=20.0)
        stopped, description = anomaly_detector.unexpected_stop(behavior, config)
        
        assert stopped is False
        assert description == ""

    def test_abnormal_movement_disabled(self, anomaly_detector, sample_behavior):
        """Test abnormal movement when disabled"""
        config = AbnormalMovementConfig(enable=False)
        
        abnormal, description = anomaly_detector.abnormal_movement(sample_behavior, None, config)
        
        assert abnormal is False
        assert description == ""

    def test_abnormal_movement_insufficient_time(self, anomaly_detector, sample_behavior, sample_configs):
        """Test abnormal movement when time interval is too short"""
        sample_behavior.timeInterval = 3.0  # Below threshold of 5
        
        abnormal, description = anomaly_detector.abnormal_movement(
            sample_behavior, None, sample_configs['abnormal_movement']
        )
        
        assert abnormal is False
        assert description == ""

    def test_abnormal_movement_insufficient_distance(self, anomaly_detector, sample_behavior, sample_configs):
        """Test abnormal movement when distance is too short"""
        sample_behavior.timeInterval = 10.0  # Above threshold
        sample_behavior.distance = 5.0  # Below threshold of 10
        
        abnormal, description = anomaly_detector.abnormal_movement(
            sample_behavior, None, sample_configs['abnormal_movement']
        )
        
        assert abnormal is False
        assert description == ""

    @patch('mdx.analytics.core.utils.anomaly_util.euclidean_distance')
    def test_abnormal_movement_linear_distance_cartesian(self, mock_euclidean, anomaly_detector, sample_behavior, sample_configs):
        """Test abnormal movement with linear distance in cartesian coordinates"""
        sample_behavior.timeInterval = 10.0
        sample_behavior.distance = 15.0
        
        # Mock euclidean distance calculation
        mock_euclidean.return_value = 20.0
        
        # Enable linear distance
        config = sample_configs['abnormal_movement']
        config.useLinearDistance = True
        
        # Mock calculateDeviation to return values that would trigger abnormal movement
        with patch.object(anomaly_detector, 'calculateDeviation') as mock_calc_dev:
            mock_calc_dev.return_value = (5.0, 2.0, Mock())
            
            # Mock changeInDirection to return no significant change
            with patch.object(anomaly_detector, 'changeInDirection') as mock_change_dir:
                mock_change_dir.return_value = (False, 10.0)
                
                abnormal, description = anomaly_detector.abnormal_movement(sample_behavior, None, config)
                
                # Should call euclidean distance since traj_geo_coord_enable is False
                mock_euclidean.assert_called_once()
                mock_calc_dev.assert_called_once()
                
                # Expected to be abnormal since deviation (5.0) > abnormalRelativeThreshold (2.0) * rDeviation (2.0) = 4.0
                # and deviation (5.0) < abnormalRelativeMaxThreshold (10.0) * rDeviation (2.0) = 20.0
                assert abnormal is True
                assert "Abnormal Movement" in description

    @patch('mdx.analytics.core.utils.anomaly_util.haversine_distance')
    def test_abnormal_movement_linear_distance_geographic(self, mock_haversine, anomaly_detector, sample_behavior, sample_configs):
        """Test abnormal movement with linear distance in geographic coordinates"""
        # Enable geographic coordinates by setting the app config
        anomaly_detector.app_config.set_app_config("trajGeoCoordEnable", "true")
        
        sample_behavior.timeInterval = 10.0
        sample_behavior.distance = 15.0
        
        # Mock haversine distance calculation
        mock_haversine.return_value = 25.0
        
        # Enable linear distance
        config = sample_configs['abnormal_movement']
        config.useLinearDistance = True
        
        # Mock calculateDeviation to return values that would trigger abnormal movement
        with patch.object(anomaly_detector, 'calculateDeviation') as mock_calc_dev:
            mock_calc_dev.return_value = (5.0, 2.0, Mock())
            
            # Mock changeInDirection to return no significant change
            with patch.object(anomaly_detector, 'changeInDirection') as mock_change_dir:
                mock_change_dir.return_value = (False, 10.0)
                
                abnormal, description = anomaly_detector.abnormal_movement(sample_behavior, None, config)
                
                # Should call haversine distance since traj_geo_coord_enable is True
                mock_haversine.assert_called_once()
                mock_calc_dev.assert_called_once()
                
                assert abnormal is True
                assert "Abnormal Movement" in description

    def test_abnormal_movement_direction_change_disabled(self, anomaly_detector, sample_behavior, sample_configs):
        """Test abnormal movement when direction change detection is disabled"""
        sample_behavior.timeInterval = 10.0
        sample_behavior.distance = 15.0
        
        # Disable direction change detection
        config = sample_configs['abnormal_movement']
        config.abnormalRelativeDirectionChangeEnable = False
        
        # Mock calculateDeviation to return values that would trigger abnormal movement
        with patch.object(anomaly_detector, 'calculateDeviation') as mock_calc_dev:
            mock_calc_dev.return_value = (5.0, 2.0, Mock())
            
            abnormal, description = anomaly_detector.abnormal_movement(sample_behavior, None, config)
            
            mock_calc_dev.assert_called_once()
            
            # Should be abnormal since direction change check is disabled
            assert abnormal is True
            assert "Abnormal Movement" in description

    def test_abnormal_movement_zero_rdeviation(self, anomaly_detector, sample_behavior, sample_configs):
        """Test abnormal movement when rDeviation is zero"""
        sample_behavior.timeInterval = 10.0
        sample_behavior.distance = 15.0
        
        # Mock calculateDeviation to return zero rDeviation
        with patch.object(anomaly_detector, 'calculateDeviation') as mock_calc_dev:
            mock_calc_dev.return_value = (5.0, 0.0, Mock())
            
            abnormal, description = anomaly_detector.abnormal_movement(sample_behavior, None, sample_configs['abnormal_movement'])
            
            mock_calc_dev.assert_called_once()
            
            # Should not be abnormal since rDeviation is 0
            assert abnormal is False
            assert "Abnormal Movement" in description  # Description is still generated

    def test_collision_detection_multiple_triggers(self, anomaly_detector):
        """Test collision detection with multiple trigger conditions"""
        behavior = Mock(spec=Behavior)
        behavior.speed = 2
        behavior.timeInterval = 6
        behavior.smoothLocations = Mock()
        behavior.smoothLocations.coordinates = []
        behavior.distance = 0
        behavior.timestamp = datetime.now()
        behavior.end = datetime.now()
        behavior.id = "test_id"
        behavior.sensor = Mock()
        behavior.sensor.id = "sensor1"
        behavior.object = Mock()
        behavior.object.id = "obj1"
        behavior.object.type = "Vehicle"

        config = CollisionDetectionConfig(
            enable=True,
            useDistanceStopCondition=True,
            useSpeedStopCondition=True,
            stopSpeedThreshold=5.0,
            stopTimeBySpeedThreshold=5.0,
        )

        with patch.object(anomaly_detector.stop_detection, 'is_vehicle_stopped_by_distance', return_value=True), \
             patch.object(anomaly_detector.stop_detection, 'is_vehicle_stopped_by_speed', return_value=True):
            result, trigger_modules = anomaly_detector.collision_detection(
                behavior, True, config
            )

        assert result is True
        assert len(trigger_modules) == 3
        assert 'Stop by distance' in trigger_modules
        assert 'Abnormal Movement' in trigger_modules
        assert 'Stop by speed' in trigger_modules

    def test_detect_batch_empty_list(self, anomaly_detector):
        """Test detect_batch with empty behavior list"""
        crs = None
        collisions, anomalies = anomaly_detector.detect_batch([], crs)
        
        assert collisions == []
        assert anomalies == []

    def test_detect_batch_single_behavior(self, anomaly_detector, sample_behavior):
        """Test detect_batch with single behavior"""
        # Mock the detect method to return specific values
        with patch.object(anomaly_detector, 'detect') as mock_detect:
            mock_collision_info = {"flag": True, "trigger_modules": ["Test Module"]}
            mock_anomalies = [sample_behavior]
            mock_detect.return_value = (mock_collision_info, mock_anomalies)
            
            crs = None
            collisions, anomalies = anomaly_detector.detect_batch([sample_behavior], crs)
            
            mock_detect.assert_called_once_with(sample_behavior, crs)
            
            assert len(collisions) == 1
            assert len(anomalies) == 1
            
            # Check collision tuple structure
            collision = collisions[0]
            assert collision[0] == "obj1"  # object_id from behavior.id split
            assert collision[1] == "sensor1"  # sensor_id
            assert collision[2] == sample_behavior  # behavior
            assert collision[3] == ["Test Module"]  # trigger_modules

    def test_detect_batch_no_collisions(self, anomaly_detector, sample_behavior):
        """Test detect_batch when no collisions are detected"""
        with patch.object(anomaly_detector, 'detect') as mock_detect:
            mock_collision_info = {"flag": False, "trigger_modules": []}
            mock_anomalies = []
            mock_detect.return_value = (mock_collision_info, mock_anomalies)
            
            crs = None
            collisions, anomalies = anomaly_detector.detect_batch([sample_behavior], crs)
            
            assert len(collisions) == 0
            assert len(anomalies) == 0

    def test_detect_sensor_in_ignore_list(self, anomaly_detector, sample_behavior):
        """Test detect method when sensor is in ignore list"""
        # Set up the app config to ignore sensor1
        anomaly_detector.app_config.set_sensor_config("anomalyIgnoreSensors", '["sensor1"]', "sensor1")
        anomaly_detector.app_config.set_sensor_config("anomalyClasses", '["vehicle"]', "sensor1")
        
        collision_info, anomalies = anomaly_detector.detect(sample_behavior, None)
        
        # Should return default values when sensor is ignored
        assert collision_info == {"trigger_modules": [], "flag": False}
        assert anomalies == []

    def test_detect_object_type_not_in_classes(self, anomaly_detector, sample_behavior):
        """Test detect method when object type is not in monitored classes"""
        # Set up the app config to only monitor person class (not vehicle)
        anomaly_detector.app_config.set_sensor_config("anomalyClasses", '["person"]', "sensor1")
        anomaly_detector.app_config.set_sensor_config("anomalyIgnoreSensors", '[]', "sensor1")
        
        collision_info, anomalies = anomaly_detector.detect(sample_behavior, None)
        
        # Should return default values when object type is not monitored
        assert collision_info == {"trigger_modules": [], "flag": False}
        assert anomalies == []

    def test_detect_with_anomalies(self, anomaly_detector, sample_behavior):
        """Test detect method when anomalies are detected"""
        # Set up app config for anomaly detection
        anomaly_detector.app_config.set_sensor_config("anomalyClasses", '["vehicle"]', "sensor1")
        anomaly_detector.app_config.set_sensor_config("anomalyIgnoreSensors", '[]', "sensor1")
        
        # Configure speed violation with low threshold to trigger violation
        speed_config = SpeedViolationConfig(enable=True, mphThreshold=10.0, timeIntervalSecThreshold=5.0)
        anomaly_detector.app_config.set_sensor_config("anomalySpeedViolation", speed_config.model_dump_json(), "sensor1")
        
        # Set behavior to trigger speed violation
        sample_behavior.speed = 15.0  # Above threshold
        sample_behavior.timeInterval = 10.0  # Above threshold
        
        # Mock other detection methods to return False
        with patch.object(anomaly_detector, 'unexpected_stop') as mock_unexpected:
            with patch.object(anomaly_detector, 'abnormal_movement') as mock_abnormal:
                with patch.object(anomaly_detector, 'collision_detection') as mock_collision:
                    mock_unexpected.return_value = (False, "")
                    mock_abnormal.return_value = (False, "")
                    mock_collision.return_value = (False, [])
                    
                    collision_info, anomalies = anomaly_detector.detect(sample_behavior, None)
                    
                    # Should detect speed violation
                    assert len(anomalies) == 1
                    assert anomalies[0].analyticsModule.id == "Speed Anomaly Module"
                    assert "Speed Violation" in anomalies[0].analyticsModule.description

    def test_calculate_deviation_basic(self, anomaly_detector, sample_behavior):
        """Test basic deviation calculation without CRS"""
        deviation, r_deviation, actual_trajectory = anomaly_detector.calculateDeviation(
            sample_behavior, crs=None, step=2, distanceThreshold=1.0
        )
        
        # Without CRS, deviation should be 0
        assert deviation == 0
        assert r_deviation == 0
        assert actual_trajectory is not None
        assert len(actual_trajectory.points) > 0

    @patch('mdx.analytics.core.utils.anomaly_util.euclidean_distance')
    def test_calculate_deviation_distance_filtering(self, mock_euclidean, anomaly_detector, sample_behavior):
        """Test deviation calculation with distance filtering"""
        # Mock distance to control point filtering - need enough return values for all calls
        mock_euclidean.side_effect = [0.5, 2.0, 1.5, 3.0, 4.0, 5.0]  # Multiple values to handle all calls
        
        deviation, r_deviation, actual_trajectory = anomaly_detector.calculateDeviation(
            sample_behavior, crs=None, step=1, distanceThreshold=1.0
        )
        
        # Should filter out points based on distance threshold
        assert len(actual_trajectory.points) <= len(sample_behavior.smoothLocations.coordinates)

    def test_change_in_direction_significant_change(self, anomaly_detector):
        """Test direction change detection with significant change"""
        # Create a trajectory with points that create a sharp turn
        points = [
            Coordinate(x=0, y=0),
            Coordinate(x=1, y=0),
            Coordinate(x=1, y=1)
        ]
        
        trajectory = Trajectory(
            id="test_traj",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=False
        )
        
        # Mock consecutiveBearingDiff to return significant change
        with patch.object(anomaly_detector, 'consecutiveBearingDiff') as mock_bearing:
            mock_bearing.return_value = (50.0, [50.0])  # 50 degrees change
            
            changed, max_change = anomaly_detector.changeInDirection(trajectory, 45.0)
            
            assert changed is True
            assert max_change == 50.0

    def test_change_in_direction_no_significant_change(self, anomaly_detector):
        """Test direction change detection with no significant change"""
        points = [
            Coordinate(x=0, y=0),
            Coordinate(x=1, y=0),
            Coordinate(x=2, y=0)
        ]
        
        trajectory = Trajectory(
            id="test_traj",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=False
        )
        
        # Mock consecutiveBearingDiff to return small change
        with patch.object(anomaly_detector, 'consecutiveBearingDiff') as mock_bearing:
            mock_bearing.return_value = (10.0, [10.0])  # 10 degrees change
            
            changed, max_change = anomaly_detector.changeInDirection(trajectory, 45.0)
            
            assert changed is False
            assert max_change == 10.0

    @patch('mdx.analytics.core.utils.anomaly_util.bearing')
    def test_consecutive_bearing_diff_sufficient_bearings(self, mock_bearing, anomaly_detector):
        """Test bearing difference calculation with sufficient bearings"""
        points = [
            Coordinate(x=0, y=0),
            Coordinate(x=1, y=0),
            Coordinate(x=2, y=0),
            Coordinate(x=3, y=1)
        ]
        
        trajectory = Trajectory(
            id="test_traj",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=False
        )
        
        # Mock bearing calculations
        mock_bearing.side_effect = [0.0, 0.0, 45.0]  # Third segment has 45-degree bearing
        
        max_diff, diff_list = anomaly_detector.consecutiveBearingDiff(trajectory)
        
        # Should calculate differences between consecutive bearings
        assert len(diff_list) == 2  # 3 bearings = 2 differences
        assert max_diff == max(diff_list)

    def test_consecutive_bearing_diff_insufficient_bearings(self, anomaly_detector):
        """Test bearing difference calculation with insufficient bearings"""
        points = [
            Coordinate(x=0, y=0),
            Coordinate(x=1, y=0)
        ]
        
        trajectory = Trajectory(
            id="test_traj",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=False
        )
        
        max_diff, diff_list = anomaly_detector.consecutiveBearingDiff(trajectory)
        
        # With only 2 points (1 bearing), should return default values
        assert max_diff == 0.1
        assert diff_list == [0.1]

    def test_consecutive_bearing_diff_wrap_around(self, anomaly_detector):
        """Test bearing difference calculation with wrap-around angles"""
        points = [
            Coordinate(x=0, y=0),
            Coordinate(x=1, y=0),
            Coordinate(x=2, y=0),
            Coordinate(x=3, y=0)
        ]
        
        trajectory = Trajectory(
            id="test_traj",
            start=datetime.now(),
            end=datetime.now(),
            points=points,
            enable_geo=False
        )
        
        # Mock bearing calculations to test wrap-around (e.g., 350° to 10°)
        with patch('mdx.analytics.core.utils.anomaly_util.bearing') as mock_bearing:
            mock_bearing.side_effect = [350.0, 10.0, 10.0]  # Need 3 values for 4 points -> 3 bearings
            
            max_diff, diff_list = anomaly_detector.consecutiveBearingDiff(trajectory)
            
            # Should handle wrap-around correctly
            assert diff_list[0] == 20.0  # 350 to 10 is 20 degrees, not 340




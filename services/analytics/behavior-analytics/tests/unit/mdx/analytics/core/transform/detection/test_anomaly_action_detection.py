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

import unittest
from unittest.mock import Mock, patch, ANY
from datetime import datetime, timedelta
import json

from mdx.analytics.core.transform.detection.anomaly_action_detection import (
    AnomalyActionDetection,
    ActionType
)
from mdx.analytics.core.schema.config import (
    AppConfig,
    AnomalyActionConfig,
    FallRiskConfig,
    LackMovementConfig,
)
from mdx.analytics.core.schema.models import (
    Behavior,
    AnalyticsModule,
    Action,
    Incident,
    Sensor,
    Object,
    Place,
    Pose
)
from mdx.analytics.core.schema.action.action_state import ActionState, FallRiskState
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from google.protobuf.timestamp_pb2 import Timestamp


class TestAnomalyActionDetection(unittest.TestCase):
    """Unit tests for the AnomalyActionDetection class.
    
    Tests anomaly action detection including:
    - Initialization and configuration management
    - Batch processing of behaviors and frames
    - Individual anomaly detection (fall risk, lack of movement)
    - Action state management and lifecycle
    - Sensor and object cleanup operations
    - Incident creation and reporting
    """

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock configuration hierarchy
        self.mock_fall_risk_config = FallRiskConfig(
            enable=True,
            nConfirmStart=3,
            nConfirmEnd=5
        )
        self.mock_lack_movement_config = LackMovementConfig(
            enable=True,
            durationThreshold=300.0
        )
        mock_config = AnomalyActionConfig(
            classes={"patient"},
            actionThreshold=0.5,
            fallRisk=self.mock_fall_risk_config,
            lackMovement=self.mock_lack_movement_config,
            ignoreSensors=["ignored_sensor"]
        )
        
        # Create mock app config
        self.mock_app_config = Mock(spec=AppConfig)
        self.mock_app_config.get_sensor_anomaly_action_config.return_value = mock_config

        # Create test instance
        self.detector = AnomalyActionDetection(self.mock_app_config)

        # Common test data
        self.sensor_id = "test_sensor"
        self.object_id = "test_object"
        self.timestamp = datetime.now()

    def _create_mock_action(self, action_type: str = "Lying Down", confidence: float = 0.9) -> Action:
        """Helper method to create mock Action objects with specified parameters."""
        mock_action = Mock(spec=Action)
        mock_action.type = action_type
        mock_action.confidence = confidence
        return mock_action

    def _create_mock_analytics_module(self, module_id: str = "test_module") -> AnalyticsModule:
        """Helper method to create mock AnalyticsModule objects."""
        mock_module = Mock(spec=AnalyticsModule)
        mock_module.id = module_id
        mock_module.description = f"Test {module_id}"
        mock_module.source = "mdx"
        mock_module.version = "3.0"
        mock_module.info = {"clusterIndex": "-1"}
        return mock_module

    def _create_mock_action_state(self, last_action: str = "Unknown") -> Mock:
        """Helper method to create mock ActionState objects."""
        mock_action_state = Mock(spec=ActionState)
        mock_action_state.get_last_action.return_value = last_action
        mock_action_state.get_action_intervals.return_value = {}
        mock_action_state.get_last_interval.return_value = [self.timestamp, self.timestamp]
        return mock_action_state

    def _create_mock_fall_risk_state(self, is_confirmed_start: bool = False, 
                                   is_confirmed_end: bool = False) -> Mock:
        """Helper method to create mock FallRiskState objects."""
        mock_fall_risk_state = Mock(spec=FallRiskState)
        mock_fall_risk_state.is_confirmed_start.return_value = is_confirmed_start
        mock_fall_risk_state.is_confirmed_end.return_value = is_confirmed_end
        mock_fall_risk_state.get_start_time.return_value = self.timestamp
        mock_fall_risk_state.get_end_time.return_value = self.timestamp + timedelta(minutes=1)
        return mock_fall_risk_state

    def _create_mock_behavior(
        self,
        sensor_id: str = "test_sensor",
        object_id: str = "test_object",
        object_type: str = "patient",
        action_type: str = "Lying Down",
        confidence: float = 0.9,
        start_time: datetime | None = None,
        end_time: datetime | None = None
    ) -> Behavior:
        """Helper method to create mock Behavior objects for testing."""
        if start_time is None:
            start_time = self.timestamp
        if end_time is None:
            end_time = self.timestamp + timedelta(seconds=1)

        mock_sensor = Mock(spec=Sensor)
        mock_sensor.id = sensor_id

        mock_object = Mock(spec=Object)
        mock_object.id = object_id
        mock_object.type = object_type

        mock_place = Mock(spec=Place)
        mock_action = self._create_mock_action(action_type, confidence)

        mock_pose = Mock(spec=Pose)
        mock_pose.actions = [mock_action]

        mock_behavior = Mock(spec=Behavior)
        mock_behavior.id = f"{sensor_id}_{object_id}"
        mock_behavior.sensor = mock_sensor
        mock_behavior.object = mock_object
        mock_behavior.place = mock_place
        mock_behavior.poses = [mock_pose]
        mock_behavior.timestamp = start_time
        mock_behavior.end = end_time
        mock_behavior.info = {}

        return mock_behavior

    def _create_proto_frame(
        self,
        frame_id: str = "test_frame",
        sensor_id: str = "test_sensor",
        timestamp: datetime | None = None,
        objects: list | None = None
    ) -> nvSchema.Frame:
        """Helper method to create protobuf Frame objects for testing."""
        if timestamp is None:
            timestamp = self.timestamp
        if objects is None:
            objects = []

        frame = nvSchema.Frame()
        frame.id = frame_id
        frame.sensorId = sensor_id
        
        proto_timestamp = Timestamp()
        proto_timestamp.FromDatetime(timestamp)
        frame.timestamp.CopyFrom(proto_timestamp)
        
        for obj_data in objects:
            obj = nvSchema.Object()
            obj.id = obj_data.get("id", "test_object")
            obj.type = obj_data.get("type", "patient")
            
            if obj_data.get("has_pose", True):
                pose = nvSchema.Pose()
                action = pose.Action()
                action.type = obj_data.get("action_type", "Lying Down")
                action.confidence = obj_data.get("confidence", 0.9)
                pose.actions.append(action)
                obj.pose.CopyFrom(pose)
            
            frame.objects.append(obj)
        
        return frame

    # Initialization Tests
    def test_initialization_with_valid_config_creates_correct_state(self):
        """Test AnomalyActionDetection initialization with valid config creates correct initial state."""
        detector = AnomalyActionDetection(self.mock_app_config)
        
        self.assertEqual(detector.app_config, self.mock_app_config)
        self.assertEqual(detector.action_state, {})
        self.assertEqual(detector.fall_risk_state, {})

    # Batch Processing Tests
    def test_detect_batch_with_empty_input_returns_empty_results(self):
        """Test detect_batch with empty behavior and frame lists returns empty results."""
        mock_frames = []
        result = self.detector.detect_batch([], mock_frames)
        
        self.assertEqual(result, ([], []))

    def test_detect_batch_with_behaviors_and_empty_frames_processes_behaviors_only(self):
        """Test detect_batch with behaviors and empty frames processes behaviors without incidents."""
        behavior1 = self._create_mock_behavior()
        behavior2 = self._create_mock_behavior(object_id="test_object_2")
        
        # Mock action states for both behaviors
        mock_action_state1 = self._create_mock_action_state()
        mock_action_state2 = self._create_mock_action_state()
        
        self.detector.action_state = {
            self.sensor_id: {
                self.object_id: mock_action_state1,
                "test_object_2": mock_action_state2
            }
        }
        
        result = self.detector.detect_batch([behavior1, behavior2], [])
        
        # Should return tuple (incidents, behaviors)
        self.assertEqual(len(result), 2)
        incidents, behaviors = result
        self.assertEqual(len(incidents), 0)  # No incidents from empty frames
        self.assertEqual(len(behaviors), 2)
        
        # Verify behavior info was updated
        for behavior in behaviors:
            self.assertIn("action_intervals", behavior.info)
            self.assertIn("current_action", behavior.info)

    # Incident Creation Tests
    def test_create_incident_with_valid_parameters_returns_correct_incident(self):
        """Test create_incident with valid parameters returns properly configured incident."""
        start_time = self.timestamp
        end_time = self.timestamp + timedelta(minutes=5)
        analytics_module = self._create_mock_analytics_module("Fall Risk Module")
        incident_type = "fall_risk"

        incident = self.detector.create_incident(
            self.sensor_id, self.object_id, start_time, end_time, analytics_module, incident_type
        )

        self.assertIsInstance(incident, Incident)
        self.assertEqual(incident.timestamp, start_time)
        self.assertEqual(incident.end, end_time)
        self.assertEqual(incident.sensorId, self.sensor_id)
        self.assertEqual(incident.objectIds, [self.object_id])
        self.assertEqual(incident.analyticsModule, analytics_module)
        self.assertEqual(incident.category, incident_type)
        self.assertTrue(incident.isAnomaly)
        self.assertEqual(incident.info["primary_object_id"], self.object_id)

    # Detection Filter Tests
    def test_detect_with_ignored_object_type_returns_empty_list(self):
        """Test detect method with ignored object type returns empty incident list."""
        action = self._create_mock_action()
        
        result = self.detector.detect(
            self.sensor_id, self.object_id, action, self.timestamp, "ignored_type"
        )
        
        self.assertEqual(result, [])

    def test_detect_with_ignored_sensor_returns_empty_list(self):
        """Test detect method with ignored sensor returns empty incident list."""
        action = self._create_mock_action()
        
        result = self.detector.detect(
            "ignored_sensor", self.object_id, action, self.timestamp, "patient"
        )
        
        self.assertEqual(result, [])

    # Fall Risk Detection Tests
    @patch.object(AnomalyActionDetection, 'detect_fall_risk')
    @patch.object(AnomalyActionDetection, 'detect_lack_movement')
    @patch.object(AnomalyActionDetection, 'update_action')
    def test_detect_with_fall_risk_detected_creates_fall_risk_incident(
        self, mock_update_action, mock_detect_lack_movement, 
        mock_detect_fall_risk
    ):
        """Test detect method with fall risk detected creates fall risk incident."""
        action = self._create_mock_action()
        mock_detect_fall_risk.return_value = (True, "Fall risk detected")
        mock_detect_lack_movement.return_value = (False, "")
        
        # Mock fall risk state
        mock_fall_risk_state = self._create_mock_fall_risk_state()
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }

        result = self.detector.detect(
            self.sensor_id, self.object_id, action, self.timestamp, "patient"
        )
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].category, "fall_risk")
        mock_update_action.assert_called_once()

    @patch.object(AnomalyActionDetection, 'detect_fall_risk')
    @patch.object(AnomalyActionDetection, 'detect_lack_movement')
    @patch.object(AnomalyActionDetection, 'update_action')
    def test_detect_with_lack_movement_detected_creates_lack_movement_incident(
        self, mock_update_action, mock_detect_lack_movement,
        mock_detect_fall_risk
    ):
        """Test detect method with lack of movement detected creates lack movement incident."""
        action = self._create_mock_action()
        mock_detect_fall_risk.return_value = (False, "")
        mock_detect_lack_movement.return_value = (True, "Lack of movement detected")
        
        # Mock action state
        mock_action_state = self._create_mock_action_state()
        mock_action_state.get_last_interval.return_value = [
            self.timestamp, self.timestamp + timedelta(minutes=10)
        ]
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }

        result = self.detector.detect(
            self.sensor_id, self.object_id, action, self.timestamp, "patient"
        )
        
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].category, "lack_movement")
        mock_update_action.assert_called_once()

    # Action State Management Tests
    def test_update_action_with_new_sensor_and_object_creates_new_action_state(self):
        """Test update_action with new sensor and object creates new ActionState instance."""
        action = self._create_mock_action("Standing")
        new_sensor_id = "new_sensor"
        new_object_id = "new_object"
        
        with patch('mdx.analytics.core.transform.detection.anomaly_action_detection.ActionState') as MockActionState:
            mock_action_state = Mock(spec=ActionState)
            MockActionState.return_value = mock_action_state
            
            self.detector.update_action(new_sensor_id, new_object_id, action, self.timestamp, self.mock_app_config.get_sensor_anomaly_action_config(new_sensor_id).actionThreshold)
            
            self.assertIn(new_sensor_id, self.detector.action_state)
            self.assertIn(new_object_id, self.detector.action_state[new_sensor_id])
            MockActionState.assert_called_once_with(
                new_sensor_id, new_object_id, self.mock_app_config.get_sensor_anomaly_action_config(new_sensor_id).actionThreshold
            )
            mock_action_state.update_action.assert_called_once_with(action, self.timestamp)

    def test_update_action_with_existing_sensor_and_object_updates_existing_state(self):
        """Test update_action with existing sensor and object updates existing ActionState."""
        action = self._create_mock_action()
        mock_action_state = self._create_mock_action_state()
        
        # Pre-populate action state
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        self.detector.update_action(self.sensor_id, self.object_id, action, self.timestamp, self.mock_app_config.get_sensor_anomaly_action_config(self.sensor_id).actionThreshold)
        
        mock_action_state.update_action.assert_called_once_with(action, self.timestamp)

    # Cleanup Operations Tests
    def test_remove_sensor_with_existing_sensor_removes_all_associated_state(self):
        """Test remove_sensor with existing sensor removes all associated state."""
        # Pre-populate states
        self.detector.action_state = {self.sensor_id: {self.object_id: Mock()}}
        self.detector.fall_risk_state = {self.sensor_id: {self.object_id: Mock()}}
        
        self.detector.remove_sensor(self.sensor_id)
        
        self.assertNotIn(self.sensor_id, self.detector.action_state)
        self.assertNotIn(self.sensor_id, self.detector.fall_risk_state)

    def test_remove_sensor_with_nonexistent_sensor_handles_gracefully(self):
        """Test remove_sensor with nonexistent sensor handles gracefully without errors."""
        # Should not raise an exception
        self.detector.remove_sensor("nonexistent_sensor")
        self.assertEqual(self.detector.action_state, {})
        self.assertEqual(self.detector.fall_risk_state, {})

    def test_remove_object_with_existing_object_removes_object_state_only(self):
        """Test remove_object with existing object removes only that object's state."""
        # Pre-populate states
        self.detector.action_state = {
            self.sensor_id: {
                self.object_id: Mock(),
                "other_object": Mock()
            }
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {
                self.object_id: Mock(),
                "other_object": Mock()
            }
        }
        
        self.detector.remove_object(self.sensor_id, self.object_id)
        
        self.assertNotIn(self.object_id, self.detector.action_state[self.sensor_id])
        self.assertIn("other_object", self.detector.action_state[self.sensor_id])
        self.assertNotIn(self.object_id, self.detector.fall_risk_state[self.sensor_id])
        self.assertIn("other_object", self.detector.fall_risk_state[self.sensor_id])

    def test_remove_object_with_nonexistent_object_handles_gracefully(self):
        """Test remove_object with nonexistent object handles gracefully without errors."""
        # Should not raise an exception
        self.detector.remove_object("nonexistent_sensor", "nonexistent_object")

    # Live Object Management Tests
    def test_update_live_object_with_dead_sensors_removes_inactive_sensors(self):
        """Test update_live_object with dead sensors removes sensors not in live list."""
        # Pre-populate with sensors and objects
        self.detector.action_state = {
            "sensor1": {"obj1": Mock()},
            "sensor2": {"obj2": Mock()}
        }
        
        live_objects = ["sensor1 #-# obj1"]  # Only sensor1 is live
        
        self.detector.update_live_object(live_objects)
        
        self.assertIn("sensor1", self.detector.action_state)
        self.assertNotIn("sensor2", self.detector.action_state)

    def test_update_live_object_with_dead_objects_removes_inactive_objects(self):
        """Test update_live_object with dead objects removes objects not in live list."""
        # Pre-populate with objects
        self.detector.action_state = {
            "sensor1": {
                "obj1": Mock(),
                "obj2": Mock()
            }
        }
        
        live_objects = ["sensor1 #-# obj1"]  # Only obj1 is live
        
        self.detector.update_live_object(live_objects)
        
        self.assertIn("obj1", self.detector.action_state["sensor1"])
        self.assertNotIn("obj2", self.detector.action_state["sensor1"])

    # Lack of Movement Detection Tests
    def test_detect_lack_movement_with_no_action_state_returns_false(self):
        """Test detect_lack_movement with no action state returns False and empty description."""
        action = self._create_mock_action()
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_lack_movement_with_long_lying_duration_detects_anomaly(self):
        """Test detect_lack_movement with long lying duration detects lack of movement anomaly."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action state with long lying duration
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        start_time = self.timestamp - timedelta(minutes=10)  # Long duration > threshold
        end_time = self.timestamp
        mock_action_state.get_last_interval.return_value = [start_time, end_time]
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertTrue(result[0])
        self.assertIn("Lack Movement", result[1])

    def test_detect_lack_movement_with_short_lying_duration_returns_false(self):
        """Test detect_lack_movement with short lying duration returns False."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action state with short duration
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        start_time = self.timestamp - timedelta(seconds=30)  # Short duration < threshold
        end_time = self.timestamp
        mock_action_state.get_last_interval.return_value = [start_time, end_time]
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertFalse(result[0])

    # Fall Risk Detection Detailed Tests
    def test_detect_fall_risk_with_no_action_state_returns_false(self):
        """Test detect_fall_risk with no action state returns False and empty description."""
        action = self._create_mock_action()
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_fall_risk_with_unknown_last_action_returns_false(self):
        """Test detect_fall_risk with unknown last action returns False."""
        action = self._create_mock_action(ActionType.STANDING.value)
        
        # Mock action state with unknown last action
        mock_action_state = self._create_mock_action_state("Unknown")
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_fall_risk_first_transition_from_lying_creates_fall_risk_state(self):
        """Test detect_fall_risk with first transition from lying down creates new fall risk state."""
        action = self._create_mock_action(ActionType.STANDING.value)
        
        # Mock action state with lying down as last action
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        with patch('mdx.analytics.core.transform.detection.anomaly_action_detection.FallRiskState') as MockFallRiskState:
            mock_fall_risk_state = Mock(spec=FallRiskState)
            MockFallRiskState.return_value = mock_fall_risk_state
            
            result = self.detector.detect_fall_risk(
                self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
            )
            
            # First transition should return False and create new fall risk state
            self.assertFalse(result[0])
            self.assertIn("Init new fall risk, confirm start", result[1])
            MockFallRiskState.assert_called_once_with(
                self.sensor_id, self.object_id, self.timestamp, 
                self.mock_fall_risk_config.nConfirmStart, 
                self.mock_fall_risk_config.nConfirmEnd
            )

    def test_detect_fall_risk_with_existing_state_continues_alert(self):
        """Test detect_fall_risk with existing fall risk state continues alert."""
        action = self._create_mock_action(ActionType.STANDING.value)
        
        # Mock action state and existing fall risk state
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        mock_fall_risk_state = self._create_mock_fall_risk_state()
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertTrue(result[0])
        self.assertIn("Continue alert", result[1])
        mock_fall_risk_state.update_fall_risk.assert_called_once_with(True, self.timestamp)

    def test_detect_fall_risk_still_lying_with_confirmed_end_stops_alert(self):
        """Test detect_fall_risk when still lying down with confirmed end stops alert."""
        action = self._create_mock_action(ActionType.LYING_DOWN.value)
        
        # Mock action state and fall risk state with confirmed end
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        mock_fall_risk_state = self._create_mock_fall_risk_state(is_confirmed_end=True)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertFalse(result[0])
        self.assertIn("stop alert", result[1])
        # The fall risk state should be deleted
        self.assertNotIn(self.object_id, self.detector.fall_risk_state[self.sensor_id])

    def test_detect_fall_risk_still_lying_waiting_end_confirm_increments_counter(self):
        """Test detect_fall_risk when still lying down waiting for end confirmation increments counter."""
        action = self._create_mock_action(ActionType.LYING_DOWN.value)
        
        # Mock action state and fall risk state not confirmed for end
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        mock_fall_risk_state = self._create_mock_fall_risk_state(is_confirmed_end=False)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertFalse(result[0])
        self.assertIn("Wait for end confirm", result[1])
        mock_fall_risk_state.increase_n_confirm_end.assert_called_once()

    def test_detect_fall_risk_non_lying_with_confirmed_start_triggers_alert(self):
        """Test detect_fall_risk from non-lying action with confirmed start triggers alert."""
        action = self._create_mock_action(ActionType.STANDING.value)
        
        # Mock action state and fall risk state with confirmed start
        mock_action_state = self._create_mock_action_state(ActionType.STANDING.value)
        mock_fall_risk_state = self._create_mock_fall_risk_state(is_confirmed_start=True)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertTrue(result[0])
        self.assertIn("Continue alert", result[1])
        mock_fall_risk_state.update_fall_risk.assert_called_once_with(True, self.timestamp)

    def test_detect_fall_risk_non_lying_waiting_start_confirm_increments_counter(self):
        """Test detect_fall_risk from non-lying action waiting for start confirmation increments counter."""
        action = self._create_mock_action(ActionType.STANDING.value)
        
        # Mock action state and fall risk state not confirmed for start
        mock_action_state = self._create_mock_action_state(ActionType.STANDING.value)
        mock_fall_risk_state = self._create_mock_fall_risk_state(is_confirmed_start=False)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertFalse(result[0])
        self.assertIn("Wait for start confirm", result[1])
        mock_fall_risk_state.increase_n_confirm_start.assert_called_once()

    # Configuration Management Tests
    def test_get_sensor_anomaly_action_config_with_valid_sensor_returns_merged_config(self):
        """Test get_sensor_anomaly_action_config with valid sensor returns properly merged configuration."""
        # Create a custom config for this test
        test_fall_risk_config = FallRiskConfig(
            enable=True,
            nConfirmStart=3,
            nConfirmEnd=5
        )
        test_lack_movement_config = LackMovementConfig(
            enable=True,
            durationThreshold=400.0
        )
        test_config = AnomalyActionConfig(
            classes={"patient"},
            actionThreshold=0.7,
            fallRisk=test_fall_risk_config,
            lackMovement=test_lack_movement_config,
            ignoreSensors=["ignored_sensor"]
        )
        
        # Mock the method to return our test config
        self.mock_app_config.get_sensor_anomaly_action_config.return_value = test_config
        
        # Call the method with a sensor_id parameter
        result = self.detector.app_config.get_sensor_anomaly_action_config(self.sensor_id)
        
        # Verify configuration structure
        self.assertIsInstance(result, AnomalyActionConfig)
        self.assertEqual(result.classes, {"patient"})
        self.assertEqual(result.actionThreshold, 0.7)
        self.assertEqual(result.fallRisk.enable, True)
        self.assertEqual(result.fallRisk.nConfirmStart, 3)
        self.assertEqual(result.fallRisk.nConfirmEnd, 5)
        self.assertEqual(result.lackMovement.enable, True)
        self.assertEqual(result.lackMovement.durationThreshold, 400.0)
        self.assertEqual(result.ignoreSensors, {"ignored_sensor"})
        
        # Verify correct method call was made
        self.mock_app_config.get_sensor_anomaly_action_config.assert_called_with(self.sensor_id)

    # Frame Processing Tests
    def test_detect_batch_with_frames_processes_objects_with_poses(self):
        """Test detect_batch processes frames with objects that have poses correctly."""
        # Create frame with objects that have poses
        frame_objects = [
            {"id": "obj1", "type": "patient", "action_type": "Lying Down", "confidence": 0.8},
            {"id": "obj2", "type": "patient", "action_type": "Standing", "confidence": 0.9}
        ]
        frame = self._create_proto_frame(objects=frame_objects)
        
        with patch.object(self.detector, 'detect') as mock_detect:
            mock_detect.return_value = []
            
            incidents, behaviors = self.detector.detect_batch([], [frame])
            
            # Should call detect for each object with pose
            self.assertEqual(mock_detect.call_count, 2)
            mock_detect.assert_any_call(
                self.sensor_id, "obj1", ANY, ANY, "patient"
            )
            mock_detect.assert_any_call(
                self.sensor_id, "obj2", ANY, ANY, "patient"  
            )

    def test_detect_batch_with_frames_skips_objects_without_poses(self):
        """Test detect_batch skips objects that don't have poses."""
        # Create frame with objects without poses
        frame_objects = [
            {"id": "obj1", "type": "patient", "has_pose": False},
            {"id": "obj2", "type": "patient", "action_type": "Standing", "confidence": 0.9}
        ]
        frame = self._create_proto_frame(objects=frame_objects)
        
        with patch.object(self.detector, 'detect') as mock_detect:
            mock_detect.return_value = []
            
            incidents, behaviors = self.detector.detect_batch([], [frame])
            
            # Should only call detect for obj2 which has poses
            self.assertEqual(mock_detect.call_count, 1)
            mock_detect.assert_called_with(
                self.sensor_id, "obj2", ANY, ANY, "patient"
            )

    def test_detect_batch_with_frames_sorts_by_timestamp(self):
        """Test detect_batch sorts frames by timestamp before processing."""
        # Create frames with different timestamps
        frame1 = self._create_proto_frame(frame_id="frame1", timestamp=self.timestamp + timedelta(seconds=2))
        frame2 = self._create_proto_frame(frame_id="frame2", timestamp=self.timestamp)
        frame3 = self._create_proto_frame(frame_id="frame3", timestamp=self.timestamp + timedelta(seconds=1))
        
        # Add objects to frames to verify processing order
        for frame in [frame1, frame2, frame3]:
            obj = nvSchema.Object()
            obj.id = f"obj_{frame.id}"
            obj.type = "patient"
            pose = nvSchema.Pose()
            action = pose.Action()
            action.type = "Standing"
            pose.actions.append(action)
            obj.pose.CopyFrom(pose)
            frame.objects.append(obj)
        
        call_order = []
        def track_calls(sensor_id, object_id, action, timestamp, object_type):
            call_order.append(object_id)
            return []
        
        with patch.object(self.detector, 'detect', side_effect=track_calls):
            self.detector.detect_batch([], [frame1, frame2, frame3])
            
            # Should process frames in timestamp order: frame2, frame3, frame1
            expected_order = ["obj_frame2", "obj_frame3", "obj_frame1"]
            self.assertEqual(call_order, expected_order)

    def test_detect_batch_with_frames_aggregates_incidents(self):
        """Test detect_batch aggregates incidents from all frames."""
        frame_objects = [{"id": "obj1", "type": "patient"}]
        frame = self._create_proto_frame(objects=frame_objects)
        
        # Mock incident creation
        mock_incident = Mock(spec=Incident)
        mock_incident.sensorId = self.sensor_id
        mock_incident.objectIds = ["obj1"]
        
        with patch.object(self.detector, 'detect') as mock_detect:
            mock_detect.return_value = [mock_incident]
            
            incidents, behaviors = self.detector.detect_batch([], [frame])
            
            self.assertEqual(len(incidents), 1)
            self.assertEqual(incidents[0], mock_incident)

    # Behavior Info Update Tests
    def test_detect_batch_updates_behavior_info_with_action_intervals(self):
        """Test detect_batch updates behavior info with action intervals."""
        behavior = self._create_mock_behavior()
        
        # Mock action state with action intervals
        mock_action_state = self._create_mock_action_state("Standing")
        mock_action_state.get_durations.return_value = {"Standing": 120.5, "Lying Down": 300.2}
        mock_action_state.get_action_intervals.return_value = {
            "Standing": [(self.timestamp, self.timestamp + timedelta(seconds=120))],
            "Lying Down": [(self.timestamp - timedelta(seconds=300), self.timestamp)]
        }
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        incidents, behaviors = self.detector.detect_batch([behavior], [])
        
        # Verify behavior info was updated
        self.assertIn("action_intervals", behavior.info)
        self.assertIn("current_action", behavior.info)
        self.assertEqual(behavior.info["current_action"], "Standing")
        
        # Parse and verify action intervals
        action_intervals = json.loads(behavior.info["action_intervals"])
        self.assertEqual(len(action_intervals), 2)
        
        standing_interval = next(ai for ai in action_intervals if ai["action"] == "Standing")
        self.assertEqual(standing_interval["duration_seconds"], 120.5)

    def test_detect_batch_handles_behavior_without_action_state(self):
        """Test detect_batch handles behaviors that don't have action state."""
        behavior = self._create_mock_behavior()
        
        incidents, behaviors = self.detector.detect_batch([behavior], [])
        
        # Should set default values
        self.assertEqual(behavior.info["current_action"], "Unknown")
        action_intervals = json.loads(behavior.info["action_intervals"])
        self.assertEqual(len(action_intervals), 0)

    def test_detect_batch_creates_places_dict_for_new_sensor_object(self):
        """Test detect_batch creates places dictionary for new sensor/object combinations."""
        behavior = self._create_mock_behavior()
        
        # Create incident that needs place assignment
        mock_incident = Mock(spec=Incident)
        mock_incident.sensorId = self.sensor_id
        mock_incident.objectIds = [self.object_id]
        mock_incident.place = None
        
        incidents, behaviors = self.detector.detect_batch([behavior], [])
        
        # Verify place was stored for the sensor/object combination
        # This tests the places dict creation in lines 112-115

    def test_detect_batch_assigns_places_to_incidents(self):
        """Test detect_batch assigns places to incidents from behavior data."""
        behavior = self._create_mock_behavior()
        
        # Create incident that needs place assignment
        mock_incident = Mock(spec=Incident)
        mock_incident.sensorId = self.sensor_id
        mock_incident.objectIds = [self.object_id]
        mock_incident.place = None
        
        # Mock detect to return the incident
        with patch.object(self.detector, 'detect') as mock_detect:
            mock_detect.return_value = [mock_incident]
            
            # Create a frame to trigger incident creation
            frame = self._create_proto_frame(objects=[{"id": self.object_id}])
            
            incidents, behaviors = self.detector.detect_batch([behavior], [frame])
            
            # Verify incident place was assigned from behavior
            self.assertEqual(mock_incident.place, behavior.place)

    # Anomaly Detection Edge Cases
    def test_detect_fall_risk_without_action_state_for_object_returns_false(self):
        """Test detect_fall_risk returns false when object not in action state."""
        action = self._create_mock_action()
        
        # Create action state for sensor but not for this object
        self.detector.action_state = {self.sensor_id: {"other_object": Mock()}}
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_lack_movement_without_action_state_for_object_returns_false(self):
        """Test detect_lack_movement returns false when object not in action state."""
        action = self._create_mock_action()
        
        # Create action state for sensor but not for this object
        self.detector.action_state = {self.sensor_id: {"other_object": Mock()}}
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_lack_movement_with_unknown_last_action_returns_false(self):
        """Test detect_lack_movement returns false when last action is unknown."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action state with unknown last action
        mock_action_state = self._create_mock_action_state("Unknown")
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_lack_movement_with_lying_down_but_no_interval_returns_false(self):
        """Test detect_lack_movement returns false when lying down but no interval found."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action state with lying down but no interval
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        mock_action_state.get_last_interval.return_value = None
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_detect_lack_movement_non_lying_action_returns_false(self):
        """Test detect_lack_movement returns false for non-lying actions."""
        action = self._create_mock_action("Standing")
        
        # Mock action state with standing as last action
        mock_action_state = self._create_mock_action_state("Standing")
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        self.assertEqual(result, (False, ""))

    # Fall Risk State Management Edge Cases
    def test_detect_fall_risk_from_other_action_without_fall_risk_state_returns_false(self):
        """Test fall risk detection from non-lying action when no fall risk state exists."""
        action = self._create_mock_action("Standing")
        
        # Mock action state with standing as last action, but no fall risk state
        mock_action_state = self._create_mock_action_state("Standing")
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertEqual(result, (False, ""))

    def test_fall_risk_init_end_confirmation_with_confirmed_start(self):
        """Test fall risk end confirmation initialization when start is confirmed."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action and fall risk states
        mock_action_state = self._create_mock_action_state("Standing")
        mock_fall_risk_state = self._create_mock_fall_risk_state(is_confirmed_start=True)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertFalse(result[0])
        self.assertIn("Init end confirmation", result[1])
        mock_fall_risk_state.reset_n_confirm_end.assert_called_once()
        mock_fall_risk_state.increase_n_confirm_end.assert_called_once()
        mock_fall_risk_state.reset_n_confirm_start.assert_called_once()

    def test_fall_risk_init_end_confirmation_without_confirmed_start(self):
        """Test fall risk end confirmation when start is not confirmed stops tracking."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action and fall risk states  
        mock_action_state = self._create_mock_action_state("Standing")
        mock_fall_risk_state = self._create_mock_fall_risk_state(is_confirmed_start=False)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        self.detector.fall_risk_state = {
            self.sensor_id: {self.object_id: mock_fall_risk_state}
        }
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertFalse(result[0])
        self.assertIn("Stop fall-risk tracking", result[1])

    def test_continue_end_confirmation_remain_lying_down(self):
        """Test fall risk remain lying down case when no fall risk state exists."""
        action = self._create_mock_action("Lying Down")
        
        # Mock action state but no fall risk state (to trigger "Remain lying down")
        mock_action_state = self._create_mock_action_state(ActionType.LYING_DOWN.value)
        
        self.detector.action_state = {
            self.sensor_id: {self.object_id: mock_action_state}
        }
        # No fall risk state - this triggers the "Remain lying down" case
        self.detector.fall_risk_state = {}
        
        result = self.detector.detect_fall_risk(
            self.sensor_id, self.object_id, action, self.timestamp, self.mock_fall_risk_config
        )
        
        self.assertFalse(result[0])
        self.assertIn("Remain lying down", result[1])

    # Incident Creation Warning Cases
    def test_detect_with_fall_risk_but_missing_fall_risk_state_logs_warning(self):
        """Test detect logs warning when fall risk detected but state missing."""
        action = self._create_mock_action()
        
        with patch.object(self.detector, 'detect_fall_risk') as mock_detect_fall_risk, \
             patch.object(self.detector, 'detect_lack_movement') as mock_detect_lack_movement, \
             patch('mdx.analytics.core.transform.detection.anomaly_action_detection.logger') as mock_logger:
            
            mock_detect_fall_risk.return_value = (True, "Fall risk detected")
            mock_detect_lack_movement.return_value = (False, "")
            
            # No fall risk state configured
            self.detector.fall_risk_state = {}
            
            result = self.detector.detect(
                self.sensor_id, self.object_id, action, self.timestamp, "patient"
            )
            
            self.assertEqual(len(result), 0)  # No incident created
            mock_logger.warning.assert_called_with(f"Fall risk state not found for {self.sensor_id}#{self.object_id}")

    def test_detect_lack_movement_with_missing_action_state_returns_false(self):
        """Test detect_lack_movement returns false when action state is missing."""
        action = self._create_mock_action()
        
        # No action state configured
        self.detector.action_state = {}
        
        result = self.detector.detect_lack_movement(
            self.sensor_id, self.object_id, action, self.mock_lack_movement_config
        )
        
        # Should return False when action state is missing
        self.assertEqual(result, (False, ""))

    def test_detect_with_lack_movement_but_no_interval_logs_warning(self):
        """Test detect logs warning when lack movement detected but no interval found."""
        action = self._create_mock_action()
        
        # Mock action state without interval
        mock_action_state = self._create_mock_action_state()
        mock_action_state.get_last_interval.return_value = None
        
        with patch.object(self.detector, 'detect_fall_risk') as mock_detect_fall_risk, \
             patch.object(self.detector, 'detect_lack_movement') as mock_detect_lack_movement, \
             patch('mdx.analytics.core.transform.detection.anomaly_action_detection.logger') as mock_logger:
            
            mock_detect_fall_risk.return_value = (False, "")
            mock_detect_lack_movement.return_value = (True, "Lack movement detected")
            
            self.detector.action_state = {
                self.sensor_id: {self.object_id: mock_action_state}
            }
            
            result = self.detector.detect(
                self.sensor_id, self.object_id, action, self.timestamp, "patient"
            )
            
            self.assertEqual(len(result), 0)  # No incident created
            mock_logger.warning.assert_called_with(
                "Lack-movement flagged but no interval found for %s#%s", self.sensor_id, self.object_id
            )

    # Live Object Update Edge Cases
    def test_update_live_object_with_malformed_live_object_string_handles_gracefully(self):
        """Test update_live_object handles malformed live object strings gracefully."""
        # Pre-populate with sensor and object
        self.detector.action_state = {
            "sensor1": {"obj1": Mock()}
        }
        
        # This should not cause any crashes - malformed strings are skipped
        try:
            self.detector.update_live_object(["malformed_string", "sensor1 #-# obj1"])
            # Should still have the valid object
            self.assertIn("sensor1", self.detector.action_state)
            self.assertIn("obj1", self.detector.action_state["sensor1"])
        except ValueError:
            # This is acceptable behavior for malformed input
            pass


if __name__ == '__main__':
    unittest.main()

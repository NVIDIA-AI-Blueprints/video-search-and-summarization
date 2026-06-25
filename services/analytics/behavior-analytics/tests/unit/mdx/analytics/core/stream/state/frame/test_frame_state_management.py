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

import unittest
import json
from datetime import datetime, timezone, timedelta
from google.protobuf.timestamp_pb2 import Timestamp
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.models import IncidentCategory
from mdx.analytics.core.utils.io_utils import load_json_from_file
from mdx.analytics.core.stream.state.frame.frame_state_management import FrameStateMgmt
from mdx.analytics.core.utils.schema_util import get_timestamp_from_proto_ts


class TestFrameStateMgmt(unittest.TestCase):

    def setUp(self):
        """Runs before each test method."""
        config = AppConfig(**load_json_from_file("tests/unit/resources/test_config.json"))
        config.set_sensor_config("sensorMinFrames", "10", "default")
        # Set behavior time threshold and watermark through app config
        config.set_app_config("behaviorTimeThreshold", "2020-01-01T00:00:00.000Z")
        config.set_app_config("behaviorWatermarkSec", "3600")  # 1 hour watermark
        
        # Configure safety violation incidents
        config.set_app_config("proximityViolationIncidentEnable", "true")
        config.set_app_config("proximityViolationIncidentExpirationWindow", "2.0")  # 2 seconds
        config.set_app_config("proximityViolationIncidentThreshold", "3.0")  # 3 seconds to become incident
        
        # Configure restricted area violation incidents
        config.set_app_config("restrictedAreaViolationIncidentEnable", "true")
        config.set_app_config("restrictedAreaViolationIncidentExpirationWindow", "1.0")
        config.set_app_config("restrictedAreaViolationIncidentThreshold", "2.0")
        
        # Configure confined area violation incidents
        config.set_app_config("confinedAreaViolationIncidentEnable", "true")
        config.set_app_config("confinedAreaViolationIncidentExpirationWindow", "1.5")
        config.set_app_config("confinedAreaViolationIncidentThreshold", "2.5")
        
        # Configure FOV count violation incidents
        config.set_app_config("fovCountViolationIncidentEnable", "true")
        config.set_app_config("fovCountViolationIncidentObjectThreshold", "3")  # Need 3+ objects
        config.set_app_config("fovCountViolationIncidentThreshold", "2.0")  # 2 seconds to become incident
        config.set_app_config("fovCountViolationIncidentExpirationWindow", "1.0")  # 1 second expiration
        config.set_app_config("fovCountViolationIncidentObjectType", "Person")  # Monitor Person type
        
        self.frame_mgmt = FrameStateMgmt(config)
        self.base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)

    def create_timestamp(self, seconds_offset: float) -> Timestamp:
        """Create protobuf timestamp with offset from base time."""
        ts = Timestamp()
        total_seconds = self.base_time.timestamp() + seconds_offset
        ts.seconds = int(total_seconds)
        ts.nanos = int((total_seconds - ts.seconds) * 1e9)
        return ts

    def create_frames(self, timestamps: list[int]) -> list[nvSchema.Frame]:
        """Create test frames with protobuf timestamps.
        
        Note: timestamps are relative seconds added to base_time (2025-01-01)
        to ensure they're after the behavior_time_threshold.
        """
        frames = []
        for ts in timestamps:
            frame = nvSchema.Frame()
            frame.version = "3.0"
            frame.sensorId = "sensor_1"
            frame.id = f"frame_{ts}"
            frame.timestamp.CopyFrom(self.create_timestamp(ts))
            frames.append(frame)
        return frames
    
    def create_frame_with_violations(
        self, 
        sensor_id: str, 
        frame_id: str,
        seconds_offset: float,
        proximity_violations: str = "",
        restricted_violations: list[tuple] | None = None,
        confined_violations: str = "",
        fov_metrics: list[tuple] | None = None
    ) -> nvSchema.Frame:
        """Create a frame with violation data."""
        frame = nvSchema.Frame()
        frame.version = "3.0"
        frame.sensorId = sensor_id
        frame.id = frame_id
        frame.timestamp.CopyFrom(self.create_timestamp(seconds_offset))
        
        # Add proximity violations
        if proximity_violations:
            frame.socialDistancing.info["proximityViolationObjects"] = proximity_violations
        
        # Add restricted area violations
        if restricted_violations:
            for roi_id, object_ids in restricted_violations:
                roi = nvSchema.TypeMetrics()
                roi.id = roi_id
                roi.info["restrictedAreaViolation"] = "true"
                roi.objectIds.extend(object_ids)
                frame.rois.append(roi)
        
        # Add confined area violations
        if confined_violations:
            frame.info["confinedAreaViolationObjects"] = confined_violations
        
        # Add FOV metrics
        if fov_metrics:
            for obj_type, count, object_ids in fov_metrics:
                fov = nvSchema.TypeMetrics()
                fov.type = obj_type
                fov.count = count
                fov.objectIds.extend(object_ids)
                frame.fov.append(fov)
        
        return frame

    def test_empty_inputs(self):
        """Test empty sensor_id or frames."""
        # update_frames returns None, but updates internal state
        self.frame_mgmt.update_frames("", [])
        self.assertEqual(len(self.frame_mgmt.state), 0)

        self.frame_mgmt.update_frames("sensor_1", [])
        self.assertEqual(len(self.frame_mgmt.state), 0)

    def test_single_batch_processing(self):
        """Test processing of a single batch of frames with more frames than sensor_min_frames."""
        frames = self.create_frames([1, 2, 5, 4, 3, 6, 7, 8, 9, 10, 11, 12])
        self.frame_mgmt.update_frames("sensor_1", frames)
        
        # Check internal state
        self.assertIn("sensor_1", self.frame_mgmt.state)
        state = self.frame_mgmt.state["sensor_1"]
        # Should keep last 10 frames (sensor_min_frames)
        self.assertEqual(len(state.last_x_frames), 10)
        # Frames should be sorted by timestamp, keeping the last 10
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in state.last_x_frames]
        self.assertEqual(actual_timestamps, expected_timestamps)

    def test_state_update_with_overlapping_frames(self):
        """Test updating state when frames overlap with the lastXframes."""
        frames_batch1 = self.create_frames([1, 2, 5, 4, 3])
        frames_batch2 = self.create_frames([6, 7, 9, 8, 10, 11, 12])
        frames_batch3 = self.create_frames([14, 13, 15])

        # Process first batch
        self.frame_mgmt.update_frames("sensor_1", frames_batch1)
        self.assertIn("sensor_1", self.frame_mgmt.state)
        state = self.frame_mgmt.state["sensor_1"]
        self.assertEqual(len(state.last_x_frames), 5)
        # Should be sorted: [1, 2, 3, 4, 5]
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [1, 2, 3, 4, 5]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in state.last_x_frames]
        self.assertEqual(actual_timestamps, expected_timestamps)

        # Process second batch
        self.frame_mgmt.update_frames("sensor_1", frames_batch2)
        state = self.frame_mgmt.state["sensor_1"]
        self.assertEqual(len(state.last_x_frames), 10)  # Keep last 10 frames
        # Should keep frames 3-12
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in state.last_x_frames]
        self.assertEqual(actual_timestamps, expected_timestamps)

        # Process third batch
        self.frame_mgmt.update_frames("sensor_1", frames_batch3)
        state = self.frame_mgmt.state["sensor_1"]
        self.assertEqual(len(state.last_x_frames), 10)  # Keep last 10 frames
        # Should keep frames 6-15
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in state.last_x_frames]
        self.assertEqual(actual_timestamps, expected_timestamps)

    def test_get_state_specific_sensor(self):
        """Test getting state for a specific sensor."""
        # Test getting state for non-existent sensor
        self.assertIsNone(self.frame_mgmt.get_state("non_existent_sensor"))

        # Add some frames and get state
        frames = self.create_frames([1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
        frames2 = self.create_frames([11, 12])
        self.frame_mgmt.update_frames("sensor_1", frames)
        self.frame_mgmt.update_frames("sensor_1", frames2)
        state = self.frame_mgmt.get_state("sensor_1")
        self.assertIsNotNone(state)
        # get_state with sensor_id returns FrameState
        self.assertEqual(state.id, "sensor_1")  # type: ignore
        self.assertEqual(len(state.last_x_frames), 10)  # type: ignore
        # Should keep frames 3-12
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [3, 4, 5, 6, 7, 8, 9, 10, 11, 12]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in state.last_x_frames]  # type: ignore
        self.assertEqual(actual_timestamps, expected_timestamps)

    def test_get_state_all_sensors(self):
        """Test getting state for all sensors."""
        # Initially should be empty
        self.assertEqual(self.frame_mgmt.get_state(), {})

        # Add frames for sensor_1
        frames1 = self.create_frames([1, 2, 3])
        self.frame_mgmt.update_frames("sensor_1", frames1)
        
        # Need to create frames with sensor_2 ID
        frames2 = []
        for ts in [4, 5, 6, 7, 8]:
            frame = nvSchema.Frame()
            frame.version = "3.0"
            frame.sensorId = "sensor_2"
            frame.id = f"frame_{ts}"
            frame.timestamp.CopyFrom(self.create_timestamp(ts))
            frames2.append(frame)
        
        frames3 = []
        for ts in [9, 10, 11, 12]:
            frame = nvSchema.Frame()
            frame.version = "3.0"
            frame.sensorId = "sensor_2"
            frame.id = f"frame_{ts}"
            frame.timestamp.CopyFrom(self.create_timestamp(ts))
            frames3.append(frame)
            
        frames4 = []
        for ts in [13, 14, 15]:
            frame = nvSchema.Frame()
            frame.version = "3.0"
            frame.sensorId = "sensor_2"
            frame.id = f"frame_{ts}"
            frame.timestamp.CopyFrom(self.create_timestamp(ts))
            frames4.append(frame)
        
        self.frame_mgmt.update_frames("sensor_2", frames2)
        self.frame_mgmt.update_frames("sensor_2", frames3)
        self.frame_mgmt.update_frames("sensor_2", frames4)
        
        # Get all states
        all_states = self.frame_mgmt.get_state()

        # Verify states
        self.assertEqual(len(all_states), 2)  # type: ignore
        self.assertIn("sensor_1", all_states)  # type: ignore
        self.assertIn("sensor_2", all_states)  # type: ignore

        # Verify sensor_1 state
        self.assertEqual(all_states["sensor_1"].id, "sensor_1")  # type: ignore
        self.assertEqual(len(all_states["sensor_1"].last_x_frames), 3)  # type: ignore
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [1, 2, 3]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in all_states["sensor_1"].last_x_frames]  # type: ignore
        self.assertEqual(actual_timestamps, expected_timestamps)

        # Verify sensor_2 state
        self.assertEqual(all_states["sensor_2"].id, "sensor_2")  # type: ignore
        self.assertEqual(len(all_states["sensor_2"].last_x_frames), 10)  # type: ignore
        # Should keep frames 6-15
        expected_timestamps = [self.base_time.timestamp() + ts for ts in [6, 7, 8, 9, 10, 11, 12, 13, 14, 15]]
        actual_timestamps = [get_timestamp_from_proto_ts(f.timestamp).timestamp() for f in all_states["sensor_2"].last_x_frames]  # type: ignore
        self.assertEqual(actual_timestamps, expected_timestamps)

    
    # ============= Comprehensive Violation and Incident Tests =============
    
    def test_safety_violation_state_creation(self):
        """Test creation and tracking of safety violation states."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0, 
                proximity_violations="obj1,obj2|obj3,obj4"
            ),
            self.create_frame_with_violations(
                "sensor1", "frame2", 0.5,
                proximity_violations="obj1,obj2"
            ),
            self.create_frame_with_violations(
                "sensor1", "frame3", 1.0,
                proximity_violations="obj1,obj2|obj5,obj6"
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should have 3 violation states (obj1 primary, obj3 primary, obj5 primary)
        self.assertEqual(len(state.safety_violation_states), 3)  # type: ignore
        
        # Check specific violation state
        violation_key = "sensor1 #-# obj1"
        self.assertIn(violation_key, state.safety_violation_states)  # type: ignore
        violation = state.safety_violation_states[violation_key]  # type: ignore
        self.assertEqual(violation.primary_object_id, "obj1")
        self.assertIn("obj2", violation.object_ids)

    def test_safety_violation_expiration_window(self):
        """Test that violations expire if gap exceeds expiration window."""
        # First violation at t=0
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            proximity_violations="obj1,obj2"
        )
        
        # Second violation at t=3 (exceeds 2 second expiration window)
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 3,
            proximity_violations="obj1,obj2"
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame1])
        state = self.frame_mgmt.get_state("sensor1")
        first_violation = state.safety_violation_states["sensor1 #-# obj1"]  # type: ignore
        first_start = first_violation.start
        
        self.frame_mgmt.update_frames("sensor1", [frame2])
        state = self.frame_mgmt.get_state("sensor1")
        second_violation = state.safety_violation_states["sensor1 #-# obj1"]  # type: ignore
        
        # Should be a new violation state due to expiration
        self.assertNotEqual(first_start, second_violation.start)

    def test_proximity_violation_incident_generation(self):
        """Test that incidents are generated when violations exceed threshold."""
        # Create continuous violations for 4 seconds (exceeds 3 second threshold)
        frames = []
        for i in range(9):  # 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Get incidents
        incidents = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        
        # Should have 1 incident (duration > 3 seconds)
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(incident.category, "Proximity Violation")
        self.assertIn("obj1", incident.objectIds)
        self.assertIn("obj2", incident.objectIds)
        self.assertEqual(incident.info["primaryObjectId"], "obj1")

    def test_restricted_area_violation_state(self):
        """Test restricted area violation tracking."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                restricted_violations=[("roi1", ["person1", "person2"])]
            ),
            self.create_frame_with_violations(
                "sensor1", "frame2", 0.5,
                restricted_violations=[("roi1", ["person1"]), ("roi2", ["person3"])]
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should have 3 violation states (person1 in roi1, person2 in roi1, person3 in roi2)
        self.assertEqual(len(state.restricted_area_violation_states), 3)  # type: ignore
        
        # Check specific violation
        violation_key = "sensor1 #-# person1"
        self.assertIn(violation_key, state.restricted_area_violation_states)  # type: ignore
        violation = state.restricted_area_violation_states[violation_key]  # type: ignore
        self.assertEqual(violation.info["roiId"], "roi1")

    def test_confined_area_violation_state(self):
        """Test confined area violation tracking."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                confined_violations="forklift1,forklift2|robot1"
            ),
            self.create_frame_with_violations(
                "sensor1", "frame2", 0.5,
                confined_violations="forklift1"
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should have 3 violation states
        self.assertEqual(len(state.confined_area_violation_states), 3)  # type: ignore
        
        # Check specific violation
        violation_key = "sensor1 #-# forklift1"
        self.assertIn(violation_key, state.confined_area_violation_states)  # type: ignore

    def test_get_all_incidents(self):
        """Test getting all types of incidents."""
        # Create frames with all violation types
        frames = []
        for i in range(10):  # 5 seconds of violations
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2",
                    restricted_violations=[("roi1", ["person1"])],
                    confined_violations="forklift1"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Get all incidents
        all_incidents = self.frame_mgmt.get_incidents("sensor1")
        
        # Should have 3 incidents (one of each type)
        self.assertEqual(len(all_incidents), 3)
        
        # Check incident categories
        categories = [incident.category for incident in all_incidents]
        self.assertIn("Proximity Violation", categories)
        self.assertIn("Restricted Area Violation", categories)
        self.assertIn("Confined Area Violation", categories)

    def test_empty_violation_strings(self):
        """Test handling of empty violation strings."""
        frame = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            proximity_violations="",  # Empty
            confined_violations=""  # Empty
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame])
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should have no violations
        self.assertEqual(len(state.safety_violation_states), 0)  # type: ignore
        self.assertEqual(len(state.confined_area_violation_states), 0)  # type: ignore

    def test_multiple_sensors_violations(self):
        """Test violation tracking across multiple sensors."""
        # Sensor 1 frames
        frames1 = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                proximity_violations="obj1,obj2"
            )
        ]
        
        # Sensor 2 frames  
        frames2 = [
            self.create_frame_with_violations(
                "sensor2", "frame2", 0,
                proximity_violations="obj3,obj4"
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames1)
        self.frame_mgmt.update_frames("sensor2", frames2)
        
        # Check both sensors have separate states
        state1 = self.frame_mgmt.get_state("sensor1")
        state2 = self.frame_mgmt.get_state("sensor2")
        
        self.assertEqual(len(state1.safety_violation_states), 1)  # type: ignore
        self.assertEqual(len(state2.safety_violation_states), 1)  # type: ignore
        
        # Verify different violations
        self.assertIn("sensor1 #-# obj1", state1.safety_violation_states)  # type: ignore
        self.assertIn("sensor2 #-# obj3", state2.safety_violation_states)  # type: ignore

    def test_incident_threshold_boundary(self):
        """Test incident generation at exact threshold boundary."""
        # Test exactly at threshold (should trigger incident with >= logic)
        frames = []
        for i in range(7):  # 0, 0.5, 1, 1.5, 2, 2.5, 3
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        incidents = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        
        # Should have 1 incident (duration = 3 seconds, meets threshold)
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(incident.category, "Proximity Violation")
        
        # Test just below threshold (should NOT trigger incident)
        self.frame_mgmt = FrameStateMgmt(self.frame_mgmt.config)  # Reset state
        frames_below = []
        for i in range(6):  # 0, 0.5, 1, 1.5, 2, 2.5 (total 2.5 seconds)
            frames_below.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames_below)
        incidents_below = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        
        # Should have 0 incidents (duration = 2.5 seconds, below threshold)
        self.assertEqual(len(incidents_below), 0)

    def test_disabled_incident_types(self):
        """Test that disabled incident types don't generate incidents."""
        # Disable restricted area incidents
        self.frame_mgmt.config.set_app_config("restrictedAreaViolationIncidentEnable", "false")
        
        # Create violations
        frames = []
        for i in range(10):
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2",
                    restricted_violations=[("roi1", ["person1"])]
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        all_incidents = self.frame_mgmt.get_incidents("sensor1")
        
        # Should only have safety violation incident, not restricted area
        categories = [incident.category for incident in all_incidents]
        self.assertIn("Proximity Violation", categories)
        self.assertNotIn("Restricted Area Violation", categories)
    
    # ============= FOV Count Violation Tests =============
    
    def test_fov_count_violation_state_creation(self):
        """Test creation and tracking of FOV count violation states."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]  # Exceeds threshold of 3
            ),
            self.create_frame_with_violations(
                "sensor1", "frame2", 0.5,
                fov_metrics=[("Person", 5, ["p1", "p2", "p3", "p4", "p5"])]  # Still exceeds
            ),
            self.create_frame_with_violations(
                "sensor1", "frame3", 1.0,
                fov_metrics=[("Person", 2, ["p1", "p2"])]  # Below threshold
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should have one FOV count violation state (single state per sensor)
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(violation)
        
        # Check violation details - should be from the last exceeding frame
        self.assertEqual(violation.sensor_id, "sensor1")  # type: ignore[union-attr]
        self.assertEqual(violation.primary_object_id, "")  # type: ignore[union-attr]  # No primary object for FOV count
        # FOV count has no primary ID, so object_ids order is not deterministic
        self.assertCountEqual(violation.object_ids, ["p1", "p2", "p3", "p4", "p5"])  # type: ignore[union-attr]
    
    def test_fov_count_violation_threshold_boundary(self):
        """Test FOV count violation at exact threshold boundary."""
        # Test exactly at threshold (should trigger violation with >= logic)
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 3, ["p1", "p2", "p3"])]  # Exactly at threshold
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame1])
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should have violation state (count >= threshold)
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        
        # Test below threshold
        self.frame_mgmt = FrameStateMgmt(self.frame_mgmt.config)  # Reset
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 0,
            fov_metrics=[("Person", 2, ["p1", "p2"])]  # Below threshold
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame2])
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should NOT have violation state
        self.assertIsNone(state.fov_count_violation_state)  # type: ignore
    
    def test_fov_count_violation_expiration(self):
        """Test that FOV count violations expire after gap."""
        # First violation
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        
        # Second violation after expiration window (1 second)
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 1.5,
            fov_metrics=[("Person", 4, ["p5", "p6", "p7", "p8"])]
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame1])
        state = self.frame_mgmt.get_state("sensor1")
        first_violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(first_violation)
        first_start = first_violation.start  # type: ignore[union-attr]
        
        self.frame_mgmt.update_frames("sensor1", [frame2])
        state = self.frame_mgmt.get_state("sensor1")
        second_violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(second_violation)
        
        # Should be a new violation due to expiration
        self.assertNotEqual(first_start, second_violation.start)  # type: ignore[union-attr]
        # Object IDs should reflect the new violation group
        self.assertCountEqual(second_violation.object_ids, ["p5", "p6", "p7", "p8"])  # type: ignore[union-attr]
    
    def test_fov_count_violation_continues_within_gap(self):
        """Test that FOV count violations do not expire when gap is within window."""
        # First violation
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        # Second frame within expiration window (0.8s < 1s window)
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 0.8,
            fov_metrics=[("Person", 4, ["p3", "p4", "p5", "p6"])]
        )

        self.frame_mgmt.update_frames("sensor1", [frame1])
        state = self.frame_mgmt.get_state("sensor1")
        first_violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(first_violation)
        first_start = first_violation.start  # type: ignore[union-attr]
        first_end = first_violation.end  # type: ignore[union-attr]

        self.frame_mgmt.update_frames("sensor1", [frame2])
        state = self.frame_mgmt.get_state("sensor1")
        updated_violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(updated_violation)

        # Should be the same violation (continuous within gap)
        self.assertEqual(first_start, updated_violation.start)  # type: ignore[union-attr]
        self.assertGreater(updated_violation.end, first_end)  # type: ignore[union-attr]
        # Object IDs should accumulate across frames
        self.assertCountEqual(updated_violation.object_ids, ["p1", "p2", "p3", "p4", "p5", "p6"])  # type: ignore[union-attr]

    def test_fov_count_violation_completion_and_cleanup(self):
        """Test that FOV count violation state is moved to completed_states after expiration window."""
        # Initial violation
        frame1 = self.create_frame_with_violations(
            "sensor_1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        self.frame_mgmt.update_frames("sensor_1", [frame1])
        state = self.frame_mgmt.get_state("sensor_1")
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        
        # Advance time beyond expiration window (1s configured) - violation should be completed
        late_frame = self.create_frame_with_violations(
            "sensor_1", "frame2", 10,
            fov_metrics=[]  # No new violation
        )
        self.frame_mgmt.update_frames("sensor_1", [late_frame])
        state = self.frame_mgmt.get_state("sensor_1")
        # Active state should be cleared (moved to completed_states)
        self.assertIsNone(state.fov_count_violation_state)  # type: ignore
        # Completed states should have the violation
        self.assertIn("sensor_1", self.frame_mgmt.completed_states)
        
        # Getting incidents should clean up completed_states
        self.frame_mgmt.get_incidents("sensor_1")
        self.assertNotIn("sensor_1", self.frame_mgmt.completed_states)
    
    def test_fov_count_violation_incident_generation(self):
        """Test that FOV count incidents are generated when duration exceeds threshold."""
        # Create continuous violations for 2.5 seconds (exceeds 2 second threshold)
        frames = []
        for i in range(6):  # 0, 0.5, 1, 1.5, 2, 2.5
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    fov_metrics=[("Person", 4, [f"p{j}" for j in range(1, 5)])]
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Get FOV count incidents
        incidents = self.frame_mgmt.get_fov_count_violation_incidents("sensor1")
        
        # Should have 1 incident
        self.assertEqual(len(incidents), 1)
        incident = incidents[0]
        self.assertEqual(incident.category, "FOV Count Violation")
        # FOV count has no primary ID, so objectIds order is not deterministic
        self.assertCountEqual(incident.objectIds, ["p1", "p2", "p3", "p4"])
        # Aggregate violations should not carry a primaryObjectId
        self.assertNotIn("primaryObjectId", incident.info)
        # Incident info should include the object timeline
        self.assertIn("objectTimeline", incident.info)
        incident_timeline = json.loads(incident.info["objectTimeline"])
        self.assertIn("p1", incident_timeline)
        self.assertGreaterEqual(len(incident_timeline["p1"]), 1)
        
        # Test just below threshold
        self.frame_mgmt = FrameStateMgmt(self.frame_mgmt.config)  # Reset
        frames_below = []
        for i in range(4):  # 0, 0.5, 1, 1.5 (total 1.5 seconds)
            frames_below.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames_below)
        incidents_below = self.frame_mgmt.get_fov_count_violation_incidents("sensor1")
        
        # Should have 0 incidents (below 2 second threshold)
        self.assertEqual(len(incidents_below), 0)
    
    def test_fov_count_wrong_object_type(self):
        """Test that FOV counts for wrong object types don't trigger violations."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                fov_metrics=[
                    ("Vehicle", 5, ["v1", "v2", "v3", "v4", "v5"]),  # Wrong type
                    ("Person", 1, ["p1"])  # Right type but below threshold
                ]
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should NOT have violation (wrong object type)
        self.assertIsNone(state.fov_count_violation_state)  # type: ignore
    
    def test_fov_count_multiple_object_types(self):
        """Test handling of multiple object types in FOV metrics."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                fov_metrics=[
                    ("Person", 4, ["p1", "p2", "p3", "p4"]),  # Exceeds threshold
                    ("Vehicle", 2, ["v1", "v2"]),  # Different type
                    ("Animal", 5, ["a1", "a2", "a3", "a4", "a5"])  # Another type
                ]
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should only track Person violations
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(violation)
        self.assertEqual(violation.object_ids, ["p1", "p2", "p3", "p4"])  # type: ignore[union-attr]
    
    def test_fov_count_violation_disabled(self):
        """Test that FOV count violations don't generate when disabled."""
        # Disable FOV count incidents
        self.frame_mgmt.config.set_app_config("fovCountViolationIncidentEnable", "false")
        
        # Create violations that would normally trigger
        frames = []
        for i in range(10):
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    fov_metrics=[("Person", 5, ["p1", "p2", "p3", "p4", "p5"])]
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Should not generate incidents when disabled
        incidents = self.frame_mgmt.get_incidents("sensor1")
        categories = [incident.category for incident in incidents]
        self.assertNotIn("FOV Count Violation", categories)
    
    def test_fov_count_combined_with_other_violations(self):
        """Test FOV count violations alongside other violation types."""
        # Adjust test config for this test to have same threshold for all
        self.frame_mgmt.config.set_app_config("proximityViolationIncidentThreshold", "2.0")
        self.frame_mgmt.config.set_app_config("restrictedAreaViolationIncidentThreshold", "2.0")
        self.frame_mgmt.config.set_app_config("confinedAreaViolationIncidentThreshold", "2.0")
        self.frame_mgmt.config.set_app_config("fovCountViolationIncidentThreshold", "2.0")
        
        frames = []
        for i in range(5):  # 0, 0.5, 1, 1.5, 2 seconds = 2 second duration
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2",
                    restricted_violations=[("roi1", ["person1"])],
                    confined_violations="forklift1",
                    fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Get all incidents
        all_incidents = self.frame_mgmt.get_incidents("sensor1")
        
        # Should have 4 incidents (one of each type) with 2 second threshold
        self.assertEqual(len(all_incidents), 4)
        
        # Check incident categories
        categories = [incident.category for incident in all_incidents]
        self.assertIn("Proximity Violation", categories)
        self.assertIn("Restricted Area Violation", categories)
        self.assertIn("Confined Area Violation", categories)
        self.assertIn("FOV Count Violation", categories)
    
    def test_fov_count_empty_object_ids(self):
        """Test handling of FOV metrics with empty object IDs."""
        frame = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, [])]  # Count says 4 but no IDs
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame])
        state = self.frame_mgmt.get_state("sensor1")
        
        # Should still create violation based on count (object IDs not used)
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(violation)
    
    def test_fov_count_violation_id_format(self):
        """Test that FOV count violations use sensor ID as violation ID."""
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
            )
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        state = self.frame_mgmt.get_state("sensor1")
        
        # FOV count violation should exist as single optional state
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(violation)
        
        # Verify it has no primary object ID
        self.assertEqual(violation.primary_object_id, "")  # type: ignore[union-attr]
        self.assertEqual(violation.sensor_id, "sensor1")  # type: ignore[union-attr]
    
    def test_fov_count_state_update(self):
        """Test that FOV count state updates correctly with continuous violations."""
        # First frame with 4 people
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        
        # Second frame with 5 people (some same, some different)
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 0.5,
            fov_metrics=[("Person", 5, ["p2", "p3", "p4", "p5", "p6"])]
        )
        
        self.frame_mgmt.update_frames("sensor1", [frame1])
        state = self.frame_mgmt.get_state("sensor1")
        first_violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(first_violation)
        first_start = first_violation.start  # type: ignore[union-attr]
        first_end = first_violation.end  # type: ignore[union-attr]
        
        self.frame_mgmt.update_frames("sensor1", [frame2])
        state = self.frame_mgmt.get_state("sensor1")
        updated_violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(updated_violation)
        
        # Should be same violation (continuous)
        self.assertEqual(first_start, updated_violation.start)  # type: ignore[union-attr]
        # End time should be updated
        self.assertGreater(updated_violation.end, first_end)  # type: ignore[union-attr]
        # Object IDs should aggregate across the violation window (order not important)
        self.assertCountEqual(updated_violation.object_ids, ["p1", "p2", "p3", "p4", "p5", "p6"])  # type: ignore[union-attr]
        # Timeline should capture when each object was present
        timeline = {
            obj_id: [
                (
                    interval["start"],
                    interval["end"],
                )
                for interval in updated_violation.object_presence[obj_id]  # type: ignore[union-attr]
            ]
            for obj_id in updated_violation.object_presence  # type: ignore[union-attr]
        }
        expected_timeline = {
            "p1": [(self.base_time, self.base_time)],
            "p2": [(self.base_time, self.base_time + timedelta(seconds=0.5))],
            "p3": [(self.base_time, self.base_time + timedelta(seconds=0.5))],
            "p4": [(self.base_time, self.base_time + timedelta(seconds=0.5))],
            "p5": [(self.base_time + timedelta(seconds=0.5), self.base_time + timedelta(seconds=0.5))],
            "p6": [(self.base_time + timedelta(seconds=0.5), self.base_time + timedelta(seconds=0.5))],
        }
        for object_id, intervals in expected_timeline.items():
            self.assertIn(object_id, timeline)
            self.assertEqual(len(timeline[object_id]), len(intervals))
            for idx, (start_dt, end_dt) in enumerate(intervals):
                self.assertEqual(timeline[object_id][idx][0], start_dt)
                self.assertEqual(timeline[object_id][idx][1], end_dt)

    def test_fov_count_empty_object_ids_remain_empty(self):
        """FOV count violations keep empty IDs when metrics have none."""
        frame = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, [])]  # Count says 4 but no IDs
        )
        self.frame_mgmt.update_frames("sensor1", [frame])
        state = self.frame_mgmt.get_state("sensor1")
        self.assertIsNotNone(state.fov_count_violation_state)  # type: ignore
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(violation)
        self.assertEqual(violation.object_ids, [])  # type: ignore[union-attr]

    # ============= Object State TTL Cleanup Tests =============

    def test_object_ttl_default_value(self):
        """Test that the default TTL is 3600 seconds (1 hour)."""
        default_ttl = self.frame_mgmt.config.incident_object_ttl
        self.assertEqual(default_ttl, 3600, "Default incident_object_ttl should be 3600 seconds")

    def test_object_ttl_cleans_old_presence_data(self):
        """Test that object presence data older than TTL is cleaned up."""
        # Set a short TTL for testing (10 seconds)
        self.frame_mgmt.config.set_app_config("incidentObjectTtl", "10")
        
        # Create initial violation at t=0
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame1])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIn("p1", violation.object_presence)  # type: ignore[union-attr]
        
        # Create new violation at t=15 (beyond 10s TTL)
        # p1's old data should be cleaned up since it's not in this frame
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 15,
            fov_metrics=[("Person", 4, ["p2", "p3", "p4", "p5"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame2])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        
        # p1 should be removed (its data is older than TTL and not refreshed)
        self.assertNotIn("p1", violation.object_presence)  # type: ignore[union-attr]
        # p2, p3, p4 should still exist (refreshed in frame2)
        self.assertIn("p2", violation.object_presence)  # type: ignore[union-attr]
        self.assertIn("p5", violation.object_presence)  # type: ignore[union-attr]

    def test_object_ttl_removes_stale_object_ids(self):
        """Test that stale objects are removed from both object_presence and object_ids."""
        # Set a short TTL for testing (5 seconds)
        self.frame_mgmt.config.set_app_config("incidentObjectTtl", "5")
        
        # Create initial violation with p1, p2, p3, p4
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame1])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        self.assertCountEqual(violation.object_ids, ["p1", "p2", "p3", "p4"])  # type: ignore[union-attr]
        
        # Create new violation at t=10 (beyond 5s TTL) with only p3, p4, p5, p6
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 10,
            fov_metrics=[("Person", 4, ["p3", "p4", "p5", "p6"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame2])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        
        # p1, p2 should be removed from object_ids (stale)
        self.assertNotIn("p1", violation.object_ids)  # type: ignore[union-attr]
        self.assertNotIn("p2", violation.object_ids)  # type: ignore[union-attr]
        # p3, p4, p5, p6 should remain
        self.assertIn("p3", violation.object_ids)  # type: ignore[union-attr]
        self.assertIn("p4", violation.object_ids)  # type: ignore[union-attr]
        self.assertIn("p5", violation.object_ids)  # type: ignore[union-attr]
        self.assertIn("p6", violation.object_ids)  # type: ignore[union-attr]

    def test_object_ttl_preserves_fresh_data(self):
        """Test that data within TTL window is preserved."""
        # Set TTL to 60 seconds
        self.frame_mgmt.config.set_app_config("incidentObjectTtl", "60")
        # Set expiration window long enough so violation doesn't expire between frames
        self.frame_mgmt.config.set_app_config("fovCountViolationIncidentExpirationWindow", "15")
        
        # Create violations at t=0, t=10, t=20 (all within expiration window and TTL)
        frames = [
            self.create_frame_with_violations(
                "sensor1", "frame1", 0,
                fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
            ),
            self.create_frame_with_violations(
                "sensor1", "frame2", 10,
                fov_metrics=[("Person", 4, ["p2", "p3", "p4", "p5"])]
            ),
            self.create_frame_with_violations(
                "sensor1", "frame3", 20,
                fov_metrics=[("Person", 4, ["p3", "p4", "p5", "p6"])]
            ),
        ]
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        
        # All objects should be preserved (all within 60s TTL from t=20)
        self.assertCountEqual(
            violation.object_ids,  # type: ignore[union-attr]
            ["p1", "p2", "p3", "p4", "p5", "p6"]
        )

    def test_object_ttl_filters_old_intervals(self):
        """Test that old intervals are filtered but fresh ones are kept."""
        # Set TTL to 10 seconds
        self.frame_mgmt.config.set_app_config("incidentObjectTtl", "10")
        # Set expiration window to allow gaps
        self.frame_mgmt.config.set_app_config("fovCountViolationIncidentExpirationWindow", "2")
        
        # Create violation at t=0
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame1])
        
        # Create violation at t=5 (within TTL, creates new interval for p1)
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 5,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame2])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        
        # p1 should have intervals (both within TTL from t=5)
        p1_intervals = violation.object_presence.get("p1", [])  # type: ignore[union-attr]
        self.assertGreaterEqual(len(p1_intervals), 1)
        
        # Create violation at t=15 (first interval at t=0 now outside 10s TTL)
        frame3 = self.create_frame_with_violations(
            "sensor1", "frame3", 15,
            fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
        )
        self.frame_mgmt.update_frames("sensor1", [frame3])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        
        # p1 should still exist (has fresh data at t=15)
        self.assertIn("p1", violation.object_presence)  # type: ignore[union-attr]
        # Intervals older than t=5 (cutoff = t=15 - 10s = t=5) should be filtered
        p1_intervals = violation.object_presence.get("p1", [])  # type: ignore[union-attr]
        for interval in p1_intervals:
            self.assertGreaterEqual(
                interval["end"],
                self.base_time + timedelta(seconds=5),
                "Old intervals should be filtered out"
            )

    def test_object_ttl_with_proximity_violations(self):
        """Test TTL cleanup works with proximity violations."""
        # Set a short TTL
        self.frame_mgmt.config.set_app_config("incidentObjectTtl", "5")
        
        # Create proximity violation at t=0
        frame1 = self.create_frame_with_violations(
            "sensor1", "frame1", 0,
            proximity_violations="obj1,obj2"
        )
        self.frame_mgmt.update_frames("sensor1", [frame1])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation_key = "sensor1 #-# obj1"
        self.assertIn(violation_key, state.safety_violation_states)  # type: ignore
        violation = state.safety_violation_states[violation_key]  # type: ignore
        self.assertIn("obj1", violation.object_ids)
        self.assertIn("obj2", violation.object_ids)
        
        # Create new violation at t=10 (beyond 5s TTL) with only obj1, obj3
        frame2 = self.create_frame_with_violations(
            "sensor1", "frame2", 10,
            proximity_violations="obj1,obj3"
        )
        self.frame_mgmt.update_frames("sensor1", [frame2])
        
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.safety_violation_states[violation_key]  # type: ignore
        
        # obj2 should be removed (stale), obj1 and obj3 should remain
        self.assertNotIn("obj2", violation.object_ids)
        self.assertIn("obj1", violation.object_ids)
        self.assertIn("obj3", violation.object_ids)

    # ============= Duplicate Incident Prevention Tests =============

    def test_duplicate_prevention_active_violation_unchanged_end(self):
        """Test that active violations with unchanged end time don't generate duplicate incidents."""
        # Create violations that exceed threshold
        frames = []
        for i in range(9):  # 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4 (4 seconds > 3s threshold)
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # First call - should generate incident
        incidents1 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents1), 1)
        first_incident_end = incidents1[0].end
        
        # Add frames without new violations (end time unchanged)
        frames_no_violation = [
            self.create_frame_with_violations("sensor1", "frame_no_viol", 4.5, proximity_violations="")
        ]
        self.frame_mgmt.update_frames("sensor1", frames_no_violation)
        
        # Second call - should NOT generate duplicate (end time unchanged)
        incidents2 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents2), 0, "Should not generate duplicate when end time unchanged")
        
        # Verify violation state still exists and end time is tracked
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.safety_violation_states["sensor1 #-# obj1"]  # type: ignore
        self.assertEqual(violation.end, first_incident_end)
        self.assertIn("lastReportedEndTs", violation.info)

    def test_duplicate_prevention_active_violation_changed_end(self):
        """Test that active violations with changed end time generate new incidents."""
        # Create initial violations
        frames = []
        for i in range(9):  # 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # First call - should generate incident
        incidents1 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents1), 1)
        first_incident_end = incidents1[0].end
        
        # Add frames with violations continuing (end time changes)
        frames_continued = [
            self.create_frame_with_violations("sensor1", "frame5", 4.5, proximity_violations="obj1,obj2"),
            self.create_frame_with_violations("sensor1", "frame6", 5.0, proximity_violations="obj1,obj2")
        ]
        self.frame_mgmt.update_frames("sensor1", frames_continued)
        
        # Second call - should generate new incident (end time changed)
        incidents2 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents2), 1, "Should generate new incident when end time changes")
        second_incident = incidents2[0]
        self.assertGreater(second_incident.end, first_incident_end, "New incident should have later end time")
        self.assertEqual(second_incident.timestamp, incidents1[0].timestamp, "Start time should be same")

    def test_duplicate_prevention_completed_violation_always_generates(self):
        """Test that completed violations always generate incidents even if end time is same."""
        # Create violations that exceed threshold
        frames = []
        for i in range(9):  # 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Get incident (marks lastReportedEndTs)
        incidents1 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents1), 1)
        first_incident_end = incidents1[0].end
        
        # Wait for violation to expire (beyond 2s expiration window)
        # Add frame at t=7 (3 seconds after last violation at t=4, exceeds 2s window)
        late_frame = self.create_frame_with_violations(
            "sensor1", "frame_late", 7.0, proximity_violations=""
        )
        self.frame_mgmt.update_frames("sensor1", [late_frame])
        
        # Violation should be completed (moved to completed_states)
        state = self.frame_mgmt.get_state("sensor1")
        self.assertNotIn("sensor1 #-# obj1", state.safety_violation_states)  # type: ignore
        self.assertIn("sensor1", self.frame_mgmt.completed_states)
        
        # Get incidents - should generate from completed state even though end time is same
        # Use get_incidents which handles both active and completed violations
        incidents2 = self.frame_mgmt.get_incidents("sensor1")
        # Filter to proximity violations only
        proximity_incidents = [inc for inc in incidents2 if inc.category == IncidentCategory.PROXIMITY_VIOLATION.value]
        self.assertEqual(len(proximity_incidents), 1, "Completed violations should always generate incidents")
        self.assertEqual(proximity_incidents[0].end, first_incident_end, "End time should match")
        self.assertEqual(proximity_incidents[0].info.get("isComplete"), "true", "Should be marked as complete")

    def test_internal_tracking_field_excluded_from_output(self):
        """Test that lastReportedEndTs is excluded from incident output."""
        # Create violations that exceed threshold
        frames = []
        for i in range(9):  # 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # Get incidents
        incidents = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents), 1)
        
        # Verify lastReportedEndTs is NOT in incident output
        self.assertNotIn("lastReportedEndTs", incidents[0].info, 
                         "Internal tracking field should be excluded from incident output")
        
        # But it should exist in violation state
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.safety_violation_states["sensor1 #-# obj1"]  # type: ignore
        self.assertIn("lastReportedEndTs", violation.info, 
                     "Tracking field should exist in violation state")

    def test_multiple_get_incidents_calls_same_end_time(self):
        """Test that multiple calls to get_incidents with same end time only generate once."""
        # Create violations that exceed threshold
        frames = []
        for i in range(9):  # 0, 0.5, 1, 1.5, 2, 2.5, 3, 3.5, 4
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    proximity_violations="obj1,obj2"
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # First call
        incidents1 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents1), 1)
        
        # Second call without new frames (end time unchanged)
        incidents2 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents2), 0, "Should not generate duplicate on second call")
        
        # Third call without new frames
        incidents3 = self.frame_mgmt.get_proximity_violation_incidents("sensor1")
        self.assertEqual(len(incidents3), 0, "Should not generate duplicate on third call")

    def test_duplicate_prevention_fov_count_violation(self):
        """Test duplicate prevention works for FOV count violations."""
        # Create FOV count violations that exceed threshold
        frames = []
        for i in range(6):  # 0, 0.5, 1, 1.5, 2, 2.5 (2.5 seconds > 2s threshold)
            frames.append(
                self.create_frame_with_violations(
                    "sensor1", f"frame{i}", i * 0.5,
                    fov_metrics=[("Person", 4, ["p1", "p2", "p3", "p4"])]
                )
            )
        
        self.frame_mgmt.update_frames("sensor1", frames)
        
        # First call - should generate incident
        incidents1 = self.frame_mgmt.get_fov_count_violation_incidents("sensor1")
        self.assertEqual(len(incidents1), 1)
        first_incident_end = incidents1[0].end
        
        # Add frame without new violation (end time unchanged)
        frames_no_violation = [
            self.create_frame_with_violations("sensor1", "frame_no_viol", 3.0, fov_metrics=[])
        ]
        self.frame_mgmt.update_frames("sensor1", frames_no_violation)
        
        # Second call - should NOT generate duplicate
        incidents2 = self.frame_mgmt.get_fov_count_violation_incidents("sensor1")
        self.assertEqual(len(incidents2), 0, "Should not generate duplicate FOV count incident")
        
        # Verify tracking field exists
        state = self.frame_mgmt.get_state("sensor1")
        violation = state.fov_count_violation_state  # type: ignore
        self.assertIsNotNone(violation)
        self.assertIn("lastReportedEndTs", violation.info)  # type: ignore[union-attr]
        self.assertEqual(violation.end, first_incident_end)  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()

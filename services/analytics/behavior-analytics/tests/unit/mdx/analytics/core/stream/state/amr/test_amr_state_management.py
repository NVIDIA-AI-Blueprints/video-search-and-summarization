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
from unittest.mock import Mock
from datetime import datetime, timezone, timedelta
from google.protobuf.timestamp_pb2 import Timestamp

from mdx.analytics.core.schema.config import AppConfig
from mdx.analytics.core.schema.proto import schema_pb2 as nvSchema
from mdx.analytics.core.stream.state.amr.amr_state_management import AmrStateMgmt
from mdx.analytics.core.transform.calibration.calibration_e import CalibrationE


class TestAmrStateMgmt(unittest.TestCase):
    """Test suite for AmrStateMgmt class."""

    def setUp(self):
        """Set up test fixtures before each test method."""
        # Create mock config
        self.config = Mock(spec=AppConfig)
        self.config.behavior_water_mark = 60  # 60 seconds watermark
        self.config.behavior_time_threshold = datetime(2025, 1, 1, tzinfo=timezone.utc)
        
        # Create mock calibration
        self.calibration = Mock(spec=CalibrationE)
        self.calibration.roi_restricted_types = Mock(return_value={})
        
        # Initialize AmrStateMgmt
        self.amr_mgmt = AmrStateMgmt(self.config, self.calibration)

    def create_timestamp(self, seconds: int, nanos: int = 0) -> Timestamp:
        """Helper to create protobuf Timestamp."""
        ts = Timestamp()
        ts.seconds = seconds
        ts.nanos = nanos
        return ts

    def create_frame(self, sensor_id: str, timestamp_seconds: int, 
                    objects: list[dict] | None = None, rois: list[dict] | None = None) -> nvSchema.Frame:
        """
        Helper to create a test frame.
        
        :param sensor_id: Sensor ID for the frame
        :param timestamp_seconds: Timestamp in seconds since epoch
        :param objects: List of object dictionaries with 'id' and 'info' keys
        :param rois: List of ROI dictionaries with 'id', 'type', 'objectIds', and 'info' keys
        :return: nvSchema.Frame object
        """
        frame = nvSchema.Frame()
        frame.sensorId = sensor_id
        frame.timestamp.CopyFrom(self.create_timestamp(timestamp_seconds))
        
        # Add objects if provided
        if objects:
            for obj_dict in objects:
                obj = frame.objects.add()
                obj.id = obj_dict.get('id', '')
                if 'info' in obj_dict:
                    for key, value in obj_dict['info'].items():
                        obj.info[key] = value
        
        # Add ROIs if provided
        if rois:
            for roi_dict in rois:
                roi = frame.rois.add()
                roi.id = roi_dict.get('id', '')
                roi.type = roi_dict.get('type', '')
                if 'objectIds' in roi_dict:
                    roi.objectIds.extend(roi_dict['objectIds'])
                if 'info' in roi_dict:
                    for key, value in roi_dict['info'].items():
                        roi.info[key] = value
        
        return frame

    def test_initialization(self):
        """Test AmrStateMgmt initialization."""
        self.assertIsNotNone(self.amr_mgmt)
        self.assertEqual(self.amr_mgmt.config, self.config)
        self.assertEqual(self.amr_mgmt.calibration, self.calibration)
        self.assertEqual(len(self.amr_mgmt.states), 0)

    def test_empty_frames_input(self):
        """Test update_amr_states with empty frames list."""
        result = self.amr_mgmt.update_amr_states("sensor1", [])
        self.assertEqual(len(result), 0)
        self.assertEqual(len(self.amr_mgmt.states), 0)

    def test_get_ocr_id_found(self):
        """Test _get_ocr_id when OCR ID is found."""
        frame = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}},
                {'id': 'obj2', 'info': {'ocrId': 'AMR002'}}
            ]
        )
        
        ocr_id = self.amr_mgmt._get_ocr_id('obj1', frame)
        self.assertEqual(ocr_id, 'AMR001')
        
        ocr_id = self.amr_mgmt._get_ocr_id('obj2', frame)
        self.assertEqual(ocr_id, 'AMR002')

    def test_get_ocr_id_not_found(self):
        """Test _get_ocr_id when OCR ID is not found or object doesn't exist."""
        frame = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}},
                {'id': 'obj2', 'info': {}}  # No ocrId
            ]
        )
        
        # Object exists but no ocrId
        ocr_id = self.amr_mgmt._get_ocr_id('obj2', frame)
        self.assertEqual(ocr_id, '')
        
        # Object doesn't exist
        ocr_id = self.amr_mgmt._get_ocr_id('obj3', frame)
        self.assertEqual(ocr_id, '')

    def test_single_amr_state_creation(self):
        """Test creating a new AMR state when it doesn't exist."""
        # Setup calibration mock to return restricted types
        # The ROI type 'AMR' needs to be in the restricted types list for that ROI
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']  # ROI 'roi1' restricts type 'AMR'
        }
        
        # Create frame with AMR ROI
        frame = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        result = self.amr_mgmt.update_amr_states("sensor1", [frame])
        
        # Default mute is True, and restrictedAreaViolation='false' means restricted_state=True
        # So no state change on first creation (both are True)
        self.assertEqual(len(result), 0)
        
        # State should be created internally - now stored under sensor_id
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertIn('AMR001', sensor_state.amr_states)
        state = sensor_state.amr_states['AMR001']
        self.assertEqual(state.id, 'AMR001')
        self.assertEqual(state.roi_id, 'roi1')
        self.assertEqual(state.sensor_id, 'sensor1')
        self.assertEqual(state.object_id, 'obj1')
        self.assertEqual(state.mute, True)  # restricted_state = not False = True
        self.assertEqual(state.mute_state_changed, False)

    def test_mute_state_change(self):
        """Test AMR mute state changes are detected and reported."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        # First frame - AMR not in restricted area
        frame1 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        # Process first frame
        result = self.amr_mgmt.update_amr_states("sensor1", [frame1])
        # Default mute=True, restrictedAreaViolation='false' means restricted_state=True, no change
        self.assertEqual(len(result), 0)
        
        # Second frame - AMR enters restricted area (mute = true)
        frame2 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, 12, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'true'}
                }
            ]
        )
        
        # Process second frame
        result = self.amr_mgmt.update_amr_states("sensor1", [frame2])
        # restrictedAreaViolation='true' means restricted_state=False, mute changes from True to False
        self.assertEqual(len(result), 1)  # State changed
        self.assertEqual(result[0].id, 'AMR001')
        self.assertEqual(result[0].mute, False)  # restricted_state = not True = False
        
        # Third frame - AMR leaves restricted area (mute = false)
        frame3 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, 13, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        # Process third frame
        result = self.amr_mgmt.update_amr_states("sensor1", [frame3])
        # restrictedAreaViolation='false' means restricted_state=True, mute changes from False to True
        self.assertEqual(len(result), 1)  # State changed again
        self.assertEqual(result[0].id, 'AMR001')
        self.assertEqual(result[0].mute, True)  # restricted_state = not False = True

    def test_multiple_amrs_in_same_roi(self):
        """Test handling multiple AMRs in the same ROI."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        # Frame with multiple AMRs in same ROI
        frame = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}},
                {'id': 'obj2', 'info': {'ocrId': 'AMR002'}},
                {'id': 'obj3', 'info': {'ocrId': 'AMR003'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1', 'obj2', 'obj3'],
                    'info': {'restrictedAreaViolation': 'true'}
                }
            ]
        )
        
        result = self.amr_mgmt.update_amr_states("sensor1", [frame])
        
        # All AMRs should be tracked under sensor1
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertEqual(len(sensor_state.amr_states), 3)
        self.assertIn('AMR001', sensor_state.amr_states)
        self.assertIn('AMR002', sensor_state.amr_states)
        self.assertIn('AMR003', sensor_state.amr_states)
        
        # restrictedAreaViolation='true' means restricted_state=False
        # Default mute=True, so all change from True to False
        self.assertEqual(len(result), 3)  # All three have state changes
        for amr_id in ['AMR001', 'AMR002', 'AMR003']:
            state = sensor_state.amr_states[amr_id]
            self.assertEqual(state.mute, False)  # restricted_state = not True = False
            self.assertEqual(state.roi_id, 'roi1')

    def test_time_filtering(self):
        """Test that frames are filtered by time threshold."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        base_time = datetime(2025, 1, 2, tzinfo=timezone.utc)
        
        # Create frames with different timestamps but same AMR
        frames = []
        for i in range(5):
            # Create frames spanning 100 seconds
            timestamp_seconds = int((base_time + timedelta(seconds=i*25)).timestamp())
            frame = self.create_frame(
                "sensor1",
                timestamp_seconds,
                objects=[
                    {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}  # Same AMR in all frames
                ],
                rois=[
                    {
                        'id': 'roi1',
                        'type': 'AMR',
                        'objectIds': ['obj1'],
                        'info': {'restrictedAreaViolation': 'false'}
                    }
                ]
            )
            frames.append(frame)
        
        # Process all frames
        result = self.amr_mgmt.update_amr_states("sensor1", frames)
        
        # Only frames within the watermark window should be processed
        # With watermark of 60 seconds, only frames within last 60 seconds of the latest frame
        # Latest frame is at base_time + 100 seconds, so only frames after base_time + 40 should be included
        # Since we're tracking the same AMR, we should have 1 sensor state with 1 AMR state
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertEqual(len(sensor_state.amr_states), 1)
        self.assertIn('AMR001', sensor_state.amr_states)
        # Default mute=True, restrictedAreaViolation='false' means restricted_state=True, no change
        self.assertEqual(len(result), 0)
        self.assertEqual(sensor_state.amr_states['AMR001'].mute, True)

    def test_frames_before_behavior_threshold(self):
        """Test that frames before behavior_time_threshold are filtered out."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        # Create frame before threshold
        frame_before = self.create_frame(
            "sensor1",
            int(datetime(2024, 12, 31, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        # Create frame after threshold
        frame_after = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj2', 'info': {'ocrId': 'AMR002'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj2'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        # Process both frames
        result = self.amr_mgmt.update_amr_states("sensor1", [frame_before, frame_after])
        
        # Only frame after threshold should be processed
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertEqual(len(sensor_state.amr_states), 1)
        self.assertIn('AMR002', sensor_state.amr_states)
        self.assertNotIn('AMR001', sensor_state.amr_states)
        # Default mute=True, restrictedAreaViolation='false' means restricted_state=True, no change
        self.assertEqual(len(result), 0)

    def test_non_amr_rois_ignored(self):
        """Test that non-AMR ROIs are ignored."""
        # Setup calibration mock - these ROIs have restricted types but none are AMR type
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['Vehicle'],
            'roi2': ['Person']
        }
        
        # Create frame with non-AMR ROIs
        frame = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'VEH001'}},
                {'id': 'obj2', 'info': {'ocrId': 'PER001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'Vehicle',  # Not AMR
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                },
                {
                    'id': 'roi2',
                    'type': 'Person',  # Not AMR
                    'objectIds': ['obj2'],
                    'info': {'restrictedAreaViolation': 'true'}
                }
            ]
        )
        
        result = self.amr_mgmt.update_amr_states("sensor1", [frame])
        
        # No AMR states should be created since no AMR ROIs exist
        self.assertEqual(len(result), 0)
        # A sensor state is created but with empty amr_states
        self.assertIn('sensor1', self.amr_mgmt.states)
        self.assertEqual(len(self.amr_mgmt.states['sensor1'].amr_states), 0)
    
    def test_multiple_rois_with_mixed_types(self):
        """Test handling frames with both AMR and non-AMR ROIs."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR'],
            'roi2': ['Vehicle'],
            'roi3': ['AMR']
        }
        
        # Create frame with mixed ROI types
        frame = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}},
                {'id': 'obj2', 'info': {'ocrId': 'VEH001'}},
                {'id': 'obj3', 'info': {'ocrId': 'AMR002'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'true'}
                },
                {
                    'id': 'roi2',
                    'type': 'Vehicle',  # Not AMR
                    'objectIds': ['obj2'],
                    'info': {'restrictedAreaViolation': 'false'}
                },
                {
                    'id': 'roi3',
                    'type': 'AMR',
                    'objectIds': ['obj3'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        result = self.amr_mgmt.update_amr_states("sensor1", [frame])
        
        # Check internal states first
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertEqual(len(sensor_state.amr_states), 2)
        self.assertIn('AMR001', sensor_state.amr_states)
        self.assertIn('AMR002', sensor_state.amr_states)
        
        # AMR001: restrictedAreaViolation='true' means restricted_state=False
        # Default mute=True changes to False
        self.assertEqual(sensor_state.amr_states['AMR001'].mute, False)
        
        # AMR002: restrictedAreaViolation='false' means restricted_state=True  
        # Default mute=True stays True, no change
        self.assertEqual(sensor_state.amr_states['AMR002'].mute, True)
        
        # Only AMR001 has a state change
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'AMR001')

    def test_state_persistence_across_updates(self):
        """Test that AMR states persist across multiple updates."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        # First update - create state
        frame1 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        result1 = self.amr_mgmt.update_amr_states("sensor1", [frame1])
        # Default mute=True, restrictedAreaViolation='false' means restricted_state=True, no change
        self.assertEqual(len(result1), 0)
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertIn('AMR001', sensor_state.amr_states)
        initial_state = sensor_state.amr_states['AMR001']
        self.assertEqual(initial_state.mute, True)
        
        # Second update - same AMR, same mute state, different frame
        frame2 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, 1, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        result2 = self.amr_mgmt.update_amr_states("sensor1", [frame2])
        
        # State should persist, no change reported since mute state is the same
        self.assertEqual(len(result2), 0)
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertIn('AMR001', sensor_state.amr_states)
        self.assertEqual(sensor_state.amr_states['AMR001'].mute, True)

    def test_sorted_frames_processing(self):
        """Test that frames are processed in chronological order."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        base_time = datetime(2025, 1, 2, tzinfo=timezone.utc)
        
        # Create frames in random order
        frames = []
        timestamps = [30, 10, 50, 20, 40]  # Seconds offset from base_time
        
        for i, offset in enumerate(timestamps):
            frame = self.create_frame(
                "sensor1",
                int((base_time + timedelta(seconds=offset)).timestamp()),
                objects=[
                    {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
                ],
                rois=[
                    {
                        'id': 'roi1',
                        'type': 'AMR',
                        'objectIds': ['obj1'],
                        'info': {'restrictedAreaViolation': str(offset > 25).lower()}
                    }
                ]
            )
            frames.append(frame)
        
        # Process frames
        result = self.amr_mgmt.update_amr_states("sensor1", frames)
        
        # Final state should reflect the chronologically last frame (offset=50)
        # which has restrictedAreaViolation = 'true' (offset > 25)
        # restrictedAreaViolation='true' means restricted_state=False
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertIn('AMR001', sensor_state.amr_states)
        self.assertEqual(sensor_state.amr_states['AMR001'].mute, False)  # restricted_state = not True = False
        
        # There should be a state change from default True to False
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].id, 'AMR001')

    def test_mute_state_changed_flag_reset(self):
        """Test that mute_state_changed flag is reset after being returned."""
        # Setup calibration mock
        self.calibration.roi_restricted_types.return_value = {
            'roi1': ['AMR']
        }
        
        # Create initial frame
        frame1 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'false'}
                }
            ]
        )
        
        # First update creates state - default mute=True, restrictedAreaViolation='false' means restricted_state=True, no change
        result1 = self.amr_mgmt.update_amr_states("sensor1", [frame1])
        self.assertEqual(len(result1), 0)
        
        # Create frame with state change
        frame2 = self.create_frame(
            "sensor1",
            int(datetime(2025, 1, 2, 1, tzinfo=timezone.utc).timestamp()),
            objects=[
                {'id': 'obj1', 'info': {'ocrId': 'AMR001'}}
            ],
            rois=[
                {
                    'id': 'roi1',
                    'type': 'AMR',
                    'objectIds': ['obj1'],
                    'info': {'restrictedAreaViolation': 'true'}
                }
            ]
        )
        
        # First update with state change - restrictedAreaViolation='true' means restricted_state=False
        # mute changes from True to False
        result = self.amr_mgmt.update_amr_states("sensor1", [frame2])
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].mute, False)
        
        # Check that flag is reset in internal state
        self.assertIn('sensor1', self.amr_mgmt.states)
        sensor_state = self.amr_mgmt.states['sensor1']
        self.assertIn('AMR001', sensor_state.amr_states)
        self.assertEqual(sensor_state.amr_states['AMR001'].mute_state_changed, False)
        
        # Same frame again should not report change
        result = self.amr_mgmt.update_amr_states("sensor1", [frame2])
        self.assertEqual(len(result), 0)


if __name__ == "__main__":
    unittest.main()

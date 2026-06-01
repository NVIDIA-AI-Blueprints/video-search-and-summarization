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
from datetime import datetime
from unittest.mock import Mock

from mdx.analytics.core.schema.action.action_state import ActionState, FallRiskState
from mdx.analytics.core.schema.models import Action


class TestActionState(unittest.TestCase):
    """Unit tests for the ActionState class.
    
    Tests action state management including:
    - Initialization and configuration
    - Action updates based on confidence thresholds
    - Action interval tracking
    - State retrieval methods
    """

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.sensor_id = "sensor_001"
        self.object_id = "object_001"
        self.action_threshold = 0.5
        self.action_state = ActionState(self.sensor_id, self.object_id, self.action_threshold)
        self.test_timestamp = datetime(2025, 1, 15, 10, 30, 0)

    def _create_mock_action(self, action_type: str, confidence: float) -> Mock:
        """Helper method to create mock Action objects."""
        action = Mock(spec=Action)
        action.type = action_type
        action.confidence = confidence
        return action

    # Initialization Tests
    def test_init_with_valid_parameters_creates_correct_state(self):
        """Test ActionState initialization with valid parameters creates correct initial state."""
        action_state = ActionState("test_sensor", "test_object", 0.5)
        
        self.assertEqual(action_state.sensor_id, "test_sensor")
        self.assertEqual(action_state.object_id, "test_object")
        self.assertEqual(action_state.last_action, "Unknown")
        self.assertEqual(action_state.action_intervals, {})
        self.assertEqual(action_state.ACTION_CONFIDENCE_THRESHOLD, 0.5)

    def test_init_with_different_threshold_sets_correct_value(self):
        """Test ActionState initialization with different threshold sets correct threshold value."""
        action_state = ActionState("sensor", "object", 0.7)
        
        self.assertEqual(action_state.ACTION_CONFIDENCE_THRESHOLD, 0.7)

    # Action Update Tests - High Confidence
    def test_update_action_with_high_confidence_new_action_creates_interval(self):
        """Test updating action with high confidence for new action type creates new interval."""
        action = self._create_mock_action("Lying Down", 0.95)
        
        self.action_state.update_action(action, self.test_timestamp)
        
        self.assertEqual(self.action_state.last_action, "Lying Down")
        self.assertIn("Lying Down", self.action_state.action_intervals)
        self.assertEqual(len(self.action_state.action_intervals["Lying Down"]), 1)
        self.assertEqual(self.action_state.action_intervals["Lying Down"][0], 
                        [self.test_timestamp, self.test_timestamp])

    def test_update_action_with_high_confidence_same_action_extends_interval(self):
        """Test updating action with high confidence for same action type extends existing interval."""
        action1 = self._create_mock_action("Sitting", 0.8)
        action2 = self._create_mock_action("Sitting", 0.9)
        
        timestamp1 = datetime(2025, 1, 15, 10, 30, 0)
        timestamp2 = datetime(2025, 1, 15, 10, 30, 5)
        
        self.action_state.update_action(action1, timestamp1)
        self.action_state.update_action(action2, timestamp2)
        
        self.assertEqual(self.action_state.last_action, "Sitting")
        self.assertEqual(len(self.action_state.action_intervals["Sitting"]), 1)
        self.assertEqual(self.action_state.action_intervals["Sitting"][0][0], timestamp1)
        self.assertEqual(self.action_state.action_intervals["Sitting"][0][1], timestamp2)

    def test_update_action_with_multiple_different_actions_creates_separate_intervals(self):
        """Test updating with multiple different action types creates separate intervals."""
        action1 = self._create_mock_action("Lying Down", 0.9)
        action2 = self._create_mock_action("Sitting", 0.8)
        
        timestamp1 = datetime(2025, 1, 15, 10, 30, 0)
        timestamp2 = datetime(2025, 1, 15, 10, 35, 0)
        
        self.action_state.update_action(action1, timestamp1)
        self.action_state.update_action(action2, timestamp2)
        
        self.assertEqual(self.action_state.last_action, "Sitting")
        self.assertIn("Lying Down", self.action_state.action_intervals)
        self.assertIn("Sitting", self.action_state.action_intervals)
        self.assertEqual(len(self.action_state.action_intervals["Lying Down"]), 1)
        self.assertEqual(len(self.action_state.action_intervals["Sitting"]), 1)

    # Action Update Tests - Low Confidence
    def test_update_action_with_low_confidence_ignores_update(self):
        """Test updating action with low confidence ignores the update."""
        action = self._create_mock_action("Standing", 0.3)
        
        self.action_state.update_action(action, self.test_timestamp)
        
        self.assertEqual(self.action_state.last_action, "Unknown")
        self.assertEqual(self.action_state.action_intervals, {})

    def test_update_action_with_threshold_confidence_ignores_update(self):
        """Test updating action with confidence exactly at threshold ignores the update."""
        action = self._create_mock_action("Walking", 0.5)
        
        self.action_state.update_action(action, self.test_timestamp)
        
        self.assertEqual(self.action_state.last_action, "Unknown")
        self.assertEqual(self.action_state.action_intervals, {})

    def test_update_action_with_just_above_threshold_confidence_accepts_update(self):
        """Test updating action with confidence just above threshold accepts the update."""
        action = self._create_mock_action("running", 0.51)
        
        self.action_state.update_action(action, self.test_timestamp)
        
        self.assertEqual(self.action_state.last_action, "running")
        self.assertIn("running", self.action_state.action_intervals)

    # Get Last Action Tests
    def test_get_last_action_with_no_actions_returns_unknown(self):
        """Test getting last action when no actions recorded returns Unknown."""
        result = self.action_state.get_last_action()
        self.assertEqual(result, "Unknown")

    def test_get_last_action_with_recorded_action_returns_action_type(self):
        """Test getting last action after recording action returns correct action type."""
        action = self._create_mock_action("dancing", 0.75)
        
        self.action_state.update_action(action, self.test_timestamp)
        result = self.action_state.get_last_action()
        
        self.assertEqual(result, "dancing")

    def test_get_last_action_with_multiple_updates_returns_latest_action(self):
        """Test getting last action after multiple updates returns most recent action."""
        action1 = self._create_mock_action("Walking", 0.8)
        action2 = self._create_mock_action("jumping", 0.9)
        
        self.action_state.update_action(action1, self.test_timestamp)
        self.action_state.update_action(action2, self.test_timestamp)
        
        result = self.action_state.get_last_action()
        self.assertEqual(result, "jumping")

    # Get Last Interval Tests
    def test_get_last_interval_with_nonexistent_action_returns_default_interval(self):
        """Test getting last interval for non-existent action type returns default interval."""
        result = self.action_state.get_last_interval("nonexistent_action")
        
        self.assertIsNone(result)

    def test_get_last_interval_with_existing_action_returns_correct_interval(self):
        """Test getting last interval for existing action type returns correct interval."""
        action = self._create_mock_action("sleeping", 0.95)
        
        self.action_state.update_action(action, self.test_timestamp)
        result = self.action_state.get_last_interval("sleeping")
        
        self.assertEqual(result, [self.test_timestamp, self.test_timestamp])

    def test_get_last_interval_with_multiple_intervals_returns_most_recent(self):
        """Test getting last interval when multiple intervals exist returns most recent."""
        action = self._create_mock_action("eating", 0.85)
        
        timestamp1 = datetime(2025, 1, 15, 10, 30, 0)
        timestamp2 = datetime(2025, 1, 15, 11, 30, 0)
        
        # First interval
        self.action_state.update_action(action, timestamp1)
        
        # Change to different action to create separate interval
        action2 = self._create_mock_action("drinking", 0.7)
        self.action_state.update_action(action2, timestamp1)
        
        # Back to original action (creates new interval)
        self.action_state.update_action(action, timestamp2)
        
        result = self.action_state.get_last_interval("eating")
        self.assertEqual(result, [timestamp2, timestamp2])

    # Get Durations Tests
    def test_get_durations_with_initial_state_returns_empty_dict(self):
        """Test getting durations with initial state returns empty dictionary."""
        result = self.action_state.get_durations()
        
        self.assertEqual(result, {})
        self.assertIsInstance(result, dict)

    def test_get_durations_with_single_action_instant_returns_zero_duration(self):
        """Test getting durations with single instantaneous action returns zero duration."""
        action = self._create_mock_action("Standing", 0.8)
        
        self.action_state.update_action(action, self.test_timestamp)
        result = self.action_state.get_durations()
        
        self.assertIn("Standing", result)
        self.assertEqual(result["Standing"], 0)

    def test_get_durations_with_single_action_extended_returns_correct_duration(self):
        """Test getting durations with single extended action returns correct duration."""
        action = self._create_mock_action("Sitting", 0.9)
        
        timestamp1 = datetime(2025, 1, 15, 10, 30, 0)
        timestamp2 = datetime(2025, 1, 15, 10, 30, 5)  # 5 seconds later
        expected_duration = int((timestamp2 - timestamp1).total_seconds())
        
        self.action_state.update_action(action, timestamp1)
        self.action_state.update_action(action, timestamp2)
        result = self.action_state.get_durations()
        
        self.assertIn("Sitting", result)
        self.assertEqual(result["Sitting"], expected_duration)

    def test_get_durations_with_multiple_different_actions_returns_all_durations(self):
        """Test getting durations with multiple different actions returns all action durations."""
        action1 = self._create_mock_action("Walking", 0.8)
        action2 = self._create_mock_action("Running", 0.9)
        
        timestamp1 = datetime(2025, 1, 15, 10, 30, 0)
        timestamp2 = datetime(2025, 1, 15, 10, 30, 10)
        
        self.action_state.update_action(action1, timestamp1)
        self.action_state.update_action(action2, timestamp2)
        result = self.action_state.get_durations()
        
        self.assertIn("Walking", result)
        self.assertIn("Running", result)
        self.assertEqual(result["Walking"], 0)  # New action resets duration
        self.assertEqual(result["Running"], 0)  # Instantaneous action

    def test_get_durations_with_action_sequence_accumulates_duration_correctly(self):
        """Test getting durations with action sequence accumulates duration correctly."""
        action = self._create_mock_action("Lying Down", 0.95)
        
        timestamps = [
            datetime(2025, 1, 15, 10, 30, 0),   # Start
            datetime(2025, 1, 15, 10, 30, 10),  # +10 seconds
            datetime(2025, 1, 15, 10, 30, 25),  # +15 seconds more
            datetime(2025, 1, 15, 10, 30, 40)   # +15 seconds more
        ]
        
        for timestamp in timestamps:
            self.action_state.update_action(action, timestamp)
        
        result = self.action_state.get_durations()
        expected_total_duration = int((timestamps[-1] - timestamps[0]).total_seconds())  # 40 seconds total
        
        self.assertIn("Lying Down", result)
        self.assertEqual(result["Lying Down"], expected_total_duration)

    def test_get_durations_with_alternating_actions_resets_durations_correctly(self):
        """Test getting durations with alternating actions resets durations correctly."""
        action1 = self._create_mock_action("Standing", 0.8)
        action2 = self._create_mock_action("Sitting", 0.9)
        
        timestamps = [
            datetime(2025, 1, 15, 10, 30, 0),   # Start Standing
            datetime(2025, 1, 15, 10, 30, 5),   # Continue Standing (+5s)
            datetime(2025, 1, 15, 10, 30, 10),  # Switch to Sitting
            datetime(2025, 1, 15, 10, 30, 20),  # Continue Sitting (+10s)
            datetime(2025, 1, 15, 10, 30, 25),  # Switch back to Standing
            datetime(2025, 1, 15, 10, 30, 35)   # Continue Standing (+10s)
        ]
        
        self.action_state.update_action(action1, timestamps[0])  # Start Standing
        self.action_state.update_action(action1, timestamps[1])  # Continue Standing
        self.action_state.update_action(action2, timestamps[2])  # Switch to Sitting
        self.action_state.update_action(action2, timestamps[3])  # Continue Sitting
        self.action_state.update_action(action1, timestamps[4])  # Switch back to Standing
        self.action_state.update_action(action1, timestamps[5])  # Continue Standing
        
        result = self.action_state.get_durations()
        
        # Standing duration should be reset when it restarts, so only the last sequence counts
        expected_standing_duration = float((timestamps[5] - timestamps[4]).total_seconds()) + \
            float((timestamps[1] - timestamps[0]).total_seconds())
        expected_sitting_duration = float((timestamps[3] - timestamps[2]).total_seconds())
        
        self.assertIn("Standing", result)
        self.assertIn("Sitting", result)
        self.assertEqual(result["Standing"], expected_standing_duration)
        self.assertEqual(result["Sitting"], expected_sitting_duration)

    # Complex Workflow Tests
    def test_action_sequence_with_mixed_confidence_produces_correct_state(self):
        """Test action sequence with mixed confidence levels produces correct final state."""
        actions_sequence = [
            ("Lying Down", 0.9, datetime(2025, 1, 15, 10, 0, 0)),
            ("Lying Down", 0.8, datetime(2025, 1, 15, 10, 5, 0)),
            ("Sitting", 0.7, datetime(2025, 1, 15, 10, 10, 0)),
            ("Sitting", 0.85, datetime(2025, 1, 15, 10, 15, 0)),
            ("Standing", 0.6, datetime(2025, 1, 15, 10, 20, 0))
        ]
        
        for action_type, confidence, timestamp in actions_sequence:
            action = self._create_mock_action(action_type, confidence)
            self.action_state.update_action(action, timestamp)
        
        # Check final state
        self.assertEqual(self.action_state.get_last_action(), "Standing")
        
        # Check intervals exist
        lying_interval = self.action_state.get_last_interval("Lying Down")
        sitting_interval = self.action_state.get_last_interval("Sitting")
        standing_interval = self.action_state.get_last_interval("Standing")
        
        self.assertIsNotNone(lying_interval)
        self.assertIsNotNone(sitting_interval)
        self.assertIsNotNone(standing_interval)
        
        # Verify interval timestamps - with None checks for type safety
        if (lying_interval is not None and sitting_interval is not None and 
            standing_interval is not None):
            
            self.assertEqual(lying_interval[0], datetime(2025, 1, 15, 10, 0, 0))
            self.assertEqual(lying_interval[1], datetime(2025, 1, 15, 10, 5, 0))
            self.assertEqual(sitting_interval[0], datetime(2025, 1, 15, 10, 10, 0))
            self.assertEqual(sitting_interval[1], datetime(2025, 1, 15, 10, 15, 0))
            self.assertEqual(standing_interval[0], datetime(2025, 1, 15, 10, 20, 0))
            self.assertEqual(standing_interval[1], datetime(2025, 1, 15, 10, 20, 0))


class TestFallRiskState(unittest.TestCase):
    """Unit tests for the FallRiskState class.
    
    Tests fall risk state management including:
    - Initialization and configuration
    - Fall risk status updates
    - Confirmation counter management
    - Time tracking for fall risk events
    """

    def setUp(self):
        """Set up test fixtures before each test method."""
        self.sensor_id = "sensor_001"
        self.object_id = "object_001"  
        self.test_timestamp = datetime(2025, 1, 15, 10, 30, 0)
        self.default_start_threshold = 10
        self.default_end_threshold = 50
        self.fall_risk_state = FallRiskState(
            self.sensor_id, 
            self.object_id, 
            self.test_timestamp, 
            self.default_start_threshold, 
            self.default_end_threshold
        )

    # Initialization Tests
    def test_init_with_default_parameters_creates_correct_state(self):
        """Test FallRiskState initialization with default parameters creates correct initial state."""
        timestamp = datetime(2025, 1, 15, 12, 0, 0)
        fall_risk_state = FallRiskState("test_sensor", "test_object", timestamp, 10, 50)
        
        self.assertEqual(fall_risk_state.sensor_id, "test_sensor")
        self.assertEqual(fall_risk_state.object_id, "test_object")
        self.assertFalse(fall_risk_state.fall_risk)
        self.assertEqual(fall_risk_state.start_time, timestamp)
        self.assertEqual(fall_risk_state.end_time, timestamp)
        self.assertEqual(fall_risk_state.n_confirm_start, 0)
        self.assertEqual(fall_risk_state.n_confirm_end, 0)
        self.assertEqual(fall_risk_state.N_CONFIRM_START, 10)
        self.assertEqual(fall_risk_state.N_CONFIRM_END, 50)

    def test_init_with_custom_thresholds_sets_correct_values(self):
        """Test FallRiskState initialization with custom thresholds sets correct threshold values."""
        timestamp = datetime(2025, 1, 15, 12, 0, 0)
        fall_risk_state = FallRiskState("test_sensor", "test_object", timestamp, 5, 25)
        
        self.assertEqual(fall_risk_state.N_CONFIRM_START, 5)
        self.assertEqual(fall_risk_state.N_CONFIRM_END, 25)

    # Fall Risk Status Tests
    def test_is_fall_risk_with_initial_state_returns_false(self):
        """Test is_fall_risk with initial state returns False."""
        self.assertFalse(self.fall_risk_state.is_fall_risk())

    def test_update_fall_risk_to_true_sets_correct_state(self):
        """Test updating fall risk to True sets correct state and timestamp."""
        new_timestamp = datetime(2025, 1, 15, 11, 0, 0)
        self.fall_risk_state.update_fall_risk(True, new_timestamp)
        
        self.assertTrue(self.fall_risk_state.is_fall_risk())
        self.assertEqual(self.fall_risk_state.get_end_time(), new_timestamp)

    def test_update_fall_risk_to_false_after_true_sets_correct_state(self):
        """Test updating fall risk to False after being True sets correct state."""
        # First set to True
        self.fall_risk_state.update_fall_risk(True, self.test_timestamp)
        self.assertTrue(self.fall_risk_state.is_fall_risk())
        
        # Then set to False
        new_timestamp = datetime(2025, 1, 15, 11, 0, 0)
        self.fall_risk_state.update_fall_risk(False, new_timestamp)
        
        self.assertFalse(self.fall_risk_state.is_fall_risk())
        self.assertEqual(self.fall_risk_state.get_end_time(), new_timestamp)

    # Time Management Tests
    def test_update_start_time_sets_correct_timestamp(self):
        """Test updating start time sets correct timestamp."""
        new_timestamp = datetime(2025, 1, 15, 11, 0, 0)
        self.fall_risk_state.update_start_time(new_timestamp)
        
        self.assertEqual(self.fall_risk_state.get_start_time(), new_timestamp)

    def test_update_end_time_sets_correct_timestamp(self):
        """Test updating end time sets correct timestamp."""
        new_timestamp = datetime(2025, 1, 15, 11, 30, 0)
        self.fall_risk_state.update_end_time(new_timestamp)
        
        self.assertEqual(self.fall_risk_state.get_end_time(), new_timestamp)

    def test_get_start_time_returns_correct_timestamp(self):
        """Test getting start time returns correct timestamp."""
        result = self.fall_risk_state.get_start_time()
        self.assertEqual(result, self.test_timestamp)

    def test_get_end_time_returns_correct_timestamp(self):
        """Test getting end time returns correct timestamp."""
        result = self.fall_risk_state.get_end_time()
        self.assertEqual(result, self.test_timestamp)

    # Start Confirmation Tests
    def test_increase_n_confirm_start_increments_counter(self):
        """Test increasing n_confirm_start increments counter correctly."""
        initial_count = self.fall_risk_state.n_confirm_start
        self.fall_risk_state.increase_n_confirm_start()
        
        self.assertEqual(self.fall_risk_state.n_confirm_start, initial_count + 1)

    def test_increase_n_confirm_start_multiple_times_accumulates_correctly(self):
        """Test increasing n_confirm_start multiple times accumulates correctly."""
        for _ in range(5):
            self.fall_risk_state.increase_n_confirm_start()
        
        self.assertEqual(self.fall_risk_state.n_confirm_start, 5)

    def test_is_confirmed_start_below_threshold_returns_false(self):
        """Test is_confirmed_start below threshold returns False."""
        for _ in range(9):  # Below threshold of 10
            self.fall_risk_state.increase_n_confirm_start()
        
        self.assertFalse(self.fall_risk_state.is_confirmed_start())

    def test_is_confirmed_start_at_threshold_returns_true(self):
        """Test is_confirmed_start at threshold returns True."""
        for _ in range(10):  # At threshold of 10
            self.fall_risk_state.increase_n_confirm_start()
        
        self.assertTrue(self.fall_risk_state.is_confirmed_start())

    def test_is_confirmed_start_above_threshold_returns_true(self):
        """Test is_confirmed_start above threshold returns True."""
        for _ in range(15):  # Above threshold of 10
            self.fall_risk_state.increase_n_confirm_start()
        
        self.assertTrue(self.fall_risk_state.is_confirmed_start())

    def test_reset_n_confirm_start_resets_counter_to_zero(self):
        """Test resetting n_confirm_start resets counter to zero."""
        # First increase the counter
        for _ in range(5):
            self.fall_risk_state.increase_n_confirm_start()
        self.assertEqual(self.fall_risk_state.n_confirm_start, 5)
        
        # Then reset it
        self.fall_risk_state.reset_n_confirm_start()
        self.assertEqual(self.fall_risk_state.n_confirm_start, 0)

    # End Confirmation Tests
    def test_increase_n_confirm_end_increments_counter(self):
        """Test increasing n_confirm_end increments counter correctly."""
        initial_count = self.fall_risk_state.n_confirm_end
        self.fall_risk_state.increase_n_confirm_end()
        
        self.assertEqual(self.fall_risk_state.n_confirm_end, initial_count + 1)

    def test_increase_n_confirm_end_multiple_times_accumulates_correctly(self):
        """Test increasing n_confirm_end multiple times accumulates correctly."""
        for _ in range(10):
            self.fall_risk_state.increase_n_confirm_end()
        
        self.assertEqual(self.fall_risk_state.n_confirm_end, 10)

    def test_is_confirmed_end_below_threshold_returns_false(self):
        """Test is_confirmed_end below threshold returns False."""
        for _ in range(49):  # Below threshold of 50
            self.fall_risk_state.increase_n_confirm_end()
        
        self.assertFalse(self.fall_risk_state.is_confirmed_end())

    def test_is_confirmed_end_at_threshold_returns_true(self):
        """Test is_confirmed_end at threshold returns True."""
        for _ in range(50):  # At threshold of 50
            self.fall_risk_state.increase_n_confirm_end()
        
        self.assertTrue(self.fall_risk_state.is_confirmed_end())

    def test_is_confirmed_end_above_threshold_returns_true(self):
        """Test is_confirmed_end above threshold returns True."""
        for _ in range(60):  # Above threshold of 50
            self.fall_risk_state.increase_n_confirm_end()
        
        self.assertTrue(self.fall_risk_state.is_confirmed_end())

    def test_reset_n_confirm_end_resets_counter_to_zero(self):
        """Test resetting n_confirm_end resets counter to zero."""
        # First increase the counter
        for _ in range(10):
            self.fall_risk_state.increase_n_confirm_end()
        self.assertEqual(self.fall_risk_state.n_confirm_end, 10)
        
        # Then reset it
        self.fall_risk_state.reset_n_confirm_end()
        self.assertEqual(self.fall_risk_state.n_confirm_end, 0)

    # Workflow Tests
    def test_start_confirmation_workflow_progresses_correctly(self):
        """Test complete start confirmation workflow progresses correctly."""
        # Initially not confirmed
        self.assertFalse(self.fall_risk_state.is_confirmed_start())
        
        # Increase confirmations one by one
        for _ in range(9):
            self.fall_risk_state.increase_n_confirm_start()
            self.assertFalse(self.fall_risk_state.is_confirmed_start())
        
        # Final confirmation should trigger confirmed start
        self.fall_risk_state.increase_n_confirm_start()
        self.assertTrue(self.fall_risk_state.is_confirmed_start())

    def test_end_confirmation_workflow_progresses_correctly(self):
        """Test complete end confirmation workflow progresses correctly."""
        # Initially not confirmed
        self.assertFalse(self.fall_risk_state.is_confirmed_end())
        
        # Increase confirmations to near threshold
        for _ in range(45):
            self.fall_risk_state.increase_n_confirm_end()
        self.assertFalse(self.fall_risk_state.is_confirmed_end())
        
        for _ in range(4):
            self.fall_risk_state.increase_n_confirm_end()
            self.assertFalse(self.fall_risk_state.is_confirmed_end())
        
        # Final confirmation should trigger confirmed end
        self.fall_risk_state.increase_n_confirm_end()
        self.assertTrue(self.fall_risk_state.is_confirmed_end())

    def test_reset_after_confirmation_workflow_works_correctly(self):
        """Test resetting counters after confirmation workflow works correctly."""
        # Confirm start
        for _ in range(10):
            self.fall_risk_state.increase_n_confirm_start()
        self.assertTrue(self.fall_risk_state.is_confirmed_start())
        
        # Reset and verify
        self.fall_risk_state.reset_n_confirm_start()
        self.assertFalse(self.fall_risk_state.is_confirmed_start())
        
        # Confirm end
        for _ in range(50):
            self.fall_risk_state.increase_n_confirm_end()
        self.assertTrue(self.fall_risk_state.is_confirmed_end())
        
        # Reset and verify
        self.fall_risk_state.reset_n_confirm_end()
        self.assertFalse(self.fall_risk_state.is_confirmed_end())

    def test_custom_thresholds_workflow_respects_configuration(self):
        """Test workflow with custom thresholds respects configuration values."""
        timestamp = datetime(2025, 1, 15, 12, 0, 0)
        fall_risk_state = FallRiskState("test_sensor", "test_object", timestamp, 3, 7)
        
        # Test start confirmation with custom threshold
        for _ in range(2):
            fall_risk_state.increase_n_confirm_start()
            self.assertFalse(fall_risk_state.is_confirmed_start())
        
        fall_risk_state.increase_n_confirm_start()
        self.assertTrue(fall_risk_state.is_confirmed_start())
        
        # Test end confirmation with custom threshold
        for _ in range(6):
            fall_risk_state.increase_n_confirm_end()
            self.assertFalse(fall_risk_state.is_confirmed_end())
        
        fall_risk_state.increase_n_confirm_end()
        self.assertTrue(fall_risk_state.is_confirmed_end())

    def test_complete_fall_risk_lifecycle_maintains_state_consistency(self):
        """Test complete fall risk lifecycle maintains state consistency."""
        timestamps = [
            datetime(2025, 1, 15, 10, 0, 0),
            datetime(2025, 1, 15, 10, 5, 0),
            datetime(2025, 1, 15, 10, 10, 0),
            datetime(2025, 1, 15, 10, 15, 0)
        ]
        
        # Update start time
        self.fall_risk_state.update_start_time(timestamps[0])
        self.assertEqual(self.fall_risk_state.get_start_time(), timestamps[0])
        
        # Set fall risk to True
        self.fall_risk_state.update_fall_risk(True, timestamps[1])
        self.assertTrue(self.fall_risk_state.is_fall_risk())
        self.assertEqual(self.fall_risk_state.get_end_time(), timestamps[1])
        
        # Update end time
        self.fall_risk_state.update_end_time(timestamps[2])
        self.assertEqual(self.fall_risk_state.get_end_time(), timestamps[2])
        
        # Set fall risk to False
        self.fall_risk_state.update_fall_risk(False, timestamps[3])
        self.assertFalse(self.fall_risk_state.is_fall_risk())
        self.assertEqual(self.fall_risk_state.get_end_time(), timestamps[3])


if __name__ == '__main__':
    unittest.main()

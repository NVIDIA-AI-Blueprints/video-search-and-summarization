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

import pytest
from unittest.mock import patch
from pydantic import ValidationError

from mdx.analytics.core.utils.processing_stats import ProcessingStats, BatchStats


class TestProcessingStats:
    """Test suite for ProcessingStats class functionality."""

    def test_init_default_values(self):
        """Test ProcessingStats initialization with default values."""
        stats = ProcessingStats(worker_id="worker1")
        
        assert stats.worker_id == "worker1"
        assert isinstance(stats.start, float)
        assert stats.start > 0  # Should be a valid timestamp
        assert stats.num_msgs == 0

    def test_init_with_custom_values(self):
        """Test ProcessingStats initialization with custom values."""
        stats = ProcessingStats(
            worker_id=123,
            start=500.0,
            num_msgs=10
        )
        
        assert stats.worker_id == 123
        assert stats.start == 500.0
        assert stats.num_msgs == 10

    def test_init_with_string_worker_id(self):
        """Test ProcessingStats initialization with string worker_id."""
        stats = ProcessingStats(worker_id="worker_abc_123")
        assert stats.worker_id == "worker_abc_123"

    def test_init_with_integer_worker_id(self):
        """Test ProcessingStats initialization with integer worker_id."""
        stats = ProcessingStats(worker_id=999)
        assert stats.worker_id == 999

    def test_update_positive_messages(self):
        """Test updating message count with positive values."""
        stats = ProcessingStats(worker_id="worker1")
        
        stats.update(5)
        assert stats.num_msgs == 5
        
        stats.update(10)
        assert stats.num_msgs == 15

    def test_update_zero_messages(self):
        """Test updating message count with zero."""
        stats = ProcessingStats(worker_id="worker1")
        initial_count = stats.num_msgs
        
        stats.update(0)
        assert stats.num_msgs == initial_count

    def test_update_negative_messages(self):
        """Test updating message count with negative values."""
        stats = ProcessingStats(worker_id="worker1", num_msgs=10)
        
        stats.update(-3)
        assert stats.num_msgs == 7
        
        stats.update(-10)
        assert stats.num_msgs == -3

    def test_update_large_numbers(self):
        """Test updating message count with large numbers."""
        stats = ProcessingStats(worker_id="worker1")
        
        stats.update(1000000)
        assert stats.num_msgs == 1000000
        
        stats.update(999999999)
        assert stats.num_msgs == 1000999999

    def test_multiple_updates(self):
        """Test multiple consecutive updates."""
        stats = ProcessingStats(worker_id="worker1")
        
        updates = [1, 5, 10, 2, 8]
        for update_val in updates:
            stats.update(update_val)
        
        assert stats.num_msgs == sum(updates)

    def test_msgs_per_sec_basic_calculation(self):
        """Test basic messages per second calculation."""
        start_time = 1000.0
        current_time = 1010.0  # 10 seconds later
        
        # Create stats with explicit start time, then mock time.time for the calculation
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        stats.update(100)
        
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=current_time):
            # 100 messages in 10 seconds = 10.0 msgs/sec
            assert stats.msgs_per_sec == 10.0

    def test_msgs_per_sec_with_decimals(self):
        """Test messages per second calculation with decimal results."""
        start_time = 1000.0
        current_time = 1003.0  # 3 seconds later
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        stats.update(10)
        
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=current_time):
            # 10 messages in 3 seconds = 3.33 msgs/sec (rounded to 2 decimals)
            assert stats.msgs_per_sec == 3.33

    def test_msgs_per_sec_zero_messages(self):
        """Test messages per second calculation with zero messages."""
        start_time = 1000.0
        current_time = 1005.0
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=current_time):
            assert stats.msgs_per_sec == 0.0

    def test_msgs_per_sec_immediate_calculation(self):
        """Test messages per second calculation immediately after creation."""
        start_time = 1000.0
        current_time = 1000.1  # 0.1 seconds later
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        
        with patch("mdx.analytics.core.utils.processing_stats.time.time", return_value=current_time):
            stats.update(1)
            
            # 1 message in 0.1 seconds = 10.0 msgs/sec
            assert stats.msgs_per_sec == 10.0

    def test_msgs_per_sec_very_small_time_difference(self):
        """Test messages per second calculation with very small time differences."""
        start_time = 1000.0
        current_time = 1000.001  # 0.001 seconds later
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        
        with patch("mdx.analytics.core.utils.processing_stats.time.time", return_value=current_time):
            stats.update(1)
            
            # 1 message in 0.001 seconds = 1000.0 msgs/sec
            assert stats.msgs_per_sec == 1000.0

    def test_msgs_per_sec_division_by_zero_protection(self):
        """Test messages per second when elapsed time is zero."""
        # This tests the edge case where time.time() returns the same value as start
        fixed_time = 1000.0
        
        stats = ProcessingStats(worker_id="worker1", start=fixed_time)
        stats.update(10)
        
        # When elapsed time is 0, division by zero should occur
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=fixed_time):
            with pytest.raises(ZeroDivisionError):
                _ = stats.msgs_per_sec

    def test_msgs_per_sec_rounding(self):
        """Test that messages per second is properly rounded to 2 decimal places."""
        start_time = 1000.0
        current_time = 1003.0  # 3 seconds later
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        
        with patch("mdx.analytics.core.utils.processing_stats.time.time", return_value=current_time):
            stats.update(1)
            
            # 1 message in 3 seconds = 0.333... msgs/sec
            # Should be rounded to 0.33
            assert stats.msgs_per_sec == 0.33

    def test_msgs_per_sec_multiple_calls(self):
        """Test that msgs_per_sec recalculates on each call."""
        start_time = 1000.0
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        stats.update(10)
        
        # First calculation at 5 seconds
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=1005.0):
            first_rate = stats.msgs_per_sec
            assert first_rate == 2.0  # 10 msgs / 5 seconds
        
        # Second calculation at 10 seconds (no new messages)
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=1010.0):
            second_rate = stats.msgs_per_sec
            assert second_rate == 1.0  # 10 msgs / 10 seconds
        
        # Third calculation with more messages
        stats.update(10)  # Total 20 messages
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=1010.0):
            third_rate = stats.msgs_per_sec
            assert third_rate == 2.0  # 20 msgs / 10 seconds

    def test_frozen_fields_immutability(self):
        """Test that frozen fields cannot be modified after creation."""
        stats = ProcessingStats(worker_id="worker1", start=1000.0)
        
        # These should raise ValidationError due to frozen=True
        with pytest.raises(ValidationError):
            stats.worker_id = "new_worker"
            
        with pytest.raises(ValidationError):
            stats.start = 2000.0

    def test_mutable_num_msgs_field(self):
        """Test that num_msgs field can be modified directly."""
        stats = ProcessingStats(worker_id="worker1")
        
        # This should work as num_msgs is not frozen
        stats.num_msgs = 50
        assert stats.num_msgs == 50

    @pytest.mark.parametrize("worker_id,expected", [
        ("worker1", "worker1"),
        (123, 123),
        ("", ""),
        (0, 0),
        ("worker_with_underscores", "worker_with_underscores"),
        ("worker-with-dashes", "worker-with-dashes"),
    ])
    def test_worker_id_variations(self, worker_id, expected):
        """Test ProcessingStats with various worker_id formats."""
        stats = ProcessingStats(worker_id=worker_id)
        assert stats.worker_id == expected

    @pytest.mark.parametrize("num_msgs,elapsed,expected_rate", [
        (0, 1.0, 0.0),
        (10, 1.0, 10.0),
        (100, 10.0, 10.0),
        (1, 3.0, 0.33),
        (7, 3.0, 2.33),
        (1000, 1.0, 1000.0),
        (1, 0.5, 2.0),
        (1, 0.1, 10.0),
        (1, 0.01, 100.0),
    ])
    def test_msgs_per_sec_parametrized(self, num_msgs, elapsed, expected_rate):
        """Test messages per second calculation with various scenarios."""
        start_time = 1000.0
        current_time = start_time + elapsed
        
        stats = ProcessingStats(worker_id="worker1", start=start_time)
        
        with patch("mdx.analytics.core.utils.processing_stats.time.time", return_value=current_time):
            stats.update(num_msgs)
            
            assert stats.msgs_per_sec == expected_rate


class TestBatchStats:
    """Test suite for BatchStats class functionality."""

    def test_batch_stats_init_default_values(self):
        """Test BatchStats initialization with default values."""
        stats = BatchStats(worker_id="batch_worker", batch_id=100)
        
        assert stats.worker_id == "batch_worker"
        assert stats.batch_id == 100
        assert isinstance(stats.start, float)
        assert stats.start > 0  # Should be a valid timestamp
        assert stats.num_msgs == 0

    def test_batch_stats_init_custom_values(self):
        """Test BatchStats initialization with custom values."""
        stats = BatchStats(
            worker_id="custom_worker",
            batch_id=999,
            start=1500.0,
            num_msgs=25
        )
        
        assert stats.worker_id == "custom_worker"
        assert stats.batch_id == 999
        assert stats.start == 1500.0
        assert stats.num_msgs == 25

    def test_batch_stats_inherits_functionality(self):
        """Test that BatchStats inherits all ProcessingStats functionality."""
        start_time = 2000.0
        current_time = 2005.0
        
        stats = BatchStats(worker_id="batch_worker", batch_id=500, start=start_time)
        stats.update(50)
        
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=current_time):
            assert stats.num_msgs == 50
            assert stats.msgs_per_sec == 10.0  # 50 msgs / 5 seconds

    def test_batch_stats_frozen_fields(self):
        """Test that BatchStats frozen fields cannot be modified."""
        stats = BatchStats(worker_id="worker1", batch_id=100)
        
        with pytest.raises(ValidationError):
            stats.worker_id = "new_worker"
            
        with pytest.raises(ValidationError):
            stats.start = 3000.0
            
        with pytest.raises(ValidationError):
            stats.batch_id = 200

    def test_batch_stats_mutable_field(self):
        """Test that BatchStats num_msgs field can be modified."""
        stats = BatchStats(worker_id="worker1", batch_id=100)
        
        stats.num_msgs = 75
        assert stats.num_msgs == 75

    @pytest.mark.parametrize("batch_id", [1, 999, 0, 123456789])
    def test_batch_id_variations(self, batch_id):
        """Test BatchStats with various batch_id values."""
        stats = BatchStats(worker_id="worker1", batch_id=batch_id)
        assert stats.batch_id == batch_id

    def test_batch_stats_multiple_updates(self):
        """Test multiple updates on BatchStats."""
        stats = BatchStats(worker_id="worker1", batch_id=100)
        
        updates = [5, 15, 20, 10]
        for update_val in updates:
            stats.update(update_val)
        
        assert stats.num_msgs == sum(updates)

    def test_batch_stats_msgs_per_sec_calculation(self):
        """Test messages per second calculation for BatchStats."""
        start_time = 3000.0
        current_time = 3002.0  # 2 seconds later
        
        stats = BatchStats(worker_id="batch_worker", batch_id=200, start=start_time)
        stats.update(20)
        
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=current_time):
            # 20 messages in 2 seconds = 10.0 msgs/sec
            assert stats.msgs_per_sec == 10.0

    def test_batch_stats_zero_division_edge_case(self):
        """Test BatchStats with zero elapsed time edge case."""
        fixed_time = 4000.0
        
        stats = BatchStats(worker_id="worker1", batch_id=300, start=fixed_time)
        stats.update(5)
        
        with patch('mdx.analytics.core.utils.processing_stats.time.time', return_value=fixed_time):
            with pytest.raises(ZeroDivisionError):
                _ = stats.msgs_per_sec


class TestEdgeCasesAndErrorScenarios:
    """Test suite for edge cases and error scenarios."""

    def test_negative_start_time(self):
        """Test ProcessingStats with negative start time."""
        stats = ProcessingStats(worker_id="worker1", start=-100.0)
        assert stats.start == -100.0

    def test_very_large_start_time(self):
        """Test ProcessingStats with very large start time."""
        large_time = 9999999999.0
        stats = ProcessingStats(worker_id="worker1", start=large_time)
        assert stats.start == large_time

    def test_negative_initial_num_msgs(self):
        """Test ProcessingStats with negative initial message count."""
        stats = ProcessingStats(worker_id="worker1", num_msgs=-10)
        assert stats.num_msgs == -10

    def test_very_large_num_msgs(self):
        """Test ProcessingStats with very large message count."""
        large_count = 999999999999
        stats = ProcessingStats(worker_id="worker1", num_msgs=large_count)
        assert stats.num_msgs == large_count

    def test_computed_field_property_behavior(self):
        """Test that msgs_per_sec behaves as a computed property."""
        stats = ProcessingStats(worker_id="worker1")
        
        # Verify it's a property and not a regular attribute
        assert hasattr(ProcessingStats, 'msgs_per_sec')
        assert isinstance(getattr(ProcessingStats, 'msgs_per_sec'), property)

    def test_pydantic_model_behavior(self):
        """Test Pydantic model features and validation."""
        # Test model_dump
        stats = ProcessingStats(worker_id="worker1", start=1000.0, num_msgs=5)
        model_dict = stats.model_dump()
        
        expected_keys = {'worker_id', 'start', 'num_msgs', 'msgs_per_sec'}
        assert set(model_dict.keys()) == expected_keys
        assert model_dict['worker_id'] == "worker1"
        assert model_dict['start'] == 1000.0
        assert model_dict['num_msgs'] == 5

    def test_batch_stats_pydantic_behavior(self):
        """Test BatchStats Pydantic model features."""
        stats = BatchStats(worker_id="worker1", batch_id=100, start=1000.0, num_msgs=10)
        model_dict = stats.model_dump()
        
        expected_keys = {'worker_id', 'start', 'num_msgs', 'msgs_per_sec', 'batch_id'}
        assert set(model_dict.keys()) == expected_keys
        assert model_dict['batch_id'] == 100

    def test_inheritance_relationship(self):
        """Test the inheritance relationship between BatchStats and ProcessingStats."""
        assert issubclass(BatchStats, ProcessingStats)
        
        batch_stats = BatchStats(worker_id="worker1", batch_id=100)
        assert isinstance(batch_stats, ProcessingStats)
        assert isinstance(batch_stats, BatchStats)
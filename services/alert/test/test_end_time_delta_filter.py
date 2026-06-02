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
Unit tests for the End Time Delta Filter feature in RedisHandler.

This module tests:
- _parse_iso_to_epoch(): ISO timestamp to epoch conversion
- _check_end_delta(): Core delta checking logic with Redis
- filter_by_end_time_delta(): Public filter method for message batches

Run with: pytest test/test_end_time_delta_filter.py -v
"""

import hashlib
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import yaml


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def temp_config_file():
    """Create a temporary config file for testing."""
    config = {
        "event_bridge": {
            "redis_source": {
                "host": "localhost",
                "port": 6379,
                "db": 0,
                "dedup_ttl_seconds": 300,
                "end_time_in_dedup_key_categories": [],
                "protect_confirmed_verdicts": {
                    "enabled": False,
                    "ttl_seconds": 600
                },
                "end_time_delta_filter": {
                    "enabled": True,
                    "threshold_seconds": 5,
                    "ttl_seconds": 3600
                }
            }
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name
    
    yield config_path
    
    # Cleanup
    os.unlink(config_path)


@pytest.fixture
def temp_config_file_disabled():
    """Create a temporary config file with end_time_delta_filter disabled."""
    config = {
        "event_bridge": {
            "redis_source": {
                "host": "localhost",
                "port": 6379,
                "db": 0,
                "dedup_ttl_seconds": 300,
                "end_time_in_dedup_key_categories": [],
                "protect_confirmed_verdicts": {
                    "enabled": False,
                    "ttl_seconds": 600
                },
                "end_time_delta_filter": {
                    "enabled": False,
                    "threshold_seconds": 5,
                    "ttl_seconds": 3600
                }
            }
        }
    }
    
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        yaml.dump(config, f)
        config_path = f.name
    
    yield config_path
    
    # Cleanup
    os.unlink(config_path)


@pytest.fixture
def mock_redis_client():
    """Create a mock Redis client."""
    return MagicMock()


@pytest.fixture
def redis_handler_enabled(temp_config_file, mock_redis_client):
    """Create a RedisHandler with end_time_delta_filter enabled."""
    with patch('redis.Redis', return_value=mock_redis_client):
        from its_redis.redis_handler import RedisHandler
        handler = RedisHandler(config_file=temp_config_file)
        handler._redis_client = mock_redis_client
        return handler


@pytest.fixture
def redis_handler_disabled(temp_config_file_disabled, mock_redis_client):
    """Create a RedisHandler with end_time_delta_filter disabled."""
    with patch('redis.Redis', return_value=mock_redis_client):
        from its_redis.redis_handler import RedisHandler
        handler = RedisHandler(config_file=temp_config_file_disabled)
        handler._redis_client = mock_redis_client
        return handler


@pytest.fixture
def sample_incident():
    """Sample incident message with objectIds."""
    return {
        "id": "incident-001",
        "sensorId": "Sensor-001",
        "timestamp": "2024-01-15T10:30:00Z",
        "end": "2024-01-15T10:30:10Z",
        "objectIds": [3, 1, 2],
        "category": "Tailgating",
        "analyticsModule": {"id": "VST-Tailgating"}
    }


@pytest.fixture
def sample_alert():
    """Sample alert message without objectIds."""
    return {
        "id": "alert-001",
        "sensorId": "Sensor-001",
        "timestamp": "2024-01-15T10:30:00Z",
        "notification_type": "alert",
        "category": "traffic"
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _parse_iso_to_epoch()
# ─────────────────────────────────────────────────────────────────────────────

class TestParseIsoToEpoch:
    """Tests for the _parse_iso_to_epoch method."""

    def test_valid_iso_with_z_suffix(self, redis_handler_enabled):
        """Test parsing ISO timestamp with Z (UTC) suffix."""
        result = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        assert result is not None
        # Verify it's a reasonable epoch (after year 2000)
        assert result > 946684800  # 2000-01-01

    def test_valid_iso_with_timezone(self, redis_handler_enabled):
        """Test parsing ISO timestamp with explicit timezone."""
        result = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result > 946684800

    def test_valid_iso_with_milliseconds(self, redis_handler_enabled):
        """Test parsing ISO timestamp with milliseconds."""
        result = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00.123456Z")
        assert result is not None
        # Should preserve fractional seconds
        assert result != int(result)

    def test_valid_iso_with_positive_offset(self, redis_handler_enabled):
        """Test parsing ISO timestamp with positive timezone offset."""
        result = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00+05:30")
        assert result is not None

    def test_valid_iso_with_negative_offset(self, redis_handler_enabled):
        """Test parsing ISO timestamp with negative timezone offset."""
        result = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00-08:00")
        assert result is not None

    def test_none_input(self, redis_handler_enabled):
        """Test that None input returns None."""
        result = redis_handler_enabled._parse_iso_to_epoch(None)
        assert result is None

    def test_empty_string(self, redis_handler_enabled):
        """Test that empty string returns None."""
        result = redis_handler_enabled._parse_iso_to_epoch("")
        assert result is None

    def test_invalid_format(self, redis_handler_enabled):
        """Test that invalid format returns None."""
        result = redis_handler_enabled._parse_iso_to_epoch("not-a-date")
        assert result is None

    def test_invalid_date(self, redis_handler_enabled):
        """Test that invalid date (month 13) returns None."""
        result = redis_handler_enabled._parse_iso_to_epoch("2024-13-45T10:30:00Z")
        assert result is None

    def test_partial_timestamp(self, redis_handler_enabled):
        """Test that partial timestamp (date only) returns None or valid epoch."""
        # Python's fromisoformat can handle date-only strings in Python 3.11+
        result = redis_handler_enabled._parse_iso_to_epoch("2024-01-15")
        # Either None or a valid epoch is acceptable
        assert result is None or result > 0

    def test_consistency_z_vs_offset(self, redis_handler_enabled):
        """Test that Z and +00:00 produce the same epoch."""
        result_z = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        result_offset = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00+00:00")
        assert result_z == result_offset


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _check_end_delta()
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckEndDelta:
    """Tests for the _check_end_delta method."""

    def test_first_occurrence_stores_and_processes(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """First time seeing an incident - should store epoch and return True."""
        mock_redis_client.get.return_value = None  # Key doesn't exist
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True
        mock_redis_client.set.assert_called_once()
        # Verify TTL is set correctly
        call_args = mock_redis_client.set.call_args
        assert call_args.kwargs.get('ex') == 3600  # TTL from config

    def test_significant_change_updates_and_processes(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """End time changed by >= threshold - should update and return True."""
        # Stored epoch is 10 seconds earlier than current end
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        # Current end is 10 seconds later (delta = 10s >= 5s threshold)
        sample_incident["end"] = "2024-01-15T10:30:10Z"
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True
        mock_redis_client.set.assert_called_once()

    def test_insignificant_change_skips(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """End time changed by < threshold - should return False without update."""
        # Stored epoch
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        # Current end is only 2 seconds later (delta = 2s < 5s threshold)
        sample_incident["end"] = "2024-01-15T10:30:02Z"
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is False
        mock_redis_client.set.assert_not_called()

    def test_exact_threshold_processes(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """End time changed by exactly threshold - should process (>= comparison)."""
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        # Current end is exactly 5 seconds later (delta = 5s == 5s threshold)
        sample_incident["end"] = "2024-01-15T10:30:05Z"
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True
        mock_redis_client.set.assert_called_once()

    def test_missing_end_field_allows_through(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """Missing end field - should allow through (fail-open)."""
        del sample_incident["end"]
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True
        mock_redis_client.get.assert_not_called()  # Should return early

    def test_invalid_end_field_allows_through(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """Invalid end field - should allow through (fail-open)."""
        sample_incident["end"] = "not-a-valid-date"
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True
        mock_redis_client.get.assert_not_called()

    def test_redis_get_error_allows_through(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """Redis GET error - should allow through (fail-open)."""
        mock_redis_client.get.side_effect = Exception("Redis connection failed")
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True  # Fail-open

    def test_redis_set_error_still_returns_true(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """Redis SET error on first occurrence - should still return True."""
        mock_redis_client.get.return_value = None
        mock_redis_client.set.side_effect = Exception("Redis write failed")
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        assert result is True  # Fail-open

    def test_key_format_is_correct(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """Verify the Redis key format is correct."""
        mock_redis_client.get.return_value = None
        
        redis_handler_enabled._check_end_delta(sample_incident)
        
        # Get the key that was used
        call_args = mock_redis_client.set.call_args
        key = call_args.args[0]
        
        # Verify key starts with correct prefix
        assert key.startswith("vlm:enddelta:")
        
        # Verify key contains expected components (lowercase)
        assert "sensor-001" in key
        assert "2024-01-15T10:30:00Z" in key
        assert "tailgating" in key
        assert "vst-tailgating" in key

    def test_key_uses_sorted_object_ids(self, redis_handler_enabled, mock_redis_client):
        """Verify object IDs are sorted in key generation."""
        mock_redis_client.get.return_value = None
        
        # Test with unsorted IDs
        msg1 = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [3, 1, 2],
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        redis_handler_enabled._check_end_delta(msg1)
        key1 = mock_redis_client.set.call_args.args[0]
        
        mock_redis_client.reset_mock()
        
        # Same IDs in different order
        msg2 = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1, 2, 3],  # Different order
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        redis_handler_enabled._check_end_delta(msg2)
        key2 = mock_redis_client.set.call_args.args[0]
        
        # Keys should be identical despite different objectIds order
        assert key1 == key2

    def test_negative_delta_is_handled(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """Verify that going backwards in time (negative delta) is handled with abs()."""
        # Stored epoch is LATER than current end
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:20Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        # Current end is 10 seconds EARLIER
        sample_incident["end"] = "2024-01-15T10:30:10Z"
        
        result = redis_handler_enabled._check_end_delta(sample_incident)
        
        # Delta is abs(10 - 20) = 10s >= 5s threshold, should process
        assert result is True


# ─────────────────────────────────────────────────────────────────────────────
# Tests for filter_by_end_time_delta()
# ─────────────────────────────────────────────────────────────────────────────

class TestFilterByEndTimeDelta:
    """Tests for the filter_by_end_time_delta method."""

    def test_disabled_filter_returns_all_messages(self, redis_handler_disabled, sample_incident, sample_alert):
        """When filter is disabled, all messages pass through unchanged."""
        messages = [sample_incident, sample_alert]
        
        result = redis_handler_disabled.filter_by_end_time_delta(messages)
        
        assert len(result) == 2
        assert result == messages

    def test_enabled_filter_processes_first_incident(self, redis_handler_enabled, sample_incident, mock_redis_client):
        """First incident should be processed and stored."""
        mock_redis_client.get.return_value = None  # First occurrence
        
        messages = [sample_incident]
        result = redis_handler_enabled.filter_by_end_time_delta(messages)
        
        assert len(result) == 1
        assert result[0] == sample_incident

    def test_alerts_always_pass_through(self, redis_handler_enabled, sample_alert, mock_redis_client):
        """Alerts (no objectIds) always pass through without Redis check."""
        messages = [sample_alert]
        
        result = redis_handler_enabled.filter_by_end_time_delta(messages)
        
        assert len(result) == 1
        assert result[0] == sample_alert
        mock_redis_client.get.assert_not_called()  # No Redis interaction for alerts

    def test_mixed_messages_filtered_correctly(self, redis_handler_enabled, mock_redis_client):
        """Test batch with mixed message types."""
        alert = {
            "id": "alert-1",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "notification_type": "alert"
        }
        
        incident_new = {
            "id": "incident-1",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1, 2],
            "category": "tailgating",
            "analyticsModule": {"id": "vst"}
        }
        
        incident_skip = {
            "id": "incident-2",
            "sensorId": "sensor-002",
            "timestamp": "2024-01-15T10:31:00Z",
            "end": "2024-01-15T10:31:02Z",  # Only 2s change
            "objectIds": [3, 4],
            "category": "tailgating",
            "analyticsModule": {"id": "vst"}
        }
        
        # First incident: new key
        # Second incident: stored epoch exists, small delta
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:31:00Z")
        mock_redis_client.get.side_effect = [None, str(stored_epoch)]
        
        messages = [alert, incident_new, incident_skip]
        result = redis_handler_enabled.filter_by_end_time_delta(messages)
        
        # Alert passes, first incident passes, second incident skipped
        assert len(result) == 2
        assert result[0]["id"] == "alert-1"
        assert result[1]["id"] == "incident-1"

    def test_empty_list_returns_empty(self, redis_handler_enabled):
        """Empty message list returns empty list."""
        result = redis_handler_enabled.filter_by_end_time_delta([])
        
        assert result == []

    def test_all_messages_skipped_returns_empty(self, redis_handler_enabled, mock_redis_client):
        """All incidents with small deltas returns empty list."""
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        incident1 = {
            "id": "incident-1",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:01Z",  # 1s delta
            "objectIds": [1],
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        incident2 = {
            "id": "incident-2",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:02Z",  # 2s delta
            "objectIds": [1],
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        messages = [incident1, incident2]
        result = redis_handler_enabled.filter_by_end_time_delta(messages)
        
        assert len(result) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Tests for Config Loading
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigLoading:
    """Tests for end_time_delta_filter configuration loading."""

    def test_enabled_config_loaded(self, redis_handler_enabled):
        """Verify enabled config values are loaded correctly."""
        assert redis_handler_enabled._end_delta_enabled is True
        assert redis_handler_enabled._end_delta_threshold == 5
        assert redis_handler_enabled._end_delta_ttl == 3600

    def test_disabled_config_loaded(self, redis_handler_disabled):
        """Verify disabled config values are loaded correctly."""
        assert redis_handler_disabled._end_delta_enabled is False
        assert redis_handler_disabled._end_delta_threshold == 5
        assert redis_handler_disabled._end_delta_ttl == 3600

    def test_missing_config_uses_defaults(self, mock_redis_client):
        """Verify default values when config section is missing."""
        config = {
            "event_bridge": {
                "redis_source": {
                    "host": "localhost",
                    "port": 6379,
                    "db": 0,
                    "dedup_ttl_seconds": 300
                    # No end_time_delta_filter section
                }
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name
        
        try:
            with patch('redis.Redis', return_value=mock_redis_client):
                from its_redis.redis_handler import RedisHandler
                handler = RedisHandler(config_file=config_path)
                
                # Should use defaults
                assert handler._end_delta_enabled is False
                assert handler._end_delta_threshold == 5
                assert handler._end_delta_ttl == 3600
        finally:
            os.unlink(config_path)

    def test_custom_threshold_and_ttl(self, mock_redis_client):
        """Verify custom threshold and TTL values are loaded."""
        config = {
            "event_bridge": {
                "redis_source": {
                    "host": "localhost",
                    "port": 6379,
                    "db": 0,
                    "dedup_ttl_seconds": 300,
                    "end_time_delta_filter": {
                        "enabled": True,
                        "threshold_seconds": 10,  # Custom threshold
                        "ttl_seconds": 7200       # Custom TTL
                    }
                }
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config, f)
            config_path = f.name
        
        try:
            with patch('redis.Redis', return_value=mock_redis_client):
                from its_redis.redis_handler import RedisHandler
                handler = RedisHandler(config_file=config_path)
                
                assert handler._end_delta_enabled is True
                assert handler._end_delta_threshold == 10
                assert handler._end_delta_ttl == 7200
        finally:
            os.unlink(config_path)


# ─────────────────────────────────────────────────────────────────────────────
# Integration-style Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEndDeltaFilterIntegration:
    """Integration-style tests simulating real usage patterns."""

    def test_incident_progression_scenario(self, redis_handler_enabled, mock_redis_client):
        """Simulate an incident progressing over time."""
        messages_processed = []
        
        # Simulate Redis state
        stored_epochs = {}
        
        def mock_get(key):
            return stored_epochs.get(key)
        
        def mock_set(key, value, ex=None):
            stored_epochs[key] = value
        
        mock_redis_client.get.side_effect = mock_get
        mock_redis_client.set.side_effect = mock_set
        
        base_incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "objectIds": [1, 2, 3],
            "category": "tailgating",
            "analyticsModule": {"id": "vst"}
        }
        
        # T+0s: First occurrence
        incident_t0 = {**base_incident, "id": "t0", "end": "2024-01-15T10:30:00Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_t0])
        assert len(result) == 1  # Processed
        
        # T+2s: Small delta
        incident_t2 = {**base_incident, "id": "t2", "end": "2024-01-15T10:30:02Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_t2])
        assert len(result) == 0  # Skipped
        
        # T+4s: Still small delta from T+0
        incident_t4 = {**base_incident, "id": "t4", "end": "2024-01-15T10:30:04Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_t4])
        assert len(result) == 0  # Skipped
        
        # T+6s: Now significant delta (6s >= 5s threshold)
        incident_t6 = {**base_incident, "id": "t6", "end": "2024-01-15T10:30:06Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_t6])
        assert len(result) == 1  # Processed
        
        # T+8s: Small delta from T+6
        incident_t8 = {**base_incident, "id": "t8", "end": "2024-01-15T10:30:08Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_t8])
        assert len(result) == 0  # Skipped
        
        # T+12s: Significant delta from T+6 (6s)
        incident_t12 = {**base_incident, "id": "t12", "end": "2024-01-15T10:30:12Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_t12])
        assert len(result) == 1  # Processed

    def test_multiple_incidents_independent(self, redis_handler_enabled, mock_redis_client):
        """Verify that different incidents are tracked independently."""
        stored_epochs = {}
        
        def mock_get(key):
            return stored_epochs.get(key)
        
        def mock_set(key, value, ex=None):
            stored_epochs[key] = value
        
        mock_redis_client.get.side_effect = mock_get
        mock_redis_client.set.side_effect = mock_set
        
        # Two different incidents (different objectIds)
        incident_a = {
            "id": "a",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1, 2],
            "category": "tailgating",
            "analyticsModule": {"id": "vst"}
        }
        
        incident_b = {
            "id": "b",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [3, 4],  # Different objects = different incident
            "category": "tailgating",
            "analyticsModule": {"id": "vst"}
        }
        
        # Both should be processed (first occurrence for each)
        result = redis_handler_enabled.filter_by_end_time_delta([incident_a, incident_b])
        assert len(result) == 2
        
        # Small update to incident_a only
        incident_a_small = {**incident_a, "id": "a-small", "end": "2024-01-15T10:30:12Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_a_small])
        assert len(result) == 0  # Skipped (only 2s delta)
        
        # Large update to incident_b
        incident_b_large = {**incident_b, "id": "b-large", "end": "2024-01-15T10:30:20Z"}
        result = redis_handler_enabled.filter_by_end_time_delta([incident_b_large])
        assert len(result) == 1  # Processed (10s delta)


# ─────────────────────────────────────────────────────────────────────────────
# Edge Case Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:
    """Edge case and boundary condition tests."""

    def test_empty_sensor_id(self, redis_handler_enabled, mock_redis_client):
        """Test handling of empty sensorId."""
        mock_redis_client.get.return_value = None
        
        incident = {
            "sensorId": "",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1],
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        result = redis_handler_enabled.filter_by_end_time_delta([incident])
        assert len(result) == 1  # Should still process

    def test_empty_object_ids(self, redis_handler_enabled, mock_redis_client):
        """Test handling of empty objectIds list."""
        mock_redis_client.get.return_value = None
        
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [],  # Empty but present
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        result = redis_handler_enabled.filter_by_end_time_delta([incident])
        assert len(result) == 1  # Should process (has objectIds key)

    def test_missing_analytics_module(self, redis_handler_enabled, mock_redis_client):
        """Test handling of missing analyticsModule."""
        mock_redis_client.get.return_value = None
        
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1],
            "category": "test"
            # No analyticsModule
        }
        
        result = redis_handler_enabled.filter_by_end_time_delta([incident])
        assert len(result) == 1

    def test_missing_category(self, redis_handler_enabled, mock_redis_client):
        """Test handling of missing category."""
        mock_redis_client.get.return_value = None
        
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1],
            "analyticsModule": {"id": "test"}
            # No category
        }
        
        result = redis_handler_enabled.filter_by_end_time_delta([incident])
        assert len(result) == 1

    def test_whitespace_in_fields(self, redis_handler_enabled, mock_redis_client):
        """Test that whitespace in fields is handled (stripped)."""
        mock_redis_client.get.return_value = None
        
        incident = {
            "sensorId": "  Sensor-001  ",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1],
            "category": "  Tailgating  ",
            "analyticsModule": {"id": "  VST  "}
        }
        
        redis_handler_enabled.filter_by_end_time_delta([incident])
        
        key = mock_redis_client.set.call_args.args[0]
        # Verify whitespace is stripped and case is normalized
        assert "  " not in key
        assert "sensor-001" in key
        assert "tailgating" in key

    def test_very_large_delta(self, redis_handler_enabled, mock_redis_client):
        """Test handling of very large delta (days apart)."""
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-20T10:30:00Z",  # 5 days later
            "objectIds": [1],
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        result = redis_handler_enabled.filter_by_end_time_delta([incident])
        assert len(result) == 1  # Should process

    def test_fractional_seconds_in_delta(self, redis_handler_enabled, mock_redis_client):
        """Test that fractional seconds are considered in delta calculation."""
        stored_epoch = redis_handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00.000000Z")
        mock_redis_client.get.return_value = str(stored_epoch)
        
        # Delta of 4.9 seconds (just under threshold)
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:04.900000Z",
            "objectIds": [1],
            "category": "test",
            "analyticsModule": {"id": "test"}
        }
        
        result = redis_handler_enabled.filter_by_end_time_delta([incident])
        assert len(result) == 0  # Should skip (4.9s < 5s)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

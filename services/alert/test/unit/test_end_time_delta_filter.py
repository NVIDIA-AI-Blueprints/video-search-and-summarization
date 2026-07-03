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
Unit tests for the End Time Delta Filter in the in-process DedupStateHandler.

Redis was removed: this state now lives in an in-process TTL cache, so
these tests exercise the real cache (no mock backend) and rely on the
public / semi-public methods.

This module tests:
- _parse_iso_to_epoch(): ISO timestamp to epoch conversion
- _check_end_delta(): core delta checking logic
- filter_by_end_time_delta(): public filter method for message batches
"""

import os
import tempfile

import pytest
import yaml


# ─────────────────────────────────────────────────────────────────────────────
# Test Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _write_config(enabled: bool, threshold=5, ttl=3600) -> str:
    config = {
        "event_bridge": {
            "redis_source": {
                "host": "localhost",
                "port": 6379,
                "db": 0,
                "dedup_ttl_seconds": 300,
                "end_time_in_dedup_key_categories": [],
                "protect_confirmed_verdicts": {"enabled": False, "ttl_seconds": 600},
                "end_time_delta_filter": {
                    "enabled": enabled,
                    "threshold_seconds": threshold,
                    "ttl_seconds": ttl,
                },
            }
        }
    }
    fd, path = tempfile.mkstemp(suffix=".yaml")
    with os.fdopen(fd, "w") as f:
        yaml.dump(config, f)
    return path


@pytest.fixture
def temp_config_file():
    path = _write_config(enabled=True)
    yield path
    os.unlink(path)


@pytest.fixture
def temp_config_file_disabled():
    path = _write_config(enabled=False)
    yield path
    os.unlink(path)


@pytest.fixture
def handler_enabled(temp_config_file):
    from clients.redis_handler import RedisHandler

    return RedisHandler(config_file=temp_config_file)


@pytest.fixture
def handler_disabled(temp_config_file_disabled):
    from clients.redis_handler import RedisHandler

    return RedisHandler(config_file=temp_config_file_disabled)


@pytest.fixture
def sample_incident():
    return {
        "id": "incident-001",
        "sensorId": "Sensor-001",
        "timestamp": "2024-01-15T10:30:00Z",
        "end": "2024-01-15T10:30:10Z",
        "objectIds": [3, 1, 2],
        "category": "Tailgating",
        "analyticsModule": {"id": "VST-Tailgating"},
    }


@pytest.fixture
def sample_alert():
    return {
        "id": "alert-001",
        "sensorId": "Sensor-001",
        "timestamp": "2024-01-15T10:30:00Z",
        "notification_type": "alert",
        "category": "traffic",
    }


def _incident(sensor="sensor-001", ts="2024-01-15T10:30:00Z", end="2024-01-15T10:30:00Z",
              ids=(1, 2, 3), category="tailgating", am="vst"):
    return {
        "sensorId": sensor,
        "timestamp": ts,
        "end": end,
        "objectIds": list(ids),
        "category": category,
        "analyticsModule": {"id": am},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _parse_iso_to_epoch()
# ─────────────────────────────────────────────────────────────────────────────


class TestParseIsoToEpoch:
    def test_valid_iso_with_z_suffix(self, handler_enabled):
        result = handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z")
        assert result is not None
        assert result > 946684800

    def test_valid_iso_with_timezone(self, handler_enabled):
        result = handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00+00:00")
        assert result is not None
        assert result > 946684800

    def test_valid_iso_with_milliseconds(self, handler_enabled):
        result = handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00.123456Z")
        assert result is not None
        assert result != int(result)

    def test_valid_iso_with_positive_offset(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00+05:30") is not None

    def test_valid_iso_with_negative_offset(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00-08:00") is not None

    def test_none_input(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch(None) is None

    def test_empty_string(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch("") is None

    def test_invalid_format(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch("not-a-date") is None

    def test_invalid_date(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch("2024-13-45T10:30:00Z") is None

    def test_partial_timestamp(self, handler_enabled):
        result = handler_enabled._parse_iso_to_epoch("2024-01-15")
        assert result is None or result > 0

    def test_consistency_z_vs_offset(self, handler_enabled):
        assert handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00Z") == \
            handler_enabled._parse_iso_to_epoch("2024-01-15T10:30:00+00:00")


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _check_end_delta()
# ─────────────────────────────────────────────────────────────────────────────


class TestCheckEndDelta:
    def test_first_occurrence_stores_and_processes(self, handler_enabled, sample_incident):
        assert handler_enabled._check_end_delta(sample_incident) is True
        # A repeat with the same end must now be treated as a duplicate
        # (delta 0 < threshold) — proves the first call stored state.
        assert handler_enabled._check_end_delta(sample_incident) is False

    def test_significant_change_updates_and_processes(self, handler_enabled, sample_incident):
        sample_incident["end"] = "2024-01-15T10:30:00Z"
        assert handler_enabled._check_end_delta(sample_incident) is True
        moved = dict(sample_incident, end="2024-01-15T10:30:10Z")
        assert handler_enabled._check_end_delta(moved) is True

    def test_insignificant_change_skips(self, handler_enabled, sample_incident):
        sample_incident["end"] = "2024-01-15T10:30:00Z"
        assert handler_enabled._check_end_delta(sample_incident) is True
        moved = dict(sample_incident, end="2024-01-15T10:30:02Z")
        assert handler_enabled._check_end_delta(moved) is False

    def test_exact_threshold_processes(self, handler_enabled, sample_incident):
        sample_incident["end"] = "2024-01-15T10:30:00Z"
        assert handler_enabled._check_end_delta(sample_incident) is True
        moved = dict(sample_incident, end="2024-01-15T10:30:05Z")
        assert handler_enabled._check_end_delta(moved) is True

    def test_missing_end_field_allows_through(self, handler_enabled, sample_incident):
        del sample_incident["end"]
        assert handler_enabled._check_end_delta(sample_incident) is True

    def test_invalid_end_field_allows_through(self, handler_enabled, sample_incident):
        sample_incident["end"] = "not-a-valid-date"
        assert handler_enabled._check_end_delta(sample_incident) is True

    def test_sorted_object_ids_share_cohort(self, handler_enabled):
        # Different objectIds order must map to the same cohort key: seed
        # with one order, a small-delta repeat in another order is skipped.
        first = _incident(ids=(3, 1, 2), end="2024-01-15T10:30:00Z")
        assert handler_enabled._check_end_delta(first) is True
        reordered = _incident(ids=(1, 2, 3), end="2024-01-15T10:30:02Z")
        assert handler_enabled._check_end_delta(reordered) is False

    def test_whitespace_and_case_normalized_into_same_cohort(self, handler_enabled):
        first = _incident(sensor="sensor-001", category="tailgating", am="vst",
                          end="2024-01-15T10:30:00Z")
        assert handler_enabled._check_end_delta(first) is True
        messy = _incident(sensor="  Sensor-001  ", category="  Tailgating  ", am="  VST  ",
                          end="2024-01-15T10:30:02Z")
        assert handler_enabled._check_end_delta(messy) is False

    def test_negative_delta_is_handled(self, handler_enabled, sample_incident):
        sample_incident["end"] = "2024-01-15T10:30:20Z"
        assert handler_enabled._check_end_delta(sample_incident) is True
        earlier = dict(sample_incident, end="2024-01-15T10:30:10Z")
        assert handler_enabled._check_end_delta(earlier) is True  # abs(10-20)=10 >= 5


# ─────────────────────────────────────────────────────────────────────────────
# Tests for filter_by_end_time_delta()
# ─────────────────────────────────────────────────────────────────────────────


class TestFilterByEndTimeDelta:
    def test_disabled_filter_returns_all_messages(self, handler_disabled, sample_incident, sample_alert):
        messages = [sample_incident, sample_alert]
        result = handler_disabled.filter_by_end_time_delta(messages)
        assert len(result) == 2
        assert result == messages

    def test_enabled_filter_processes_first_incident(self, handler_enabled, sample_incident):
        result = handler_enabled.filter_by_end_time_delta([sample_incident])
        assert len(result) == 1
        assert result[0] == sample_incident

    def test_alerts_always_pass_through(self, handler_enabled, sample_alert):
        result = handler_enabled.filter_by_end_time_delta([sample_alert])
        assert len(result) == 1
        assert result[0] == sample_alert

    def test_mixed_messages_filtered_correctly(self, handler_enabled):
        alert = {
            "id": "alert-1",
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "notification_type": "alert",
        }
        incident_new = _incident(sensor="sensor-001", ts="2024-01-15T10:30:00Z",
                                 end="2024-01-15T10:30:10Z", ids=(1, 2))
        incident_new["id"] = "incident-1"
        # Seed a cohort for the "skip" incident then send a small-delta update.
        seed = _incident(sensor="sensor-002", ts="2024-01-15T10:31:00Z",
                         end="2024-01-15T10:31:00Z", ids=(3, 4))
        handler_enabled.filter_by_end_time_delta([seed])
        incident_skip = _incident(sensor="sensor-002", ts="2024-01-15T10:31:00Z",
                                  end="2024-01-15T10:31:02Z", ids=(3, 4))
        incident_skip["id"] = "incident-2"

        result = handler_enabled.filter_by_end_time_delta([alert, incident_new, incident_skip])
        assert len(result) == 2
        assert result[0]["id"] == "alert-1"
        assert result[1]["id"] == "incident-1"

    def test_empty_list_returns_empty(self, handler_enabled):
        assert handler_enabled.filter_by_end_time_delta([]) == []


# ─────────────────────────────────────────────────────────────────────────────
# Tests for Config Loading
# ─────────────────────────────────────────────────────────────────────────────


class TestConfigLoading:
    def test_enabled_config_loaded(self, handler_enabled):
        assert handler_enabled._end_delta_enabled is True
        assert handler_enabled._end_delta_threshold == 5
        assert handler_enabled._end_delta_ttl == 3600

    def test_disabled_config_loaded(self, handler_disabled):
        assert handler_disabled._end_delta_enabled is False
        assert handler_disabled._end_delta_threshold == 5
        assert handler_disabled._end_delta_ttl == 3600

    def test_missing_config_uses_defaults(self):
        config = {
            "event_bridge": {
                "redis_source": {
                    "host": "localhost",
                    "port": 6379,
                    "db": 0,
                    "dedup_ttl_seconds": 300,
                }
            }
        }
        fd, config_path = tempfile.mkstemp(suffix=".yaml")
        with os.fdopen(fd, "w") as f:
            yaml.dump(config, f)
        try:
            from clients.redis_handler import RedisHandler

            handler = RedisHandler(config_file=config_path)
            assert handler._end_delta_enabled is False
            assert handler._end_delta_threshold == 5
            assert handler._end_delta_ttl == 3600
        finally:
            os.unlink(config_path)

    def test_custom_threshold_and_ttl(self):
        path = _write_config(enabled=True, threshold=10, ttl=7200)
        try:
            from clients.redis_handler import RedisHandler

            handler = RedisHandler(config_file=path)
            assert handler._end_delta_enabled is True
            assert handler._end_delta_threshold == 10
            assert handler._end_delta_ttl == 7200
        finally:
            os.unlink(path)


# ─────────────────────────────────────────────────────────────────────────────
# Integration-style Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEndDeltaFilterIntegration:
    def test_incident_progression_scenario(self, handler_enabled):
        base = dict(sensorId="sensor-001", timestamp="2024-01-15T10:30:00Z",
                    objectIds=[1, 2, 3], category="tailgating",
                    analyticsModule={"id": "vst"})

        def send(end):
            return handler_enabled.filter_by_end_time_delta([{**base, "end": end}])

        assert len(send("2024-01-15T10:30:00Z")) == 1   # T+0 first
        assert len(send("2024-01-15T10:30:02Z")) == 0   # T+2 small
        assert len(send("2024-01-15T10:30:04Z")) == 0   # T+4 still small
        assert len(send("2024-01-15T10:30:06Z")) == 1   # T+6 significant
        assert len(send("2024-01-15T10:30:08Z")) == 0   # small from T+6
        assert len(send("2024-01-15T10:30:12Z")) == 1   # significant from T+6

    def test_multiple_incidents_independent(self, handler_enabled):
        incident_a = _incident(ids=(1, 2), end="2024-01-15T10:30:10Z")
        incident_b = _incident(ids=(3, 4), end="2024-01-15T10:30:10Z")
        result = handler_enabled.filter_by_end_time_delta([incident_a, incident_b])
        assert len(result) == 2

        a_small = _incident(ids=(1, 2), end="2024-01-15T10:30:12Z")
        assert len(handler_enabled.filter_by_end_time_delta([a_small])) == 0

        b_large = _incident(ids=(3, 4), end="2024-01-15T10:30:20Z")
        assert len(handler_enabled.filter_by_end_time_delta([b_large])) == 1


# ─────────────────────────────────────────────────────────────────────────────
# Edge Case Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_empty_sensor_id(self, handler_enabled):
        incident = _incident(sensor="", ids=(1,), category="test", am="test",
                             end="2024-01-15T10:30:10Z")
        assert len(handler_enabled.filter_by_end_time_delta([incident])) == 1

    def test_empty_object_ids(self, handler_enabled):
        incident = _incident(ids=(), category="test", am="test", end="2024-01-15T10:30:10Z")
        assert len(handler_enabled.filter_by_end_time_delta([incident])) == 1

    def test_missing_analytics_module(self, handler_enabled):
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1],
            "category": "test",
        }
        assert len(handler_enabled.filter_by_end_time_delta([incident])) == 1

    def test_missing_category(self, handler_enabled):
        incident = {
            "sensorId": "sensor-001",
            "timestamp": "2024-01-15T10:30:00Z",
            "end": "2024-01-15T10:30:10Z",
            "objectIds": [1],
            "analyticsModule": {"id": "test"},
        }
        assert len(handler_enabled.filter_by_end_time_delta([incident])) == 1

    def test_very_large_delta(self, handler_enabled):
        incident = _incident(ids=(1,), category="test", am="test", end="2024-01-15T10:30:00Z")
        handler_enabled.filter_by_end_time_delta([incident])
        far = _incident(ids=(1,), category="test", am="test", end="2024-01-20T10:30:00Z")
        assert len(handler_enabled.filter_by_end_time_delta([far])) == 1

    def test_fractional_seconds_in_delta(self, handler_enabled):
        incident = _incident(ids=(1,), category="test", am="test",
                             end="2024-01-15T10:30:00.000000Z")
        handler_enabled.filter_by_end_time_delta([incident])
        near = _incident(ids=(1,), category="test", am="test",
                        end="2024-01-15T10:30:04.900000Z")
        assert len(handler_enabled.filter_by_end_time_delta([near])) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

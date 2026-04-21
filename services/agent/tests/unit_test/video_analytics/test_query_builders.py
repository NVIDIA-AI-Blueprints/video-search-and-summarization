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
"""Tests for vss_agents/video_analytics/query_builders.py."""

from vss_agents.video_analytics.query_builders import BehaviorQueryBuilder
from vss_agents.video_analytics.query_builders import FramesQueryBuilder
from vss_agents.video_analytics.query_builders import IncidentQueryBuilder


class TestIncidentQueryBuilder:
    """Tests for IncidentQueryBuilder class."""

    def test_build_query_by_id(self):
        """Test building query by incident ID."""
        query = IncidentQueryBuilder.build_query_by_id("incident-123")
        assert "query" in query
        assert "bool" in query["query"]
        # Should have a term match for Id.keyword
        must_clauses = query["query"]["bool"]["must"]
        assert any("term" in clause and "Id.keyword" in clause.get("term", {}) for clause in must_clauses)

    def test_build_query_basic(self):
        """Test building basic incident query."""
        query = IncidentQueryBuilder.build_query(
            source=None,
            source_type=None,
            start_time=None,
            end_time=None,
        )
        assert "query" in query
        assert "bool" in query["query"]

    def test_build_query_with_sensor(self):
        """Test building query with sensor filter."""
        query = IncidentQueryBuilder.build_query(
            source="sensor-001",
            source_type="sensor",
            start_time="2022-08-25T00:00:00.000Z",
            end_time="2022-08-25T01:00:00.000Z",
        )
        must_clauses = query["query"]["bool"]["must"]
        # Should have sensorId.keyword term
        assert any("term" in clause for clause in must_clauses)

    def test_build_query_with_place(self):
        """Test building query with place filter."""
        query = IncidentQueryBuilder.build_query(
            source="San Jose",
            source_type="place",
            start_time="2022-08-25T00:00:00.000Z",
            end_time="2022-08-25T01:00:00.000Z",
        )
        must_clauses = query["query"]["bool"]["must"]
        # Should have wildcard match for place
        assert any("wildcard" in clause for clause in must_clauses)

    def test_build_query_with_time_range(self):
        """Test building query with time range."""
        query = IncidentQueryBuilder.build_query(
            source=None,
            source_type=None,
            start_time="2022-08-25T00:00:00.000Z",
            end_time="2022-08-25T01:00:00.000Z",
        )
        must_clauses = query["query"]["bool"]["must"]
        # Should have range filters
        assert any("range" in clause for clause in must_clauses)

    def test_build_query_vlm_verified(self):
        """Test building query with VLM verification."""
        query = IncidentQueryBuilder.build_query(
            source=None,
            source_type=None,
            start_time=None,
            end_time=None,
            vlm_verified=True,
            vlm_verdict="confirmed",
        )
        must_clauses = query["query"]["bool"]["must"]
        # Should have verdict filter
        assert any("term" in clause for clause in must_clauses)

    def test_build_query_vlm_not_confirmed(self):
        """Test building query with not-confirmed VLM verdict."""
        query = IncidentQueryBuilder.build_query(
            source=None,
            source_type=None,
            start_time=None,
            end_time=None,
            vlm_verified=True,
            vlm_verdict="not-confirmed",
        )
        must_clauses = query["query"]["bool"]["must"]
        # Should have terms filter for rejected and verification-failed
        assert any("terms" in clause for clause in must_clauses)

    def test_build_query_vlm_verdict_all(self):
        """Test building query with 'all' VLM verdict (covers line 92)."""
        query = IncidentQueryBuilder.build_query(
            source=None,
            source_type=None,
            start_time=None,
            end_time=None,
            vlm_verified=True,
            vlm_verdict="all",
        )
        # With "all" verdict, no additional verdict filter should be added
        # Query should still be valid
        assert "query" in query
        assert "bool" in query["query"]


class TestFramesQueryBuilder:
    """Tests for FramesQueryBuilder class."""

    def test_build_query(self):
        """Test building frames query."""
        query = FramesQueryBuilder.build_query(
            sensor_id="sensor-001",
            start_time="2022-08-25T00:00:00.000Z",
            end_time="2022-08-25T01:00:00.000Z",
        )
        assert "query" in query
        must_clauses = query["query"]["bool"]["must"]
        # Should have sensor filter
        assert any("term" in clause for clause in must_clauses)
        # Should have time range
        assert any("range" in clause for clause in must_clauses)

    def test_fov_histogram_aggregation(self):
        """Test FOV histogram aggregation."""
        agg = FramesQueryBuilder.fov_histogram_aggregation(bucket_size_sec=60)
        assert "eventsOverTime" in agg
        assert "date_histogram" in agg["eventsOverTime"]
        assert agg["eventsOverTime"]["date_histogram"]["fixed_interval"] == "60s"

    def test_fov_histogram_with_object_type(self):
        """Test FOV histogram aggregation with object type filter."""
        agg = FramesQueryBuilder.fov_histogram_aggregation(bucket_size_sec=60, object_type="person")
        assert "eventsOverTime" in agg
        # Should have filter for object type
        filter_bool = agg["eventsOverTime"]["aggs"]["fov"]["aggs"]["searchAggFilter"]["filter"]["bool"]["filter"]
        assert len(filter_bool) > 0


class TestBehaviorQueryBuilder:
    """Tests for BehaviorQueryBuilder class."""

    def test_default_constants(self):
        """Test default constants."""
        assert BehaviorQueryBuilder.DEFAULT_STATIONARY_OBJECT_MAX_TIME_INTERVAL_SEC == 500
        assert BehaviorQueryBuilder.DEFAULT_STATIONARY_OBJECT_MIN_DISTANCE_METERS == 5
        assert BehaviorQueryBuilder.DEFAULT_SHORT_LIVED_BEHAVIOR_MIN_TIME_INTERVAL_SEC == 3

    def test_build_average_speed_query_sensor(self):
        """Test building average speed query for sensor."""
        query = BehaviorQueryBuilder.build_average_speed_query(
            source="sensor-001",
            source_type="sensor",
            start_time="2022-08-25T00:00:00.000Z",
            end_time="2022-08-25T01:00:00.000Z",
        )
        assert "query" in query
        must_clauses = query["query"]["bool"]["must"]
        # Should have time range filters
        assert any("range" in clause for clause in must_clauses)
        # Should have sensor filter
        assert any("term" in clause for clause in must_clauses)

    def test_build_average_speed_query_place(self):
        """Test building average speed query for place."""
        query = BehaviorQueryBuilder.build_average_speed_query(
            source="San Jose",
            source_type="place",
            start_time="2022-08-25T00:00:00.000Z",
            end_time="2022-08-25T01:00:00.000Z",
        )
        must_clauses = query["query"]["bool"]["must"]
        # Should have wildcard for place
        assert any("wildcard" in clause for clause in must_clauses)

    def test_average_speed_per_direction_aggregation(self):
        """Test average speed per direction aggregation."""
        agg = BehaviorQueryBuilder.average_speed_per_direction_aggregation()
        assert "directions" in agg
        assert "terms" in agg["directions"]
        assert "averageSpeed" in agg["directions"]["aggs"]

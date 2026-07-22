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
Domain-specific query builders for video analytics.

Each builder knows how to construct queries for a specific domain.
"""

from copy import deepcopy

from .es_client import BASE_QUERY_TEMPLATE


class IncidentQueryBuilder:
    """
    Build incident-specific queries.

    Supports interface.get_incidents() and interface.get_incident()
    """

    @staticmethod
    def build_query_by_id(incident_id: str) -> dict:
        """
        Build query for a single incident by exact ID match.

        Args:
            incident_id: The incident ID to query

        Returns:
            Elasticsearch query body
        """
        query = deepcopy(BASE_QUERY_TEMPLATE)

        # Exact match on Id.keyword field
        query["query"]["bool"]["must"].append({"term": {"Id.keyword": incident_id}})

        return query

    @staticmethod
    def build_query(
        source: str | None,
        source_type: str | None,
        start_time: str | None,
        end_time: str | None,
        vlm_verified: bool = False,
        vlm_verdict: str | None = None,
    ) -> dict:
        """
        Build query for incidents.

        Args:
            source: Optional sensor ID or place/city name (None to query all)
            source_type: Optional "sensor" or "place" (None to query all). "place" uses wildcard matching.
            start_time: Optional ISO format timestamp (None to get most recent incidents)
            end_time: Optional ISO format timestamp (None to get most recent incidents)
            vlm_verified: Whether VLM verification is enabled
            vlm_verdict: Optional VLM verdict filter ('all', 'confirmed', 'rejected', 'verification-failed', 'not-confirmed')

        Returns:
            Elasticsearch query body
        """
        query = deepcopy(BASE_QUERY_TEMPLATE)

        # Time range filter (incidents have start and end times)
        # Only add time filters if timestamps are provided
        if start_time is not None and end_time is not None:
            query["query"]["bool"]["must"].extend(
                [{"range": {"timestamp": {"lte": end_time}}}, {"range": {"end": {"gte": start_time}}}]
            )

        # Source filter (sensor or place) - only if provided
        if source is not None and source_type is not None:
            if source_type == "sensor":
                query["query"]["bool"]["must"].append({"term": {"sensorId.keyword": source}})
            elif source_type == "place":
                # Use wildcard matching to allow partial place name matches
                # Works for both city names and intersection names
                # Example: "Dubuque" matches "city=Dubuque/intersection=HWY_20_AND_LOCUST"  # pragma: allowlist secret
                # Example: "HWY_20_AND_LOCUST" matches "city=Dubuque/intersection=HWY_20_AND_LOCUST"  # pragma: allowlist secret
                query["query"]["bool"]["must"].append({"wildcard": {"place.name.keyword": f"*{source}*"}})

        # VLM verdict filter - only if vlm_verified is enabled and verdict is provided
        if vlm_verified and vlm_verdict is not None:
            if vlm_verdict == "all":
                # No additional filtering needed
                pass
            elif vlm_verdict == "not-confirmed":
                # Filter for both "rejected" and "verification-failed"
                query["query"]["bool"]["must"].append(
                    {"terms": {"info.verdict.keyword": ["rejected", "verification-failed"]}}
                )
            else:
                # Filter for specific verdict (confirmed, rejected, or verification-failed)
                query["query"]["bool"]["must"].append({"term": {"info.verdict.keyword": vlm_verdict}})

        return query


class FramesQueryBuilder:
    """
    Build frames-specific queries.

    Mirrors logic for frames index queries.
    Supports FOV occupancy queries.
    """

    @staticmethod
    def build_query(sensor_id: str, start_time: str, end_time: str) -> dict:
        """
        Build query for frames.

        Args:
            sensor_id: Sensor ID
            start_time: ISO format timestamp
            end_time: ISO format timestamp

        Returns:
            Elasticsearch query body
        """
        query = deepcopy(BASE_QUERY_TEMPLATE)

        # Sensor filter
        query["query"]["bool"]["must"].append({"term": {"sensorId.keyword": sensor_id}})

        # Time range filter
        query["query"]["bool"]["must"].append({"range": {"timestamp": {"gte": start_time, "lte": end_time}}})

        return query

    @staticmethod
    def fov_histogram_aggregation(bucket_size_sec: int, object_type: str | None = None) -> dict:
        """
        Histogram aggregation for FOV object counts over time buckets.

        Uses frames index with nested fov field.

        Args:
            bucket_size_sec: Size of each time bucket in seconds
            object_type: Optional filter for specific object type

        Returns:
            Aggregation specification for histogram of FOV occupancy
        """
        agg = {
            "eventsOverTime": {
                "date_histogram": {"field": "timestamp", "fixed_interval": f"{bucket_size_sec}s"},
                "aggs": {
                    "fov": {
                        "nested": {"path": "fov"},
                        "aggs": {
                            "searchAggFilter": {
                                "filter": {"bool": {"filter": []}},
                                "aggs": {
                                    "objectType": {
                                        "terms": {"field": "fov.type.keyword", "size": 1000},
                                        "aggs": {"avgCount": {"avg": {"field": "fov.count"}}},
                                    }
                                },
                            }
                        },
                    }
                },
            }
        }

        # Add object type filter if specified
        if object_type:
            # Deep nested access - mypy can't track the dict structure
            events_over_time: dict = agg["eventsOverTime"]
            fov_aggs: dict = events_over_time["aggs"]["fov"]["aggs"]
            filter_list: list = fov_aggs["searchAggFilter"]["filter"]["bool"]["filter"]
            filter_list.append({"term": {"fov.type.keyword": object_type}})

        return agg


class BehaviorQueryBuilder:
    """
    Build behavior/metrics queries.

    Supports interface.get_fov_histogram() and interface.get_average_speeds()
    """

    DEFAULT_STATIONARY_OBJECT_MAX_TIME_INTERVAL_SEC = 500
    DEFAULT_STATIONARY_OBJECT_MIN_DISTANCE_METERS = 5
    DEFAULT_SHORT_LIVED_BEHAVIOR_MIN_TIME_INTERVAL_SEC = 3

    @staticmethod
    def build_average_speed_query(source: str, source_type: str, start_time: str, end_time: str) -> dict:
        """
        Build average speed query.

        Args:
            source: Sensor ID or place name
            source_type: "sensor" or "place"
            start_time: ISO format timestamp (fromTimestamp)
            end_time: ISO format timestamp (toTimestamp)

        Returns:
            Elasticsearch query body
        """
        query = deepcopy(BASE_QUERY_TEMPLATE)

        # Time range filter
        query["query"]["bool"]["must"].extend(
            [{"range": {"timestamp": {"lte": end_time}}}, {"range": {"end": {"gte": start_time}}}]
        )

        # Filter out short-lived behaviors and stationary objects
        query["query"]["bool"]["must"].extend(
            [
                {
                    "range": {
                        "timeInterval": {
                            "gte": BehaviorQueryBuilder.DEFAULT_SHORT_LIVED_BEHAVIOR_MIN_TIME_INTERVAL_SEC,
                            "lte": BehaviorQueryBuilder.DEFAULT_STATIONARY_OBJECT_MAX_TIME_INTERVAL_SEC,
                        }
                    }
                },
                {"range": {"distance": {"gte": BehaviorQueryBuilder.DEFAULT_STATIONARY_OBJECT_MIN_DISTANCE_METERS}}},
            ]
        )

        # Source filter
        if source_type == "place":
            # Use wildcard matching to allow partial place name matches
            query["query"]["bool"]["must"].append({"wildcard": {"place.name.keyword": f"*{source}*"}})
        elif source_type == "sensor":
            query["query"]["bool"]["must"].append(
                # Must be an exact match; otherwise "v1" would also match "v2", "v3", etc.
                # NOTE: In VA indices this is typically mapped as a keyword already.
                {"term": {"sensor.id": source}}
            )

        return query

    @staticmethod
    def average_speed_per_direction_aggregation() -> dict:
        """
        Aggregation for average speed per direction.

        Exactly matches web-api-core/queryTemplates/averageSpeedPerDirection.json
        Groups by direction and calculates avg speed for each direction.

        Returns:
            Aggregation specification for average speed per direction
        """
        return {
            "directions": {
                "terms": {"field": "direction.keyword", "size": 100},
                "aggs": {"averageSpeed": {"avg": {"field": "speed"}}},
            }
        }

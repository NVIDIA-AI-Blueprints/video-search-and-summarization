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
from collections.abc import AsyncGenerator
from copy import deepcopy
import json
from typing import Any

from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function import FunctionGroup
from nat.cli.register_workflow import register_function_group
from nat.data_models.component_ref import FunctionRef
from nat.data_models.function import FunctionGroupBaseConfig
from pydantic import BaseModel
from pydantic import Field
from pydantic import field_validator
from pydantic import model_validator

from .es_client import BASE_QUERY_TEMPLATE
from .es_client import ESClient
from .query_builders import BehaviorQueryBuilder
from .query_builders import FramesQueryBuilder
from .query_builders import IncidentQueryBuilder
from .utils import build_place_map
from .utils import build_sensor_map
from .utils import compute_bucket_size_seconds
from .utils import create_empty_histogram_buckets
from .utils import create_events_from_incidents
from .utils import parse_vst_sensor_list_response
from .utils import sweep_overlapping_incidents
from .utils import validate_iso_timestamp


# Input models for functions
class EmptyInput(BaseModel):
    """Empty input for functions that take no parameters."""

    pass


class GetSensorIdsInput(BaseModel):
    """Input for get_sensor_ids function."""

    place: str | None = Field(default=None, description="Optional place name to filter sensor IDs")


class GetIncidentInput(BaseModel):
    """Input for get_incident function."""

    id: str = Field(description="The incident ID to retrieve")
    includes: list[str] | None = Field(default=None, description="The metadata fields to include in the output")


class GetIncidentsInputBase(BaseModel):
    """Base input for get_incidents function (without VLM verdict)."""

    source: str | None = Field(
        default=None,
        description="Optional source of the incidents (sensor ID or place/city name). If provided, source_type must also be provided. Place can be exact name or natural language description of place.",
    )
    start_time: str | None = Field(
        default=None,
        description="Optional start time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ). If omitted, returns the most recent incidents up to max_count.",
    )
    end_time: str | None = Field(
        default=None,
        description="Optional end time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ). If omitted, returns the most recent incidents up to max_count.",
    )
    source_type: str | None = Field(
        default=None,
        description="The type of the source (must be 'sensor' or 'place'). 'place' uses wildcard matching and can match city names or intersection names. Required if source is provided.",
    )
    max_count: int = Field(default=10, description="The maximum number of incidents to return")
    includes: list[str] | None = Field(default=None, description="The metadata fields to include in the output")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timestamps(cls, v: str | None) -> str | None:
        """Validate timestamp format."""
        if v is None:
            return None
        return validate_iso_timestamp(v)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str | None) -> str | None:
        """Validate source_type is either 'sensor' or 'place'."""
        if v is not None and v not in ["sensor", "place"]:
            raise ValueError(f"Video Analytics: source_type must be 'sensor' or 'place', got: '{v}'")
        return v

    @model_validator(mode="after")
    def validate_source_and_type_together(self) -> "GetIncidentsInputBase":
        """Validate that source and source_type are provided together."""
        if (self.source is None) != (self.source_type is None):
            raise ValueError("Video Analytics: source and source_type must both be provided or both be omitted")
        return self

    @model_validator(mode="after")
    def validate_timestamps_together(self) -> "GetIncidentsInputBase":
        """Validate that start_time and end_time are provided together."""
        if (self.start_time is None) != (self.end_time is None):
            raise ValueError("Video Analytics: start_time and end_time must both be provided or both be omitted")
        return self


class GetIncidentsInputWithVLM(GetIncidentsInputBase):
    """Extended input for get_incidents function with VLM verdict support."""

    vlm_verdict: str | None = Field(
        default=None,
        description="Optional VLM verdict filter (must be 'all', 'confirmed', 'rejected', 'verification-failed', or 'not-confirmed').",
    )

    @field_validator("vlm_verdict")
    @classmethod
    def validate_vlm_verdict(cls, v: str | None) -> str | None:
        """Validate vlm_verdict is one of the allowed values."""
        if v is not None:
            allowed_verdicts = ["all", "confirmed", "rejected", "verification-failed", "not-confirmed"]
            if v not in allowed_verdicts:
                raise ValueError(f"Video Analytics: vlm_verdict must be one of {allowed_verdicts}, got: '{v}'")
        return v


class FovHistogramInput(BaseModel):
    """Input for get_fov_histogram function."""

    source: str = Field(description="The source of the object counts (sensor ID)")
    start_time: str = Field(description="The start time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)")
    end_time: str = Field(description="The end time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)")
    object_type: str | None = Field(default=None, description="Optional type of the object to filter by")
    bucket_count: int = Field(default=10, description="Number of time buckets for histogram (default: 10)")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timestamps(cls, v: str) -> str:
        """Validate timestamp format."""
        return validate_iso_timestamp(v)


class AverageSpeedsInput(BaseModel):
    """Input for get_average_speeds function."""

    source: str = Field(description="The source of the query (sensor ID or place name)")
    start_time: str = Field(description="The start time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)")
    end_time: str = Field(description="The end time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)")
    source_type: str = Field(description="The type of the source (must be 'sensor' or 'place')")

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timestamps(cls, v: str) -> str:
        """Validate timestamp format."""
        return validate_iso_timestamp(v)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        """Validate source_type is either 'sensor' or 'place'."""
        if v not in ["sensor", "place"]:
            raise ValueError(f"Video Analytics: source_type must be 'sensor' or 'place', got: '{v}'")
        return v


class AnalyzeInput(BaseModel):
    """Input for analyze function."""

    start_time: str = Field(description="The start time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)")
    end_time: str = Field(description="The end time of query (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)")
    source: str = Field(description="The source of the analysis (sensor ID or place name)")
    source_type: str = Field(description="The type of the source (must be 'sensor' or 'place')")
    analysis_type: str = Field(
        description=(
            "Type of analysis to perform (must be one of: 'max_min_incidents', 'average_speed', 'avg_num_people', 'avg_num_vehicles'):\n"
            "- max_min_incidents: Returns both minimum and maximum overlapping incidents\n"
            "- average_speed: Returns average speeds per direction\n"
            "- avg_num_people: Returns average number of people detected over time\n"
            "- avg_num_vehicles: Returns average number of vehicles detected over time"
        )
    )

    @field_validator("start_time", "end_time")
    @classmethod
    def validate_timestamps(cls, v: str) -> str:
        """Validate timestamp format."""
        return validate_iso_timestamp(v)

    @field_validator("source_type")
    @classmethod
    def validate_source_type(cls, v: str) -> str:
        """Validate source_type is either 'sensor' or 'place'."""
        if v not in ["sensor", "place"]:
            raise ValueError(f"Video Analytics: source_type must be 'sensor' or 'place', got: '{v}'")
        return v

    @field_validator("analysis_type")
    @classmethod
    def validate_analysis_type(cls, v: str) -> str:
        """Validate analysis_type is one of the allowed values."""
        allowed_types = ["max_min_incidents", "average_speed", "avg_num_people", "avg_num_vehicles"]
        if v not in allowed_types:
            raise ValueError(f"Video Analytics: analysis_type must be one of {allowed_types}, got: '{v}'")
        return v


class VideoAnalyticsToolConfig(FunctionGroupBaseConfig, name="video_analytics"):
    """Configuration for video analytics tools."""

    es_url: str = Field(default="http://localhost:9200", description="Elasticsearch URL")
    index_prefix: str = Field(default="", description="Index prefix for all ES indexes")
    vlm_verified: bool = Field(
        default=False, description="If true, query VLM verified incidents index instead of regular incidents"
    )
    vst_sensor_list_tool: FunctionRef | None = Field(
        default=None, description="Optional VST sensor list tool to filter active sensors"
    )
    embedding_model_name: str | None = Field(
        default="sentence-transformers/all-MiniLM-L6-v2",
        description="Name of the sentence-transformers model to use for semantic place search. If provided, enables semantic search fallback when wildcard matching returns no results. (default: all-MiniLM-L6-v2, 384 dims)",
    )
    include: list[str] = Field(
        default_factory=lambda: [
            "get_incident",
            "get_incidents",
            "get_sensor_ids",
            "get_places",
            "get_fov_histogram",
            "get_average_speeds",
            "analyze",
        ],
        description="The list of functions to include in the video analytics function group.",
    )


@register_function_group(config_type=VideoAnalyticsToolConfig)
async def video_analytics(_config: VideoAnalyticsToolConfig, _builder: Builder) -> AsyncGenerator[FunctionGroup]:
    """
    Video analytics function group with ES integration.

    Mirrors the web-apis pattern where ES client is initialized once
    and shared across all tool functions.
    """

    # Initialize shared ES client
    es_client = ESClient(_config.es_url, _config.index_prefix)
    group = FunctionGroup(config=_config)

    # Cache calibration data (fetch once and reuse)
    # This avoids repeated ES queries for place/sensor information
    cached_sensors = []
    cached_sensor_map = {}
    cached_place_map = {}

    # Semantic search components
    embedding_model = None
    place_embedding_cache = None

    try:
        calibration_result = await es_client.get_by_id(index_key="calibration", doc_id="calibration")
        if calibration_result:
            calibration = calibration_result.get("calibration", {})
            cached_sensors = calibration.get("sensors", [])
            # Pre-build both maps for efficient lookups
            cached_sensor_map = build_sensor_map(cached_sensors)
            cached_place_map = build_place_map(cached_sensors)

            # Generate embeddings for semantic place search if model is configured
            if _config.embedding_model_name:
                try:
                    from .embeddings import EmbeddingModel
                    from .embeddings import PlaceEmbeddingCache

                    embedding_model = EmbeddingModel(_config.embedding_model_name)
                    place_embedding_cache = PlaceEmbeddingCache()

                    # Collect all place names before batch encoding
                    # cached_place_map structure: {"city": ["intersection1", "intersection2", ...]}
                    all_place_names = []
                    for city, intersections in cached_place_map.items():
                        if city:
                            all_place_names.append(city)
                        for intersection in intersections:
                            if intersection:
                                all_place_names.append(intersection)

                    # Batch encode all places at once
                    if all_place_names:
                        all_embeddings = embedding_model.encode_batch(all_place_names)
                        place_embedding_cache.add_places_batch(all_place_names, all_embeddings)

                except Exception:
                    # Silently disable semantic search if initialization fails
                    embedding_model = None
                    place_embedding_cache = None
    except Exception:
        # Log error but continue - functions will return empty results
        pass

    async def _get_vst_sensor_names() -> set[str] | None:
        """
        Fetch active sensor names from VST tool.

        Returns:
            Set of active sensor names, or None if VST is not configured or fails
        """
        if not _config.vst_sensor_list_tool:
            return None

        try:
            sensor_list_tool = await _builder.get_tool(
                _config.vst_sensor_list_tool, wrapper_type=LLMFrameworkEnum.LANGCHAIN
            )
            sensors_str = await sensor_list_tool.ainvoke(input={})

            # Parse sensor list response using helper function
            result = parse_vst_sensor_list_response(sensors_str)
            return result
        except (json.JSONDecodeError, KeyError, ConnectionError, ValueError):
            return None
        except Exception:
            return None

    def _semantic_place_search(query: str) -> list[str]:
        """
        Find places semantically similar to the query using cached embeddings.

        Uses hardcoded parameters:
        - threshold: 0.5 (minimum cosine similarity)
        - top_k: 5 (maximum number of matches)

        Args:
            query: The search query text

        Returns:
            List of place names that match semantically
        """
        # Hardcoded parameters for semantic search
        semantic_threshold = 0.5
        semantic_top_k = 3

        # Check if semantic search is available
        if embedding_model is None or place_embedding_cache is None:
            return []

        try:
            # Generate query embedding
            query_embedding = embedding_model.encode(query)

            # Find similar places
            results = place_embedding_cache.find_similar(
                query_embedding, top_k=semantic_top_k, threshold=semantic_threshold
            )

            # Return just the place names
            return [place_name for place_name, _score in results]
        except Exception:
            return []

    async def _get_incident(input: GetIncidentInput) -> dict:
        """
        Get a specific incident by ID from the video analytics system.

        Returns the complete incident data including all available fields unless limited by the includes parameter.

        Args:
            input: Input parameters including incident id and optional includes

        Returns:
            dict: The incident data, or None if not found
        """
        query = IncidentQueryBuilder.build_query_by_id(incident_id=input.id)

        # Choose index based on config vlm_verified setting
        index_key = "vlm_incidents" if _config.vlm_verified else "incidents"

        # Default fields to include
        incident_fields = ["Id", "id", "timestamp", "end", "sensorId"]

        # Add additional fields based on includes parameter
        if input.includes:
            for metadata in input.includes:
                incident_fields.append(metadata)

        # Query for single incident
        incidents = await es_client.search(
            index_key=index_key, query_body=query, size=1, source_includes=incident_fields
        )

        # Return the incident if found, otherwise None
        return incidents[0] if incidents else {}

    async def _get_incidents(input: GetIncidentsInputBase) -> dict:
        """
        Get incidents from the video analytics system. By default, only the most recent 10 incidents will be returned.
        Each incident only contains the incident id, start time, end time and sensor id unless additional fields are requested via includes.

        If source and source_type are omitted, all incidents will be queried (filtered by time range if provided).
        If start_time and end_time are omitted, returns the most recent incidents up to max_count.

        Args:
            input: Input parameters including optional source, optional source_type, optional start_time, optional end_time, max_count, and includes. If vlm_verified is enabled, also includes vlm_verdict.

        Returns:
            dict: A dictionary containing the list of incidents and a flag indicating if there are more incidents available
        """
        # Get vlm_verdict if it exists on the input model (only present when vlm_verified=True)
        vlm_verdict = getattr(input, "vlm_verdict", None)

        # Build query using IncidentQueryBuilder
        # If source/source_type are None, query all incidents
        if input.source is not None and input.source_type is not None:
            query = IncidentQueryBuilder.build_query(
                source=input.source,
                source_type=input.source_type,
                start_time=input.start_time,
                end_time=input.end_time,
                vlm_verified=_config.vlm_verified,
                vlm_verdict=vlm_verdict,
            )
        else:
            # Query all incidents without source filter
            query = IncidentQueryBuilder.build_query(
                source=None,
                source_type=None,
                start_time=input.start_time,
                end_time=input.end_time,
                vlm_verified=_config.vlm_verified,
                vlm_verdict=vlm_verdict,
            )

        # Choose index based on config vlm_verified setting
        index_key = "vlm_incidents" if _config.vlm_verified else "incidents"

        # Fetch extra records to check if there are more
        fetch_size = input.max_count + 1

        # Default fields to include
        incident_fields = ["Id", "id", "timestamp", "end", "sensorId"]

        # Add additional fields based on includes parameter
        if input.includes:
            for metadata in input.includes:
                incident_fields.append(metadata)

        # Sort by timestamp descending (most recent first)
        incidents = await es_client.search(
            index_key=index_key,
            query_body=query,
            size=fetch_size,
            sort="timestamp:desc",
            source_includes=incident_fields,
        )

        # If no results and semantic search is available, try semantic fallback
        if (
            len(incidents) == 0
            and input.source is not None
            and input.source_type == "place"
            and embedding_model is not None
            and place_embedding_cache is not None
        ):
            # Find semantically similar places
            matched_places = _semantic_place_search(input.source)

            if matched_places:
                # Build new query with OR clause for all matched places
                # Use term queries for exact matching on the matched place names
                query = deepcopy(BASE_QUERY_TEMPLATE)

                # Add time range filters if provided
                if input.start_time is not None and input.end_time is not None:
                    query["query"]["bool"]["must"].extend(
                        [
                            {"range": {"timestamp": {"lte": input.end_time}}},
                            {"range": {"end": {"gte": input.start_time}}},
                        ]
                    )

                # Add should clause with all matched places (at least one must match)
                # Use wildcard matching since place names are stored as "city=X/intersection=Y"
                query["query"]["bool"]["should"] = [
                    {"wildcard": {"place.name.keyword": f"*{place_name}*"}} for place_name in matched_places
                ]
                query["query"]["bool"]["minimum_should_match"] = 1

                # Add VLM verdict filter if applicable
                if _config.vlm_verified and vlm_verdict is not None:
                    if vlm_verdict == "all":
                        pass
                    elif vlm_verdict == "not-confirmed":
                        query["query"]["bool"]["must"].append(
                            {"terms": {"info.verdict.keyword": ["rejected", "verification-failed"]}}
                        )
                    else:
                        query["query"]["bool"]["must"].append({"term": {"info.verdict.keyword": vlm_verdict}})

                # Execute semantic search query
                incidents = await es_client.search(
                    index_key=index_key,
                    query_body=query,
                    size=fetch_size,
                    sort="timestamp:desc",
                    source_includes=incident_fields,
                )

        # Apply pagination
        paginated_incidents = incidents[0 : input.max_count]
        has_more = len(incidents) > input.max_count

        return {"incidents": paginated_incidents, "has_more": has_more}

    async def _get_sensor_ids(input: GetSensorIdsInput) -> list[str]:
        """
        Get the list of sensor IDs from calibration configuration, optionally filtered by place.

        If VST sensor list is available, returns sensors that are either:
        - In calibration data AND in VST active list
        - In VST active list but NOT in calibration data (appended to the result)

        Args:
            input: Input parameters including optional place filter

        Returns:
            list[str]: List of sensor IDs
        """
        # Use cached calibration data, or fetch on-demand if cache is empty
        sensors = cached_sensors
        sensor_map = cached_sensor_map

        if not sensors:
            # Fallback: fetch calibration data from ES if cache is empty
            calibration_result = await es_client.get_by_id(index_key="calibration", doc_id="calibration")

            if not calibration_result:
                # No calibration data, return VST sensors as list
                vst_sensors = await _get_vst_sensor_names()
                return list(vst_sensors) if vst_sensors else []

            calibration = calibration_result.get("calibration", {})
            sensors = calibration.get("sensors", [])
            sensor_map = build_sensor_map(sensors)

        # Get active sensors from VST (fetched on-demand each time)
        active_sensor_names = await _get_vst_sensor_names()

        # If place filter is specified, use sensor map
        if input.place:
            # Search all cities for the specified intersection (place)
            for _city, intersections in sensor_map.items():
                if input.place in intersections:
                    sensor_ids = intersections[input.place]
                    # Filter by active sensors if available
                    if active_sensor_names is not None:
                        sensor_ids = [sid for sid in sensor_ids if sid in active_sensor_names]
                    return sensor_ids
            return []
        else:
            # Return all sensor IDs
            sensor_ids = [sensor.get("id") for sensor in sensors if "id" in sensor]
            # Filter by active sensors if available, then append any VST-only sensors
            if active_sensor_names is not None:
                # First, filter calibration sensors to those in VST active list
                filtered_ids = [sid for sid in sensor_ids if sid in active_sensor_names]

                # Then append any sensors from VST that aren't in calibration data
                calibration_sensor_ids = set(sensor_ids)
                vst_only_sensors = [sid for sid in active_sensor_names if sid not in calibration_sensor_ids]

                sensor_ids = filtered_ids + vst_only_sensors

            return sensor_ids

    async def _get_places(input: EmptyInput) -> dict:  # noqa: ARG001
        """
        Get the hierarchical map of all available places.

        Returns the place_map structure: city -> [intersection]

        Args:
            input: Empty input (no parameters required)

        Returns:
            dict: Hierarchical place map with structure:
                  {
                      "city_name": ["intersection1", "intersection2", ...],
                      ...
                  }
        """
        # Use cached place map, or fetch on-demand if cache is empty
        place_map = cached_place_map

        if not place_map:
            # Fallback: fetch calibration data from ES if cache is empty
            calibration_result = await es_client.get_by_id(index_key="calibration", doc_id="calibration")

            if not calibration_result:
                return {}

            calibration = calibration_result.get("calibration", {})
            sensors = calibration.get("sensors", [])
            place_map = build_place_map(sensors)

        return place_map

    async def _get_fov_histogram(input: FovHistogramInput) -> dict:
        """
        Returns FOV occupancy histogram with time buckets and object types.

        Uses frames index with nested fov field.

        Args:
            input: Input parameters including source, start_time, end_time, optional object_type, and bucket_count

        Returns:
            dict: Histogram with structure:
                {
                    "bucketSizeInSec": 180,
                    "histogram": [
                        {
                            "start": "2023-01-12T11:20:10.000Z",
                            "end": "2023-01-12T11:23:10.000Z",
                            "objects": [
                                {"type": "Person", "averageCount": 5},
                                {"type": "Vehicle", "averageCount": 2}
                            ]
                        },
                        ...
                    ]
                }
        """
        # Compute bucket size
        bucket_size_sec = compute_bucket_size_seconds(
            start_time=input.start_time, end_time=input.end_time, bucket_count=input.bucket_count
        )

        # Build query using FramesQueryBuilder (matches web-apis pattern)
        query = FramesQueryBuilder.build_query(
            sensor_id=input.source, start_time=input.start_time, end_time=input.end_time
        )

        # Build FOV histogram aggregation
        aggs = FramesQueryBuilder.fov_histogram_aggregation(
            bucket_size_sec=bucket_size_sec, object_type=input.object_type
        )

        # Execute aggregation on frames index
        results = await es_client.aggregate(index_key="frames", query_body=query, aggs=aggs)

        # Collect all object types seen across all buckets
        object_types = set()
        bucket_map = {}

        if results and "eventsOverTime" in results:
            for time_bucket in results["eventsOverTime"].get("buckets", []):
                start_time_str = time_bucket.get("key_as_string", time_bucket["key"])

                # Parse start time and compute end time
                from datetime import datetime
                from datetime import timedelta

                start_dt = datetime.fromisoformat(start_time_str.replace("Z", "+00:00"))
                end_dt = start_dt + timedelta(seconds=bucket_size_sec)

                # Format with milliseconds explicitly (isoformat() omits .000 when microseconds are 0)
                start_str = start_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
                end_str = end_dt.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

                objects_data: list[dict[str, Any]] = []
                bucket_data: dict[str, Any] = {"start": start_str, "end": end_str, "objects": objects_data}

                # Navigate nested aggregation structure: fov.searchAggFilter.objectType.buckets
                # This matches the web-apis nested aggregation on fov field
                fov_agg = time_bucket.get("fov", {})
                search_filter = fov_agg.get("searchAggFilter", {})
                object_type_buckets = search_filter.get("objectType", {}).get("buckets", [])

                for obj_bucket in object_type_buckets:
                    obj_type = obj_bucket["key"]
                    avg_count = obj_bucket["avgCount"]["value"]

                    objects_data.append({"type": obj_type, "averageCount": round(avg_count) if avg_count else 0})
                    object_types.add(obj_type)

                bucket_map[bucket_data["start"]] = bucket_data

        # Create empty histogram covering full time range
        empty_histogram = create_empty_histogram_buckets(
            start_time=input.start_time, end_time=input.end_time, bucket_size_sec=bucket_size_sec
        )

        # Fill in data from bucket_map and ensure all object types appear in all buckets
        histogram: list[dict[str, Any]] = []
        for empty_bucket in empty_histogram:
            if empty_bucket["start"] in bucket_map:
                # Use bucket with data
                bucket: dict[str, Any] = bucket_map[empty_bucket["start"]]
            else:
                # Use empty bucket
                bucket = empty_bucket

            # Ensure all object types are represented (with 0 if missing)
            objects_list: list[dict[str, Any]] = bucket["objects"]
            existing_types = {obj["type"] for obj in objects_list}
            for obj_type in object_types:
                if obj_type not in existing_types:
                    objects_list.append({"type": obj_type, "averageCount": 0})

            # Sort objects by type for consistency
            objects_list.sort(key=lambda x: x["type"])
            histogram.append(bucket)

        return {"bucketSizeInSec": bucket_size_sec, "histogram": histogram}

    async def _get_average_speeds(input: AverageSpeedsInput) -> dict:
        """
        Returns average speed per direction at source.

        Queries behavior index and groups by direction.

        Args:
            input: Input parameters including source, start_time, end_time, and source_type

        Returns:
            dict: Average speed metrics per direction
                {
                    "metrics": [
                        {"direction": "North", "averageSpeed": "25 mph"},
                        {"direction": "South", "averageSpeed": "30 mph"}
                    ]
                }
        """
        # Build query exactly matching web-apis (lines 109-126 in Behavior.js)
        query = BehaviorQueryBuilder.build_average_speed_query(
            source=input.source, source_type=input.source_type, start_time=input.start_time, end_time=input.end_time
        )

        # Build aggregation matching averageSpeedPerDirection.json
        aggs = BehaviorQueryBuilder.average_speed_per_direction_aggregation()

        # Execute aggregation
        results = await es_client.aggregate(index_key="behavior", query_body=query, aggs=aggs)

        # Format results matching web-apis output (lines 130-143 in Behavior.js)
        metrics = []
        if results and "directions" in results:
            for direction_bucket in results["directions"].get("buckets", []):
                direction = direction_bucket["key"]
                avg_speed_value = direction_bucket.get("averageSpeed", {}).get("value")

                # Format speed with unit (web-apis uses mph for cartesian, assuming mph here)
                # In web-apis line 290: result.averageSpeed = `${Math.floor(result.avgSpeedDetails.averageSpeed)} ${averageSpeedUnit}`;
                if avg_speed_value is not None:
                    speed_str = f"{int(avg_speed_value)} mph"
                else:
                    speed_str = "0 mph"

                metrics.append({"direction": direction, "averageSpeed": speed_str})

        return {"metrics": metrics}

    async def _analyze(input: AnalyzeInput) -> str:
        """
        Analyze the incidents in the video analytics system.

        Args:
            input: Input parameters including start_time, end_time, source, source_type, and analysis_type

        Returns:
            str: The analysis result in natural language
        """
        if input.analysis_type == "max_min_incidents":
            # Build query for incidents
            query = IncidentQueryBuilder.build_query(
                source=input.source, source_type=input.source_type, start_time=input.start_time, end_time=input.end_time
            )

            # Choose index based on config vlm_verified setting
            index_key = "vlm_incidents" if _config.vlm_verified else "incidents"

            # Limit analysis to most recent 1000 incidents
            # Fetch +1 to detect if there are more incidents
            max_incidents_to_analyze = 1000
            fetch_size = max_incidents_to_analyze + 1

            # Fetch incidents with timestamp and end times, sorted by most recent first
            all_incidents = await es_client.search(
                index_key=index_key,
                query_body=query,
                size=fetch_size,
                sort="timestamp:desc",
                source_includes=["timestamp", "end"],
            )

            if not all_incidents:
                return f"Between {input.start_time} and {input.end_time}, there were no incidents at {input.source}."

            # Check if there are more incidents beyond our analysis window
            has_more = len(all_incidents) > max_incidents_to_analyze
            incidents = all_incidents[:max_incidents_to_analyze]

            # Use utility function to convert incidents to events
            events, valid_incident_count = create_events_from_incidents(incidents)

            if valid_incident_count == 0:
                return f"Between {input.start_time} and {input.end_time}, there were no valid incidents with timestamps at {input.source}."

            # Sweep through events to find BOTH minimum and maximum overlapping counts in single pass
            max_count, max_time, min_count, min_time = sweep_overlapping_incidents(events)

            # Format response with both min and max
            more_msg = f" (analyzed most recent {valid_incident_count} incidents; more exist)" if has_more else ""

            # Build comprehensive response
            result_parts = [
                f"Between {input.start_time} and {input.end_time}, there were a total of {valid_incident_count} incidents analyzed at {input.source}{more_msg}."
            ]

            # Add maximum overlap information
            max_time_str = max_time.strftime("%Y-%m-%d %H:%M:%S") if max_time else "during the period"
            result_parts.append(f"Maximum overlap: {max_count} incident(s) at {max_time_str}.")

            # Add minimum overlap information
            min_time_str = min_time.strftime("%Y-%m-%d %H:%M:%S") if min_time else "during the period"
            result_parts.append(f"Minimum overlap: {min_count} incident(s) at {min_time_str}.")

            return " ".join(result_parts)

        elif input.analysis_type == "average_speed":
            # Get average speed per direction
            result = await _get_average_speeds(
                AverageSpeedsInput(
                    source=input.source,
                    start_time=input.start_time,
                    end_time=input.end_time,
                    source_type=input.source_type,
                )
            )
            metrics = result.get("metrics", [])
            if metrics:
                speed_summary = ", ".join([f"{m['direction']}: {m['averageSpeed']}" for m in metrics])
                return (
                    f"Average speeds at {input.source} between {input.start_time} and {input.end_time}: {speed_summary}"
                )
            else:
                return f"No speed data available at {input.source} between {input.start_time} and {input.end_time}."

        elif input.analysis_type == "avg_num_people":
            # Get average number of people over the time period
            result = await _get_fov_histogram(
                FovHistogramInput(
                    source=input.source, start_time=input.start_time, end_time=input.end_time, object_type="Person"
                )
            )
            # Extract objects from histogram buckets
            histogram = result.get("histogram", [])
            person_counts = []
            for bucket in histogram:
                for obj in bucket.get("objects", []):
                    if obj["type"] == "Person":
                        person_counts.append(obj["averageCount"])

            if person_counts:
                overall_average = sum(person_counts) / len(person_counts)
                return f"The average number of people at {input.source} between {input.start_time} and {input.end_time} was {overall_average:.2f}."
            return f"No people detected at {input.source} between {input.start_time} and {input.end_time}."

        elif input.analysis_type == "avg_num_vehicles":
            # Get average number of vehicles over the time period
            result = await _get_fov_histogram(
                FovHistogramInput(
                    source=input.source, start_time=input.start_time, end_time=input.end_time, object_type="Vehicle"
                )
            )
            # Extract objects from histogram buckets
            histogram = result.get("histogram", [])
            vehicle_counts = []
            for bucket in histogram:
                for obj in bucket.get("objects", []):
                    if obj["type"] == "Vehicle":
                        vehicle_counts.append(obj["averageCount"])

            if vehicle_counts:
                overall_average = sum(vehicle_counts) / len(vehicle_counts)
                return f"The average number of vehicles at {input.source} between {input.start_time} and {input.end_time} was {overall_average:.2f}."
            return f"No vehicles detected at {input.source} between {input.start_time} and {input.end_time}."

        return f"Unknown analysis type: {input.analysis_type}"

    # Register functions based on config
    if "get_incident" in _config.include:
        group.add_function(name="get_incident", fn=_get_incident, description=_get_incident.__doc__)

    if "get_incidents" in _config.include:
        # When vlm_verified=True, include vlm_verdict parameter otherwise, exclude vlm_verdict parameter completely
        if _config.vlm_verified:

            async def _get_incidents_vlm(input: GetIncidentsInputWithVLM) -> dict:
                return await _get_incidents(input)

            group.add_function(name="get_incidents", fn=_get_incidents_vlm, description=_get_incidents.__doc__)
        else:

            async def _get_incidents_base(input: GetIncidentsInputBase) -> dict:
                return await _get_incidents(input)

            group.add_function(name="get_incidents", fn=_get_incidents_base, description=_get_incidents.__doc__)

    if "get_sensor_ids" in _config.include:
        group.add_function(name="get_sensor_ids", fn=_get_sensor_ids, description=_get_sensor_ids.__doc__)

    if "get_places" in _config.include:
        group.add_function(name="get_places", fn=_get_places, description=_get_places.__doc__)

    if "get_fov_histogram" in _config.include:
        group.add_function(name="get_fov_histogram", fn=_get_fov_histogram, description=_get_fov_histogram.__doc__)

    if "get_average_speeds" in _config.include:
        group.add_function(name="get_average_speeds", fn=_get_average_speeds, description=_get_average_speeds.__doc__)

    if "analyze" in _config.include:
        group.add_function(name="analyze", fn=_analyze, description=_analyze.__doc__)

    yield group

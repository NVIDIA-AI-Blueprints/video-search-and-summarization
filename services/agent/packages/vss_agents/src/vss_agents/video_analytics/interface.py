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
from abc import ABC
from abc import abstractmethod
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_search.data_models.nvschema import Incident


class IncidentMetadata(StrEnum):
    PLACE = "place"
    CATEGORY = "category"
    IS_ANOMALY = "isAnomaly"
    OBJECT_IDS = "objectIds"
    FRAME_IDS = "frameIds"
    ANALYTICS_MODULE = "analyticsModule"
    TYPE = "type"
    INFO = "info"


class VideoAnalyticsInterface(ABC):
    """
    Interface class for video analytics system.
    """

    @abstractmethod
    async def get_incident(
        self,
        id: str,
        *,
        includes: list[IncidentMetadata] | None = None,
    ) -> "Incident | None":
        """
        Get a specific incident by ID from the video analytics system.

        Returns the complete incident data including all available fields unless limited by the includes parameter.

        Input:
            id: str
                The incident ID to retrieve.
            includes: list[IncidentMetadata] | None
                The metadata fields to include in the output.

        Output:
            Incident | None: The incident data, or None if not found.
        """
        pass

    @abstractmethod
    async def get_incidents(
        self,
        start_time: str | None = None,
        end_time: str | None = None,
        *,
        source: str | None = None,
        source_type: str | None = None,  # Must be "sensor" or "place" if source is provided
        max_count: int = 10,
        includes: list[IncidentMetadata] | None = None,
        vlm_verdict: str
        | None = None,  # Must be "all", "confirmed", "rejected", "verification-failed", or "not-confirmed"
    ) -> tuple[list["Incident"], bool]:
        """
        Get incidents from the video analytics system. By default, only the most recent 10 incidents will be returned and
        each incident only contains the incident id, start time, end time and sensor id unless additional fields are requested via includes.

        If source and source_type are omitted, all incidents will be queried (filtered by time range if provided).
        If start_time and end_time are omitted, returns the most recent incidents up to max_count.

        Input:
            start_time: str | None
                Optional start time of the incidents (ISO format). If omitted, returns the most recent incidents up to max_count.
            end_time: str | None
                Optional end time of the incidents (ISO format). If omitted, returns the most recent incidents up to max_count.
            source: str | None
                Optional source of the incidents (sensor ID or place/city name). If provided, source_type must also be provided.
            source_type: Literal["sensor", "place"] | None
                The type of the source. 'place' uses wildcard matching and can match city names or intersection names. Required if source is provided.
            max_count: int
                The maximum number of incidents to return.
            includes: list[IncidentMetadata]
                The metadata to be included in the output.
            vlm_verdict: Literal["all", "confirmed", "rejected", "verification-failed", "not-confirmed"] | None
                Optional VLM verdict filter. Can only be used when vlm_verified config is enabled.
        Output:
            (list[Incident], bool): The list of incidents and a boolean flag indicating if there are more incidents available.
        """
        pass

    @abstractmethod
    async def get_sensor_ids(self, place: str | None = None) -> list[str]:
        """
        Get the list of sensor IDs from calibration configuration, optionally filtered by place.

        Input:
            place: str | None
                Optional place name to filter sensor IDs

        Output:
            list[str]: List of sensor IDs
        """
        pass

    @abstractmethod
    async def get_places(self) -> dict:
        """
        Get the hierarchical map of all available places

        Returns the place_map structure: city -> [intersection]

        Output:
            dict: Hierarchical place map with structure:
                {
                    "city_name": ["intersection1", "intersection2", ...],
                    ...
                }
        """
        pass

    @abstractmethod
    async def get_fov_histogram(
        self,
        source: str,
        start_time: str,
        end_time: str,
        object_type: str | None = None,
        bucket_count: int = 10,
    ) -> dict:
        """
        Returns FOV occupancy histogram with time buckets showing object counts over time.

        Queries frames index with nested fov field.

        Input:
            source: str
                The source of the object counts (sensor ID).
            start_time: str
                The start time of query (ISO format).
            end_time: str
                The end time of query (ISO format).
            object_type: str | None
                Optional type of the object to filter by.
            bucket_count: int
                Number of time buckets for histogram (default: 10).

        Output:
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
        pass

    @abstractmethod
    async def get_average_speeds(
        self,
        source: str,
        start_time: str,
        end_time: str,
        source_type: str,  # Must be "sensor" or "place"
    ) -> dict:
        """
        Returns average speed per direction at source.

        Queries behavior index and groups by direction.

        Input:
            source: str
                The source of the query (sensor ID or place name).
            start_time: str
                The start time of query (ISO format).
            end_time: str
                The end time of query (ISO format).
            source_type: Literal["sensor", "place"]
                The type of the source.

        Output:
            dict: Average speed metrics per direction
                {
                    "metrics": [
                        {"direction": "North", "averageSpeed": "25 mph"},
                        {"direction": "South", "averageSpeed": "30 mph"}
                    ]
                }
        """
        pass

    @abstractmethod
    async def analyze(
        self,
        start_time: str,
        end_time: str,
        source: str,
        source_type: str,  # Must be "sensor" or "place"
        analysis_type: str,  # Must be one of: "max_min_incidents", "average_speed", "avg_num_people", "avg_num_vehicles"
    ) -> str:
        """
        Analyze the incidents in the video analytics system.
        Input:
            start_time: str
                The start time of the incidents.
            end_time: str
                The end time of the incidents.
            source: str
            source_type: str
                The type of the source. Must be "sensor" or "place".
            analysis_type: str
                The type of the analysis. Must be one of: "max_min_incidents", "average_speed", "avg_num_people", "avg_num_vehicles".
                - max_min_incidents: Returns both min and max overlapping incidents
                - average_speed: Returns average speeds per direction
                - avg_num_people: Returns average number of people detected over time
                - avg_num_vehicles: Returns average number of vehicles detected over time
        Output:
            str: The analysis result in natural language.
        """
        pass

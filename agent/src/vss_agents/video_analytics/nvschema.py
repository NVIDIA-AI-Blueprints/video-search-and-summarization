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
from typing import Any

from pydantic import BaseModel
from pydantic import ConfigDict
from pydantic import Field


class Location(BaseModel):
    latitude: float = Field(0, description="Latitude of the location", alias="lat")
    longitude: float = Field(0, description="Longitude of the location", alias="lon")
    altitude: float = Field(0, description="Altitude of the location", alias="alt")


class Coordinates(BaseModel):
    latitude: float = Field(0, description="Latitude of the coordinates", alias="lat")
    longitude: float = Field(0, description="Longitude of the coordinates", alias="lon")
    altitude: float = Field(0, description="Altitude of the coordinates", alias="alt")


class Place(BaseModel):
    id: str = Field("...", description="ID of the place where the incident occurred", alias="id")
    name: str = Field("...", description="Name of the place where the incident occurred", alias="name")
    place_type: str = Field("...", description="Type of the place where the incident occurred", alias="type")
    location: Location | None = Field(None, description="Location of the place where the incident occurred")
    coordinates: Coordinates | None = Field(None, description="Coordinates of the place where the incident occurred")


class Incident(BaseModel):
    """
    Pydantic model for NVSchema Incident.

    This model is used to represent incidents from the video analytics system.
    It contains both required fields (always present) and optional metadata fields.
    """

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    # Required fields (always included)
    id: str = Field("...", description="Incident ID", alias="Id")
    sensor_id: str = Field("...", description="Sensor ID where the incident occurred", alias="sensorId")
    start_time: str = Field("...", description="Start time of the incident (ISO format)", alias="timestamp")
    end_time: str = Field("...", description="End time of the incident (ISO format)", alias="end")

    # Optional metadata fields (included based on 'includes' parameter)
    place: Place | None = Field(None, description="Place where the incident occurred")
    category: str | None = Field(None, description="Category of the incident")
    object_ids: list[str] | None = Field(
        None,
        description="Array of object IDs involved in the incident",
        alias="objectIds",
    )
    frame_ids: list[str] | None = Field(
        None,
        description="Array of frame IDs associated with the incident",
        alias="frameIds",
    )
    analytics_module: str | None = Field(
        None, description="Analytics module that detected the incident", alias="analyticsModule"
    )
    info: dict[str, Any] | None = Field(None, description="Additional incident information")
    incident_type: str | None = Field(None, description="Type of the incident", alias="type")
    is_anomaly: bool | None = Field(None, description="Whether the incident is an anomaly", alias="isAnomaly")

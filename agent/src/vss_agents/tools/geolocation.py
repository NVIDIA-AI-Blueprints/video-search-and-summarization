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
import logging
from typing import Any

import aiohttp
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class GeolocationConfig(FunctionBaseConfig, name="geolocation"):
    """Configuration for the geolocation information tool."""

    timeout: int = Field(default=10, description="Request timeout in seconds for the OpenStreetMap API call.")


class GeolocationInput(BaseModel):
    """Input for the geolocation information tool."""

    latitude: float = Field(..., description="Latitude coordinate of the location")
    longitude: float = Field(..., description="Longitude coordinate of the location")


class GeolocationOutput(BaseModel):
    """Output from the geolocation information tool."""

    # Reference: https://nominatim.org/release-docs/latest/api/Output/#geocodejson
    type: str | None = Field(
        default=None,
        description="The 'address level' of the object (house, street, district, city, county, state, country, locality). ",
    )
    city: str | None = Field(default=None, description="City name where the coordinates are located. ")
    county: str | None = Field(default=None, description="County name where the coordinates are located. ")
    state: str | None = Field(default=None, description="State name where the coordinates are located. ")
    country: str | None = Field(default=None, description="Country name where the coordinates are located. ")
    road: str | None = Field(default=None, description="Road name where the coordinates are located. ")
    speed_limit: str | None = Field(default=None, description="Speed limit at the location. ")
    full_address: str | None = Field(default=None, description="Full address of the location. ")
    category: str | None = Field(
        default=None,
        description="OpenStreetMap feature category defining the broad type (e.g. boundary, highway, amenity). ",
    )
    subtype_within_category: str | None = Field(
        default=None,
        description="Specific feature subtype (e.g. residential, restaurant) within the OpenStreetMap category. ",
    )


@register_function(config_type=GeolocationConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def geolocation(config: GeolocationConfig, __builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """Tool for getting geolocation information from latitude and longitude coordinates."""

    def _extract_location_info(geo_data: dict[str, Any]) -> dict[str, Any]:
        """Extract structured location information from GeocodeJSON response."""
        try:
            geocoding = geo_data["features"][0]["properties"]["geocoding"]
        except Exception:
            return {
                "type": None,
                "city": None,
                "county": None,
                "state": None,
                "country": None,
                "road": None,
                "speed_limit": None,
                "full_address": None,
                "category": None,
                "subtype_within_category": None,
            }

        speed_limit = (geocoding.get("extra") or {}).get("maxspeed", None)
        # Convert speed_limit to string
        speed_limit = None if speed_limit is None else str(speed_limit)

        return {
            "type": geocoding.get("type", None),
            "city": geocoding.get("city", None),
            "county": geocoding.get("county", None),
            "state": geocoding.get("state", None),
            "country": geocoding.get("country", None),
            "road": geocoding.get("name", None),
            "speed_limit": speed_limit,
            "full_address": geocoding.get("label", None),
            "category": geocoding.get("osm_key", None),
            "subtype_within_category": geocoding.get("osm_value", None),
        }

    async def _geolocation(geo_input: GeolocationInput) -> GeolocationOutput:
        """
        Get geolocation information from latitude and longitude coordinates.

        Returns: Location information including road details, speed limits, and OpenStreetMap feature classification.
        """

        async with aiohttp.ClientSession() as session:
            # Get reverse geocoding information from OpenStreetMap
            url = "https://nominatim.openstreetmap.org/reverse"
            params: dict[str, str | int | float] = {
                "lat": geo_input.latitude,
                "lon": geo_input.longitude,
                "format": "geocodejson",
                "addressdetails": 1,
                "extratags": 1,
            }
            headers = {"User-Agent": "GeoLocation-Tool/1.0"}

            try:
                async with session.get(
                    url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=config.timeout)
                ) as response:
                    if response.status == 200:
                        geo_data = await response.json()
                    else:
                        raise RuntimeError(
                            f"Failed to fetch location data: Nominatim API returned HTTP {response.status}. "
                        )
            except Exception as e:
                raise RuntimeError(f"Failed to fetch location data: {e}") from e

        location_info = _extract_location_info(geo_data)

        return GeolocationOutput(
            type=location_info["type"],
            city=location_info["city"],
            county=location_info["county"],
            state=location_info["state"],
            country=location_info["country"],
            road=location_info["road"],
            speed_limit=location_info["speed_limit"],
            full_address=location_info["full_address"],
            category=location_info["category"],
            subtype_within_category=location_info["subtype_within_category"],
        )

    function_info = FunctionInfo.create(
        single_fn=_geolocation,
        description=config.__doc__,
        input_schema=GeolocationInput,
        single_output_schema=GeolocationOutput,
    )

    yield function_info

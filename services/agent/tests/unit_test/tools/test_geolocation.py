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
"""Unit tests for geolocation module."""

from vss_agents.tools.geolocation import GeolocationConfig
from vss_agents.tools.geolocation import GeolocationInput
from vss_agents.tools.geolocation import GeolocationOutput


class TestGeolocationConfig:
    """Test GeolocationConfig model."""

    def test_config_defaults(self):
        config = GeolocationConfig()
        assert config.timeout == 10

    def test_config_custom_timeout(self):
        config = GeolocationConfig(timeout=30)
        assert config.timeout == 30


class TestGeolocationInput:
    """Test GeolocationInput model."""

    def test_input_creation(self):
        input_data = GeolocationInput(latitude=37.7749, longitude=-122.4194)
        assert input_data.latitude == 37.7749
        assert input_data.longitude == -122.4194

    def test_input_zero_coordinates(self):
        input_data = GeolocationInput(latitude=0.0, longitude=0.0)
        assert input_data.latitude == 0.0
        assert input_data.longitude == 0.0

    def test_input_negative_coordinates(self):
        input_data = GeolocationInput(latitude=-33.8688, longitude=151.2093)
        assert input_data.latitude == -33.8688
        assert input_data.longitude == 151.2093

    def test_input_extreme_latitude(self):
        input_data = GeolocationInput(latitude=90.0, longitude=0.0)
        assert input_data.latitude == 90.0

    def test_input_extreme_longitude(self):
        input_data = GeolocationInput(latitude=0.0, longitude=180.0)
        assert input_data.longitude == 180.0


class TestGeolocationOutput:
    """Test GeolocationOutput model."""

    def test_output_defaults(self):
        output = GeolocationOutput()
        assert output.type is None
        assert output.city is None
        assert output.county is None
        assert output.state is None
        assert output.country is None
        assert output.road is None
        assert output.speed_limit is None
        assert output.full_address is None
        assert output.category is None
        assert output.subtype_within_category is None

    def test_output_full_data(self):
        output = GeolocationOutput(
            type="street",
            city="San Francisco",
            county="San Francisco County",
            state="California",
            country="United States",
            road="Market Street",
            speed_limit="25 mph",
            full_address="123 Market Street, San Francisco, CA 94102",
            category="highway",
            subtype_within_category="residential",
        )
        assert output.type == "street"
        assert output.city == "San Francisco"
        assert output.state == "California"
        assert output.country == "United States"
        assert output.road == "Market Street"
        assert output.speed_limit == "25 mph"

    def test_output_partial_data(self):
        output = GeolocationOutput(
            city="New York",
            country="United States",
        )
        assert output.city == "New York"
        assert output.country == "United States"
        assert output.state is None

    def test_output_serialization(self):
        output = GeolocationOutput(city="Tokyo", country="Japan")
        data = output.model_dump()
        assert data["city"] == "Tokyo"
        assert data["country"] == "Japan"
        assert data["state"] is None

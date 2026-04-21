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
"""Unit tests for nvschema module."""

from vss_agents.video_analytics.nvschema import Coordinates
from vss_agents.video_analytics.nvschema import Incident
from vss_agents.video_analytics.nvschema import Location
from vss_agents.video_analytics.nvschema import Place


class TestLocation:
    """Test Location model."""

    def test_location_defaults(self):
        loc = Location()
        assert loc.latitude == 0
        assert loc.longitude == 0
        assert loc.altitude == 0

    def test_location_with_values(self):
        # Must use aliases (lat, lon, alt) as model uses alias
        loc = Location(lat=37.7749, lon=-122.4194, alt=100.0)
        assert loc.latitude == 37.7749
        assert loc.longitude == -122.4194
        assert loc.altitude == 100.0

    def test_location_with_aliases(self):
        loc = Location(lat=40.7128, lon=-74.0060, alt=50.0)
        assert loc.latitude == 40.7128
        assert loc.longitude == -74.0060
        assert loc.altitude == 50.0


class TestCoordinates:
    """Test Coordinates model."""

    def test_coordinates_defaults(self):
        coords = Coordinates()
        assert coords.latitude == 0
        assert coords.longitude == 0
        assert coords.altitude == 0

    def test_coordinates_with_values(self):
        # Must use aliases (lat, lon, alt) as model uses alias
        coords = Coordinates(lat=51.5074, lon=-0.1278, alt=11.0)
        assert coords.latitude == 51.5074
        assert coords.longitude == -0.1278

    def test_coordinates_with_aliases(self):
        coords = Coordinates(lat=35.6762, lon=139.6503, alt=40.0)
        assert coords.latitude == 35.6762
        assert coords.longitude == 139.6503


class TestPlace:
    """Test Place model."""

    def test_place_minimal(self):
        # Must use alias 'type' instead of 'place_type'
        place = Place(id="place-001", name="Main Street", type="intersection")
        assert place.id == "place-001"
        assert place.name == "Main Street"
        assert place.place_type == "intersection"
        assert place.location is None
        assert place.coordinates is None

    def test_place_with_location(self):
        loc = Location(lat=37.7749, lon=-122.4194)
        place = Place(
            id="place-002",
            name="Downtown",
            type="area",
            location=loc,
        )
        assert place.location is not None
        assert place.location.latitude == 37.7749

    def test_place_with_coordinates(self):
        coords = Coordinates(lat=40.7128, lon=-74.0060)
        place = Place(
            id="place-003",
            name="Times Square",
            type="landmark",
            coordinates=coords,
        )
        assert place.coordinates is not None
        assert place.coordinates.latitude == 40.7128

    def test_place_with_aliases(self):
        place = Place(
            id="p1",
            name="Test Place",
            type="test",
        )
        assert place.place_type == "test"


class TestIncident:
    """Test Incident model."""

    def test_incident_minimal(self):
        incident = Incident(
            id="incident-001",
            sensor_id="sensor-001",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T00:01:00.000Z",
        )
        assert incident.id == "incident-001"
        assert incident.sensor_id == "sensor-001"
        assert incident.start_time == "2025-01-01T00:00:00.000Z"
        assert incident.end_time == "2025-01-01T00:01:00.000Z"

    def test_incident_with_aliases(self):
        incident = Incident(
            Id="i1",
            sensorId="s1",
            timestamp="2025-01-01T00:00:00.000Z",
            end="2025-01-01T01:00:00.000Z",
        )
        assert incident.id == "i1"
        assert incident.sensor_id == "s1"
        assert incident.start_time == "2025-01-01T00:00:00.000Z"
        assert incident.end_time == "2025-01-01T01:00:00.000Z"

    def test_incident_with_optional_fields(self):
        place = Place(id="p1", name="Test", place_type="intersection")
        incident = Incident(
            id="i2",
            sensor_id="s2",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            place=place,
            category="traffic",
            object_ids=["obj1", "obj2"],
            frame_ids=["frame1"],
            analytics_module="va-module",
            info={"key": "value"},
            incident_type="collision",
            is_anomaly=True,
        )
        assert incident.place is not None
        assert incident.place.name == "Test"
        assert incident.category == "traffic"
        assert incident.object_ids == ["obj1", "obj2"]
        assert incident.frame_ids == ["frame1"]
        assert incident.analytics_module == "va-module"
        assert incident.info == {"key": "value"}
        assert incident.incident_type == "collision"
        assert incident.is_anomaly is True

    def test_incident_with_aliased_optional_fields(self):
        incident = Incident(
            Id="i3",
            sensorId="s3",
            timestamp="2025-01-01T00:00:00.000Z",
            end="2025-01-01T01:00:00.000Z",
            objectIds=["o1"],
            frameIds=["f1"],
            analyticsModule="mod",
            isAnomaly=False,
        )
        assert incident.object_ids == ["o1"]
        assert incident.frame_ids == ["f1"]
        assert incident.analytics_module == "mod"
        assert incident.is_anomaly is False

    def test_incident_allows_extra_fields(self):
        incident = Incident(
            id="i4",
            sensor_id="s4",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            extra_field="extra_value",
        )
        assert incident.id == "i4"
        # Extra fields are allowed due to model_config

    def test_incident_serialization(self):
        incident = Incident(
            id="i5",
            sensor_id="s5",
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T01:00:00.000Z",
            category="test",
        )
        data = incident.model_dump()
        assert "id" in data
        assert data["category"] == "test"

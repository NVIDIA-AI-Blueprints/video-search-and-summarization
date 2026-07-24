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
"""Unit tests for interface module."""

import pytest

from vss_agents.video_analytics.interface import IncidentMetadata
from vss_agents.video_analytics.interface import VideoAnalyticsInterface


class TestIncidentMetadata:
    """Test IncidentMetadata enum."""

    def test_place_value(self):
        assert IncidentMetadata.PLACE == "place"

    def test_category_value(self):
        assert IncidentMetadata.CATEGORY == "category"

    def test_is_anomaly_value(self):
        assert IncidentMetadata.IS_ANOMALY == "isAnomaly"

    def test_object_ids_value(self):
        assert IncidentMetadata.OBJECT_IDS == "objectIds"

    def test_frame_ids_value(self):
        assert IncidentMetadata.FRAME_IDS == "frameIds"

    def test_analytics_module_value(self):
        assert IncidentMetadata.ANALYTICS_MODULE == "analyticsModule"

    def test_type_value(self):
        assert IncidentMetadata.TYPE == "type"

    def test_info_value(self):
        assert IncidentMetadata.INFO == "info"

    def test_all_values_count(self):
        assert len(IncidentMetadata) == 8


class TestVideoAnalyticsInterface:
    """Test VideoAnalyticsInterface abstract class."""

    def test_interface_is_abstract(self):
        """Test that interface cannot be instantiated directly."""
        with pytest.raises(TypeError):
            VideoAnalyticsInterface()

    def test_interface_defines_methods(self):
        """Test that interface defines required abstract methods."""
        assert hasattr(VideoAnalyticsInterface, "get_incident")
        assert hasattr(VideoAnalyticsInterface, "get_incidents")
        assert hasattr(VideoAnalyticsInterface, "get_sensor_ids")
        assert hasattr(VideoAnalyticsInterface, "get_places")
        assert hasattr(VideoAnalyticsInterface, "get_fov_histogram")
        assert hasattr(VideoAnalyticsInterface, "get_average_speeds")
        assert hasattr(VideoAnalyticsInterface, "analyze")

    def test_concrete_implementation(self):
        """Test that a concrete implementation can be created."""

        class ConcreteImplementation(VideoAnalyticsInterface):
            async def get_incident(self, id, *, includes=None):
                return None

            async def get_incidents(
                self,
                start_time=None,
                end_time=None,
                *,
                source=None,
                source_type=None,
                max_count=10,
                includes=None,
                vlm_verdict=None,
            ):
                return ([], False)

            async def get_sensor_ids(self, place=None):
                return []

            async def get_places(self):
                return {}

            async def get_fov_histogram(self, source, start_time, end_time, object_type=None, bucket_count=10):
                return {}

            async def get_average_speeds(self, source, start_time, end_time, source_type):
                return {}

            async def analyze(self, start_time, end_time, source, source_type, analysis_type):
                return ""

        # Should not raise
        impl = ConcreteImplementation()
        assert impl is not None

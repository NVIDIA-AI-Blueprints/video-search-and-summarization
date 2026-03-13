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
"""Unit tests for incidents module."""

from vss_agents.tools.incidents import DuckDBIncidentsManager
from vss_agents.tools.incidents import VARetrievalConfig
from vss_agents.tools.incidents import VARetrievalInput


class TestVARetrievalConfig:
    """Test VARetrievalConfig model."""

    def test_defaults(self):
        config = VARetrievalConfig()
        assert config.minio_url == "http://localhost:9000"
        assert config.access_key == "minioadmin"
        assert config.secret_key == "minioadmin"
        assert config.bucket_name == "incidents-bucket"
        assert config.prefix == ""
        assert config.db_path == ":memory:"
        assert config.file_extensions == [".json", ".ndjson"]
        assert config.auto_refresh is False

    def test_custom_values(self):
        config = VARetrievalConfig(
            minio_url="http://custom-minio:9000",
            access_key="custom-access",
            secret_key="custom-secret",
            bucket_name="custom-bucket",
            prefix="incidents/",
            db_path="/tmp/incidents.duckdb",
            file_extensions=[".json"],
            auto_refresh=True,
        )
        assert config.minio_url == "http://custom-minio:9000"
        assert config.access_key == "custom-access"
        assert config.secret_key == "custom-secret"
        assert config.bucket_name == "custom-bucket"
        assert config.prefix == "incidents/"
        assert config.db_path == "/tmp/incidents.duckdb"
        assert config.file_extensions == [".json"]
        assert config.auto_refresh is True


class TestVARetrievalInput:
    """Test VARetrievalInput model."""

    def test_defaults(self):
        input_data = VARetrievalInput()
        assert input_data.action is None
        assert input_data.sql_query is None
        assert input_data.id is None
        assert input_data.start_time is None
        assert input_data.end_time is None
        assert input_data.source is None
        assert input_data.source_type is None
        assert input_data.max_count == 10
        assert input_data.includes is None

    def test_sql_query_action(self):
        input_data = VARetrievalInput(action="query", sql_query="SELECT * FROM incidents LIMIT 10")
        assert input_data.action == "query"
        assert input_data.sql_query == "SELECT * FROM incidents LIMIT 10"

    def test_get_schema_action(self):
        input_data = VARetrievalInput(action="get_schema")
        assert input_data.action == "get_schema"

    def test_single_incident_retrieval(self):
        input_data = VARetrievalInput(id="incident-123")
        assert input_data.id == "incident-123"

    def test_time_range_query(self):
        input_data = VARetrievalInput(
            start_time="2025-01-01T00:00:00.000Z",
            end_time="2025-01-01T23:59:59.000Z",
            source="sensor-001",
            source_type="sensor",
            max_count=50,
        )
        assert input_data.start_time == "2025-01-01T00:00:00.000Z"
        assert input_data.end_time == "2025-01-01T23:59:59.000Z"
        assert input_data.source == "sensor-001"
        assert input_data.source_type == "sensor"
        assert input_data.max_count == 50

    def test_place_source_type(self):
        input_data = VARetrievalInput(
            source="Main Street",
            source_type="place",
        )
        assert input_data.source == "Main Street"
        assert input_data.source_type == "place"

    def test_with_includes(self):
        input_data = VARetrievalInput(includes=["objectIds", "info", "place"])
        assert input_data.includes == ["objectIds", "info", "place"]


class TestDuckDBIncidentsManagerNormalizeTimestamp:
    """Test DuckDBIncidentsManager.normalize_timestamp static method."""

    def test_none_timestamp(self):
        result = DuckDBIncidentsManager.normalize_timestamp(None)
        assert result is None

    def test_z_suffix_conversion(self):
        result = DuckDBIncidentsManager.normalize_timestamp("2025-01-01T12:00:00Z")
        assert "+00:00" in result or "Z" in result  # DuckDB format

    def test_timestamp_with_offset(self):
        result = DuckDBIncidentsManager.normalize_timestamp("2025-01-01T12:00:00+05:00")
        assert result is not None

    def test_timestamp_with_milliseconds(self):
        result = DuckDBIncidentsManager.normalize_timestamp("2025-01-01T12:00:00.123Z")
        assert result is not None

    def test_timestamp_with_microseconds(self):
        result = DuckDBIncidentsManager.normalize_timestamp("2025-01-01T12:00:00.123456Z")
        assert result is not None


class TestDuckDBIncidentsManagerInit:
    """Test DuckDBIncidentsManager initialization."""

    def test_init(self):
        config = VARetrievalConfig()
        manager = DuckDBIncidentsManager(config)
        assert manager.config == config
        assert manager._initialized is False
        assert manager.s3_client is None
        assert manager.conn is None

    def test_class_level_instances(self):
        """Test that class-level storage is initialized."""
        assert isinstance(DuckDBIncidentsManager._instances, dict)
        assert isinstance(DuckDBIncidentsManager._locks, dict)

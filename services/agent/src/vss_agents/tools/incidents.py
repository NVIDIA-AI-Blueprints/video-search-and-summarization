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

import ast
from collections.abc import AsyncGenerator
import json
import logging
from typing import Any
from typing import ClassVar

import boto3
from dateutil import parser as dateutil_parser
import duckdb
from nat.builder.builder import Builder
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.builder.function_info import FunctionInfo
from nat.cli.register_workflow import register_function
from nat.data_models.function import FunctionBaseConfig
from pydantic import BaseModel
from pydantic import Field

logger = logging.getLogger(__name__)


class VARetrievalConfig(FunctionBaseConfig, name="va_retrieval"):
    """Configuration for the List Incidents tool."""

    minio_url: str = Field(
        "http://localhost:9000",
        description="The endpoint URL of the MinIO/S3 server",
    )
    access_key: str = Field(
        "minioadmin",
        description="The access key of the S3 bucket",
    )
    secret_key: str = Field(
        "minioadmin",
        description="The secret key of the S3 bucket",
    )
    bucket_name: str = Field(
        "incidents-bucket",
        description="The name of the S3 bucket containing incident data",
    )
    prefix: str = Field(
        "",
        description="The prefix/folder path in the bucket to search for incident files",
    )
    db_path: str = Field(
        ":memory:",
        description="DuckDB database path (':memory:' for in-memory, or file path for persistence)",
    )
    file_extensions: list[str] = Field(
        [".json", ".ndjson"],
        description="List of file extensions to load as incident data",
    )
    auto_refresh: bool = Field(
        False,
        description="Whether to automatically refresh data from bucket on each query",
    )


class VARetrievalInput(BaseModel):
    """Input for va_retrieval tool that supports SQL queries, high-level incident retrieval, and single incident lookup."""

    # SQL query mode
    action: str | None = Field(
        None,
        description="The action to perform: 'get_schema' or 'query'. Use for direct SQL access.",
    )
    sql_query: str | None = Field(
        None,
        description="The SQL query to perform (required if action='query')",
    )

    # Single-incident retrieval mode (get_incident)
    id: str | None = Field(
        default=None,
        description="Specific incident ID to retrieve (mirrors video_analytics.get_incident 'id' field).",
    )

    # High-level incident retrieval mode
    start_time: str | None = Field(None, description="Start time in ISO format (e.g., 2025-11-13T16:00:00.000Z)")
    end_time: str | None = Field(None, description="End time in ISO format (e.g., 2025-11-13T17:00:00.000Z)")
    source: str | None = Field(None, description="Source ID (e.g., sensor ID or place ID)")
    source_type: str | None = Field(None, description="Source type: 'sensor' or 'place'")
    max_count: int = Field(10, description="Maximum number of incidents to return")
    includes: list[str] | None = Field(None, description="Additional fields to include (e.g., objectIds, info)")


class DuckDBIncidentsManager:
    """Manager class for DuckDB-based incident storage and querying."""

    # Class-level storage for singleton instances
    _instances: ClassVar[dict[Any, "DuckDBIncidentsManager"]] = {}
    _locks: ClassVar[dict[Any, Any]] = {}

    def __init__(self, config: "VARetrievalConfig") -> None:
        self.config = config
        self._initialized = False
        self.s3_client: Any = None
        self.conn: Any = None

    @staticmethod
    def normalize_timestamp(timestamp: str | None) -> str | None:
        """
        Normalize timestamp to DuckDB-compatible format.

        DuckDB handles ISO 8601 timestamps well, but we ensure consistency:
        - Converts 'Z' suffix to explicit '+00:00' timezone
        - Adds UTC timezone if missing
        - Validates timestamp format

        Args:
            timestamp: ISO format timestamp string or None

        Returns:
            Normalized timestamp string or None
        """
        if not timestamp or not isinstance(timestamp, str):
            return timestamp
        try:
            # Handle common timestamp formats
            if timestamp.endswith("Z"):
                # Convert Zulu time to explicit UTC offset
                return timestamp[:-1] + "+00:00"
            elif "T" in timestamp and ("+" not in timestamp and "-" not in timestamp.split("T")[1]):
                # Add UTC timezone if missing (no offset after the time part)
                return timestamp + "+00:00"
            else:
                # Already has timezone info or is in acceptable format
                return timestamp
        except Exception as e:
            logger.warning(f"Failed to normalize timestamp {timestamp}: {e}")
            return timestamp

    @classmethod
    async def get_instance(cls, config: VARetrievalConfig) -> "DuckDBIncidentsManager":
        """Get or create a singleton instance for the given configuration."""
        # Create a unique key based on config values
        config_key = (config.minio_url, config.access_key, config.bucket_name, config.prefix, config.db_path)

        # Ensure we have a lock for this config
        if config_key not in cls._locks:
            import asyncio

            cls._locks[config_key] = asyncio.Lock()

        # Use the lock to ensure thread-safe singleton creation
        async with cls._locks[config_key]:
            if config_key not in cls._instances:
                # Create new instance
                instance = cls(config)
                await instance._async_init()
                cls._instances[config_key] = instance
                logger.info(f"Created new DuckDBIncidentsManager instance for {config.bucket_name}/{config.prefix}")
            else:
                logger.info(
                    f"Reusing existing DuckDBIncidentsManager instance for {config.bucket_name}/{config.prefix}"
                )

        return cls._instances[config_key]

    async def _async_init(self) -> None:
        """Asynchronously initialize the manager."""
        if self._initialized:
            return

        # Initialize S3 client
        self.s3_client = boto3.client(
            "s3",
            endpoint_url=self.config.minio_url,
            aws_access_key_id=self.config.access_key,
            aws_secret_access_key=self.config.secret_key,
            region_name="us-east-1",
            verify=True,
        )

        # Initialize DuckDB connection
        self.conn = duckdb.connect(self.config.db_path)
        self._setup_database()

        # Load initial data
        await self.load_incidents_from_bucket()

        self._initialized = True

    @classmethod
    def clear_instances(cls) -> None:
        """Clear all singleton instances. Useful for testing or forced refresh."""
        cls._instances.clear()
        logger.info("Cleared all DuckDBIncidentsManager singleton instances")

    async def refresh_data(self) -> None:
        """Manually refresh data from S3 bucket."""
        if not self._initialized:
            await self._async_init()
        else:
            logger.info("Manually refreshing incidents data from S3")
            await self.load_incidents_from_bucket()

    def _setup_database(self) -> None:
        """Set up the database schema and indexes."""
        # Create incidents table schema
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS incidents (
                Id VARCHAR PRIMARY KEY,
                sensorId VARCHAR NOT NULL,
                timestamp TIMESTAMP NOT NULL,
                end_timestamp TIMESTAMP,
                category VARCHAR,
                isAnomaly BOOLEAN DEFAULT FALSE,
                place JSON,
                analyticsModule JSON,
                info JSON,
                objectIds JSON,
                frameIds JSON,
                type VARCHAR DEFAULT 'mdx-incidents',
                -- Additional metadata
                source_file VARCHAR,
                loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Create indexes for performance
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_sensor_timestamp ON incidents(sensorId, timestamp DESC)")
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_category ON incidents(category)")

        # Create metadata table to track loaded files
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS loaded_files (
                file_path VARCHAR PRIMARY KEY,
                file_size BIGINT,
                last_modified TIMESTAMP,
                loaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                record_count INTEGER
            )
        """)

    async def load_incidents_from_bucket(self) -> int:
        """Load all incident files from the configured S3 bucket."""
        logger.info(f"Loading incidents from bucket: {self.config.bucket_name}/{self.config.prefix}")

        # List all objects in the bucket with the given prefix
        paginator = self.s3_client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=self.config.bucket_name, Prefix=self.config.prefix)

        total_loaded = 0
        for page in pages:
            if "Contents" not in page:
                continue

            for obj in page["Contents"]:
                key = obj["Key"]

                # Check if file extension matches
                if not any(key.lower().endswith(ext) for ext in self.config.file_extensions):
                    continue

                # Check if file was already loaded
                existing = self.conn.execute(
                    "SELECT last_modified FROM loaded_files WHERE file_path = ?", [key]
                ).fetchone()

                if existing:
                    # Convert database timestamp to datetime for comparison
                    db_last_modified = existing[0]
                    if isinstance(db_last_modified, str):
                        # Parse ISO format string
                        db_last_modified = dateutil_parser.isoparse(db_last_modified)

                    # Make S3 timestamp timezone-naive for comparison
                    s3_last_modified = obj["LastModified"]
                    if s3_last_modified.tzinfo:
                        s3_last_modified = s3_last_modified.replace(tzinfo=None)
                    if db_last_modified.tzinfo:
                        db_last_modified = db_last_modified.replace(tzinfo=None)

                    if db_last_modified >= s3_last_modified:
                        logger.debug(f"Skipping already loaded file: {key}")
                        continue

                try:
                    # Download file content
                    response = self.s3_client.get_object(Bucket=self.config.bucket_name, Key=key)
                    content = response["Body"].read()

                    # Load based on file type
                    if key.lower().endswith(".json"):
                        loaded = await self.load_json_content(content, key)
                    else:
                        logger.warning(f"Unsupported file type: {key}")
                        continue

                    # Update metadata
                    # Store timestamp without timezone for consistency
                    last_modified = obj["LastModified"]
                    if last_modified.tzinfo:
                        last_modified = last_modified.replace(tzinfo=None)

                    self.conn.execute(
                        """
                        INSERT OR REPLACE INTO loaded_files
                        (file_path, file_size, last_modified, record_count)
                        VALUES (?, ?, ?, ?)
                    """,
                        [key, obj["Size"], last_modified, loaded],
                    )

                    total_loaded += loaded
                    logger.info(f"Loaded {loaded} incidents from {key}")

                except Exception as e:
                    logger.error(f"Error loading file {key}: {e}")
                    continue

        logger.info(f"Total incidents loaded: {total_loaded}")
        return total_loaded

    async def load_json_content(self, content: bytes, source_file: str) -> int:
        """Load incidents from JSON content."""
        data = json.loads(content)

        # Handle both single incident and array of incidents
        incidents = data if isinstance(data, list) else [data]
        if len(incidents) == 0:
            return 0

        # Map JSON fields to database columns
        field_mapping = {
            "end": "end_timestamp",  # Rename 'end' to 'end_timestamp' to match DB schema
            "start": "start_timestamp",  # Map 'start' if present (SQL reserved keyword)
        }

        # Timestamp fields that need conversion
        timestamp_fields = {"timestamp", "end_timestamp", "start_timestamp", "end", "start"}

        # Process incidents to rename fields and handle timestamps
        processed_incidents = []
        for incident in incidents:
            processed_incident = {}
            for key, value in incident.items():
                # Use mapped column name if it exists, otherwise use original key
                column_name = field_mapping.get(key, key)

                # Convert timestamp strings to DuckDB-compatible format
                if key in timestamp_fields and value and isinstance(value, str):
                    value = self.normalize_timestamp(value)

                processed_incident[column_name] = value
            processed_incidents.append(processed_incident)

        keys = [*processed_incidents[0].keys()]
        columns = ", ".join(keys) + ", source_file"
        placeholders = ", ".join(["?"] * len(keys)) + ", ?"
        insert_sql = f"""
            INSERT OR REPLACE INTO incidents (
                {columns}
            ) VALUES ({placeholders})
        """

        count = 0
        for incident in processed_incidents:
            try:
                self.conn.execute(
                    insert_sql,
                    [
                        *[incident.get(key) for key in keys],
                        source_file,
                    ],
                )
                count += 1
            except Exception as e:
                logger.error(f"Error inserting incident from {source_file}: {e}")

        return count

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql)
        columns = [desc[0] for desc in cursor.description]
        rows = cursor.fetchall()
        return [dict(zip(columns, row, strict=True)) for row in rows]

    def get_schema(self) -> list[tuple[Any, ...]]:
        result: list[tuple[Any, ...]] = self.conn.execute("DESCRIBE incidents").fetchall()
        return result


@register_function(config_type=VARetrievalConfig, framework_wrappers=[LLMFrameworkEnum.LANGCHAIN])
async def va_retrieval(config: VARetrievalConfig, _builder: Builder) -> AsyncGenerator[FunctionInfo]:
    """
    Query the video analytics incident database stored in DuckDB.

    Supports two modes:
    1. SQL mode: Direct SQL queries (action='query' or 'get_schema')
    2. High-level mode: Retrieve incidents by time range, sensor, etc.

    SQL Mode Input:
        action: 'get_schema' or 'query'
        sql_query: SQL query string (required if action='query')

    High-level Mode Input:
        start_time: ISO timestamp (e.g., "2025-11-13T16:00:00.000Z")
        end_time: ISO timestamp
        source: sensor ID or place ID (optional)
        source_type: 'sensor' or 'place' (optional)
        max_count: maximum incidents to return (default: 10)

    Returns:
        SQL mode: String representation of query results
        High-level mode: List of incident dictionaries with parsed JSON fields
    """

    # Get or create singleton manager instance
    manager = await DuckDBIncidentsManager.get_instance(config)

    async def _va_retrieval(va_retrieval_input: VARetrievalInput) -> str | list[dict]:
        # Determine mode based on which fields are provided
        if va_retrieval_input.action is not None:
            # SQL mode
            if va_retrieval_input.action == "get_schema":
                return f"Table name: incidents\n\n Table schema: {manager.get_schema()}"
            elif va_retrieval_input.action == "query":
                if not va_retrieval_input.sql_query:
                    raise ValueError("sql_query is required when action='query'")
                return str(manager.run_sql(va_retrieval_input.sql_query))
            else:
                raise ValueError(f"Invalid action: {va_retrieval_input.action}")

        elif va_retrieval_input.id:
            # Single-incident retrieval mode
            incident_id = va_retrieval_input.id

            sql_query = """
                SELECT
                    Id,
                    sensorId,
                    CAST(timestamp AS VARCHAR) as timestamp,
                    CAST(end_timestamp AS VARCHAR) as end,
                    category,
                    isAnomaly,
                    place,
                    analyticsModule,
                    info,
                    objectIds,
                    frameIds,
                    type,
                    source_file
                FROM incidents
                WHERE Id = ?
                LIMIT 1
            """

            logger.info(f"Executing single-incident SQL query for Id={incident_id}")
            query_result = manager.conn.execute(sql_query, [incident_id])
            columns = [desc[0] for desc in query_result.description]
            row = query_result.fetchone()

            if not row:
                logger.info(f"No incident found with Id={incident_id}")
                return json.dumps({})

            incident = dict(zip(columns, row, strict=True))

            # Parse JSON string fields back into objects
            json_fields = ["analyticsModule", "info", "objectIds", "frameIds", "place"]
            for field in json_fields:
                if field in incident and isinstance(incident[field], str):
                    try:
                        incident[field] = json.loads(incident[field])
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse JSON field '{field}' in incident {incident.get('Id')}: {e}")

            try:
                return json.dumps(incident)
            except TypeError:
                logger.exception("Failed to serialize single incident to JSON; falling back to string representation")
                return str(incident)

        elif va_retrieval_input.source or va_retrieval_input.start_time or va_retrieval_input.end_time:
            # High-level incident retrieval mode (time range and/or source filtering)
            # Build WHERE clause
            where_clauses = []
            params = []

            # Add time range filters if provided
            if va_retrieval_input.start_time:
                start_time = DuckDBIncidentsManager.normalize_timestamp(va_retrieval_input.start_time)
                where_clauses.append("timestamp >= ?")
                params.append(start_time)

            if va_retrieval_input.end_time:
                end_time = DuckDBIncidentsManager.normalize_timestamp(va_retrieval_input.end_time)
                where_clauses.append("timestamp <= ?")
                params.append(end_time)

            # Add source filters if provided
            if va_retrieval_input.source and va_retrieval_input.source_type:
                if va_retrieval_input.source_type.lower() == "sensor":
                    where_clauses.append("sensorId = ?")
                    params.append(va_retrieval_input.source)
                elif va_retrieval_input.source_type.lower() == "place":
                    where_clauses.append("place::json->>'id' = ?")
                    params.append(va_retrieval_input.source)

            # Ensure we have at least one filter
            if not where_clauses:
                raise ValueError("Must provide at least one filter: source information or time range")

            where_clause = " AND ".join(where_clauses)

            # Build SQL query with VARCHAR casting for timestamps
            sql_query = f"""
                SELECT
                    Id,
                    sensorId,
                    CAST(timestamp AS VARCHAR) as timestamp,
                    CAST(end_timestamp AS VARCHAR) as end,
                    category,
                    isAnomaly,
                    place,
                    analyticsModule,
                    info,
                    objectIds,
                    frameIds,
                    type,
                    source_file
                FROM incidents
                WHERE {where_clause}
                ORDER BY timestamp DESC
                LIMIT {va_retrieval_input.max_count}
            """

            logger.info(f"Executing SQL query: {sql_query}")
            query_result = manager.conn.execute(sql_query, params)
            columns = [desc[0] for desc in query_result.description]
            rows = query_result.fetchall()
            result_str = str([dict(zip(columns, row, strict=True)) for row in rows])

            # Parse result and convert JSON strings to objects
            try:
                result = ast.literal_eval(result_str)
                if not isinstance(result, list):
                    result = [result] if result else []

                # Parse JSON string fields back into objects
                json_fields = ["analyticsModule", "info", "objectIds", "frameIds", "place"]
                for incident in result:
                    for field in json_fields:
                        if field in incident and isinstance(incident[field], str):
                            try:
                                incident[field] = json.loads(incident[field])
                            except json.JSONDecodeError as e:
                                logger.warning(
                                    f"Failed to parse JSON field '{field}' in incident {incident.get('Id')}: {e}"
                                )

                logger.info(f"Retrieved {len(result)} incidents")

                try:
                    return json.dumps({"incidents": result})
                except TypeError:
                    logger.exception("Failed to serialize incidents to JSON; falling back to string representation")
                    return str({"incidents": result})

            except (ValueError, SyntaxError):
                logger.exception("Failed to parse va_retrieval result")
                logger.error(f"Result string: {result_str}")
                return []

        else:
            raise ValueError("Must provide either 'action' (SQL mode) or 'start_time'+'end_time' (high-level mode)")

    schema = "\n".join([f"{col[0]}: {col[1]}" for col in manager.get_schema()])
    logger.info(f"Table schema: {schema}")

    yield FunctionInfo.create(
        single_fn=_va_retrieval,
        description=_va_retrieval.__doc__,
        input_schema=VARetrievalInput,
        single_output_schema=str | list,
    )

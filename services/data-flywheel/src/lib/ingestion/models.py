# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Ingestion models - all Pydantic models for data ingestion.

This module contains:
- Plugin runtime models: IngestionContext, IngestionRecord, IngestionResult
- Plugin ABC: IngestionPlugin
- Configuration models: IngestionConfig, IngestionState
- Dataset type enums for validation
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from enum import Enum
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, Field

# =============================================================================
# Constants
# =============================================================================

INGESTION_WATERMARKS_COLLECTION = "ingestion_watermarks"
DEFAULT_INGESTION_START_DATE = datetime(2020, 1, 1)


# =============================================================================
# Dataset Type Enums
# =============================================================================


class EmbeddingDatasetType(str, Enum):
    """Valid dataset types for embedding_job."""

    TRIPLET = "triplet"
    BUG_PAIRS = "bug_pairs"
    BUG_PAIRS_SEPARATED = "bug_pairs_separated"

    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.values()


class LLMDatasetType(str, Enum):
    """Valid dataset types for llm_job."""

    COMPLETION = "completion"
    TOOL_CALLING = "tool_calling"
    DPO = "dpo"

    @classmethod
    def values(cls) -> set[str]:
        return {member.value for member in cls}

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls.values()


class DatasetType:
    """Helper class for dataset type validation."""

    EMBEDDING = EmbeddingDatasetType
    LLM = LLMDatasetType

    @staticmethod
    def all_values() -> set[str]:
        return EmbeddingDatasetType.values() | LLMDatasetType.values()

    @staticmethod
    def is_valid(value: str) -> bool:
        return value in DatasetType.all_values()

    @staticmethod
    def is_valid_for_job(value: str, job_type: str) -> bool:
        if job_type == "embedding_job":
            return EmbeddingDatasetType.is_valid(value)
        elif job_type == "llm_job":
            return LLMDatasetType.is_valid(value)
        return False

    @staticmethod
    def get_valid_types(job_type: str) -> set[str]:
        if job_type == "embedding_job":
            return EmbeddingDatasetType.values()
        elif job_type == "llm_job":
            return LLMDatasetType.values()
        return set()

    @staticmethod
    def validate_for_job(value: str, job_type: str) -> None:
        if not DatasetType.is_valid_for_job(value, job_type):
            valid_types = DatasetType.get_valid_types(job_type)
            raise ValueError(f"Invalid dataset_type '{value}' for {job_type}. Valid: {valid_types}")


# =============================================================================
# Plugin Runtime Models
# =============================================================================


class IngestionContext(BaseModel):
    """Context passed to plugins during execution."""

    workload_id: str = Field(..., description="Workload identifier")
    client_id: str = Field(..., description="Client identifier")
    last_sync_watermark: datetime | None = Field(
        default=None, description="Timestamp of last sync. None = full sync."
    )
    config: dict[str, Any] = Field(
        default_factory=dict, description="Plugin-specific configuration"
    )

    model_config = {"arbitrary_types_allowed": True}


class IngestionRecord(BaseModel):
    """A single record yielded by a plugin."""

    data: dict[str, Any] = Field(..., description="Record payload")
    timestamp: datetime | None = Field(default=None, description="Record timestamp for watermark")
    source_id: str | None = Field(default=None, description="Source system identifier")

    model_config = {"arbitrary_types_allowed": True}


class IngestionResult(BaseModel):
    """Result summary returned after ingestion completes."""

    records_fetched: int = Field(default=0, ge=0)
    records_loaded: int = Field(default=0, ge=0)
    records_skipped: int = Field(default=0, ge=0)
    max_timestamp: datetime | None = Field(default=None)
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    duration_seconds: float | None = Field(default=None)
    errors: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    plugin_name: str = Field(default="")
    dataset_type: str = Field(default="")

    model_config = {"arbitrary_types_allowed": True}

    @property
    def success(self) -> bool:
        return len(self.errors) == 0 or self.records_loaded > 0

    @property
    def has_errors(self) -> bool:
        return len(self.errors) > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "records_fetched": self.records_fetched,
            "records_loaded": self.records_loaded,
            "records_skipped": self.records_skipped,
            "max_timestamp": self.max_timestamp.isoformat() if self.max_timestamp else None,
            "errors": self.errors,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "duration_seconds": self.duration_seconds,
            "plugin_name": self.plugin_name,
            "dataset_type": self.dataset_type,
            "metadata": self.metadata,
            "success": self.success,
        }


# =============================================================================
# Plugin Abstract Base Class
# =============================================================================


class IngestionPlugin(ABC):
    """Abstract base class for all ingestion plugins."""

    name: str = ""

    @classmethod
    def get_info(cls) -> dict[str, str]:
        return {
            "name": cls.name,
            "class_name": cls.__name__,
            "module": cls.__module__,
            "description": cls.__doc__ or "No documentation available.",
        }

    @abstractmethod
    def validate_config(self, config: dict[str, Any]) -> bool:
        """Validate plugin-specific configuration."""
        pass

    @abstractmethod
    def ingest(self, context: IngestionContext) -> Iterator[IngestionRecord]:
        """Generator that yields records from the data source."""
        pass

    def get_connection_info(self, config: dict[str, Any]) -> str:
        host = config.get("host", config.get("server", "unknown"))
        database = config.get("database", "unknown")
        return f"{host}/{database}"


# =============================================================================
# Configuration Models
# =============================================================================


class IngestionConfig(BaseModel):
    """Configuration for data ingestion plugins."""

    plugin_name: str = Field(..., min_length=1, description="Plugin name (e.g., 'bugnemo')")
    dataset_type: str = Field(..., min_length=1, description="Dataset type (e.g., 'bug_pairs')")
    config: dict[str, Any] = Field(default_factory=dict, description="Plugin-specific parameters")
    batch_size: int = Field(default=1000, ge=1, le=10000)
    sync_interval_minutes: int = Field(default=60, ge=1)
    default_start_date: datetime | None = Field(default=None)


class IngestionState(BaseModel):
    """Watermark state per workload/client/plugin (stored in MongoDB)."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    workload_id: str = Field(...)
    client_id: str = Field(...)
    plugin_name: str = Field(...)
    last_sync_watermark: datetime | None = Field(default=None)
    last_ingestion_at: datetime | None = Field(default=None)
    last_ingestion_by_trigger_id: str | None = Field(default=None)
    total_records_ingested: int = Field(default=0)
    last_errors: list[str] | None = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str},
        "populate_by_name": True,
    }

    def to_mongo(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def from_mongo(cls, doc: dict) -> "IngestionState | None":
        if not doc:
            return None
        return cls(**doc)

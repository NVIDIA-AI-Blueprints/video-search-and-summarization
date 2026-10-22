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
from datetime import datetime
from typing import Any

from bson import ObjectId
from pydantic import BaseModel, Field, SerializeAsAny, model_validator

from src.config import JobConfig, build_job_config, config_type_for


class ConfigurationDocument(BaseModel):
    """MongoDB document model for storing configurations."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    name: str = Field(..., min_length=1, description="Unique name for the configuration")
    description: str | None = Field(None, description="Description of the configuration")
    config_type: str = Field(..., description="Type of configuration")
    # Polymorphic: holds whichever JobConfig subclass the active backend builds
    # (LLM/Embedding for NMP, AOI for DEFT). SerializeAsAny keeps subclass fields
    # on model_dump; construction/classification is delegated to the backend.
    config_data: SerializeAsAny[JobConfig] = Field(
        ..., description="Job-level configuration data as validated object"
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            ObjectId: str,
        },
    }

    @model_validator(mode="before")
    @classmethod
    def validate_config_data(cls, values: dict[str, Any]) -> dict[str, Any]:
        """Validate and construct config_data based on config_type."""
        if isinstance(values, dict) and "config_data" in values and "config_type" in values:
            config_data = values["config_data"]
            config_type = values["config_type"]

            # Build the typed object from the central contract registry.
            if isinstance(config_data, dict):
                values["config_data"] = build_job_config(config_type, config_data)

        return values

    def to_mongo(self) -> dict[str, Any]:
        """Convert ConfigurationDocument model to MongoDB document."""
        return {
            "_id": self.id,
            "name": self.name,
            "description": self.description,
            "config_type": self.config_type,
            "config_data": self.config_data.model_dump(mode="json"),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "ConfigurationDocument | None":
        """Create ConfigurationDocument from MongoDB document."""
        if not doc:
            return None
        return cls(**doc)

    def to_job_config(self) -> JobConfig:
        """Convert stored configuration data to appropriate job config object."""
        # config_data is already the proper object, just return it
        return self.config_data

    @classmethod
    def from_job_config(
        cls,
        job_config: JobConfig,
        name: str,
        description: str | None = None,
    ) -> "ConfigurationDocument":
        """Create ConfigurationDocument from any job config object."""
        config_type = config_type_for(job_config)

        return cls(
            name=name,
            description=description,
            config_type=config_type,
            config_data=job_config,  # Store the object directly
        )

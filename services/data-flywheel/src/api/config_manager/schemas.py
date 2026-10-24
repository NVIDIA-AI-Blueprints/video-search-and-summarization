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

from pydantic import BaseModel, Field, ValidationError, field_validator

from src.config import build_job_config


class ConfigurationRequest(BaseModel):
    """Request model for creating/updating a configuration."""

    name: str = Field(
        ...,
        description="Unique name for the configuration",
        examples=["production-config", "development-config"],
        min_length=1,
        max_length=100,
    )
    description: str | None = Field(
        None,
        description="Description of the configuration",
        examples=["Production configuration for data flywheel"],
        max_length=500,
    )
    config_type: str = Field(
        ...,
        # Bare ``str``, not a Literal: the authoritative gate is
        # ``build_job_config`` against the config registry.
        description="Type of configuration (e.g., llm_job, embedding_job, aoi_job, its_job)",
        examples=["llm_job", "embedding_job", "aoi_job", "its_job"],
        max_length=50,
    )
    config_data: dict[str, Any] = Field(
        ...,
        description="Job-level configuration data as dictionary  - will be validated and converted to proper object",
        examples=[
            {
                "nims": [
                    {
                        "model_name": "meta/llama-3.2-1b-instruct",
                        "model_type": "llm",
                        "context_length": 8192,
                        "gpus": 1,
                        "pvc_size": "25Gi",
                        "tag": "1.8.3",
                    }
                ],
                "data_split_config": {"eval_size": 100, "val_ratio": 0.1, "min_total_records": 50},
                "icl_config": {
                    "max_context_length": 8192,
                    "reserved_tokens": 4096,
                    "max_examples": 3,
                    "min_examples": 1,
                },
            },
            {
                "nims": [
                    {
                        "model_name": "nvidia/llama-3.2-nv-embedqa-1b-v2",
                        "model_type": "embedding",
                        "context_length": 32768,
                        "gpus": 1,
                        "pvc_size": "25Gi",
                        "tag": "1.9.0",
                    }
                ],
                "data_split_config": {
                    "val_ratio": 0.05,
                    "min_total_records": 50,
                },
                "training_config": {
                    "training_type": "sft",
                    "finetuning_type": "all_weights",
                    "epochs": 1,
                    "batch_size": 256,
                    "learning_rate": 0.000005,
                    "micro_batch_size": 1,
                    "tensor_parallel_size": 1,
                    "pipeline_parallel_size": 1,
                    "use_sequence_parallel": False,
                    "training_precision": "bf16-mixed",
                },
            },
        ],
    )

    @field_validator("config_data")
    @classmethod
    def validate_config_data(cls, v: dict[str, Any], info) -> dict[str, Any]:
        """Validate config_data by building the typed job config from the contract.

        Uses the central ``src.config`` registry (the config schemas are the shared
        contract). Backend gating — whether this deployment accepts the config type —
        is enforced separately at the API edge via ``assert_supported_type``.
        """
        config_type = info.data.get("config_type", "llm_job")

        try:
            build_job_config(config_type, v)
        except ValidationError as e:
            raise ValueError(f"Invalid configuration data for {config_type}: {e}") from e
        except ValueError:
            raise
        except Exception as e:
            raise ValueError(f"Unexpected error validating {config_type}: {e}") from e

        return v


class ConfigurationResponse(BaseModel):
    """Response model for configuration operations."""

    id: str = Field(
        ...,
        description="Unique identifier of the configuration",
        examples=["65f8a1b2c3d4e5f6a7b8c9d0"],
    )
    name: str = Field(
        ...,
        description="Name of the configuration",
        examples=["production-config"],
    )
    description: str | None = Field(
        None,
        description="Description of the configuration",
        examples=["Production configuration for data flywheel"],
    )
    config_type: str = Field(
        ...,
        description="Type of configuration",
        examples=["llm_job"],
    )
    config_data: dict[str, Any] = Field(
        ...,
        description="Job-level configuration data as dictionary (serialized from validated object)",
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when the configuration was created",
        examples=["2024-03-15T14:30:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="Timestamp when the configuration was last updated",
        examples=["2024-03-15T15:30:00Z"],
    )


class ConfigurationSummary(BaseModel):
    """Summary model for listing configurations."""

    id: str = Field(
        ...,
        description="Unique identifier of the configuration",
        examples=["65f8a1b2c3d4e5f6a7b8c9d0"],
    )
    name: str = Field(
        ...,
        description="Name of the configuration",
        examples=["production-config"],
    )
    description: str | None = Field(
        None,
        description="Description of the configuration",
        examples=["Production configuration for data flywheel"],
    )
    config_type: str = Field(
        ...,
        description="Type of configuration",
        examples=["llm_job"],
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp when the configuration was created",
        examples=["2024-03-15T14:30:00Z"],
    )
    updated_at: datetime = Field(
        ...,
        description="Timestamp when the configuration was last updated",
        examples=["2024-03-15T15:30:00Z"],
    )


class ConfigurationListResponse(BaseModel):
    """Response model for listing all configurations."""

    configurations: list[ConfigurationSummary] = Field(
        ..., description="List of configuration summaries"
    )
    total_count: int = Field(..., description="Total number of configurations", examples=[5])


class ConfigurationDeleteResponse(BaseModel):
    """Response model for configuration deletion."""

    message: str = Field(
        ...,
        description="Confirmation message",
        examples=["Configuration 'production-config' successfully deleted"],
    )
    deleted_config: str = Field(
        ...,
        description="Name of the deleted configuration",
        examples=["production-config"],
    )

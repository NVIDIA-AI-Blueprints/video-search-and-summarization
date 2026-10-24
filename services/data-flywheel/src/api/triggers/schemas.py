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

"""API request and response schemas for triggers."""

import math
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.job_types import is_deft_job_type
from src.lib.ingestion import DatasetType, IngestionConfig
from src.tasks.triggers.cron_utils import validate_cron

__all__ = [
    "ExecutionListResponse",
    "ExecutionResponse",
    "NIMExecutionResultResponse",
    "TriggerCreateRequest",
    "TriggerExecuteResponse",
    "TriggerListResponse",
    "TriggerResponse",
    "TriggerUpdateRequest",
]


def _validate_metric_thresholds(thresholds: dict[str, float] | None) -> dict[str, float] | None:
    """Validate metric_thresholds: reject empty dicts and NaN/inf values."""
    if thresholds is None:
        return None
    if len(thresholds) == 0:
        raise ValueError("metric_thresholds cannot be empty")
    for key, value in thresholds.items():
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"metric_thresholds['{key}'] cannot be NaN or Inf")
    return thresholds


class TriggerCreateRequest(BaseModel):
    """Request to create a new trigger."""

    # The runtime rule "config_name is required for embedding/LLM, optional for
    # the DEFT family" (enforced in validate_trigger_type_requirements) is not
    # expressible by a single field schema, so surface it in the OpenAPI contract
    # via a job_type-discriminated ``oneOf``. The two branch enums must stay
    # disjoint — ``oneOf`` means "matches exactly one", so a job type listed in
    # both would make every request carrying a config_name validate false.
    model_config = ConfigDict(
        json_schema_extra={
            "oneOf": [
                {
                    "properties": {
                        "job_type": {"enum": ["aoi_job", "its_job"]},
                        "config_name": {
                            "anyOf": [{"type": "string", "minLength": 1}, {"type": "null"}]
                        },
                    },
                },
                {
                    "properties": {
                        "job_type": {"enum": ["embedding_job", "llm_job"]},
                        "config_name": {"type": "string", "minLength": 1},
                    },
                    "required": ["config_name"],
                },
            ],
        }
    )

    name: str = Field(..., min_length=1, max_length=100, description="Unique name for this trigger")
    description: str | None = Field(None, max_length=500, description="Human-readable description")

    # Target
    workload_id: str = Field(..., min_length=1, description="Target workload identifier")
    client_id: str = Field(..., min_length=1, description="Target client identifier")
    job_type: Literal["embedding_job", "llm_job", "aoi_job", "its_job"] = Field(
        ..., description="Type of job to trigger"
    )

    # Trigger type
    trigger_type: Literal["data_threshold", "metric_degradation", "schedule"] = Field(
        ..., description="Type of trigger condition"
    )
    eval_schedule: str = Field(..., description="Cron expression (e.g., '0 0 * * 0' for weekly)")

    # Type-specific fields (optional, depends on trigger_type)
    metric_thresholds: dict[str, float] | None = Field(
        default=None, description="Metric thresholds for metric_degradation trigger"
    )
    min_new_records: int | None = Field(
        default=None, ge=1, description="Minimum new records for data_threshold trigger"
    )

    # Common fields
    enable_data_ingestion: bool = Field(
        default=True, description="Whether to run data ingestion before evaluation"
    )
    ingestion_timeout_minutes: int = Field(
        default=30, ge=1, description="Ingestion timeout in minutes"
    )
    ingestion_config: IngestionConfig | None = Field(
        default=None, description="Optional ingestion plugin configuration"
    )

    data_window_months: int | Literal["all"] | None = Field(
        default=None,
        ge=1,
        description="Time window in months (≥1), 'all' for all data, or null for config defaults",
    )
    config_name: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Configuration name to use for the job. Required for embedding/LLM "
            "jobs; optional for aoi_job / its_job (None uses that job type's "
            "default config)."
        ),
    )
    reference_job_id: str | None = Field(
        default=None,
        min_length=1,
        description=(
            "Optional job ID from THIS trigger's history. "
            "Used to reference a specific training run's models."
        ),
    )
    model_source: Literal["fine_tuned", "base"] = Field(
        default="base", description="Whether to use fine-tuned or base model"
    )
    enabled: bool = Field(default=True, description="Whether this trigger is active")
    max_consecutive_failures: int = Field(
        default=3, ge=1, description="Max consecutive failures before disabling"
    )

    @field_validator("eval_schedule")
    @classmethod
    def validate_eval_schedule(cls, v: str) -> str:
        validate_cron(v)
        return v

    @field_validator("metric_thresholds")
    @classmethod
    def validate_metric_thresholds_values(
        cls, v: dict[str, float] | None
    ) -> dict[str, float] | None:
        return _validate_metric_thresholds(v)

    @model_validator(mode="after")
    def validate_trigger_type_requirements(self) -> "TriggerCreateRequest":
        # Mirror RetrainingTrigger's rules (models.py) so invalid combinations
        # fail with a clean 422 at the API edge instead of 500-ing deeper when
        # the persisted model re-validates.
        if is_deft_job_type(self.job_type):
            # DEFT-family job types have no ingestion path (data lands in the
            # Storage Backend catalog) and no metric_degradation (online eval
            # drives promotion).
            if self.ingestion_config is not None:
                raise ValueError(
                    f"ingestion_config is not supported for {self.job_type} triggers — "
                    "DEFT data lands in the Storage Backend catalog, not via "
                    "ingestion plugins"
                )
            if self.trigger_type == "metric_degradation":
                raise ValueError(
                    f"metric_degradation triggers are not supported for {self.job_type} — "
                    "online evaluation drives DEFT promotion verdicts instead"
                )
        elif not self.config_name:
            raise ValueError(f"config_name is required for {self.job_type} triggers")

        if self.trigger_type == "data_threshold":
            if not self.min_new_records:
                raise ValueError("min_new_records is required for data_threshold trigger")
            # The DEFT family is excluded: it counts new records from the Storage
            # Backend catalog, not from an ingestion run.
            if not is_deft_job_type(self.job_type) and not self.enable_data_ingestion:
                raise ValueError("enable_data_ingestion must be True for data_threshold trigger")

        if self.trigger_type == "metric_degradation" and not self.metric_thresholds:
            raise ValueError("metric_thresholds is required for metric_degradation trigger")

        if self.enable_data_ingestion and self.ingestion_config:
            DatasetType.validate_for_job(self.ingestion_config.dataset_type, self.job_type)

        return self


class TriggerUpdateRequest(BaseModel):
    """Request to update an existing trigger."""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    eval_schedule: str | None = Field(default=None, min_length=1)
    metric_thresholds: dict[str, float] | None = None
    min_new_records: int | None = Field(default=None, ge=1)
    enable_data_ingestion: bool | None = None
    ingestion_timeout_minutes: int | None = Field(default=None, ge=1)
    ingestion_config: IngestionConfig | None = Field(default=None)
    data_window_months: int | Literal["all"] | None = Field(default=None, ge=1)
    config_name: str | None = Field(default=None, min_length=1)
    reference_job_id: str | None = Field(default=None, min_length=1)
    model_source: Literal["fine_tuned", "base"] | None = None
    enabled: bool | None = None
    max_consecutive_failures: int | None = Field(default=None, ge=1)

    @field_validator("eval_schedule")
    @classmethod
    def validate_eval_schedule(cls, v: str | None) -> str | None:
        if v is not None:
            validate_cron(v)
        return v

    @field_validator("metric_thresholds")
    @classmethod
    def validate_metric_thresholds_values(
        cls, v: dict[str, float] | None
    ) -> dict[str, float] | None:
        return _validate_metric_thresholds(v)


class TriggerResponse(BaseModel):
    """Response with trigger details."""

    id: str
    name: str
    description: str | None
    workload_id: str
    client_id: str
    job_type: str
    trigger_type: str
    eval_schedule: str
    metric_thresholds: dict[str, float] | None
    min_new_records: int | None
    enable_data_ingestion: bool
    ingestion_timeout_minutes: int
    ingestion_config: dict[str, Any] | None  # Serialized IngestionConfig
    data_window_months: int | Literal["all"] | None
    # Nullable to match the request/persisted models: a DEFT-family trigger on its
    # job type's default config has config_name=None, and _trigger_to_response
    # copies it verbatim — a required str here 500s every read/write path.
    config_name: str | None
    reference_job_id: str | None
    model_source: str
    latest_fine_tuned_models: dict[str, str | None]
    enabled: bool
    max_consecutive_failures: int
    created_at: datetime
    updated_at: datetime
    last_execution_time: datetime | None
    last_execution_status: str | None
    consecutive_failures: int


class TriggerListResponse(BaseModel):
    """Response with list of triggers."""

    triggers: list[TriggerResponse]
    total: int


class NIMExecutionResultResponse(BaseModel):
    """Per-NIM execution result."""

    model_name: str
    model_used: str | None = None
    metrics: dict[str, float]
    thresholds_checked: dict[str, dict]
    passed_thresholds: bool
    needs_retraining: bool
    training_triggered: bool
    customization_id: str | None
    fine_tuned_model: str | None
    post_training_metrics: dict[str, float]
    incomplete_evaluation: bool
    eval_error: str | None
    customization_error: str | None
    post_eval_error: str | None
    skip_reason: str | None


class ExecutionResponse(BaseModel):
    """Response with execution details."""

    id: str
    trigger_id: str
    trigger_name: str
    trigger_type: str
    execution_time: datetime
    completed_at: datetime | None
    new_records_ingested: int
    ingestion_duration_seconds: float | None
    eval_job_id: str | None
    eval_job_status: str | None
    training_job_id: str | None
    training_job_status: str | None
    nim_results: list[NIMExecutionResultResponse]
    total_nims: int
    nims_passed: int
    nims_failed: int
    nims_retrained: int
    all_thresholds_met: bool
    triggered_training: bool
    decision_reason: str | None
    skip_reason: str | None
    data_window_used: str | None
    status: str
    error: str | None
    eval_duration_seconds: float | None
    total_duration_seconds: float | None


class ExecutionListResponse(BaseModel):
    """Response with list of executions."""

    executions: list[ExecutionResponse]
    total: int


class TriggerExecuteResponse(BaseModel):
    """Response when manually executing a trigger."""

    message: str
    execution_id: str
    status: str

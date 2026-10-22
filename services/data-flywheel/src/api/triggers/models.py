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

"""MongoDB data models for triggers."""

from datetime import datetime
from typing import Literal

from bson import ObjectId
from pydantic import BaseModel, Field, model_validator

from src.job_types import is_deft_job_type

# Import ingestion models from centralized location
from src.lib.ingestion import DatasetType, IngestionConfig

# Re-export for backward compatibility
__all__ = [
    "IngestionConfig",
    "NIMExecutionResult",
    "RetrainingTrigger",
    "TriggerExecution",
]


# =============================================================================
# Trigger Models
# =============================================================================


class RetrainingTrigger(BaseModel):
    """MongoDB document for trigger configuration (supports both pipelines)."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    name: str = Field(..., min_length=1, max_length=100, description="Unique name for this trigger")
    description: str | None = Field(None, max_length=500)

    # Target
    workload_id: str = Field(..., description="Workload to monitor")
    client_id: str = Field(..., description="Client identifier")

    # Job type — NMP pipelines (embedding/LLM) or a DEFT-backed loop (aoi_job, its_job)
    job_type: Literal["embedding_job", "llm_job", "aoi_job", "its_job"] = Field(
        ...,
        description=(
            "Type of job to monitor and retrain (embedding or LLM pipeline, "
            "or a DEFT loop — AOI / ITS)"
        ),
    )

    # ===== TRIGGER TYPE =====
    trigger_type: Literal["data_threshold", "metric_degradation", "schedule"] = Field(
        ...,
        description="""
        Type of trigger determines execution behavior:
        - data_threshold: Check min records, if met → eval + train (always)
        - metric_degradation: Ingest → eval → compare → train only if metrics fail
        - schedule: Ingest → eval + train (always, no conditions)
        """,
    )

    # ===== TRIGGER TYPE 1: SCHEDULE =====
    eval_schedule: str = Field(
        ...,
        description="Cron expression for when to run (e.g., '0 0 * * 0' for weekly)",
        pattern=r"^(\S+\s+){4}\S+$",  # Basic cron validation
    )

    # ===== TRIGGER TYPE 2: PERFORMANCE DEGRADATION =====
    metric_thresholds: dict[str, float] | None = Field(
        default=None,
        description="""
        For metric_degradation type only. Metric name -> minimum acceptable value.
        ANY metric below threshold triggers retraining.
        Examples:
        - Embedding: {"recall_5": 0.85, "map": 0.75}
        - LLM completion: {"similarity": 8.0}
        - LLM tool calling: {"function_name": 0.95}
        - LLM DPO: {"preference_score": 8.0}
        """,
    )

    # ===== TRIGGER TYPE 3: DATA THRESHOLD =====
    min_new_records: int | None = Field(
        default=None,
        ge=1,
        description="""
        For data_threshold type only. Minimum number of new records required.
        If new_records >= this value, run eval + training.
        Example: 1000 means only run when at least 1000 new samples available.
        """,
    )

    # ===== COMMON FIELDS FOR ALL TRIGGER TYPES =====
    enable_data_ingestion: bool = Field(
        default=True, description="Trigger data ingestion before execution (all types use this)"
    )

    ingestion_timeout_minutes: int = Field(
        default=30, ge=1, description="Maximum time to wait for data ingestion to complete"
    )

    # Ingestion plugin configuration
    ingestion_config: IngestionConfig | None = Field(
        default=None,
        description="Configuration for the ingestion plugin (plugin_name, config, etc.)",
    )

    # Data window for training - ONLY FOR EMBEDDING JOBS
    data_window_months: int | Literal["all"] | None = Field(
        default=None,
        ge=1,
        description="""
        Time window for training data (embedding jobs only).
        - Integer (≥1): Number of months to look back (e.g., 1, 3, 6, 12)
        - "all": Use all available data
        - null: Use config defaults
        Examples: 1 (last month), 3 (last quarter), 6, 12 (last year), 24 (2 years)
        """,
    )

    # Configuration reference. Required for NMP job types (enforced by the
    # validator below); optional for the DEFT family, where None means that job
    # type's own ``<job_type>_config:`` block in config.deft.yaml.
    config_name: str | None = Field(
        default=None,
        min_length=1,  # parity with TriggerCreateRequest: reject "" even when constructed directly
        description=(
            "Name of job config (training_enabled/eval_only controlled by trigger). "
            "Optional for aoi_job / its_job — None uses the deployment's default "
            "config for that job type."
        ),
    )

    # ===== JOB REFERENCE (optional - for model selection from previous jobs) =====
    reference_job_id: str | None = Field(
        default=None,
        description="Optional: Specific job ID to get model from. Must be from this trigger's execution history. If not provided, uses latest job from this trigger with fine-tuning completed.",
    )

    # ===== MODEL SELECTION =====
    model_source: Literal["fine_tuned", "base"] = Field(
        default="base",
        description="""
        Which model to use for evaluation/training:
        - base: Use base model from job config's customizer_configs.target field (default)
        - fine_tuned: Use fine-tuned model from stored latest_fine_tuned_models or reference job
                      (currently only supported for embedding_job)
        """,
    )

    # ===== LATEST FINE-TUNED MODELS (auto-updated after each training) =====
    latest_fine_tuned_models: dict[str, str | None] = Field(
        default_factory=dict,
        description="""
        Per-model latest fine-tuned model names: {model_name: fine_tuned_model_name | None}.
        Key is the model name (NIM identifier, e.g., "meta/llama-3.2-1b-instruct").
        Value is a string: fine-tuned model name (e.g., "dfwbp/model@cust-abc123").
        Can be None if not yet fine-tuned.
        Note: Only model names are stored. The pipeline constructs the full target config
        (with model_uri, base_model, etc.) from the configuration using this model name.
        Auto-updated after each successful training run.
        """,
    )

    # Control flags
    enabled: bool = Field(default=True, description="Enable/disable trigger")
    max_consecutive_failures: int = Field(
        default=3, ge=1, description="Disable trigger after N consecutive failures"
    )

    # Metadata
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str | None = None

    # Execution tracking
    last_execution_time: datetime | None = None
    last_execution_status: Literal["success", "failure", "triggered_retraining"] | None = None
    consecutive_failures: int = Field(default=0, ge=0)

    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str},
        "populate_by_name": True,
    }

    @model_validator(mode="after")
    def validate_trigger_type_requirements(self) -> "RetrainingTrigger":
        """Validate that required fields are present based on trigger_type."""
        if is_deft_job_type(self.job_type):
            # DEFT-family triggers have no ingestion path: the data plane is the
            # Storage Backend catalog, and data_threshold reads its live stats
            # (records since the last snapshot) instead of ingesting.
            if self.ingestion_config is not None:
                raise ValueError(
                    f"ingestion_config is not supported for {self.job_type} triggers — "
                    "DEFT data lands in the Storage Backend catalog, not via "
                    "ingestion plugins"
                )
            self.enable_data_ingestion = False
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
            # The DEFT family is excluded: the branch above force-sets
            # enable_data_ingestion=False for it, so a narrower `!= "aoi_job"`
            # test would 422 its_job on a value this validator itself just set.
            if not is_deft_job_type(self.job_type) and not self.enable_data_ingestion:
                raise ValueError(
                    "enable_data_ingestion must be True for data_threshold trigger "
                    "(cannot check new records without ingestion)"
                )

        if self.trigger_type == "metric_degradation" and not self.metric_thresholds:
            raise ValueError("metric_thresholds is required for metric_degradation trigger")

        # Validate model_source: fine_tuned is only supported for embedding jobs
        if self.model_source == "fine_tuned" and self.job_type != "embedding_job":
            raise ValueError(
                "model_source='fine_tuned' is currently only supported for embedding_job. "
                "For llm_job, use model_source='base'."
            )

        if self.enable_data_ingestion and self.ingestion_config:
            DatasetType.validate_for_job(self.ingestion_config.dataset_type, self.job_type)

        return self

    def to_mongo(self) -> dict:
        """Convert to MongoDB document."""
        return self.model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def from_mongo(cls, doc: dict) -> "RetrainingTrigger | None":
        """Create from MongoDB document."""
        if not doc:
            return None
        return cls(**doc)


class NIMExecutionResult(BaseModel):
    """
    Per-NIM execution result within a trigger execution.

    For LLM jobs with multiple NIMs, each NIM is evaluated independently.

    Retraining behavior: If training is triggered, ALL NIMs are retrained together.
    The trigger condition varies by type:
    - data_threshold: Triggered if new_records >= min_new_records
    - metric_degradation: Triggered if ANY NIM fails its thresholds
    - schedule: Always triggered (unconditional)

    Once triggered, the training job retrains all NIMs in the configuration.
    """

    model_name: str = Field(..., description="Model name (NIM identifier) for this NIM")
    model_used: str | None = Field(
        default=None,
        description="Model that was actually used for evaluation/training (base model or previous fine-tuned model)",
    )

    # Evaluation results for this NIM
    metrics: dict[str, float] = Field(
        default_factory=dict, description="Evaluation metrics for this NIM"
    )
    thresholds_checked: dict[str, dict] = Field(
        default_factory=dict,
        description="Per-metric threshold results: {metric: {threshold, actual, passed}}",
    )
    passed_thresholds: bool = Field(
        default=True, description="True if all thresholds passed for this NIM"
    )

    # Training decision for this NIM
    needs_retraining: bool = Field(
        default=False, description="True if this NIM failed thresholds and needs retraining"
    )
    training_triggered: bool = Field(
        default=False, description="True if training was actually triggered for this NIM"
    )
    customization_id: str | None = Field(
        default=None, description="Customization ID if training was triggered"
    )
    fine_tuned_model: str | None = Field(
        default=None, description="Name of the fine-tuned model produced by training"
    )

    # Post-training evaluation results (customized-eval)
    post_training_metrics: dict[str, float] = Field(
        default_factory=dict,
        description="Evaluation metrics for the fine-tuned model (from customized-eval)",
    )

    # Evaluation completeness tracking
    incomplete_evaluation: bool = Field(
        default=False,
        description="True if evaluation is incomplete (e.g., configured metrics missing from results)",
    )

    # Error tracking
    eval_error: str | None = Field(
        default=None, description="Error message if pre-training evaluation failed"
    )
    customization_error: str | None = Field(
        default=None, description="Error message if customization/training failed"
    )
    post_eval_error: str | None = Field(
        default=None, description="Error message if post-training evaluation failed"
    )

    skip_reason: str | None = Field(
        default=None, description="Reason training was skipped for this NIM"
    )


class TriggerExecution(BaseModel):
    """MongoDB document for trigger execution history."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    trigger_id: str = Field(..., description="Reference to parent trigger")
    trigger_name: str = Field(..., description="Snapshot of trigger name")
    trigger_type: Literal["data_threshold", "metric_degradation", "schedule"] = Field(
        ..., description="Type of trigger that was executed"
    )

    # Execution metadata
    execution_time: datetime = Field(
        default_factory=datetime.utcnow, description="When this execution started"
    )
    completed_at: datetime | None = None

    # Data ingestion phase
    new_records_ingested: int = Field(
        default=0, description="Number of new records ingested before this evaluation"
    )
    available_records_since_last_training: int = Field(
        default=0, description="Total records available since last successful training"
    )
    ingestion_duration_seconds: float | None = None

    # Job references
    eval_job_id: str | None = None
    eval_job_status: str | None = None  # "completed", "failed", etc.
    training_job_id: str | None = None
    training_job_status: str | None = None

    # ===== PER-NIM RESULTS (for multi-NIM jobs like LLM) =====
    nim_results: list[NIMExecutionResult] = Field(
        default_factory=list,
        description="Per-NIM evaluation and training results. For embedding jobs, typically 1 NIM.",
    )

    # ===== SUMMARY FIELDS (aggregated from nim_results) =====
    total_nims: int = Field(default=0, description="Total number of NIMs evaluated")
    nims_passed: int = Field(default=0, description="NIMs that passed all thresholds")
    nims_failed: int = Field(default=0, description="NIMs that failed thresholds")
    nims_retrained: int = Field(default=0, description="NIMs that were retrained")

    # Overall decision (aggregated)
    all_thresholds_met: bool = Field(
        default=False, description="True if ALL NIMs passed all thresholds"
    )
    triggered_training: bool = Field(default=False, description="True if ANY NIM was retrained")
    decision_reason: str | None = None  # Human-readable explanation

    # Training skip reason (if no training despite failures)
    skip_reason: str | None = Field(
        default=None,
        description="Reason training was skipped (e.g., 'Insufficient new records: 50 < 1000')",
    )
    data_window_used: str | None = None  # Only populated for embedding jobs
    date_range_used: dict | None = (
        None  # {"from": "2024-01-01", "to": "2024-03-31"}, embedding only
    )

    # Overall execution status
    status: Literal["pending", "running", "completed", "failed", "skipped"] = "pending"
    error: str | None = None

    # Performance tracking
    eval_duration_seconds: float | None = None
    total_duration_seconds: float | None = None

    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {ObjectId: str},
        "populate_by_name": True,
    }

    def to_mongo(self) -> dict:
        """Convert to MongoDB document."""
        return self.model_dump(by_alias=True, exclude_none=True)

    @classmethod
    def from_mongo(cls, doc: dict) -> "TriggerExecution | None":
        """Create from MongoDB document."""
        if not doc:
            return None
        return cls(**doc)

    def update_summary_from_nim_results(self):
        """Recalculate summary fields from nim_results."""
        self.total_nims = len(self.nim_results)
        self.nims_passed = sum(1 for r in self.nim_results if r.passed_thresholds)
        self.nims_failed = sum(1 for r in self.nim_results if not r.passed_thresholds)
        self.nims_retrained = sum(1 for r in self.nim_results if r.training_triggered)
        self.all_thresholds_met = self.nims_failed == 0 and self.total_nims > 0
        self.triggered_training = self.nims_retrained > 0

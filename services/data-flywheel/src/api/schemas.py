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
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from src.config import DataSplitConfig, EmbeddingDataSplitConfig
from src.job_types import JobType, is_deft_job_type
from src.lib.ingestion import IngestionConfig

# ============================================================================
# ENUMS (Shared between API and Database)
# ============================================================================


class DeploymentStatus(str, Enum):
    """Status details of the deployment."""

    CREATED = "created"
    PENDING = "pending"
    RUNNING = "running"
    CANCELLED = "cancelled"
    FAILED = "failed"
    COMPLETED = "completed"
    READY = "ready"
    UNKNOWN = "unknown"


class NIMRunStatus(str, Enum):
    """Status of a NIM run workflow."""

    PENDING = "pending"
    DEPLOYING = "deploying-nim"
    RUNNING = "running-evals"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    FAILED = "failed"


class FlywheelRunStatus(str, Enum):
    """Status of a Flywheel run workflow."""

    PENDING = "pending"
    RUNNING = "running"
    # DEFT family only: the pipeline chain finished and the candidate is in the
    # hands of the Promotion Controller (shadow/canary/full rollout). The run
    # turns COMPLETED/FAILED only when the promotion reaches a terminal state.
    PROMOTING = "promoting"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# ============================================================================
# API REQUEST MODELS
# ============================================================================


class JobRequest(BaseModel):
    """Request model for creating a new job."""

    workload_id: str | None = Field(
        default=None,
        description="The unique identifier of the workload to process. Required for "
        "llm_job / embedding_job; not used by aoi_job / its_job (data comes from "
        "an object-store snapshot).",
        examples=["workload_123"],
    )

    client_id: str | None = Field(
        default=None,
        description="The unique identifier of the client to process. Required for "
        "llm_job / embedding_job; optional for aoi_job / its_job.",
        examples=["client_123"],
    )

    data_split_config: EmbeddingDataSplitConfig | DataSplitConfig | None = Field(
        None,
        description="Optional configuration for data splitting. If not provided, default config will be used.",
    )

    job_type: JobType = Field(
        default=JobType.LLM,
        description="Type of job to run - 'llm_job', 'embedding_job', 'aoi_job', or 'its_job'",
        examples=["llm_job", "embedding_job", "aoi_job", "its_job"],
    )

    config_name: str | None = Field(
        None,
        description="Optional name of the configuration to use for this job. If not provided, default config will be used.",
        examples=["my-llm-config", "production-config"],
    )

    ingestion_config: IngestionConfig | None = Field(default=None)

    @model_validator(mode="after")
    def _require_workload_and_client_for_nmp_jobs(self) -> "JobRequest":
        """LLM/embedding jobs still require workload_id + client_id; DEFT ones do not.

        A family test, not ``!= AOI``: every DEFT job type draws its data from an
        object-store snapshot addressed by the job config, so an AOI equality
        check would make a new DEFT job type inherit the NMP requirement and 422.
        """
        if not is_deft_job_type(self.job_type):
            missing = [f for f in ("workload_id", "client_id") if not getattr(self, f)]
            if missing:
                raise ValueError(f"{', '.join(missing)} required for job_type '{self.job_type}'")
        return self


# ============================================================================
# API RESPONSE MODELS
# ============================================================================


class JobResponse(BaseModel):
    """Response model for job creation."""

    id: str = Field(
        ...,
        description="The unique identifier of the created job",
        examples=["65f8a1b2c3d4e5f6a7b8c9d0"],
    )
    status: str = Field(
        ...,
        description="Current status of the job",
        examples=["queued"],
        enum=["queued", "running", "completed", "failed"],
    )
    message: str = Field(
        ...,
        description="Human-readable message about the job status",
        examples=["NIM workflow started"],
    )


class JobDeleteResponse(BaseModel):
    """Response model for job deletion."""

    id: str = Field(
        ...,
        description="The unique identifier of the created job",
        examples=["65f8a1b2c3d4e5f6a7b8c9d0"],
    )
    message: str = Field(
        ...,
        description="Human-readable message about the job status",
        examples=["Job deletion started."],
    )


class JobCancelResponse(BaseModel):
    """Response model for job cancellation."""

    id: str = Field(
        ...,
        description="The unique identifier of the job",
        examples=["65f8a1b2c3d4e5f6a7b8c9d0"],
    )
    message: str = Field(
        ...,
        description="Human-readable message about the cancellation",
        examples=["Job cancellation initiated"],
    )


class Dataset(BaseModel):
    """Model representing a dataset."""

    name: str = Field(..., description="Name of the dataset", examples=["dataset_123"])
    num_records: int = Field(
        ..., description="Number of records in the dataset", examples=[1000], ge=0
    )
    nmp_uri: str | None = Field(
        None,
        description="URI of the dataset stored in NMP",
        examples=["https://nmp.host/v1/datasets/dataset-123"],
    )


class JobListItem(BaseModel):
    """Model representing a job in the list of jobs."""

    id: str = Field(
        ..., description="The unique identifier of the job", examples=["65f8a1b2c3d4e5f6a7b8c9d0"]
    )
    workload_id: str | None = Field(
        None,
        description=(
            "The unique identifier of the workload being processed (absent for aoi_job / its_job)"
        ),
        examples=["workload_123"],
    )
    client_id: str | None = Field(
        None,
        description=(
            "The unique identifier of the client to process (absent for aoi_job / its_job)"
        ),
        examples=["client_123"],
    )
    status: str = Field(
        ...,
        description="Current status of the job",
        examples=["running", "completed", "failed"],
    )
    started_at: datetime | None = Field(
        ...,
        description="Timestamp when the job started processing",
        examples=["2024-03-15T14:30:00Z"],
    )
    finished_at: datetime | None = Field(
        None,
        description="Timestamp when the job completed or failed",
        examples=["2024-03-15T15:30:00Z"],
    )
    runtime_seconds: float = Field(
        ...,
        description="Time taken to complete the job in seconds",
        examples=[300.5],
        ge=0.0,
    )
    datasets: list[Dataset] = Field(
        default_factory=list, description="List of datasets used in this job"
    )
    error: str | None = Field(
        None,
        description="Error message if the job failed",
        examples=["Job failed: Timeout"],
    )


class JobsListResponse(BaseModel):
    """Response model for listing all jobs."""

    jobs: list[JobListItem] = Field(..., description="List of all jobs, both active and completed")


class Evaluation(BaseModel):
    """Model representing an evaluation result for a NIM."""

    eval_type: str = Field(
        ...,
        description="Type of evaluation performed",
        examples=["accuracy"],
    )

    dataset: str = Field(
        ...,
        description="Name of the dataset used for the evaluation",
        examples=["dataset_123"],
    )

    eval_name: str | None = Field(
        None,
        description="Name of the custom evaluation from config",
        examples=["custom-eval-dataset"],
    )

    scores: dict[str, float] = Field(
        default_factory=dict,
        description="Dictionary of scores for the evaluation",
        examples=[{"score": 0.85, "function_name_and_args_accuracy": 0.95}],
    )

    started_at: datetime = Field(
        ..., description="Timestamp when the evaluation started", examples=["2024-03-15T14:30:00Z"]
    )
    finished_at: datetime | None = Field(
        None,
        description="Timestamp when the evaluation completed",
        examples=["2024-03-15T14:35:00Z"],
    )
    runtime_seconds: float = Field(
        ...,
        description="Time taken to complete the evaluation in seconds",
        examples=[300.5],
        ge=0.0,
    )
    progress: float = Field(
        ...,
        description="Progress of the evaluation as a percentage",
        examples=[100.0],
        ge=0.0,
        le=100.0,
    )
    nmp_uri: str | None = Field(
        None,
        description="URI of the evaluation job in NMP",
        examples=["https://nmp.host/v1/evaluation/jobs/eval-123"],
    )
    mlflow_uri: str | None = Field(
        None,
        description="URI of the MLflow experiment for this evaluation",
        examples=["http://localhost:5000/#/experiments/123"],
    )
    error: str | None = Field(
        None,
        description="Error message if the evaluation failed",
        examples=["Evaluation failed: Timeout"],
    )


class Customization(BaseModel):
    """Model representing a customization result for a NIM."""

    started_at: datetime = Field(
        ...,
        description="Timestamp when the customization started",
        examples=["2024-03-15T14:30:00Z"],
    )
    finished_at: datetime | None = Field(
        None,
        description="Timestamp when the customization completed",
        examples=["2024-03-15T14:35:00Z"],
    )
    runtime_seconds: float = Field(
        ...,
        description="Time taken to complete the customization in seconds",
        examples=[300.5],
        ge=0.0,
    )
    progress: float = Field(
        ...,
        description="Progress of the customization as a percentage",
        examples=[100.0],
        ge=0.0,
        le=100.0,
    )
    epochs_completed: int = Field(
        ..., description="Number of epochs completed", examples=[10], ge=0
    )
    steps_completed: int = Field(..., description="Number of steps completed", examples=[100], ge=0)
    nmp_uri: str | None = Field(
        None,
        description="URI of the customization job in NMP",
        examples=["https://nmp.host/v1/customization/jobs/custom-123"],
    )
    customized_model: str | None = Field(
        None,
        description="Name of the customized model",
        examples=["customized-gpt-4"],
    )
    error: str | None = Field(
        None,
        description="Error message if the customization failed",
        examples=["Customization failed: Timeout"],
    )


class NIMResponse(BaseModel):
    """Model representing a NIM and its evaluations."""

    model_name: str = Field(..., description="Name of the NIM model", examples=["gpt-4"])

    status: NIMRunStatus = Field(
        ...,
        description="Status of the NIM run",
        examples=["pending", "running", "completed", "error"],
    )

    deployment_status: DeploymentStatus = Field(
        ..., description="Status of the NIM deployment", examples=["deployed"]
    )

    runtime_seconds: float = Field(
        ...,
        description="Time taken for the NIM run in seconds",
        examples=[300.5],
        ge=0.0,
    )

    evaluations: list[Evaluation] = Field(
        default_factory=list, description="List of evaluations performed on this NIM"
    )
    customizations: list[Customization] = Field(
        default_factory=list, description="List of customizations performed on this NIM"
    )

    error: str | None = Field(
        None,
        description="Error message if the NIM failed",
        examples=["NIM failed: Deployment timeout"],
    )


class LLMJudgeResponse(BaseModel):
    """Model representing a LLM Judge status"""

    model_name: str = Field(..., description="Name of the LLM Judge model", examples=["gpt-4"])
    type: str = Field(..., description="Type of LLM Judge", examples=["remote", "local"])

    deployment_status: DeploymentStatus = Field(
        ..., description="Status of the LLM Judge deployment", examples=["deployed"]
    )
    error: str | None = Field(
        None,
        description="Error message if the LLM Judge failed",
        examples=["LLM Judge failed: Connection timeout"],
    )


class DeftRunDetailResponse(BaseModel):
    """DEFT-side detail for a flywheel run."""

    run_id: str | None = Field(
        None,
        description=(
            "DEFT fine-tune id backing this run. Use it against the DEFT "
            "service's /v1/fine-tune/{id}/... endpoints."
        ),
        examples=["841eb4bc-e90f-4f8f-aa45-97a113df314d"],
    )
    progress: dict[str, Any] | None = Field(
        None,
        description=(
            "Last progress payload DEFT reported, verbatim. Opaque to Data "
            "Flywheel: the shape is owned by DEFT, so a workflow reporting "
            "different fields needs no change here."
        ),
        examples=[{"current_iteration": 1, "total_iterations": 5, "current_step": "train"}],
    )
    progress_at: datetime | None = Field(
        None,
        description=(
            "When progress last CHANGED, not when it was last polled. The "
            "no-progress watchdog measures its window from this instant."
        ),
        examples=["2026-08-18T18:02:41Z"],
    )
    stage: str | None = Field(
        None,
        description=(
            "Which pipeline stage the run is in. `status` cannot answer this: "
            "snapshot, training, the offline gate and registration all render "
            "as `running`."
        ),
        examples=["run_deft_training"],
    )
    stage_started_at: datetime | None = Field(
        None,
        description="When the current stage was entered, so its age is computable here.",
        examples=["2026-08-19T01:02:55Z"],
    )
    use_case: str | None = Field(None, examples=["aoi"])
    model_name: str | None = Field(None, examples=["changenet_classify"])
    last_poll_at: datetime | None = Field(
        None,
        description=(
            "When the training status was last POLLED, as opposed to "
            "`progress_at` (last CHANGED). Separates a dead worker from a slow "
            "step. Does not advance on a failed poll."
        ),
        examples=["2026-08-19T01:02:40Z"],
    )
    poll_errors: int | None = Field(
        None,
        description="Consecutive status-poll failures, ahead of the hard fail at the tolerance.",
        examples=[0],
    )
    timeout_s: float | None = Field(
        None,
        description="No-progress window in seconds, so the cancellation deadline is computable.",
        examples=[10800],
    )
    data: dict[str, Any] | None = Field(
        None,
        description="Lineage of the data that produced this model: snapshot and KPI-set URIs.",
        examples=[{"snapshot_id": "snap-a41c", "snapshot_uri": "s3://.../snapshots/a41c/"}],
    )
    baseline: dict[str, Any] | None = Field(
        None,
        description=(
            "The checkpoint this run fine-tuned FROM. A fall back to the day-0 "
            "seed discards prior generations' gains and still promotes."
        ),
        examples=[{"checkpoint": "s3://.../checkpoints/v6/model.pt"}],
    )
    result: dict[str, Any] | None = Field(
        None,
        description="What DEFT's loop produced: convergence reason, best iteration, metrics.",
        examples=[{"convergence_reason": "kpi_met", "best_iteration": 3}],
    )
    gate: dict[str, Any] | None = Field(
        None,
        description=(
            "Offline KPI gate verdict. `unmet` names WHICH constraints failed " "and by how much."
        ),
        examples=[{"passed": False, "unmet": ["FAR_pct < 200.0 (actual=240.0)"]}],
    )
    candidate: dict[str, Any] | None = Field(
        None,
        description=(
            "The candidate minted from this run. `registered: false` means no "
            "@production alias, so the NEXT run resolves a staler baseline."
        ),
        examples=[{"version": "7", "registered": True}],
    )
    promotion: dict[str, Any] | None = Field(
        None,
        description=(
            "Where the candidate is on the promotion ladder. State "
            "`pending_approval` with a `pending_gate` means it is waiting on a human."
        ),
        examples=[{"state": "pending_approval", "pending_gate": "canary"}],
    )


class JobDetailResponse(BaseModel):
    """Detailed response model for a specific job."""

    id: str = Field(
        ..., description="The unique identifier of the job", examples=["65f8a1b2c3d4e5f6a7b8c9d0"]
    )
    workload_id: str | None = Field(
        None,
        description=(
            "The unique identifier of the workload being processed (absent for aoi_job / its_job)"
        ),
        examples=["workload_123"],
    )
    client_id: str | None = Field(
        None,
        description=(
            "The unique identifier of the client to process (absent for aoi_job / its_job)"
        ),
        examples=["client_123"],
    )
    config_name: str | None = Field(
        None,
        description="The name of the configuration used for this job",
        examples=["my-llm-config", "production-config"],
    )
    status: str = Field(
        ...,
        description="Current status of the job",
        examples=["running", "completed", "failed"],
    )
    started_at: datetime | None = Field(
        ...,
        description="Timestamp when the job started processing",
        examples=["2024-03-15T14:30:00Z"],
    )
    finished_at: datetime | None = Field(
        None,
        description="Timestamp when the job completed or failed",
        examples=["2024-03-15T15:30:00Z"],
    )
    runtime_seconds: float = Field(
        ...,
        description="Time taken to complete the job in seconds",
        examples=[300.5],
    )
    deft: DeftRunDetailResponse | None = Field(
        None,
        description=(
            "DEFT-side detail for this run: the fine-tune id and where it has "
            "reached. Null for job types that are not DEFT-backed."
        ),
    )
    num_records: int = Field(
        ..., description="Number of records processed in this job", examples=[1000], ge=0
    )
    llm_judge: LLMJudgeResponse | None = Field(None, description="LLM Judge status for this job")
    nims: list[NIMResponse] = Field(
        default_factory=list, description="List of NIMs and their evaluation results for this job"
    )
    datasets: list[Dataset] = Field(
        default_factory=list, description="List of datasets used in this job"
    )
    error: str | None = Field(
        None,
        description="Error message if the job failed",
        examples=["Job failed: Timeout"],
    )

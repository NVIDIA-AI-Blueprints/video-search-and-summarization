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

from bson import ObjectId
from pydantic import BaseModel, Field

from src.api.schemas import Dataset, DeploymentStatus, FlywheelRunStatus, NIMRunStatus
from src.config import (
    DataSplitConfig,
    EmbeddingDataSplitConfig,
    EmbeddingJobConfig,
    LLMJobConfig,
    LLMJudgeConfig,
    NIMConfig,
)


class EvalType(str, Enum):
    """Types of evaluations that can be performed."""

    BASE = "base-eval"
    ICL = "icl-eval"
    CUSTOMIZED = "customized-eval"

    @classmethod
    def values(cls) -> set[str]:
        """Get all valid evaluation type values."""
        return {member.value for member in cls}

    def __str__(self) -> str:
        """String representation for easy MongoDB storage."""
        return self.value


class DatasetType(str, Enum):
    """Types of datasets that can be used for evaluations."""

    BASE = "base-dataset"
    ICL = "icl-dataset"
    TRAIN = "train-dataset"
    CUSTOM = "custom-dataset"


class WorkloadClassification(str, Enum):
    """Types of workloads that can be generated."""

    GENERIC = "generic"
    TOOL_CALLING = "tool_calling"
    DPO = "dpo"  # Direct Preference Optimization


class ToolEvalType(str, Enum):
    """Types of tool evaluations that can be performed."""

    TOOL_CALLING_METRIC = "tool-calling-metric"
    TOOL_CALLING_JUDGE = "tool-calling-judge"


class JobStatus(BaseModel):
    """Generic job status tracking"""

    job_id: str | None = None
    percent_done: float | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str | None = None


class EvaluationResult(JobStatus):
    """Individual evaluation result"""

    scores: dict[str, float] = Field(default_factory=dict)


class CustomizationResult(JobStatus):
    """Customization job result"""

    model_name: str | None = None
    evaluation_id: str | None = None  # Store evaluation ID instead of full object
    epochs_completed: int | None = None
    steps_completed: int | None = None


class TaskResult(BaseModel):
    """Result from any task in the workflow"""

    status: str | None = None
    workload_id: str | None = None
    client_id: str | None = None
    flywheel_run_id: str | None = None
    job_type: str = Field(default="llm_job", description="Type of job (llm_job or embedding_job)")
    nim: NIMConfig | None = None
    workload_type: WorkloadClassification | None = None
    datasets: dict[str, Any] = {}
    # Base evaluations
    evaluations: dict[EvalType, EvaluationResult] = {}

    # Separate customization tracking
    customization: CustomizationResult | None = None
    llm_judge_config: LLMJudgeConfig | None = None
    # Store error message if any stage fails so downstream tasks can short-circuit
    error: str | None = None
    data_split_config: EmbeddingDataSplitConfig | DataSplitConfig | None = None
    # Store customized nim config for customized model deployment
    customized_nim: dict | None = None
    job_config: LLMJobConfig | EmbeddingJobConfig | None = None

    def add_evaluation(self, eval_type: EvalType, result: EvaluationResult):
        """Helper method to add/update evaluation results"""
        self.evaluations[eval_type] = result

    def get_evaluation(self, eval_type: EvalType) -> EvaluationResult | None:
        """Helper method to get evaluation results"""
        return self.evaluations.get(eval_type)

    def update_customization(
        self,
        job_id: str,
        model_name: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        percent_done: float = 0.0,
        epochs_completed: int | None = None,
        steps_completed: int | None = None,
    ) -> None:
        """Helper method to update customization job status with training progress"""
        if not self.customization:
            self.customization = CustomizationResult(
                job_id=job_id,
                model_name=model_name,
                started_at=started_at,
                finished_at=finished_at,
                percent_done=percent_done,
                epochs_completed=epochs_completed,
                steps_completed=steps_completed,
            )
        else:
            # Update all fields
            for key, value in locals().items():
                if key not in ["self"] and hasattr(self.customization, key) and value is not None:
                    setattr(self.customization, key, value)

    def get_customization_progress(self) -> dict[str, Any]:
        """Helper method to get detailed customization progress"""
        if not self.customization:
            return {}

        return {
            "percent_done": self.customization.percent_done,
            "epochs_completed": self.customization.epochs_completed,
            "steps_completed": self.customization.steps_completed,
        }


class NIMEvaluation(BaseModel):
    """Results from a NIM evaluation run."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    nim_id: ObjectId
    job_id: str | None = None
    eval_type: EvalType
    dataset: str
    eval_name: str | None = None  # Name from custom evaluation config
    scores: dict[str, float]
    started_at: datetime
    finished_at: datetime | None = None
    runtime_seconds: float
    progress: float  # Progress percentage (0-100)
    nmp_uri: str | None = None
    mlflow_uri: str | None = None  # MLflow run URL for this evaluation
    error: str | None = None

    model_config = {
        "arbitrary_types_allowed": True,
        "json_encoders": {
            ObjectId: str,
            EvalType: str,  # Automatically convert enum to string
        },
    }

    def to_mongo(self) -> dict[str, Any]:
        """Convert NIMEvaluation model to MongoDB document."""
        return {
            "_id": self.id,
            "nim_id": self.nim_id,
            "eval_type": self.eval_type,
            "dataset": self.dataset,
            "eval_name": self.eval_name,
            "scores": self.scores,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "runtime_seconds": self.runtime_seconds,
            "progress": self.progress,
            "nmp_uri": self.nmp_uri,
            "mlflow_uri": self.mlflow_uri,
        }

    @classmethod
    def from_mongo(cls, doc: dict[str, Any]) -> "NIMEvaluation":
        """Create NIMEvaluation from MongoDB document."""
        return cls(
            _id=doc["_id"],
            nim_id=doc["nim_id"],
            eval_type=doc["eval_type"],
            dataset=doc.get(
                "dataset", "unknown"
            ),  # Default to "unknown" for backward compatibility
            eval_name=doc.get("eval_name"),  # Optional field for custom evaluations
            scores=doc["scores"],
            started_at=doc["started_at"],
            finished_at=doc["finished_at"],
            runtime_seconds=doc["runtime_seconds"],
            progress=doc["progress"],
            nmp_uri=doc.get("nmp_uri"),
            mlflow_uri=doc.get("mlflow_uri"),
        )


class NIMCustomization(BaseModel):
    """Results from a NIM customization run."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    nim_id: ObjectId
    job_id: str | None = None
    workload_id: str
    base_model: str
    customized_model: str | None = None  # Make this optional since it's set after job starts
    started_at: datetime
    finished_at: datetime | None = None
    runtime_seconds: float = 0.0
    progress: float = 0.0  # Progress percentage (0-100)
    epochs_completed: int | None = None
    steps_completed: int | None = None
    nmp_uri: str | None = None
    error: str | None = None

    model_config = {"arbitrary_types_allowed": True, "json_encoders": {ObjectId: str}}

    def to_mongo(self) -> dict:
        """Convert NIMCustomization model to MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict) -> "NIMCustomization":
        """Create NIMCustomization from MongoDB document."""
        if not data:
            return None
        return cls(**data)


class NIMRun(BaseModel):
    """Results from a NIM run."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    flywheel_run_id: ObjectId
    model_name: str
    started_at: datetime | None = None
    finished_at: datetime | None = None
    runtime_seconds: float
    evaluations: list[NIMEvaluation] = []
    status: NIMRunStatus | None = None
    deployment_status: DeploymentStatus | None = None  # NMP status
    model_config = {"arbitrary_types_allowed": True, "json_encoders": {ObjectId: str}}
    error: str | None = None

    def to_mongo(self) -> dict:
        """Convert NIMRun model to MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict) -> "NIMRun":
        """Create NIMRun from MongoDB document."""
        if not data:
            return None
        return cls(**data)


class LLMJudgeRun(BaseModel):
    """Status of LLM Judge run."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    flywheel_run_id: ObjectId
    model_name: str
    deployment_type: str
    deployment_status: DeploymentStatus | None = None
    model_config = {"arbitrary_types_allowed": True, "json_encoders": {ObjectId: str}}
    error: str | None = None

    def to_mongo(self) -> dict:
        """Convert NIMRun model to MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict) -> "NIMRun":
        """Create NIMRun from MongoDB document."""
        if not data:
            return None
        return cls(**data)


class DeftDataDetail(BaseModel):
    """What data produced this model -- lineage only.

    Deliberately URIs and ids, not dataset statistics. Record counts and class
    distributions are not fetched anywhere on this path (``get_stats()`` is
    called only by the trigger executor), so surfacing them would mean a new
    Storage Backend call per run. Class counts would also bake a domain
    vocabulary into a block shared by every DEFT use case.
    """

    snapshot_id: str | None = None
    snapshot_uri: str | None = None
    kpi_set_uri: str | None = None


class DeftBaselineDetail(BaseModel):
    """The checkpoint this run fine-tuned FROM.

    Iterative fine-tuning means each run builds on the last promoted model. A
    silent fall back to the day-0 seed discards every prior generation's gains
    and still promotes, which is invisible in the metrics -- the run simply
    looks like a weaker generation.
    """

    checkpoint: str | None = None


class DeftResultDetail(BaseModel):
    """What DEFT's loop produced."""

    #: ``kpi_met`` (converged) vs ``max_iterations`` (ran out of budget). The
    #: same final metrics mean different things under each.
    convergence_reason: str | None = None
    #: Which iteration won. Well below ``total_iterations`` means the model
    #: peaked early and later iterations made it worse.
    best_iteration: int | None = None
    #: DEFT's own metrics, stored VERBATIM. Free-form for the same reason
    #: ``progress`` is: AOI reports FAR/AUC, ITS reports mAP, and neither
    #: should need a schema change here.
    eval_metrics: dict[str, Any] | None = None
    threshold: float | None = None
    checkpoint: str | None = None


class DeftGateDetail(BaseModel):
    """The offline KPI gate's verdict -- the gate to shadow."""

    passed: bool | None = None
    metrics: dict[str, Any] | None = None
    #: WHICH constraints failed and by how much. Computed today and discarded,
    #: so a blocked promotion reports neither here nor in the log which KPI
    #: missed. Also separates a genuine regression from DEFT renaming a metric.
    unmet: list[str] | None = None


class DeftCandidateDetail(BaseModel):
    """The candidate minted from this run."""

    #: The serving/Triton version minted from DFW's Mongo-owned counter. A
    #: string, matching what the handler stores and what
    #: ``PromotionRecordResponse.candidate_version`` exposes for the same value
    #: -- typing it as an int here would advertise a shape the system does not
    #: use and disagree with the promotion API.
    version: str | None = None
    #: MLflow registration is best effort. When it fails there is no
    #: ``@production`` alias, so the NEXT run resolves a staler baseline and
    #: silently regresses -- a failure that shows up one run later.
    registered: bool | None = None


class DeftPromotionDetail(BaseModel):
    """Where the candidate is on the promotion ladder.

    ``state == "pending_approval"`` with a ``pending_gate`` is the answer to
    "what is this run waiting for": a human. No separate boolean, which could
    only drift from the state it derives from.
    """

    id: str | None = None
    state: str | None = None
    pending_gate: str | None = None


class DeftRunDetail(BaseModel):
    """DEFT-side detail for a flywheel run, as Data Flywheel observed it.

    Populated only for DEFT-backed job types. Everything here is already fetched
    by the training wait loop; this is the record of it rather than new work.
    """

    run_id: str | None = None
    #: DEFT's progress payload, stored VERBATIM. Deliberately opaque: Data
    #: Flywheel does not parse it, so a workflow reporting different fields
    #: (steps, iterations, or a future intra-step detail) needs no change here.
    #:
    #: Unknown keys are kept, not dropped. deft-service !16 adds `updated_at`
    #: and `detail` to this payload; both surface on GET /api/jobs/<id> the day
    #: it lands, with no schema change or migration here. Modelling this as a
    #: typed sub-schema would break that, so it stays a dict.
    progress: dict[str, Any] | None = None
    #: When ``progress`` last CHANGED, not when it was last polled. The
    #: no-progress watchdog measures its window from this instant, so a
    #: cancellation is auditable after the fact.
    progress_at: datetime | None = None
    #: Which pipeline stage the run is in. ``status`` cannot answer this:
    #: snapshot, training, the offline gate and registration all render as
    #: ``running``, so four very differently-shaped waits look identical from
    #: the API. Free-form rather than an enum -- the stage list belongs to the
    #: task layer, and a use case that adds one should not need a schema change
    #: here (same reasoning as ``progress``).
    stage: str | None = None
    #: When the current stage was entered, so its age is computable without a
    #: second request. ``progress_at`` only moves during training, so it says
    #: nothing about a run wedged in any other stage.
    stage_started_at: datetime | None = None
    #: Which use case this run belongs to, and the model it targets. In a
    #: deployment running both AOI and ITS, ``config_name`` is the only other
    #: discriminator and it is optional.
    use_case: str | None = None
    model_name: str | None = None
    #: When the training status was last POLLED, as opposed to ``progress_at``
    #: (last CHANGED). Identical today, which is the problem: a dead worker and
    #: a slow step look the same.
    last_poll_at: datetime | None = None
    #: Consecutive status-poll failures, ahead of the hard fail at the
    #: tolerance. A DEFT service degrading is visible before it takes the run
    #: down with it.
    poll_errors: int | None = None
    #: The no-progress window in seconds, so the cancellation deadline is
    #: computable client-side. Today ``progress_at`` is uninterpretable without
    #: knowing a worker env var.
    timeout_s: float | None = None
    data: DeftDataDetail | None = None
    baseline: DeftBaselineDetail | None = None
    result: DeftResultDetail | None = None
    gate: DeftGateDetail | None = None
    candidate: DeftCandidateDetail | None = None
    promotion: DeftPromotionDetail | None = None


class FlywheelRun(BaseModel):
    """Results from a Flywheel run."""

    id: ObjectId | None = Field(default_factory=ObjectId, alias="_id")
    # Optional so AOI (DEFT-loop) runs — which have no workload/client — can share
    # this model; still supplied for llm_job / embedding_job.
    workload_id: str | None = None
    started_at: datetime | None = None
    client_id: str | None = None
    config_name: str | None = None
    job_type: str | None = None
    status: FlywheelRunStatus = Field(default=FlywheelRunStatus.PENDING)
    finished_at: datetime | None = None
    num_records: int | None = None
    nims: list[NIMRun] = []
    # Keep the fixed datasets field that matches the database structure
    datasets: list[Dataset] = []
    error: str | None = None
    #: DEFT-backed runs only; ``None`` for llm_job / embedding_job. Mirrors the
    #: ``llm_judge`` block, which is likewise absent for job types that have no
    #: judge -- one null rather than one per field, and a home for the further
    #: DEFT detail (per-iteration metrics, promotion state) still to be surfaced.
    deft: DeftRunDetail | None = None
    model_config = {"arbitrary_types_allowed": True, "json_encoders": {ObjectId: str}}

    def to_mongo(self) -> dict:
        """Convert FlywheelRun model to MongoDB document."""
        return self.model_dump(by_alias=True)

    @classmethod
    def from_mongo(cls, data: dict) -> "FlywheelRun":
        """Create FlywheelRun from MongoDB document."""
        if not data:
            return None
        return cls(**data)

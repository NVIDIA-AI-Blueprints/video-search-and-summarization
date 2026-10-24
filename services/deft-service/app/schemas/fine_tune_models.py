from __future__ import annotations

from datetime import datetime
from typing import Any
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.config_models import ExperimentConfig, ExperimentConfigOverrides

RunState = Literal["pending", "running", "completed", "failed", "cancelled"]
ConvergenceReason = Literal["kpi_met", "max_iterations_reached", "delta_below_epsilon", "hard_stop"]


class S3TemporaryCredentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_key_id: str
    secret_access_key: str
    session_token: str


class TemporaryCredentialsStorageAccess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["temporary_credentials"]
    endpoint_url: str | None = None
    region: str | None = None
    expires_at: datetime
    credentials: S3TemporaryCredentials


class FineTuneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: str | None = Field(default=None, min_length=1)
    workflow: str | None = Field(default=None, min_length=1)
    overrides: ExperimentConfigOverrides | None = None
    config: ExperimentConfig | None = None
    step_overrides: dict[str, dict] | None = None
    storage_access: TemporaryCredentialsStorageAccess | None = None


class FineTuneResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    state: RunState


class BackendInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["docker", "kfp", "slurm", "brev"]
    run_id: str | None = None


class Progress(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_iteration: int = Field(ge=1)
    total_iterations: int = Field(ge=1)
    current_step: str | None = None
    steps_completed: list[str] = Field(default_factory=list)
    completed_steps: int = 0
    total_steps: int = 1
    #: Last time this run was observed DOING something, as opposed to last time
    #: a step boundary was crossed. Advances while a step is executing -- the
    #: stage container writing to its log is what moves it.
    #:
    #: Every other field here is step-granular, so during a multi-hour `train`
    #: they are all constant and a caller cannot tell a long step from a hang.
    #: A client polling this can: a stale `updated_at` means stalled, a moving
    #: one means working, whatever the step happens to be.
    updated_at: datetime | None = None
    #: Structured intra-step detail when the running stage publishes it, e.g.
    #: ``{"epoch": 12, "max_epoch": 29, "step": 3600, "eta": "0:19:47"}`` from a
    #: TAO training container. Free-form on purpose: what a stage can say about
    #: its own progress differs per stage, and a caller should render it rather
    #: than depend on a fixed shape. ``updated_at`` is the field to gate on.
    detail: dict[str, Any] | None = None


class RunError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step: str
    exit_code: int
    message: str
    logs: str


class RunStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    created_by: str
    state: RunState
    created_at: datetime
    progress: Progress
    backend: BackendInfo
    error: RunError | None = None
    convergence_reason: ConvergenceReason | None = None


class IterationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(ge=1)
    metrics: dict[str, float] | None = None


class RunDetailsResponse(RunStatus):
    per_iteration_metrics: list[IterationMetrics]
    resolved_config: ExperimentConfig


class FineTuneListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[RunStatus]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class ResultsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    best_checkpoint: str
    best_iteration: int = Field(ge=1)
    threshold: float
    far_pct: float
    backbone: str
    images_dir: str
    training_spec: str | None = None
    best_checkpoint_sha256: str | None = Field(
        default=None,
        description=(
            "SHA-256 content digest (hex) of the best checkpoint file. Consumers that "
            "stage the artifact — e.g. the Model Manager `pull`, which REQUIRES a sha256 "
            "and rejects the download with 409 checksum_mismatch otherwise — verify the "
            "bytes against it. Optional: simulate-mode runs (no materializable file) and "
            "older services may omit it or return null. Single-file only; multi-file model "
            "directories are not covered by one digest."
        ),
    )
    metrics: dict[str, float]


class StepLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iteration: int = Field(ge=1)
    step: str
    stdout: str
    stderr: str


class LogsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    logs: list[StepLog]
    runner_crash: str | None = None


class StageEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seq: int = Field(ge=1)
    ts: datetime
    iter: str
    stage: str
    status: Literal["ok", "error"]
    summary: str
    duration_sec: int = Field(ge=0)
    tokens: dict | None = None


class EventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    events: list[StageEvent]
    next_seq: int | None = None


ArtifactName = Literal[
    "state_snapshot",
    "event_log",
    "report_html",
    "best_model_metadata",
    "best_model_inference_spec",
]


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ArtifactName
    uri: str
    updated_at: datetime
    size_bytes: int | None = None


class ArtifactsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    artifacts: list[Artifact]

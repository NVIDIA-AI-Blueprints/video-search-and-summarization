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
"""DEFT (VSS-AOI) config schemas + the DEFT config loader.

Pure Pydantic contract for the DEFT backend (service endpoints, snapshot
selection, the DEFT job config, and the promotion/eval config) — no service
clients or SDK, so the module is generic/isolation-safe. ``Settings`` builds it
via ``load_deft_settings`` when ``backend.kind == "deft"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.config.base import JobConfig, SystemConfig, build_shared_system_kwargs

__all__ = [
    "DEFT_JOB_CONFIG_TYPES",
    "AOIJobConfig",
    "CanaryStepsConfig",
    "DEFTConfig",
    "DeftJobConfig",
    "DeftSystemConfig",
    "DeploymentStagesConfig",
    "DiskReclaimConfig",
    "EvalHarnessConfig",
    "HITLConfig",
    "KpiConstraint",
    "KpiDatasetConfig",
    "ModelManagerConfig",
    "PromotionPolicyConfig",
    "SnapshotSelectionConfig",
    "UseCaseEndpoints",
    "load_deft_settings",
]


# =============================================================================
# AOI Promotion config models (fields-only stubs; validated/used in Plan 2)
# =============================================================================


class ModelManagerConfig(BaseModel):
    """System config for reaching the standalone Model Manager service."""

    base_url: str = Field(
        default="http://localhost:8090", description="Base URL of the Model Manager service"
    )
    timeout: float = Field(
        default=30.0, description="HTTP timeout (seconds) for Model Manager calls"
    )
    pull_timeout: float = Field(
        default=900.0,
        description=(
            "Dedicated HTTP timeout (seconds) for the Model Manager PULL POST only. "
            "A real >600MB weight download inside the Model Manager regularly exceeds "
            "the short `timeout` (default 30s), so the pull gets its own longer budget "
            "while apply/convergence/cleanup keep `timeout`. Backward compatible: the default "
            "(900s) applies when unset."
        ),
    )


class DiskReclaimConfig(BaseModel):
    """Policy for the ``tasks.promotion.disk_reclaim`` background sweep.

    Service-level, not per-rollout: the sweep spans every use case and model.
    Ships inert -- ``enabled`` off, ``dry_run`` on -- so an upgrade never starts a
    deleter, and turning it on gets you logs before it gets you deletions.
    """

    enabled: bool = Field(
        default=False,
        description=(
            "Master off switch. The beat's FIRST action is to read this and "
            "return, before it builds a deployment backend or queries anything, "
            "so an off deployment costs one config read per tick."
        ),
    )
    dry_run: bool = Field(
        default=True,
        description=(
            "Forwarded as CleanupRequest.dry_run. The Model Manager runs every "
            "guard for real, logs what it would have reclaimed, and skips only "
            "the rmtree — so this measures the real predicate rather than a "
            "simulation of it. On by default: enabling the sweep gets you logs, "
            "not deletions."
        ),
    )
    min_age_hours: float = Field(
        default=24.0,
        ge=0.0,
        description=(
            "Forwarded as CleanupRequest.min_age_s (x3600). The Model Manager "
            "refuses a version whose tree was written to more recently than "
            "this. Defence in depth ONLY, and deliberately no longer load "
            "bearing: an age floor is an inference from a side effect, so what "
            "actually withholds an in-flight pull is the staging interlock the "
            "writer declares, and what withholds a staged-but-unrouted candidate "
            "is that its promotion is not terminal. A number large enough to "
            "dominate a stage/load/apply window cannot be derived — a slow "
            "download over a long enough wire outlives any of them."
        ),
    )
    keep_recent_versions: int = Field(
        default=2,
        ge=0,
        description=(
            "Never nominate the N numerically-highest known versions. Defence in "
            "depth ONLY: the live primary is protected by the permanent pins on "
            "the latest promoted record and by serving_versions, NOT by this. "
            "The counter is a high-water mark, so the live primary need not be "
            "among the highest versions -- every rejected promotion mints a "
            "candidate that never went live."
        ),
    )
    rollback_retention_hours: float = Field(
        default=168.0,
        ge=0.0,
        description=(
            "How long a terminal promotion record's candidate stays pinned past "
            "its decision time. Nothing in the codebase expires a rollback pin "
            "today, so this number is an invention rather than a derivation, and "
            "it is the setting most likely to be wrong for a given deployment. "
            "Named so it is tunable rather than buried."
        ),
    )
    max_models_per_tick: int = Field(
        default=25,
        ge=0,
        description=(
            "Blast-radius and worker-time bound. The client's retry sleeps "
            "synchronously inside the worker, so an unbounded tick is an "
            "unbounded occupancy."
        ),
    )
    max_versions_per_model_per_tick: int = Field(
        default=5,
        ge=0,
        description=(
            "With max_models_per_tick this makes a wrong predicate cost 125 "
            "directories per hour, with operator-visible ticks in between, "
            "rather than the whole repository at once."
        ),
    )


class HITLConfig(BaseModel):
    """Per-gate human-in-the-loop approval toggles."""

    pull: bool = Field(default=False, description="Require approval before the pull gate")
    deploy: bool = Field(
        default=False, description="Require approval before the shadow deploy gate"
    )
    canary: bool = Field(default=True, description="Require approval before the canary gate")
    full: bool = Field(default=True, description="Require approval before the full switch gate")


class CanaryStepsConfig(BaseModel):
    """Canary rollout step percentages (e.g. [5, 25, 50])."""

    steps: list[int] = Field(
        default_factory=lambda: [5, 25, 50],
        min_length=1,
        description=(
            "Canary traffic % steps. Must be non-empty: an empty ladder used to "
            "fall back to a single 100% step, which every serving backend rejects "
            "(canary_percent must satisfy 0 < p < 100), failing the promotion at "
            "runtime. Disable the stage with deployment_stages.canary instead."
        ),
    )


class DeploymentStagesConfig(BaseModel):
    """Which optional serving stages a rollout traverses before the full switch.

    The ladder is ``pull -> [deploy(shadow)] -> [canary] -> full``. ``pull`` and
    ``full`` are mandatory; the two stages here are optional so a deployment can
    match what its serving backend actually supports — a backend with shadow
    routing but no partial-traffic routing runs with ``canary: false``.

    Note the vocabulary shift: the gate is named ``deploy`` but the serving role
    it creates — and the flag that governs it — is ``shadow``.
    """

    shadow: bool = Field(
        default=True,
        description="Serve the candidate as a shadow before any traffic switch.",
    )
    canary: bool = Field(
        default=True,
        description="Step the candidate through partial-traffic canary weights.",
    )


# Comparison operators allowed in a structured KPI constraint. The value is the
# callable applied as ``op(metric_value, threshold)`` — a deterministic,
# validated lookup (no string parsing anywhere).
_KPI_OPS: dict[str, Any] = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
}


class KpiConstraint(BaseModel):
    """One deterministic KPI acceptance criterion: ``metric <op> threshold``.

    This is **DFW's own offline-gate criterion** — the structured, machine-checkable
    replacement for the former free-text expression (``"FAR < 0.1% at recall=100%"``),
    so the gate needs no string parsing and can't be silently mis-parsed. Thresholds
    are absolute values/fractions (``0.001``, not ``"0.1%"``) and the operator is
    validated at config load.

    NOTE: this is distinct from DEFT's ``pipeline.kpi_target`` (a *string*
    convergence stop-condition that the DEFT loop owns and parses itself, in DEFT's
    own vocabulary). That string is passed to DEFT verbatim via the ``deft`` config
    block; DFW never synthesizes it from these constraints. DEFT converges on its
    target; DFW verifies the returned metrics against these constraints.
    """

    metric: str = Field(
        ...,
        description="Metric key; matched case-insensitively against evaluator metric "
        "names (e.g. 'false_accept_rate', 'recall', 'mAP50').",
    )
    op: Literal["<", "<=", ">", ">=", "=="] = Field(
        ..., description="Comparison operator, applied as 'metric op threshold'."
    )
    threshold: float = Field(
        ..., description="Threshold as an absolute value/fraction (0.001 = 0.1%)."
    )

    def check(self, value: float) -> bool:
        """True if ``value`` satisfies this constraint."""
        return bool(_KPI_OPS[self.op](value, self.threshold))

    def render(self) -> str:
        """Render as ``'metric op threshold'`` (for logging/display only).

        This is NOT the DEFT convergence string — DEFT's ``kpi_target`` is
        configured independently and passed through the ``deft`` block verbatim.
        """
        return f"{self.metric} {self.op} {self.threshold}"


class KpiDatasetConfig(BaseModel):
    """Source of DEFT's KPI/testing evaluation set — the ``kpi/testing_set.csv`` DEFT
    judges the candidate on **during training** (``baseline_eval`` / ``evaluate`` →
    ``FAR_pct``). This is training/eval **data** sourcing, parallel to ``selection``
    (which sources the training snapshot) — and DISTINCT from ``eval``
    (``EvalHarnessConfig``), which is DFW's promotion **gate** on the metrics DEFT
    returns. When neither field is set, DEFT carves the kpi split from the snapshot.
    """

    pull_from_storage: bool = Field(
        default=True,
        description="DFW materializes a dedicated KPI set from the Storage Backend "
        "(POST /v1/kpi) at snapshot time and hands DEFT its prefix as kpi.path (DEFT uses it "
        "as the kpi/testing split; train/val/mining still come from the snapshot). Defaults on "
        "(storage serves /v1/kpi); set False to have DEFT carve the KPI split from the snapshot "
        "or to supply a static ``uri`` instead.",
    )
    uri: str | None = Field(
        default=None,
        description="Static KPI-set locator passed to DEFT as kpi.path (s3:// prefix = a "
        "manifest set DEFT converts; single object = a ready CSV). Ignored when pull_from_storage.",
    )


class EvalHarnessConfig(BaseModel):
    """DFW's promotion GATE criteria on the metrics DEFT returns (offline/online
    verdict). NOTE: this is NOT the KPI-dataset source — that is
    ``DeftJobConfig.kpi`` (``KpiDatasetConfig``), the data DEFT evaluates on."""

    kpi_target: list[KpiConstraint] = Field(
        default_factory=list,
        description="DFW's deterministic offline-gate criteria (metric/op/threshold) the "
        "candidate must satisfy. Distinct from DEFT's own string convergence target; an "
        "empty list means 'rely on the evaluator's not-worse-than-baseline check only'.",
    )
    online_target: list[KpiConstraint] = Field(
        default_factory=list,
        description="DFW's deterministic ONLINE-gate criteria (metric/op/threshold), same "
        "form and units as kpi_target. AOI gates on FAR only, e.g. "
        "[{metric: FAR_pct, op: '<', threshold: 0.1}]. Empty is fail-closed (hold).",
    )
    thresholds: dict[str, float] = Field(
        default_factory=dict,
        description="DEPRECATED, use online_target. Only a FAR entry is honoured, read as "
        "'FAR_pct < value'.",
    )
    min_sample_size: int = Field(
        default=100, description="Minimum samples before a non-hold verdict"
    )
    enabled: bool | None = Field(
        default=None,
        description="Whether the ONLINE gate runs. False skips it entirely and the candidate "
        "promotes on the offline KPI gate alone — a defensible posture for use cases with no "
        "live labelled feedback, and NOT 'no validation' (the offline gate still runs at "
        "registration). None (unstated) behaves as enabled; opting out is the deliberate act. "
        "Tri-state on purpose: an explicit True with no criteria is a contradiction worth "
        "rejecting, and only a distinct 'unstated' value survives a model_dump round trip to "
        "tell the two apart.",
    )
    max_observation_window_s: float | None = Field(
        default=None,
        ge=0,
        description="Seconds a candidate may sit in one shadow/canary phase without a "
        "decisive verdict before on_timeout applies. None (the default) means no deadline, "
        "matching prior behaviour. The clock restarts on each serving-role transition.",
    )
    on_timeout: Literal["escalate", "rollback", "advance"] = Field(
        default="escalate",
        description="What to do when max_observation_window_s elapses on a held promotion: "
        "'escalate' hands the decision to a human (pending_approval), 'rollback' gives up "
        "fail-closed, 'advance' promotes on offline signal alone (fail-open).",
    )

    @property
    def online_enabled(self) -> bool:
        """True unless the online gate was explicitly turned off."""
        return self.enabled is not False

    @model_validator(mode="after")
    def _require_criteria_when_enabled(self) -> EvalHarnessConfig:
        """Reject an enabled online gate that states no criteria.

        Fires only on ``enabled: true`` written explicitly, which makes the config
        self-contradictory: it asks for an online gate and states nothing to gate
        on.

        An *unstated* ``enabled`` with no criteria is left alone. It is ambiguous
        — a deliberate default or a dropped line — but it is not dangerous: the
        evaluator verdicts ``hold`` and logs when it has no criteria, so such a
        promotion stalls rather than auto-promoting.

        The legacy ``thresholds`` FAR entry counts as criteria, because the
        evaluator still gates on it (``_online_constraints``); rejecting it here
        would break deployments that have not migrated.
        """
        if self.enabled is not True:
            return self
        legacy_far = any(
            k in self.thresholds for k in ("FAR_pct", "far_pct", "far", "false_accept_rate")
        )
        if not self.online_target and not legacy_far:
            raise ValueError(
                "eval_harness.online_target must state at least one constraint when the "
                "online gate is enabled (e.g. [{metric: FAR_pct, op: '<', threshold: 0.1}]). "
                "To promote on the offline gate alone, set eval_harness.enabled: false "
                "explicitly — skipping online eval is fail-open and is never inferred "
                "from an absent field."
            )
        return self


class PromotionPolicyConfig(BaseModel):
    """Top-level promotion policy: gate HITL toggles, canary steps, eval config."""

    hitl: HITLConfig = Field(default_factory=HITLConfig)
    deployment_stages: DeploymentStagesConfig = Field(default_factory=DeploymentStagesConfig)
    canary_steps: CanaryStepsConfig = Field(default_factory=CanaryStepsConfig)
    eval_harness: EvalHarnessConfig = Field(default_factory=EvalHarnessConfig)
    poll_interval_s: float = Field(default=10.0, description="Seconds between convergence polls.")
    poll_max_attempts: int = Field(
        default=90,
        description=(
            "Max convergence polls before treating a swap as timed out. "
            "poll_interval_s * poll_max_attempts = 900s (15 min). The budget "
            "covers the swap settling after a routing change, not the engine "
            "build (that happens during pull, bounded by pull_timeout). It is "
            "the only thing that ends a non-terminal poll, so it is also the "
            "outage tolerance for a Model Manager restart or rolling deploy."
        ),
    )
    auto_rollback_on_regression: bool = Field(
        default=True,
        description="Roll back automatically when an online verdict is 'rollback' (regression).",
    )


class UseCaseEndpoints(BaseModel):
    """Per-use-case overrides of the services one DFW process talks to.

    ``DEFTConfig`` holds exactly ONE set of endpoints, but sibling use cases do
    not necessarily share infrastructure. Every field is optional and ``None``
    means "inherit the top-level ``deft_config`` value", so an entry states only
    what differs — and an entry that states NOTHING is the deliberate "shares
    the deployment's store" declaration, which is what every use case in the
    shipped config is.

    ``extra="forbid"`` is load-bearing: since every field is optional and unset
    means "inherit", a typo (``storage_url`` for ``storage_backend_url``) would
    otherwise be dropped and that use case would silently read and write the
    top-level store while the file claims it was repointed.

    Every field here is also a ``DEFTConfig`` field, which is what lets
    ``for_use_case`` apply an entry as a plain ``model_copy`` update; the
    validation constraints are duplicated on both models so the un-validated
    copy cannot smuggle an out-of-range value.
    """

    model_config = ConfigDict(extra="forbid")

    storage_backend_url: str | None = Field(
        default=None,
        description="Storage Backend base URL for this use case; None inherits deft_config.",
    )
    object_store_uri: str | None = Field(
        default=None,
        description="Object store holding this use case's snapshots/artifacts/results; "
        "None inherits deft_config.",
    )
    storage_api_key_env: str | None = Field(
        default=None,
        description="Env var name holding this use case's Storage Backend credential (a "
        "separate store usually means a separate credential); None inherits deft_config.",
    )
    presign_expires_s: int | None = Field(
        default=None,
        ge=60,
        le=86400,
        description="Presigned-URL TTL (seconds, 60..86400) for this use case; None inherits "
        "deft_config. The bounds still apply to a stated value.",
    )


class DEFTConfig(BaseModel):
    """Endpoints + refs for the DEFT-loop services DFW drives (all consumed, not built).

    Mirrors the shape of ``NMPConfig`` (service URLs + creds *references*, never the
    secret itself). Values are placeholders until deployment wiring lands.
    """

    deft_base_url: str = Field(
        default="http://deft:8000", description="DEFT fine-tuning service base URL (/v1/fine-tune)."
    )
    storage_backend_url: str = Field(
        default="http://storage-backend:8080",
        description="AOI Storage Backend base URL (/v1/snapshot, /v1/kpi, /v1/results).",
    )
    object_store_uri: str = Field(
        default="s3://aoi-artifacts",
        description="Object store (SeaweedFS/S3) holding snapshots, artifacts, inference results.",
    )
    aoi_results_url: str | None = Field(
        default=None,
        description=(
            "LEGACY override: direct inline-results GET endpoint for the online "
            "evaluator. When unset (the default), online samples are collected "
            "via the Storage Backend eval-set flow (POST /v1/results → poll "
            "GET /v1/eval/{{id}} → manifests) at storage_backend_url, per the "
            "Storage Backend design §4.3."
        ),
    )
    # The Model Manager endpoint is NOT here: DeftSystemConfig.model_manager is
    # the single source, so the two cannot diverge for one service.
    registry_uri: str | None = Field(
        default=None, description="MLflow registry URI; falls back to MLflowConfig when None."
    )
    api_key_env: str = Field(
        default="DEFT_API_KEY", description="Env var name holding the DEFT service credential."
    )
    storage_api_key_env: str = Field(
        default="STORAGE_BACKEND_API_KEY",
        description="Env var name holding the Storage Backend credential.",
    )
    presign_expires_s: int = Field(
        default=3600,
        ge=60,
        le=86400,
        description="TTL (seconds, 60..86400) for presigned download URLs — the default "
        "expires_in for POST /v1/get_presigned_urls minted right before a Model Manager pull.",
    )
    use_case_endpoints: dict[str, UseCaseEndpoints] = Field(
        default_factory=dict,
        description=(
            "Optional per-use-case endpoint overrides, keyed by DeftJobConfig.use_case "
            "(e.g. 'its'). Empty (the default) means every use case shares the fields "
            "above — exactly today's single-store behaviour. See UseCaseEndpoints."
        ),
    )

    def endpoints_for(self, use_case: str | None) -> UseCaseEndpoints | None:
        """The declared overrides for ``use_case``, or None when nothing is declared.

        Distinguishes "no entry" from "an entry that inherits everything" — the
        snapshot guard demands a declaration, not an override.
        """
        if not use_case:
            return None
        return self.use_case_endpoints.get(use_case)

    def for_use_case(self, use_case: str | None) -> DEFTConfig:
        """This config as ``use_case`` sees it: self, or a copy with its overrides applied.

        Returns a whole ``DEFTConfig`` (not a bag of URLs) so existing consumers
        like ``StorageBackendClient`` become use-case-aware with no signature
        change. Returns ``self`` — the identical object — whenever there is
        nothing to override, which is what keeps the AOI path byte-identical.

        FAILS CLOSED when ``use_case_endpoints`` is non-empty and this use case is
        absent from it. Silently returning ``self`` there was the whole bug: an
        UNDECLARED use case was indistinguishable from one declaring no overrides,
        so an ITS run snapshotted, presigned and promoted against AOI's store with
        nothing anywhere reporting it — a wrong number gating a promotion, not a
        crash. The rule lives HERE, on the single call every endpoint-resolving
        path already goes through (snapshot, presign, the promotion controller),
        rather than as a guard each of them has to remember to repeat.

        The predicate is the DECLARATION, never the name. ``use_case != "aoi"``
        would be wrong: an AOI-family use case registered against the AOI
        evaluator legitimately shares the top-level endpoints, and its name says
        nothing about that. What makes a use case legal is that the deployment
        SAID so — and an entry that overrides nothing (``aoi: {}``) is exactly
        that statement, which is why the check is membership rather than
        truthiness of the entry.

        An empty map keeps the pre-ITS behaviour verbatim: nothing is declared, so
        nothing can be undeclared, and every use case shares one store as before.
        A falsy ``use_case`` (None/"") is not a use case at all — it is a legacy
        caller that never routed by one — so it inherits the top level rather than
        being refused for failing to name something it never named.

        Pure function of ``use_case`` and this config: the API process, the Celery
        workers and the beat call it independently and must agree without sharing
        state.
        """
        if use_case and self.use_case_endpoints and use_case not in self.use_case_endpoints:
            raise ValueError(
                f"use_case={use_case!r} has no entry in deft_config.use_case_endpoints "
                f"(declared: {sorted(self.use_case_endpoints)}). This deployment declares "
                "per-use-case endpoints, so an undeclared use case would silently inherit "
                "the top-level ones and read, write and presign against another use case's "
                "store. Add an entry — an empty one ('{}') is the deliberate statement that "
                "it inherits everything above."
            )
        overrides = self.endpoints_for(use_case)
        if overrides is None:
            return self
        update = overrides.model_dump(exclude_none=True)
        if not update:
            return self
        return self.model_copy(update=update)


class SnapshotSelectionConfig(BaseModel):
    """How DFW asks the Storage Backend to select records for a training snapshot.

    Structured/deterministic selection (not free-text search): the Storage Backend
    freezes exactly what matches into an immutable snapshot. See
    ``drafts/design-aoi/dfw-to-object-store-requirements.md`` §1-2.
    """

    mode: str = Field(default="query", description="Selection mode; only 'query' today.")
    filters: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Facet filters (e.g. created_after, classes, label_source, AL signals). "
            "Spread FLAT onto the selection the backend receives — it reads facets at "
            "the top level (``created_after`` is required there), not under a nested key."
        ),
    )
    limit: int | None = Field(default=None, description="Optional cap on selected records.")

    @model_validator(mode="after")
    def require_created_after(self) -> SnapshotSelectionConfig:
        created_after = self.filters.get("created_after")
        if not isinstance(created_after, str) or not created_after.strip():
            raise ValueError(
                "AOI snapshot selection requires filters.created_after because the "
                "AOI Storage Backend /v1/snapshot contract requires selection.created_after. "
                "Use an ISO-8601 UTC timestamp such as '1970-01-01T00:00:00Z' to select "
                "all available records, or a deployment-specific window such as "
                "'2026-07-24T00:00:00Z'."
            )
        return self


#: Mirrors ``ModelFormat`` in ``model-manager/src/api/schemas.py``: a value legal
#: here but not there is a 422 at the `pull` gate. Duplicated, not imported,
#: because the Model Manager's own top-level ``src`` would shadow this one.
ModelFormat = Literal["onnx", "pth", "tensorrt", "triton"]


class DeftJobConfig(JobConfig):
    """Per-run config for a DEFT-loop job — ``job_type`` ``aoi_job`` OR ``its_job``.

    ONE schema behind TWO registry keys: ITS needs no config field AOI lacks, so
    ``its_job`` is a distinct JOB TYPE (it answers the submission-edge questions)
    riding the SAME contract, while ``use_case`` stays the orthogonal runtime key
    (evaluator, store, control plane, promotion policy). Because both keys resolve
    to this one class, ``config_type_for`` cannot answer by ``isinstance`` — see
    ``src.config.base.JobConfig.build``.

    Reuses the generic promotion/eval config models; DEFT-specific inputs (use
    case, snapshot selection, DEFT run params) are typed here. NMP-only concepts
    (NIMConfig, ICL, LLM judge, data splits) are intentionally absent.
    """

    use_case: str = Field(
        description="Evaluator-plugin + KPI-set selection key, e.g. 'aoi' or 'its'."
    )
    model_name: str = Field(description="Registry model name, e.g. 'changenet-aoi'.")
    model_format: ModelFormat = Field(
        default="onnx",
        description=(
            "Format of the artifact the Deployment Backend is told to pull "
            "('onnx' | 'pth' | 'tensorrt' | 'triton'), carried as "
            "PullRequest.format. Per JOB because it is a property of the model "
            "the use case trains, not of the deployment. Use 'pth' when DEFT "
            "publishes a training checkpoint the serving stack will compile "
            "itself (ITS/GDINO). Set 'tensorrt' only for a backend that will "
            "NOT build the engine itself — a prebuilt .engine pins the artifact "
            "to one GPU/driver/TensorRT triple. The default matches the "
            "handler's own fallback, so AOI is unchanged. Typed as a closed set "
            "so a typo is refused at CONFIG LOAD; left as a free str it would "
            "sail through startup and 422 at the `pull` gate instead, after a "
            "full training run had already cleared the offline gate."
        ),
    )
    baseline_version: str | None = Field(
        default=None,
        description="Explicit deployed-baseline version override. When unset (the "
        "normal case) the pipeline resolves the baseline from the MLflow "
        "@production alias at promotion time.",
    )
    selection: SnapshotSelectionConfig = Field(default_factory=SnapshotSelectionConfig)
    kpi: KpiDatasetConfig = Field(default_factory=KpiDatasetConfig)
    deft: dict[str, Any] = Field(
        default_factory=dict,
        description="DEFT run params (KPI/baseline/eval-dataset refs, callback) — shaped in impl.",
    )
    eval: EvalHarnessConfig = Field(default_factory=EvalHarnessConfig)
    promotion_policy: PromotionPolicyConfig = Field(default_factory=PromotionPolicyConfig)
    snapshot_poll_max_attempts: int = Field(
        default=60,
        ge=1,
        description="Readiness polls the snapshot/KPI-set wait tolerates before failing the "
        "stage. Default x snapshot_poll_interval_s = 300s, the previous hardcoded ceiling. "
        "Raise it for a use case whose snapshots do real work at creation time — an ITS "
        "snapshot decodes video into sampled frames, an inspection snapshot does not.",
    )
    snapshot_poll_interval_s: float = Field(
        default=5.0,
        gt=0,
        description="Seconds between snapshot/KPI-set readiness polls. Must be > 0: a zero "
        "interval turns the bounded wait into a tight spin against the Storage Backend.",
    )
    min_snapshot_records: int = Field(
        default=1,
        ge=0,
        description="Records a READY snapshot must hold before DEFT is started. The default "
        "of 1 rejects only a truly empty pool — a selection whose facets match nothing comes "
        "back READY with total_records=0, and training on it wastes a full run. Raise it for a "
        "use case with a known minimum viable pool; 0 disables the check.",
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DeftJobConfig:
        """Build from a plain dict (config_manager / YAML entrypoint)."""
        return cls(**data)


# Back-compat alias: the class's public name before ITS. Same object, so
# isinstance and ``from_dict`` are unchanged.
AOIJobConfig = DeftJobConfig

# The registry keys THIS BACKEND owns, splatted into ``JOB_CONFIG_TYPES``.
# Insertion order matters — ``aoi_job`` first, so ``config_type_for``'s
# ``isinstance`` FALLBACK still answers "aoi_job" for an UNSTAMPED
# ``DeftJobConfig``, as every pre-ITS caller already got.
DEFT_JOB_CONFIG_TYPES: Mapping[str, type[JobConfig]] = MappingProxyType(
    {
        "aoi_job": DeftJobConfig,
        "its_job": DeftJobConfig,
    }
)


class DeftSystemConfig(SystemConfig):
    """DEFT system config — the shared blocks plus the DEFT-loop endpoints.

    The Model Manager is a DEFT-only service (the AOI promotion control plane), so
    it lives here rather than on the shared ``SystemConfig`` base.
    """

    deft_config: DEFTConfig = Field(default_factory=DEFTConfig)
    model_manager: ModelManagerConfig = Field(default_factory=ModelManagerConfig)
    disk_reclaim: DiskReclaimConfig = Field(default_factory=DiskReclaimConfig)


def load_deft_settings(config_data: dict) -> tuple[SystemConfig, dict[str, JobConfig]]:
    """Build the DEFT system config + job configs from raw YAML data.

    Returns ``(system_config, job_configs)`` keyed by ``job_type``: one entry per
    DEFT-owned registry key whose ``<job_type>_config`` block is present, so a
    deployment can ship a default for BOTH. A block present but EMPTY parses to
    ``None`` and is treated as absent, rather than exploding inside ``from_dict``
    with a ``TypeError`` naming no block.

    Built via ``JobConfig.build`` so each entry is stamped with the key it loaded
    under; without the stamp ``config_type_for`` would classify an ``its_job``
    config as ``aoi_job``, since both keys are the same class.
    """
    system_config = DeftSystemConfig(
        deft_config=DEFTConfig(**config_data.get("deft_config", {})),
        model_manager=ModelManagerConfig(**config_data.get("model_manager", {})),
        disk_reclaim=DiskReclaimConfig(**(config_data.get("disk_reclaim") or {})),
        **build_shared_system_kwargs(config_data),
    )

    job_configs: dict[str, JobConfig] = {}
    for job_type, model in DEFT_JOB_CONFIG_TYPES.items():
        block = config_data.get(f"{job_type}_config")
        if block is not None:
            job_configs[job_type] = model.build(block, job_type)
    return system_config, job_configs

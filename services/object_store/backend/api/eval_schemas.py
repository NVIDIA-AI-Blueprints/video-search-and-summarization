"""Schemas for the Eval-set object-store API (track 11).

Follows the agreed AOI Storage Backend API contract (shaunakg/aoi-api
storage-backend/openapi.json): CreateEvaluationSetRequest /
CreateEvaluationSetResponse / EvaluationSetStats.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from deft_common import Status


class EvalSelection(BaseModel):
    """Contract: EvaluationSetSelection.

    model/version/role are required exact-match selectors; from/to are optional
    ISO-8601 UTC time bounds (inclusive lower / exclusive upper).
    """

    # protected_namespaces=() so the `model` field doesn't clash with pydantic's
    # `model_` namespace; populate_by_name so `from` (a Python keyword) maps to from_.
    model_config = ConfigDict(
        extra="forbid", populate_by_name=True, protected_namespaces=()
    )

    model: str = Field(min_length=1)
    version: str = Field(min_length=1)
    role: str = Field(min_length=1)
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class EvalRequest(BaseModel):
    """Contract: CreateEvaluationSetRequest (no caller-supplied eval_id)."""

    model_config = ConfigDict(extra="forbid")

    selection: EvalSelection
    reason: str | None = None
    metadata: dict[str, Any] | None = None


class EvalResponse(BaseModel):
    """Contract: CreateEvaluationSetResponse."""

    model_config = ConfigDict(extra="forbid")

    eval_id: str


class SetManifest(BaseModel):
    """Contract: SetManifest — one exported manifest object."""

    model_config = ConfigDict(extra="forbid")

    format: Literal["jsonl"] = "jsonl"
    uri: str
    size_bytes: int
    sha256: str


class EvalStatusResponse(BaseModel):
    """Contract: EvaluationSetStats (GET /v1/eval/<eval_id>)."""

    model_config = ConfigDict(extra="forbid")

    eval_id: str
    # Job lifecycle: creating | ready | failed (a subset of deft_common.Status /
    # the contract SetStatus). Serialises to the plain string value.
    status: Status
    total_records: int = 0
    manifests: list[SetManifest] = Field(default_factory=list)
    created_at: str

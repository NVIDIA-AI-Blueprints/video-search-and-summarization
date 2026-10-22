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

"""MongoDB data model for promotion records (HITL surface)."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

PromotionState = Literal[
    "pending_approval",
    "approved",
    "promoting",
    "promoted",
    "rejected",
    "rolled_back",
    "failed",
]
PromotionGate = Literal["pull", "deploy", "canary", "full"]
#: The same closed set ``DeftJobConfig.model_format`` writes, so the record
#: cannot carry a value the job config could not have produced.
ModelFormat = Literal["onnx", "pth", "tensorrt", "triton"]


def _generate_promotion_id() -> str:
    """Generate a promotion id (e.g. ``promo-<hex>``)."""
    return f"promo-{uuid4().hex[:12]}"


class PromotionRecord(BaseModel):
    """MongoDB document tracking one candidate's promotion lifecycle."""

    promotion_id: str = Field(default_factory=_generate_promotion_id)
    model_name: str = Field(..., description="Registered model name")
    candidate_version: str = Field(
        ...,
        description=(
            "Version being promoted — the DFW/Mongo-minted serving integer "
            "(e.g. '2'), which IS the serving/Triton version handed to the "
            "Model Manager on pull/apply."
        ),
    )
    baseline_version: str | None = Field(
        default=None,
        description=(
            "Currently-live baseline version — the serving integer of the last "
            "promoted candidate (the rollback/cleanup target)."
        ),
    )
    deft_run_id: str | None = Field(
        default=None,
        description=(
            "Lineage: the DEFT fine-tune run id that produced this candidate. "
            "Provenance only — the serving version is candidate_version (the "
            "Mongo-minted integer), not this id."
        ),
    )
    # Required, no default: sibling use cases share ONE promotions collection, so
    # defaulting to "aoi" would silently grade ITS detection windows with the AOI
    # FAR evaluator. There is no read-side shim either — a document without a use
    # case fails validation in ``from_mongo`` rather than being guessed at.
    use_case: str = Field(
        ...,
        min_length=1,
        description=(
            "Evaluator + KPI-set selection key (e.g. 'aoi', 'its'). Drives "
            "get_backend_provider().evaluator(use_case=...) for online/offline eval. "
            "Every writer must state it explicitly."
        ),
    )
    flywheel_run_id: str | None = Field(
        default=None,
        description=(
            "The FlywheelRun this promotion belongs to. The run stays in status "
            "'promoting' until this promotion reaches a terminal state; the "
            "controller then marks the run completed (promoted) or failed."
        ),
    )
    promotion_policy: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The PER-RUN promotion policy (serialized PromotionPolicyConfig) the "
            "controller applies to THIS promotion — HITL gates, canary steps, "
            "polling, auto-rollback. Without it the controller falls back to the "
            "deployment's default AOI job config, which silently ignores a named "
            "config's policy."
        ),
    )
    # Artifact locators the controller hands to the Deployment Backend `pull`.
    artifact_uri: str | None = Field(
        default=None, description="Registry/object-store locator for the candidate artifact"
    )
    spec_uri: str | None = Field(
        default=None,
        description=(
            "Object-store locator of the training/inference spec published "
            "alongside the checkpoint (staged as spec.yaml beside the weights "
            "by the serving stack). Optional — older DEFT services do not "
            "publish one."
        ),
    )
    # DURABLE raw object-store locators (s3:// or bare key) the controller
    # re-presigns from on EVERY pull attempt. Unlike artifact_uri/spec_uri
    # (which hold the ephemeral presigned URL), these never expire — so a
    # slow/retrying pull can always get a fresh signed URL instead of 403-ing on
    # a stale one. None for pass-through http/file locators and for older
    # records written before this field existed (backward compatible).
    artifact_locator: str | None = Field(
        default=None,
        description=(
            "Durable raw object-store locator (s3:// or bare key) for the "
            "candidate artifact. The controller re-presigns from this on each "
            "pull attempt so the download URL is never stale. None when the "
            "artifact is a pass-through http/file URL or for legacy records."
        ),
    )
    spec_locator: str | None = Field(
        default=None,
        description=(
            "Durable raw object-store locator of the training/inference spec, "
            "re-presigned per pull attempt (mirrors artifact_locator). Optional."
        ),
    )
    threshold: float | None = Field(
        default=None,
        description=(
            "DEFT's per-model best decision threshold, staged as a metadata "
            "sidecar beside the weights by the serving stack. Optional — older "
            "DEFT services do not emit one. No hard 0..1 bound (may be a "
            "logit-space cutoff)."
        ),
    )
    model_format: ModelFormat = Field(
        default="onnx", description="Artifact format handed to the pull (onnx|pth|tensorrt|triton)"
    )
    sha256: str | None = Field(default=None, description="Integrity digest verified on pull")
    # Rollout bookkeeping owned by the controller.
    serving_role: str | None = Field(
        default=None,
        description="Current rollout role the candidate serves (shadow|canary|default).",
    )
    canary_step_index: int = Field(
        default=0, description="Index into the canary step schedule (0-based)."
    )
    state: PromotionState = Field(default="pending_approval")
    pending_gate: PromotionGate | None = Field(
        default=None, description="Gate awaiting approval (set only while pending_approval)"
    )
    eval_metrics: dict[str, Any] | None = Field(default=None)
    verdict: Literal["advance", "hold", "rollback"] | None = Field(
        default=None, description="Latest online-eval verdict (drives escalation gates)"
    )
    swap_id: str | None = Field(
        default=None,
        description=(
            "Model Manager swap id for the active apply. Log correlation only; it "
            "is process-local to the Model Manager that minted it. Poll 'applied' "
            "instead."
        ),
    )
    # Must stay declared. model_config does not set `extra`, so Pydantic's
    # `ignore` default applies and from_mongo's cls(**doc) would silently drop
    # an undeclared field on read.
    applied: dict[str, Any] | None = Field(
        default=None,
        description=(
            "The Model Manager's expectation object for the outstanding apply, "
            "stored verbatim and handed back as the convergence expectation. "
            "It is opaque to DFW and stays a bare dict so no field a newer "
            "Model Manager adds is dropped."
        ),
    )
    rollback_target_reclaimed: bool = Field(
        default=False,
        description=(
            "Set by the tasks.promotion.disk_reclaim sweep when it reclaims the "
            "directory of this record's baseline_version. Advisory: written by "
            "nothing else and read by nothing except rollback's precheck, so it "
            "cannot perturb the gate machine. It must be DECLARED here rather "
            "than left as an extra key -- pydantic's default extra='ignore' "
            "would drop it in from_mongo, and the precheck would never see it."
        ),
    )
    candidate_reclaimed: bool = Field(
        default=False,
        description=(
            "Set by the tasks.promotion.disk_reclaim sweep when it reclaims the "
            "directory of this record's candidate_version, which is the version "
            "this record NOMINATES. It is what retires that nomination: "
            "known_versions is the serving stack's loaded-model list rather than "
            "a directory listing, so a reclaimed version keeps its row there and "
            "would otherwise be offered to the sweep every tick forever. "
            "Narrowing only -- it can withhold a nominee, never add one -- so a "
            "flag written in error costs a directory that is not collected, "
            "never one that is. Declared here for the same reason as the field "
            "above: pydantic's default extra='ignore' would drop it in "
            "from_mongo."
        ),
    )
    evicted_at: datetime | None = Field(
        default=None,
        description=(
            "When an unload driven by THIS promotion SUCCEEDED. The disk-reclaim "
            "sweep nominates a version only against this stamp: it is the single "
            "place DFW records that a version left serving memory, and the sweep "
            "no longer re-evicts to find out. ABSENCE IS NEVER EVIDENCE — a "
            "promotion that ended before any eviction, one whose unload raised, "
            "and one that predates this field are all unstamped, and an "
            "unstamped version is never nominated, forever."
        ),
    )
    evicted_versions: list[str] = Field(
        default_factory=list,
        description=(
            "Which versions the stamp above is about, and the reason neither "
            "field is named ``baseline_*``: a promotion evicts its BASELINE at "
            "the full switch and its own CANDIDATE when a rollback converges or "
            "a never-routed candidate is retired, so a ``baseline_``-prefixed "
            "field holding a candidate version would be a quiet lie. A LIST "
            "because one record can do both, and a single slot would let the "
            "second stamp overwrite the first, stranding that version forever. "
            "The sweep only honours a stamp whose version is one this record "
            "actually names, so a record hand-edited or migrated into the wrong "
            "shape is detectable rather than silently authoritative over some "
            "other version's directory."
        ),
    )
    evicted_version: str | None = Field(
        default=None,
        description=(
            "Superseded by ``evicted_versions``; read by the sweep, never "
            "written. Declared so records stamped before the field became a "
            "list do not read as unstamped, and so stay collectable."
        ),
    )
    created_at: datetime = Field(default_factory=datetime.utcnow)
    decided_by: str | None = Field(default=None)
    decided_at: datetime | None = Field(default=None)
    settled_at: datetime | None = Field(
        default=None,
        description=(
            "When this promotion FINISHED -- the moment its state last BECAME "
            "terminal, re-stamped on a CHANGE of terminal state so a January "
            "promotion rolled back in March settles in March. "
            "Written by PromotionController._update and by nothing "
            "else, which is the point: ``decided_at`` is an APPROVER's decision "
            "time, and on the dominant HITL path it is stamped at the approval, "
            "hours or days before the rollout ends. Ageing a record from it "
            "would retire a version against a clock that started too early. "
            "The disk-reclaim sweep's retention window measures from HERE and "
            "from nothing else: a record without this field has no known settle "
            "time, and unknown reads as 'not settled yet', forever."
        ),
    )
    decision_note: str | None = Field(
        default=None, description="Approver note / reject reason for audit context"
    )

    model_config = {"populate_by_name": True}

    def to_mongo(self) -> dict:
        """Convert to a MongoDB document."""
        return self.model_dump(exclude_none=True)

    @classmethod
    def from_mongo(cls, doc: dict) -> "PromotionRecord | None":
        """Create from a MongoDB document.

        No shim for a missing ``use_case``: deployments are always fresh, so a
        document without one is a writer bug, not an old row. Guessing it would
        grade the record with the wrong use case's evaluator and leave no signal
        that anything went wrong, so validation is allowed to raise here.
        """
        if not doc:
            return None
        doc = dict(doc)
        doc.pop("_id", None)
        return cls(**doc)


def has_served(record: "PromotionRecord | Mapping[str, Any]") -> bool:
    """True once the candidate has been placed in front of ANY traffic.

    ``serving_role`` is stamped on every serving apply (shadow / canary /
    default) and is never cleared, so it — not the gate name — is the durable
    answer to "is something live?".

    Gate names used to be a valid proxy: ``pull``/``deploy`` meant "nothing
    serving" only because the shadow deploy gate was mandatory. Once
    ``promotion_policy.deployment_stages`` can switch stages off, the FIRST
    serving gate is ``canary`` (shadow off) or ``full`` (both off), so no fixed
    set of gate names can answer this.

    Accepts a ``PromotionRecord`` or a raw Mongo document: the beats sweep raw
    dicts deliberately, so that one malformed legacy record cannot raise and
    abort the whole pass. ``to_mongo()`` uses ``exclude_none=True``, so the key
    is ABSENT (not null) until the first serving apply — ``.get()`` treats both
    identically, which is what we want.
    """
    role = record.get("serving_role") if isinstance(record, Mapping) else record.serving_role
    return role is not None

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

"""Pydantic mirrors of the Model Promotion API (model_promotion/openapi.json).

This schema module is the frozen contract between DFW's Deployment Backend and
the Model Manager. Keep it aligned with the OpenAPI document.
"""

from datetime import datetime
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

Control = Literal["shadow", "canary", "full_switch", "unload"]
Role = Literal["default", "canary", "shadow"]
#: ``pth`` is what DEFT publishes for detector use cases (ITS ships a
#: ``gdino_model_latest.pth``), so a job config can declare the artifact it hands
#: over rather than the format it is later compiled into. The builder selects its
#: handler from the staged extension, so ``.pth`` routes to ``gdino_pth``.
ModelFormat = Literal["onnx", "pth", "tensorrt", "triton"]
ErrorCode = Literal[
    "not_found",
    "bad_request",
    "version_exists",
    "checksum_mismatch",
    "signature_invalid",
    "registry_unreachable",
    "load_failed",
    "readiness_timeout",
    "dataset_empty",
    "conflict",
    "internal",
]


def _single_path_component(value: str) -> str:
    """Reject a value that would not stay put when used as a directory name.

    Model names and versions are not merely identifiers: they are joined into
    ``{repo}/{model_name}/{version}/`` to locate a directory the Model Manager
    creates, writes into and — during cleanup — deletes outright. Path joining is
    silently destructive with hostile input. A ``..`` segment climbs out of the
    repository, an absolute value discards the root altogether (``/models``
    joined with ``/etc`` is just ``/etc``), and an empty value collapses the join
    by one level, aiming a per-version delete at the whole model's directory.

    So these fields are constrained to exactly one path component. This is the
    outer of two layers; the activation layer proves containment again
    immediately before it creates or removes anything, because a validator can
    be bypassed by any future caller that does not come through the API.
    """
    if not value or value in (".", ".."):
        raise ValueError("must be a non-empty name other than '.' or '..'")
    # Both flavours regardless of host OS: a backslash that is inert on Linux is
    # a separator the day this runs anywhere else.
    if any(sep in value for sep in ("/", "\\")):
        raise ValueError("must not contain a path separator")
    if "\x00" in value:
        raise ValueError("must not contain a null byte")
    if PurePosixPath(value).is_absolute() or PureWindowsPath(value).is_absolute():
        raise ValueError("must not be an absolute path")
    return value


PathComponent = Annotated[str, AfterValidator(_single_path_component)]
"""A string safe to use as a single directory name under the model repository."""


def _parseable_uri(value: str) -> str:
    """Reject a locator ``urlparse`` cannot read.

    ``urlparse`` raises ``ValueError`` on a malformed authority (``http://[::1``
    is the easy one), and activation calls it twice per pull -- once to derive
    the artifact filename, once to pick the download scheme. Left unchecked that
    is a caller's typo answered as a server error. A scheme is deliberately NOT
    required: a bare filesystem path is a supported locator.
    """
    try:
        urlparse(value)
    except ValueError as exc:
        raise ValueError(f"is not a parseable URI: {exc}") from exc
    return value


Locator = Annotated[str, AfterValidator(_parseable_uri)]
"""An artifact/spec locator the activation layer can parse without raising."""


class HealthResponse(BaseModel):
    """Liveness/readiness payload for ``GET /v1/health``."""

    status: Literal["ok", "degraded"] = "ok"


class Error(BaseModel):
    """A single failure, carrying the caller's retry decision with it.

    ``retryable`` exists so the caller does not have to infer that from the HTTP
    status: the same status can mean either, and guessing wrong either abandons
    recoverable work or retries something that will never succeed.
    """

    code: ErrorCode
    message: str
    retryable: bool
    details: dict | None = None


class ErrorEnvelope(BaseModel):
    """The single error shape every non-2xx Model Manager response renders."""

    error: Error


class PullRequest(BaseModel):
    """Stage a version's artifacts into the shared model repository and load it.

    ``model_name`` and ``version`` name the directory the artifacts are written
    to, so both are constrained to a single path component.
    """

    model_name: PathComponent
    version: PathComponent
    artifact_uri: Locator = Field(..., description="Registry/object-store locator")
    format: ModelFormat
    sha256: str = Field(..., description="Integrity digest verified after pull")
    usecase: str | None = None
    spec_uri: Locator | None = Field(
        default=None,
        description=(
            "Object-store locator of the training/inference spec published "
            "alongside the model artifact. When present, staged as spec.yaml "
            "next to the weights in the shared model repository. Optional: "
            "absent for versions whose DEFT service did not publish a spec."
        ),
    )
    threshold: float | None = Field(
        default=None,
        # NaN/Infinity are not JSON, and this goes straight into metadata.json
        # via ``json.dumps``. An unreadable sidecar is a SILENT fallback to a
        # hardcoded default threshold -- a moved decision boundary on live
        # traffic, reported to the caller as 201 Created.
        allow_inf_nan=False,
        description=(
            "Optional decision threshold emitted by DEFT for this version. When "
            "present, persisted as a metadata.json sidecar next to the weights in "
            "the shared model repository so the serving stack can apply it on "
            "load (the spec.yaml stays byte-for-byte verbatim). Optional: absent "
            "for versions whose DEFT service did not publish a threshold. No hard "
            "0..1 bound (DEFT may emit a logit-space cutoff). A supplied threshold "
            "is always (re)written, so a changed value is never masked by a stale "
            "sidecar. An absent threshold is NOT an instruction to clear a "
            "previously staged one: the existing sidecar is left in place, because "
            'removing it does not restore "no threshold" — the serving stack has '
            "no such state and silently falls back to a hardcoded default. To "
            "remove a threshold, clean up the version and stage it afresh."
        ),
    )
    idempotency_key: str | None = Field(default=None, min_length=1)


class PullResponse(BaseModel):
    """Where the staged version landed, once it is loaded and READY."""

    model_name: str
    version: str
    repo_path: str
    status: Literal["staged"] = "staged"


class Applied(BaseModel):
    """The routing intent the Model Manager performed, and the thing to poll on.

    The caller stores this verbatim and hands it back as
    ``ConvergenceRequest.expectation``; it never has to construct one itself.

    ``from_version`` is derived, not received: it is the live default read just
    before the routing write. Convergence needs it, and a full switch destroys
    it, so it is captured at write time.
    """

    model_name: PathComponent
    control: Control
    to_version: PathComponent
    from_version: str | None = None
    canary_weight: float | None = Field(default=None, ge=0, le=1)
    key: str = Field(..., min_length=1, description="Correlation token; echoes idempotency_key.")

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def _canary_requires_a_weight(self) -> "Applied":
        """Same rule as ``ApplyRequest``, enforced again on the way back in.

        Convergence is judged against this object, and a missing weight would let
        any observed split pass.
        """
        if self.control == "canary" and self.canary_weight is None:
            raise ValueError("canary_weight is required when control == 'canary'")
        return self


ReasonCode = Literal[
    "routing_unreadable",
    "loaded_unreadable",
    "primary_moved",
    "default_version_mismatch",
    "routing_not_exclusive",
    "shadow_absent",
    "canary_absent",
    "canary_weight_mismatch",
    "candidate_not_ready",
    "still_resident",
]


class ConvergenceReason(BaseModel):
    """Why a swap has not converged, and whether waiting will help.

    ``terminal`` means the expectation can no longer come true. It says nothing
    about budgets; how long to wait is the caller's decision.
    """

    code: ReasonCode
    message: str
    terminal: bool = False
    detail: dict | None = None


class ConvergenceReport(BaseModel):
    """Whether the observed serving state satisfies an expectation.

    ``observed`` is the ordinary serving-state projection, including the
    ``routing_available`` / ``loaded_available`` flags the fail-closed rules use.
    """

    converged: bool
    reason: ConvergenceReason | None = None
    observed: "ServingStateResponse"
    at: datetime

    model_config = ConfigDict(extra="forbid")


class ConvergenceRequest(BaseModel):
    """Ask whether an ``Applied`` intent is now reflected in live serving state."""

    expectation: Applied

    model_config = ConfigDict(extra="forbid")


class ApplyRequest(BaseModel):
    """Activate an already-staged version under a rollout control.

    The version must already be READY: the serving stack refuses to route
    traffic to one that is not, which is why the load and its engine build happen
    during ``pull`` rather than here.
    """

    model_name: PathComponent
    version: PathComponent
    control: Control
    usecase: str | None = None
    canary_weight: float | None = Field(default=None, ge=0, le=1)
    idempotency_key: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _canary_requires_a_weight(self) -> "ApplyRequest":
        """Reject a canary with no weight.

        Convergence skips the weight check when it is absent, so a 5% canary
        would be satisfied by a live 50% one.
        """
        if self.control == "canary" and self.canary_weight is None:
            raise ValueError("canary_weight is required when control == 'canary'")
        return self


class ApplyResponse(BaseModel):
    """The result of an ``apply``.

    ``applied`` is what the caller persists and polls on. ``swap_id`` is a log
    correlation token only: nothing resolves it, and
    ``GET /v1/models/status/{swap_id}`` returns 410.
    """

    swap_id: str
    to_version: str
    control: Control
    started_at: datetime
    from_version: str | None = None
    applied: Applied = Field(
        ...,
        description=(
            "What was applied. Hand this back as the convergence expectation; "
            "it is always present."
        ),
    )
    noop: bool = Field(
        default=False,
        description=(
            "True when live state already satisfied the intent, so no routing "
            "write was issued. Do not overwrite a previously persisted "
            "'applied' with one from a noop response: its from_version was read "
            "after the original write landed, so it is not the displaced version."
        ),
    )


class RoutingEntry(BaseModel):
    """One version's share of traffic: ``weight`` is a fraction, not a percent."""

    version: str
    role: Role
    weight: float = Field(..., ge=0, le=1)


class ServingStateResponse(BaseModel):
    """Read-only view of what is live for a model, keyed by model name.

    Additive to the frozen Model Promotion contract: this is a Model Manager
    extension, not a mirror of an ``model_promotion/openapi.json`` operation.
    The spec document owns pull/apply/cleanup; this endpoint exists
    because that contract has no by-name read, only ``status/{swap_id}`` keyed
    on a restart-volatile in-process id.

    ``known_versions`` is the field a caller should use to pick the next
    non-colliding version. It is the UNION of what the serving backend reports
    for this model and what occupies a directory in the model repo — including
    versions whose state is ``UNAVAILABLE``/unloaded, and versions the backend
    does not report at all.

    Both halves are load-bearing. A floor computed only from ready/serving
    versions would be empty on a box where nothing is loaded. And the backend's
    own list is not sufficient either: a version staged but never loaded has no
    entry, and the unified contract explicitly permits an implementation to omit
    an unloaded version from its status snapshot rather than retain a tombstone.
    Either way the directory outlives the report, and a caller minting from the
    report alone would re-stage on top of it.

    ``routing_available`` / ``loaded_available`` preserve the "unknown vs empty"
    distinction from ``ServingState``. When a flag is False the corresponding
    list is *unknown*, not *empty*, and a caller that would act destructively (or
    mint a version) on that basis must fail closed rather than trust the empty
    list.
    """

    model_name: str
    default_version: str | None = None
    routing: list[RoutingEntry] = Field(default_factory=list)
    serving_versions: list[str] = Field(
        default_factory=list,
        description="Versions referenced by routing (default/canary/shadow). Unknown when routing_available is false.",
    )
    known_versions: list[str] = Field(
        default_factory=list,
        description=(
            "Version floor for this model: every version the serving backend reports, "
            "including UNAVAILABLE/unloaded ones, plus any version that still occupies a "
            "directory in the model repo. Mint the next version above this. Unknown, not "
            "empty, when loaded_available is false."
        ),
    )
    ready_versions: list[str] = Field(
        default_factory=list,
        description=(
            "Versions the backend reports servable, and the set convergence is "
            "judged on. Narrower than known_versions, which also includes the "
            "on-disk listing. Unknown when loaded_available is false."
        ),
    )
    ready: bool = False
    routing_available: bool = True
    loaded_available: bool = True


class UnloadRequest(BaseModel):
    """Evict a version from serving memory. Touches no files.

    ``model_name`` and ``version`` keep ``PathComponent`` even though this verb
    deletes nothing: the inventory read it performs joins the same two values
    into a repository path.
    """

    model_name: PathComponent
    version: PathComponent
    usecase: str | None = None


class UnloadResponse(BaseModel):
    """The outcome of an eviction. Carries no ``disk_reclaimed``.

    Not a subclass of ``CleanupResponse``: a constant ``disk_reclaimed: false``
    here would look like cleanup's degraded outcome -- evicted, directory not
    cleared -- which is the one signal an operator has for a volume that will not
    drain.
    """

    version: str
    status: Literal["removed"] = "removed"


class CleanupRequest(BaseModel):
    """Reclaim a retired version's directory. Evicts nothing.

    This is the one destructive verb in the contract, so ``model_name`` and
    ``version`` are constrained to a single path component each: they select the
    directory that gets removed.
    """

    model_name: PathComponent
    version: PathComponent
    usecase: str | None = None
    min_age_s: float | None = Field(
        default=None,
        ge=0.0,
        description=(
            "Refuse to act on this version at all unless its directory was last "
            "modified at least this many seconds ago. Checked BEFORE the backend "
            "reads, not merely before the delete, because the hazard it covers -- "
            "a directory being written right now -- is one a backend publishes as "
            "deletable within seconds of the mkdir. A directory that is not there "
            "at all is not a young one: the floor has no opinion on it and the "
            "guards below decide, which is what keeps a reclaim idempotent after "
            "the directory is already gone. An age that cannot be READ refuses. "
            "Narrowing only -- it can add a refusal, never authorise a delete. The "
            "effective floor is the greater of this and MODEL_MANAGER_RECLAIM_MIN_AGE_S."
        ),
    )
    dry_run: bool = Field(
        default=False,
        description=(
            "Run every guard for real and evaluate the deletable predicate for "
            "real, then log what would have been reclaimed and skip only the "
            "removal. Measures the actual predicate rather than a simulation of it."
        ),
    )
    evicted_by_caller: bool = Field(
        default=False,
        description=(
            "The CALLER asserts it evicted this version and can enumerate what "
            "it evicted -- not a backend observation, and nothing here confirms "
            "it. It satisfies exactly one clause, the absent-row branch on a "
            "backend that drops an unloaded version's row, and clears no guard: "
            "a PRESENT row must still read unloaded; routing, residency, build, "
            "aliasing and stream refcounts are untouched; and a PUBLISHED "
            "deletable entry the same payload reports no row for stays dropped. "
            "Omitting it can only add refusals."
        ),
    )


class CleanupResponse(BaseModel):
    """Outcome of a cleanup: a reclaim that happened, or did not."""

    version: str
    status: Literal["removed", "kept"] = Field(
        description=(
            "Where the version's directory stands when this call returns: "
            "'removed' if it is not on the shared model repository, 'kept' if it "
            "is still there. The POSTCONDITION, which is what the existing "
            "contract already says this field carries -- while disk_reclaimed is "
            "what THIS call did. The two differ in exactly one direction: a "
            "directory that was already gone reads removed with no reclaim "
            "claimed by a dry run, and never the reverse. It was a one-valued "
            "Literal until this release, so a refusal that deleted nothing and a "
            "dry run that deleted nothing both answered 'removed' about a "
            "directory that was still there -- a constant, and therefore not an "
            "answer. Deliberately carries NO default: a construction that forgets "
            "it must fail here rather than inherit the claim that something was "
            "removed. WIRE NOTE: 'kept' is a value no caller has seen before. "
            "DFW's client passes the field through and compares it to nothing, "
            "but a consumer validating against the previous one-value enum would "
            "reject it, which is the existing Model-Manager-first deploy order."
        ),
    )
    disk_reclaimed: bool = Field(
        default=False,
        description=(
            "Whether this version's directory is done with the reclaim path: it "
            "went away, and nothing further will ever be freed for it. False "
            "means some term of the delete decision withheld -- the serving stack "
            "did not report the directory as safe to delete, or one of this "
            "service's own roots refused, or a dry run was asked for -- so the "
            "caller must offer the version again. It is what a sweep retires a "
            "nomination on, which is why a directory that was ALREADY gone "
            "reports true: reporting the removal this call did not perform would "
            "be truthful about the call and would re-offer that version on every "
            "tick, forever. A dry run is the one case that reports false over an "
            "absent directory, because a dry run's answer must move nothing in "
            "the caller either; status still says the directory is gone. "
            "Additive Model Manager extension to the frozen contract; a caller "
            "that ignores it sees the unchanged pre-existing behaviour."
        ),
    )


# ``ConvergenceReport.observed`` forward-references ServingStateResponse, defined
# further down.
ConvergenceReport.model_rebuild()

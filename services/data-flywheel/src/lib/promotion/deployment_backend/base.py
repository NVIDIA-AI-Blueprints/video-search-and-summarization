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

"""Base interface for the Deployment Backend abstraction.

The DFW-side client abstraction the Promotion Controller drives: the Model
Promotion API verbs (pull / apply / convergence / rollback / cleanup), implemented
by an HTTP client against the standalone Model Manager service. Same structure as
``src/lib/deployment/adapters/base.py``.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PromotionControl(str, Enum):
    """Rollout control verbs understood by the Model Manager apply call."""

    SHADOW = "shadow"
    CANARY = "canary"
    FULL_SWITCH = "full_switch"
    UNLOAD = "unload"


@dataclass
class RoutingEntry:
    """A single routing assignment (matches Model Promotion ``RoutingEntry``)."""

    version: str
    role: str
    weight: float


@dataclass
class PullResult:
    """Result of staging (pulling) a model version into the serving repo."""

    model_name: str
    version: str
    repo_path: str
    status: str


@dataclass
class ApplyResult:
    """Result of activating a staged version with a rollout control.

    ``applied`` is the Model Manager's own expectation object: persist it and
    hand it back to ``convergence`` verbatim. A bare dict, because a mirrored
    dataclass would drop fields a newer Model Manager adds and the wire forbids
    extras, so a dropped field becomes a 422.

    ``swap_id`` is a log correlation token only. Nothing resolves it.
    """

    # First and undefaulted on purpose: a missing token is then a TypeError at
    # the call site, not a promotion that polls None.
    applied: dict[str, Any]
    to_version: str
    control: PromotionControl
    started_at: str
    from_version: str | None = None
    noop: bool = False
    swap_id: str | None = None


@dataclass
class ServingState:
    """What a serving stack currently holds for one model, keyed by model name.

    The standing answer to "what is live right now?", askable before anything has
    started; ``ConvergenceReport`` answers about one specific applied intent.

    ``known_versions`` includes versions that are NOT loaded: an unloaded version
    directory still owns its slot, so it still collides.

    The ``*_available`` flags keep "nothing is there" apart from "I could not find
    out" -- an empty list with ``routing_available=False`` means unknown.
    """

    model_name: str
    default_version: str | None = None
    routing: list[RoutingEntry] = field(default_factory=list)
    serving_versions: list[str] = field(default_factory=list)
    known_versions: list[str] = field(default_factory=list)
    #: Versions the stack reports SERVABLE. ``None`` is unknown -- an older Model
    #: Manager omits it -- and unknown must never read as "nothing is servable".
    ready_versions: list[str] | None = None
    ready: bool = False
    routing_available: bool = True
    known_available: bool = True


@dataclass
class ConvergenceReason:
    """Why an intent has not landed yet, and whether waiting can still help.

    ``code`` stays a plain ``str``, not an enum: the Model Manager owns that
    vocabulary and may add to it. Branch on ``terminal``, log ``code``.

    ``terminal`` says whether the backend can still get there; how long to wait is
    the caller's decision.
    """

    code: str
    message: str
    terminal: bool = False
    detail: dict | None = None


@dataclass
class ConvergenceReport:
    """The answer to "has this intent landed, and will waiting help?"

    Carries no observed serving state: nothing in DFW reads it, and
    ``reason.message``/``reason.detail`` already carry what a log needs.
    """

    converged: bool
    reason: ConvergenceReason | None = None


class BackendError(Exception):
    """Typed error raised when the Model Promotion API returns an ``ErrorEnvelope``.

    Mirrors the wire ``Error`` object: a machine-readable ``code``, a human
    ``message``, and a ``retryable`` hint the retry layer honors. ``http_status``
    and ``details`` are diagnostics.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool,
        *,
        http_status: int | None = None,
        details: dict | None = None,
    ):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.http_status = http_status
        self.details = details


class DeploymentBackend(ABC):
    """Abstract client the Promotion Controller uses to drive a serving stack.

    Rollback is expressed as ``apply(full_switch, previous_version)``: the AOI
    contract has no dedicated rollback endpoint.
    """

    @abstractmethod
    def pull(
        self,
        model_name: str,
        version: str,
        artifact_uri: str,
        fmt: str,
        sha256: str,
        *,
        spec_uri: str | None = None,
        threshold: float | None = None,
        idempotency_key: str | None = None,
    ) -> PullResult:
        """Stage a registered version into the serving model repo.

        ``spec_uri`` stages as ``spec.yaml`` next to the checkpoint; ``threshold``
        persists as a metadata sidecar. Both absent when DEFT emitted neither.
        """
        ...

    @abstractmethod
    def apply(
        self,
        model_name: str,
        version: str,
        control: PromotionControl,
        *,
        canary_weight: float | None = None,
        idempotency_key: str | None = None,
    ) -> ApplyResult:
        """Activate a staged version with the given rollout control."""
        ...

    @abstractmethod
    def convergence(self, applied: dict[str, Any]) -> ConvergenceReport:
        """Has ``applied`` landed in live serving state, and will waiting help?

        ``applied`` comes back exactly as ``apply`` returned it, and is opaque
        here. A pure read; must not mutate anything.

        Raise ``BackendError`` when the question could not be ASKED. Reporting
        that as "not converged" would fail a swap on an unanswerable poll.
        """
        ...

    @abstractmethod
    def rollback(
        self,
        model_name: str,
        previous_version: str,
        *,
        idempotency_key: str | None = None,
    ) -> ApplyResult:
        """Roll back to a previous version (== apply(full_switch, previous))."""
        ...

    def unload(self, model_name: str, version: str) -> dict:
        """Evict a version from serving memory without reclaiming its files.

        Concrete, not abstract, so adding the verb does not break out-of-tree
        backends at import time. It raises rather than falling back to
        ``cleanup``, which may delete the full switch's rollback target.
        """
        raise NotImplementedError(f"{type(self).__name__} has no unload verb")

    def cleanup(
        self,
        model_name: str,
        version: str,
        *,
        min_age_s: float | None = None,
        dry_run: bool = False,
        evicted_by_caller: bool = False,
    ) -> dict:
        """Reclaim the disk an already-evicted version is holding.

        ``status`` and ``disk_reclaimed`` differ: a guard may refuse the delete
        while the call still returns 200, and reporting that as a plain success
        is how a shared model repository fills up unnoticed.

        ``min_age_s`` and ``dry_run`` are narrowing-only: they may WITHHOLD a
        reclaim, never authorise one.

        ``evicted_by_caller`` is the one argument that can WIDEN, and it asserts
        something about the CALLER's records, not the serving stack -- for
        backends that drop a status row on unload, where nothing observable
        separates "evicted" from "never loaded". It satisfies that one clause
        only: routing, residency, streams and staging still refuse on their own.

        Concrete, not abstract, for the same reason as ``unload``.
        """
        raise NotImplementedError(f"{type(self).__name__} has no cleanup verb")

    def serving_state(self, model_name: str) -> ServingState | None:
        """What the serving stack currently holds for ``model_name``.

        ``None`` means "unknown", never "nothing": callers must not read it as an
        empty stack.

        NOT abstract: a backend that cannot introspect its serving stack is still
        usable, and callers degrade rather than break.
        """
        return None

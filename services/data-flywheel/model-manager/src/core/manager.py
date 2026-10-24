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

"""ModelManager: client-agnostic orchestration of the Model Promotion verbs.

    pull        -> adapter.stage
    apply       -> adapter.apply_control + a bounded, advisory inline wait
    convergence -> compare a caller-supplied expectation against adapter.serving_state
    cleanup     -> refuse if still serving/resident/referenced, else reclaim disk
    unload      -> the same refusal stack, then adapter.unload (touches no files)

Rollback has no endpoint: it is ``apply(control="full_switch", version=previous)``
driven by DFW. ``pull`` owns the slow part of a promotion (staging plus the
engine build, minutes); ``apply`` is short because its target is already servable.

Holds no promotion state: convergence is a pure function of (expectation, live
serving state), so a restart loses nothing and any replica can answer.
"""

import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath
from time import sleep
from uuid import uuid4

from src.adapters import activation as activation_mod
from src.adapters.base import (
    EvictionAttestation,
    ServingAdapter,
    ServingState,
    VersionInventory,
)
from src.adapters.registry import get_adapter
from src.api.schemas import (
    Applied,
    ApplyRequest,
    ApplyResponse,
    CleanupRequest,
    CleanupResponse,
    ConvergenceReason,
    ConvergenceReport,
    PullRequest,
    PullResponse,
    ServingStateResponse,
    UnloadRequest,
    UnloadResponse,
)
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)


class ModelManager:
    """Orchestrates pull/apply/convergence/cleanup against a ``ServingAdapter``."""

    def __init__(
        self,
        adapter: ServingAdapter | None = None,
    ):
        self.adapter = adapter or get_adapter(settings.client)

    # ------------------------------------------------------------------ pull
    def pull(self, req: PullRequest) -> PullResponse:
        """Stage a version via the adapter (Explicit mode performs the load).

        ``req.idempotency_key`` passes straight through: re-driving a pull whose
        response was lost re-attaches to the build already running instead of
        starting a second conversion of the same weights.
        """
        name = req.model_name or settings.model_name
        repo_path = self.adapter.stage(
            name,
            req.version,
            req.artifact_uri,
            req.sha256,
            spec_uri=req.spec_uri,
            threshold=req.threshold,
            idempotency_key=req.idempotency_key,
        )
        return PullResponse(model_name=name, version=req.version, repo_path=repo_path)

    # ----------------------------------------------------------------- apply
    def apply(self, req: ApplyRequest) -> ApplyResponse:
        """Activate a staged version with a rollout control.

        The control is issued before this returns, so a rejected control raises
        here rather than surfacing later as a poll that never converges. The
        backend refuses to route to a version that is not READY.
        """
        name = req.model_name or settings.model_name
        candidate = req.version
        swap_id = f"swap-{uuid4().hex[:12]}"
        started_at = datetime.now(UTC)

        before = self.adapter.serving_state(name)
        from_version = before.default_version

        # A re-driven apply must not write again: a second routing write is
        # another chance for a reload to race live inference. Guarded on the
        # ROUTING predicate, not full convergence -- routing cannot influence
        # readiness. Once it lands, ``from_version`` reads as the post-write
        # default, so a fresh expectation could never converge; ``noop`` tells
        # the caller to keep the one it has.
        already = _control_reflected(
            req.control,
            candidate,
            before,
            from_version=from_version,
            canary_weight=req.canary_weight,
        )
        if already:
            logger.info(
                "apply %s@%s control=%s is already satisfied; no routing write issued",
                name,
                candidate,
                req.control,
            )
        else:
            self.adapter.apply_control(
                control=req.control,
                name=name,
                candidate=candidate,
                from_version=from_version,
                canary_weight=req.canary_weight,
                idempotency_key=req.idempotency_key,
            )

        # from_version is irrecoverable once a full switch lands.
        logger.info(
            "apply %s@%s control=%s from=%s weight=%s -> %s",
            name,
            candidate,
            req.control,
            from_version,
            "none" if req.canary_weight is None else f"{req.canary_weight:.3f}",
            swap_id,
        )

        self._await_ready(
            name,
            req.control,
            candidate,
            from_version=from_version,
            canary_weight=req.canary_weight,
        )

        return ApplyResponse(
            swap_id=swap_id,
            from_version=from_version,
            to_version=candidate,
            control=req.control,
            started_at=started_at,
            applied=self._applied(req, name, candidate, from_version),
            noop=already,
        )

    @staticmethod
    def _applied(req: ApplyRequest, name: str, candidate: str, from_version: str | None) -> Applied:
        """Build the intent in the shape it will be handed back to us.

        ``key`` falls back to a derived value, so a caller that sends none still
        gets a usable correlation token.
        """
        return Applied(
            model_name=name,
            control=req.control,
            to_version=candidate,
            from_version=from_version,
            canary_weight=req.canary_weight,
            key=req.idempotency_key or f"{name}:{candidate}:{req.control}",
        )

    def convergence(self, expectation: Applied) -> ConvergenceReport:
        """Answer whether an intent is reflected in live serving state.

        Stateless, so replicas agree and a restart loses nothing. Do not add
        mutation here: no reconcile-on-read, no phase settling, no side effects.
        """
        state = self.adapter.serving_state(expectation.model_name)
        converged, reason = _evaluate(
            state,
            expectation.control,
            expectation.to_version,
            from_version=expectation.from_version,
            canary_weight=expectation.canary_weight,
        )
        logger.info(
            "convergence %s@%s control=%s key=%s -> %s%s",
            expectation.model_name,
            expectation.to_version,
            expectation.control,
            expectation.key,
            converged,
            "" if reason is None else f" ({reason.code}, terminal={reason.terminal})",
        )
        return ConvergenceReport(
            converged=converged,
            reason=reason,
            # The same snapshot the verdict came from: reading again could show
            # a later routing revision that contradicts it.
            observed=self._project(expectation.model_name, state),
            at=datetime.now(UTC),
        )

    def _await_ready(
        self,
        name: str,
        control: str,
        candidate: str,
        *,
        from_version: str | None = None,
        canary_weight: float | None = None,
    ) -> None:
        """Wait briefly for the routing write to show up. Advisory only.

        Records nothing and must never write a failure verdict; it only makes the
        fast case already converged by the caller's first poll. A still-loading
        candidate is left to the caller's budget.
        """
        reason = None
        for attempt in range(settings.readiness_max_checks):
            converged, reason = _evaluate(
                self.adapter.serving_state(name),
                control,
                candidate,
                from_version=from_version,
                canary_weight=canary_weight,
            )
            if converged:
                return
            if attempt < settings.readiness_max_checks - 1:
                sleep(settings.readiness_poll_interval)
        logger.info(
            "apply %s@%s control=%s has not converged inline (%s); the caller's "
            "convergence poll owns it from here",
            name,
            candidate,
            control,
            None if reason is None else reason.code,
        )

    # --------------------------------------------------- serving state (read)
    def serving_state(self, model_name: str | None = None) -> ServingStateResponse:
        """Return the current serving state for ``model_name``.

        Keyed by model name, not an in-process swap id, so a caller can ask "what
        is live?" without having driven an apply, and across a restart.

        ``known_versions`` maps from ``ServingState.loaded_versions``, which is
        UNFILTERED by READY state -- renamed on the wire so callers do not
        misread "loaded" as "ready to serve".
        """
        name = model_name or settings.model_name
        return self._project(name, self.adapter.serving_state(name))

    @staticmethod
    def _project(name: str, state: ServingState) -> ServingStateResponse:
        """Render an already-read ``ServingState`` onto the wire schema.

        Split out so ``convergence`` can keep its verdict and its observation
        from the same read instead of asking the backend twice.
        """
        return ServingStateResponse(
            model_name=name,
            default_version=state.default_version,
            routing=state.routing,
            # Unordered upstream; sorted for a deterministic body.
            serving_versions=sorted(state.serving_versions),
            known_versions=list(state.loaded_versions),
            # The set convergence is judged on: ``known_versions`` is wider (it
            # includes the on-disk listing) and ``ready`` is service-wide.
            ready_versions=list(state.ready_versions),
            ready=state.ready,
            routing_available=state.routing_available,
            loaded_available=state.loaded_available,
        )

    # --------------------------------------------------------------- cleanup
    def _refuse_if_in_use(
        self, name: str, version: str, state: ServingState, inventory: VersionInventory
    ) -> None:
        """Refuse unless the backend's own reads show the version is idle.

        Shared by both destructive verbs so they cannot drift; callers pass the
        reads in because each picks its own. Messages say "retire", not "clean
        up": ``unload`` raises them too. Every clause refuses; none authorises.
        """
        if not state.routing_available:
            # Unknown routing cannot prove this is not the live default.
            raise ModelManagerError(
                code="registry_unreachable",
                message=(
                    f"Refusing to retire {name}@{version}: serving/routing state "
                    "is unavailable; failing closed to avoid retiring a live version"
                ),
                retryable=True,
                http_status=503,
            )
        if version in state.serving_versions:
            raise ModelManagerError(
                code="conflict",
                message=(
                    f"Refusing to retire {name}@{version}: still serving as default/canary/shadow"
                ),
                retryable=False,
                http_status=409,
            )

        # The inventory reports two things routing cannot: a conversion running
        # for this version, and a loaded shadow/canary routing did not surface.
        if not inventory.available:
            raise ModelManagerError(
                code="registry_unreachable",
                message=(
                    f"Refusing to retire {name}@{version}: the per-version inventory is "
                    "unavailable, so it cannot be shown that no build is in flight for it"
                ),
                retryable=True,
                http_status=503,
            )
        row = inventory.version(version)
        if row is not None and row.building:
            raise ModelManagerError(
                code="conflict",
                message=(
                    f"Refusing to retire {name}@{version}: an engine build is still in "
                    "flight for it"
                ),
                retryable=True,
                http_status=409,
            )
        if row is not None and row.routed_as:
            raise ModelManagerError(
                code="conflict",
                message=(f"Refusing to retire {name}@{version}: still routed as {row.routed_as}"),
                retryable=False,
                http_status=409,
            )

        if row is not None and row.streams:
            # A stream can outlive the routing entry that attached it, so
            # ``routed_as`` misses this. Retryable: streams detach on their own.
            raise ModelManagerError(
                code="conflict",
                message=(
                    f"Refusing to retire {name}@{version}: the backend reports "
                    f"{row.streams} stream(s) still attached to it"
                ),
                retryable=True,
                http_status=409,
            )

    def _evict(self, name: str, version: str) -> EvictionAttestation:
        """Prove the version is not in use, evict it, and return the backend's word.

        The ``unload`` verb; ``cleanup`` does not come through here.

        The read is destructive, so a 200 stating no routing policy scores as
        unknown rather than "nothing is routed" -- otherwise the guard passes
        over the live default and evicting it black-holes traffic. The cost is a
        retryable 503 that leaks a GPU slot until the next retire.
        """
        self._refuse_if_in_use(
            name,
            version,
            self.adapter.serving_state(name, destructive=True),
            self.adapter.inventory(name),
        )
        return self.adapter.unload(name, version)

    def unload(self, req: UnloadRequest) -> UnloadResponse:
        """Evict a version from serving memory. Touches no files.

        The directory stays so a rollback still has something to load. No
        ``disk_reclaimed`` in the response: a ``false`` here would read exactly
        like cleanup's degraded "evicted, but the directory stayed", the one
        signal an operator has for a filling volume.
        """
        name = req.model_name or settings.model_name
        self._evict(name, req.version)
        logger.info("unloaded %s@%s; the version directory is untouched", name, req.version)
        return UnloadResponse(version=req.version)

    def cleanup(self, req: CleanupRequest) -> CleanupResponse:
        """Reclaim a retired version's directory. Evicts nothing, ever.

        The only component that may delete a version directory, but not the
        authority on whether that is safe. The delete is a conjunction of terms,
        each of which can only withhold:

        * the caller nominated this version (and may assert a recorded eviction
          -- ``CleanupRequest.evicted_by_caller``, the one term not a read);
        * the backend affirms it, via a ``deletable`` list or a tombstone row;
        * this service's read-only roots -- routing, residency, aliasing, stream
          refcounts, the staging marker, the age floor -- all decline to refuse.

        ``status`` says where the directory stands; ``disk_reclaimed`` says
        whether the caller may stop offering the version. An already-gone
        directory reads removed and reclaimed though this call deleted nothing,
        so a sweep that lost its bookkeeping write converges.

        An in-flight build is not one of the roots: on the unified adapter the
        staging marker covers that window -- ``stage_and_load`` holds
        ``.staging.lock`` across the whole load, engine build included.

        No shared snapshot: one reclaim makes THREE status reads and TWO routing
        reads, pinned by ``TestOneReclaimsReads``. The read that AUTHORISES is
        the oldest and the reads that REFUSE are the newest, taken immediately
        before the ``rmtree``.
        """
        name = req.model_name or settings.model_name

        # Measured: a backend publishes a directory that exists and was never
        # loaded as deletable -- exactly the profile of an in-flight pull.
        floor = max(req.min_age_s or 0.0, settings.reclaim_min_age_s)
        if floor > 0.0:
            age = activation_mod.version_age_seconds(name, req.version)
            if age is None:
                raise ModelManagerError(
                    code="conflict",
                    message=(
                        f"Refusing to clean up {name}@{req.version}: its directory age could "
                        "not be determined, and an age floor was required"
                    ),
                    retryable=False,
                    http_status=409,
                )
            # An absent directory is not a young one; the staging marker, read
            # closer to the ``rmtree``, covers one a pull is about to fill.
            if age != activation_mod.DIRECTORY_ABSENT and age < floor:
                raise ModelManagerError(
                    code="conflict",
                    message=(
                        f"Refusing to clean up {name}@{req.version}: its directory was modified "
                        f"{age:.0f}s ago, under the required floor of {floor:.0f}s"
                    ),
                    retryable=False,
                    http_status=409,
                )

        # The only call site that names a version, which lets an adapter nominate
        # one on a backend that publishes no ``deletable`` list.
        inventory = self.adapter.inventory(
            name, reclaiming=req.version, evicted_by_caller=req.evicted_by_caller
        )

        # Destructive, and last, so the guards score the freshest routing view:
        # a read stating no policy at all otherwise passes them vacuously.
        state = self.adapter.serving_state(name, destructive=True)
        self._refuse_if_in_use(name, req.version, state, inventory)
        self._refuse_if_rooted(name, req.version, state, inventory)

        dry_run = req.dry_run or settings.reclaim_dry_run

        # The goal state already holds. Without this the terms below always
        # refuse -- no backend publishes a directory it cannot see as deletable --
        # and the sweep re-offers the version every tick forever. After the
        # guards, so a directory that vanished while something still names it
        # raises instead of reporting a tidy success.
        if activation_mod.version_directory_absent(name, req.version):
            if dry_run:
                # ``disk_reclaimed`` is what the sweep ACTS on: true writes Mongo.
                logger.info(
                    "dry run: %s@%s has no directory on disk, so there was nothing to "
                    "reclaim; reporting it removed but claiming no reclaim",
                    name,
                    req.version,
                )
                return CleanupResponse(version=req.version, status="removed", disk_reclaimed=False)
            logger.info(
                "reclaimed %s@%s: its directory is already gone, so this call deleted "
                "nothing and there is nothing left to reclaim",
                name,
                req.version,
            )
            return CleanupResponse(version=req.version, status="removed", disk_reclaimed=True)

        if req.version not in inventory.deletable:
            # "No", distinct from the 503's "I could not find out".
            logger.info("kept %s@%s on disk: not reported deletable", name, req.version)
            return CleanupResponse(version=req.version, status="kept", disk_reclaimed=False)

        if dry_run:
            # Every guard ran for real; only the ``rmtree`` is withheld.
            logger.info(
                "dry run: would have reclaimed %s@%s; leaving the directory in place",
                name,
                req.version,
            )
            return CleanupResponse(version=req.version, status="kept", disk_reclaimed=False)

        self.adapter.delete_version(name, req.version)
        logger.info("cleaned up %s@%s: version directory removed", name, req.version)
        return CleanupResponse(version=req.version, status="removed", disk_reclaimed=True)

    def _refuse_if_rooted(
        self, name: str, version: str, state: ServingState, inventory: VersionInventory
    ) -> None:
        """Refuse the reclaim while anything still reaches into this directory.

        Reclaim-only, each for its own reason. Residency cannot join the shared
        stack: evicting a resident version is what ``unload`` is FOR, so every
        eviction would refuse itself. Staging is about a pull writing files, which
        an eviction never does. Aliasing is invisible to the backend's ``409
        MODEL_IN_USE``, which speaks about the version it was ASKED about.
        """
        if activation_mod.staging_in_progress(name, version):
            # The writer's own marker, not an inference from a side effect.
            raise ModelManagerError(
                code="conflict",
                message=(
                    f"Refusing to clean up {name}@{version}: a pull is staging into its directory"
                ),
                retryable=False,
                http_status=409,
            )

        if not state.loaded_available:
            # A separate probe from routing that fails on its own, leaving
            # ``resident_versions`` empty and the root below passing vacuously.
            raise ModelManagerError(
                code="registry_unreachable",
                message=(
                    f"Refusing to clean up {name}@{version}: the backend's loaded-model "
                    "lookup is unavailable, so it cannot be shown that the version is no "
                    "longer resident"
                ),
                retryable=True,
                http_status=503,
            )

        row = inventory.version(version)
        if version in state.resident_versions or (row is not None and row.loaded):
            # ``infer_deletable`` checks residency only on the inference path, so
            # a published ``deletable`` list bypasses it. Either source may know.
            raise ModelManagerError(
                code="conflict",
                message=(
                    f"Refusing to clean up {name}@{version}: the backend still reports it "
                    "resident in serving memory"
                ),
                retryable=True,
                http_status=409,
            )

        for other in inventory.versions:
            if other.version == version:
                continue
            if not other.loaded:
                # Nothing rewrites an evicted row's ``config``/``engine``, so
                # refusing on any row naming the path would keep the directory
                # uncollectable forever. ``loaded`` is the STRICT reading, so an
                # unreadable neighbour still refuses.
                continue
            for path in other.artifact_paths:
                if not _lies_within_version_dir(path, name, version):
                    continue
                # Seen live: ITS@4 came up ``ready`` with its config and engine
                # under ITS/1/, and nothing about ITS@1's own state said so. Not
                # retryable: someone must reload the aliaser.
                raise ModelManagerError(
                    code="conflict",
                    message=(
                        f"Refusing to clean up {name}@{version}: version {other.version} is "
                        f"serving out of this directory ({path})"
                    ),
                    retryable=False,
                    http_status=409,
                )


def _lies_within_version_dir(path: str, name: str, version: str) -> bool:
    """Does ``path`` sit inside the ``name``/``version`` directory?

    By path component, never substring: ITS/1 must not match ITS/11.

    Matched wherever ``{name}/{version}`` appears, not against this process's
    repository root: the serving stack reports the paths IT sees and the two need
    not agree on the mount point. A false positive costs a refused reclaim; a
    false negative costs the weights under a live instance.
    """
    parts = PurePosixPath(path).parts
    return any(
        parts[index] == name and parts[index + 1] == version for index in range(len(parts) - 1)
    )


def _candidate_servable(control: str, candidate: str, state: ServingState) -> bool:
    """Return True when *this* version is actually servable (or gone, for unload).

    Gated on the PER-VERSION state from ``GET /v1/models``, never the
    service-wide ``GET /v1/ready``: AOI Inference drops that to 503 for reasons
    unrelated to the candidate (notably ``engine_build_in_progress``), so a clean
    swap would never be marked succeeded.

    ``unload`` is the mirror image: demanding READY would never converge.
    """
    if control == "unload":
        return True
    if not state.loaded_available:
        # Lookup failed: "unknown", not "serving". Never succeed on a failed probe.
        return False
    return candidate in state.ready_versions


def _control_reflected(
    control: str,
    candidate: str,
    state: ServingState,
    *,
    from_version: str | None = None,
    canary_weight: float | None = None,
) -> bool:
    """Return True when the normalized state reflects the *requested* control.

    Validated against the full intended routing shape, not just the presence of
    ``candidate``: otherwise a 5% canary would be marked succeeded by an unrelated
    50% rollout, or a shadow against the wrong primary would pass.
    """
    # An unreadable routing view is not a converged one. ``full_switch`` is
    # otherwise one empty list away from the opposite: ``all([])`` is True.
    # ``unload`` is exempt -- it is judged against the resident set.
    if control != "unload" and not state.routing_available:
        return False

    if control == "full_switch":
        return state.default_version == candidate and all(
            e.role == "default" for e in state.routing
        )
    if control == "unload":
        # The RESIDENT set, not the known set: an unloaded version keeps its
        # directory and so stays in ``loaded_versions``. An unavailable
        # list_models must not be read as "not loaded".
        return state.loaded_available and candidate not in state.resident_versions

    # shadow/canary leave the previous version serving as the default; the primary
    # that ``apply_control`` writes is ``from_version or candidate``.
    if control in ("shadow", "canary"):
        expected_primary = from_version or candidate
        if state.default_version != expected_primary:
            return False
        if control == "shadow":
            return any(e.role == "shadow" and e.version == candidate for e in state.routing)
        for entry in state.routing:
            if entry.role == "canary" and entry.version == candidate:
                return canary_weight is None or abs(entry.weight - canary_weight) <= 1e-6
        return False

    return False


def _evaluate(
    state: ServingState,
    control: str,
    candidate: str,
    *,
    from_version: str | None = None,
    canary_weight: float | None = None,
) -> tuple[bool, ConvergenceReason | None]:
    """Judge an intent against observed state, and name the reason when it fails.

    The verdict comes from ``_control_reflected`` and ``_candidate_servable``;
    this only adds a name by walking the same conditions again, so keep the two
    in agreement. ``terminal`` means the expectation can no longer come true, not
    that a budget elapsed -- the caller owns that.
    """
    reflected = _control_reflected(
        control, candidate, state, from_version=from_version, canary_weight=canary_weight
    )
    if reflected and _candidate_servable(control, candidate, state):
        return True, None

    def because(code: str, message: str, *, terminal: bool = False, **detail):
        return ConvergenceReason(
            code=code, message=message, terminal=terminal, detail=detail or None
        )

    if control != "unload" and not state.routing_available:
        return False, because(
            "routing_unreadable",
            f"cannot read routing; {candidate} cannot be shown to have landed",
        )

    if not reflected:
        if control == "full_switch":
            observed = state.default_version
            if observed != candidate:
                # A third version holding the default means another apply landed
                # on top of this one; waiting cannot recover that.
                #
                # Truthiness on BOTH, not ``is not None``: a routing view with no
                # primary is an unsettled read, not a third version, and both ways
                # there keep ``routing_available`` True -- a unified policy with no
                # ``model`` key, and an AOI default of ``''``. Worst gate to be
                # wrong at: a false terminal here enqueues an auto-rollback.
                if from_version and observed and observed not in (candidate, from_version):
                    return False, because(
                        "primary_moved",
                        f"expected {candidate} or {from_version} to be default, but "
                        f"{observed} is; another apply landed and this expectation is stale",
                        terminal=True,
                        expected=candidate,
                        observed=observed,
                    )
                return False, because(
                    "default_version_mismatch",
                    f"{candidate} is not default yet; {observed} still is",
                    expected=candidate,
                    observed=observed,
                )
            extra = [e.role for e in state.routing if e.role != "default"]
            return False, because(
                "routing_not_exclusive",
                f"{candidate} is default but {len(extra)} non-default "
                f"{'entry' if len(extra) == 1 else 'entries'} remain: {extra}",
                roles=extra,
            )

        if control == "unload":
            if not state.loaded_available:
                return False, because(
                    "loaded_unreadable",
                    f"cannot read the loaded set; cannot prove {candidate} is gone",
                )
            return False, because(
                "still_resident", f"{candidate} is still resident in serving memory"
            )

        # Terminality needs POSITIVE evidence that a different, real primary
        # holds. Two absences are not that: no ``from_version`` means this
        # expectation asserted nothing about the primary (a day-0 deploy), and no
        # observed default is an unsettled read -- the unified state layer yields
        # ``default_version=None`` from a policy carrying only a shadow slot.
        # DFW STOPS on terminal, so either misread fails the promotion at ~0s.
        if from_version and state.default_version and state.default_version != from_version:
            return False, because(
                "primary_moved",
                f"expected {from_version} to remain default, but "
                f"{state.default_version} is; this expectation is stale",
                terminal=True,
                expected=from_version,
                observed=state.default_version,
            )
        if state.default_version != (from_version or candidate):
            # Not the shadow/canary checks: they name the ROUTING ARM, and the
            # canary branch formats a ``None`` weight -- TypeError on 48 vectors.
            return False, because(
                "default_version_mismatch",
                (
                    f"no default version is routed yet; the {control} write for "
                    f"{candidate} has not been reflected"
                    if not state.default_version
                    else (
                        f"{state.default_version} is default; this expectation named no "
                        f"prior default, so it cannot be shown to have moved"
                    )
                ),
                expected=from_version or candidate,
                observed=state.default_version,
            )
        if control == "shadow":
            return False, because("shadow_absent", f"no shadow entry for {candidate} in routing")
        entry = next(
            (e for e in state.routing if e.role == "canary" and e.version == candidate), None
        )
        if entry is None:
            return False, because("canary_absent", f"no canary entry for {candidate} in routing")
        return False, because(
            "canary_weight_mismatch",
            f"canary for {candidate} is routed at {entry.weight:.3f}, expected {canary_weight:.3f}",
            expected=canary_weight,
            observed=entry.weight,
        )

    # Reflected, but the candidate is not servable.
    if not state.loaded_available:
        return False, because(
            "loaded_unreadable",
            f"cannot read the loaded set; cannot prove {candidate} is servable",
        )
    return False, because(
        "candidate_not_ready",
        f"routing for {candidate} landed; the version is not READY yet",
    )


# Module-level singleton used by the API layer.
model_manager = ModelManager()

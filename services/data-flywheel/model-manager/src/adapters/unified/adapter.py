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

"""Serving adapter for the Unified Model Control API.

ONE adapter for every backend that speaks the contract, with no per-backend
capability profile. Load completion is reported per request on the wire (``200``
finished, any other 2xx accepted and warming), and which routing stages a rollout
uses is DFW's ``promotion_policy.deployment_stages`` -- and is a property of a
DEPLOYMENT anyway, since the shipped AOI service gates shadow and canary on its
own configuration. A backend that will not accept a stage says so with
``400 FIELD_UNSUPPORTED`` or ``409 MODE_DISABLED``.

The adapter therefore sends only the contract's portable core.

Verb -> unified mapping::

    stage(version)      -> activation.stage_and_load -> POST /api/v1/model/load
    apply(shadow)       -> POST /api/v1/model/routing {default: {model, shadow}}
    apply(canary, pct)  -> POST /api/v1/model/routing {default: {model, canary, canary_percent}}
    apply(full_switch)  -> POST /api/v1/model/routing
                           {default: {model, shadow: null if a non-default role is live}}
    apply(unload)       -> refused; see ``unload``
    serving_state()     -> GET  /api/v1/model/status
    inventory()         -> GET  /api/v1/model/status + repo listing + build-status
    delete_version()    -> activation.delete

``full_switch`` sends ``default.model`` alone, which on a REPLACING backend
clears any shadow or canary as a side effect (deft-aoi-inference v0.0.1,
verified). The spec is ambiguous about which habit applies, and RTVI-CV MERGES
instead, so ``_surviving_roles`` adds an explicit ``shadow: null`` whenever live
state shows a non-default role occupied. Never ``canary: null`` (not nullable)
nor ``canary_percent: null`` (constrained ``0 < n < 100``) -- ``shadow: null`` is
the only clear that is expressible.

Clearing the shadow is not enough on RTVI-CV once streams are attached: a
default-only write does not re-bind an already-bound stream, which kills the app
on promotion. ``settings.unified_stream_routes`` names the streams in the same
write; see that setting for the mechanism and for why it is off by default.
"""

import logging
import re
from typing import NamedTuple

from src.adapters.base import (
    DeleteEvidence,
    EvictionAttestation,
    ServingAdapter,
    ServingState,
    VersionInventory,
)
from src.adapters.unified import builder
from src.adapters.unified import inventory as inventory_mod
from src.adapters.unified import state as state_mod
from src.adapters.unified.activation import UnifiedActivation
from src.adapters.unified.client import UnifiedModelClient
from src.config import settings
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)


class _RoutingRead(NamedTuple):
    """One source's answer: the routing policy, plus any stream-pinned versions.

    ``view`` is the flat ``model-status.routing`` shape, whatever it was read
    from. An EMPTY view is still an answer -- "nothing is routed" -- unlike
    ``None``, which means no source could say at all.

    ``pinned`` is separate because a per-stream route carries no role and no
    weight, so it has no place among the routing ENTRIES -- but it must still
    reach ``serving_versions``, which is what cleanup consults before deleting.
    """

    view: dict
    pinned: frozenset[str] = frozenset()


# Mirrors the serving backend's own model-name rule, so a name is refused here
# for the same reason it would be refused there -- before staging spends a
# multi-hundred-megabyte download finding out.
_BACKEND_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")

# Versions name integer directories in the shared model repository -- an
# MM-owned invariant, since MM creates them. Leading zeros are refused because
# "007" and "7" are distinct directories resolving to the same version. Stricter
# than the backend's ``str(version).isdigit()``, which accepts non-ASCII digits.
_VERSION_RE = re.compile(r"[1-9][0-9]*")


class UnifiedServingAdapter(ServingAdapter):
    """``ServingAdapter`` speaking the Unified Model Control API."""

    def __init__(
        self,
        client: UnifiedModelClient | None = None,
        activation: UnifiedActivation | None = None,
    ):
        self.client = client or UnifiedModelClient()
        self.activation = activation or UnifiedActivation(self.client)

    # ----------------------------------------------------------------- stage
    def stage(
        self,
        name: str,
        version: str,
        artifact_uri: str,
        sha256: str,
        *,
        spec_uri: str | None = None,
        threshold: float | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        """Stage a version into the shared repo and load it."""
        self._reject_unservable_target(name, version)
        return self.activation.stage_and_load(
            name,
            version,
            artifact_uri,
            sha256,
            spec_uri=spec_uri,
            threshold=threshold,
            idempotency_key=idempotency_key,
        )

    # --------------------------------------------------------- apply_control
    def apply_control(
        self,
        *,
        control: str,
        name: str,
        candidate: str,
        from_version: str | None,
        canary_weight: float | None,
        idempotency_key: str | None = None,
    ) -> None:
        """Map a Model Promotion control verb onto a unified routing write.

        ``idempotency_key`` is accepted and dropped — the unified envelopes
        define no such field and reject unknown ones.
        """
        if control == "shadow":
            self.client.put_routing(
                {
                    "model": _ref(name, self._primary(control, name, candidate, from_version)),
                    "shadow": _ref(name, candidate),
                }
            )
        elif control == "canary":
            self.client.put_routing(
                {
                    "model": _ref(name, self._primary(control, name, candidate, from_version)),
                    "canary": _ref(name, candidate),
                    "canary_percent": _canary_percent(name, candidate, canary_weight),
                }
            )
        elif control == "full_switch":
            self._full_switch(name, candidate)
        elif control == "unload":
            self.unload(name, candidate)
        else:  # pragma: no cover - guarded by the Control literal
            raise ModelManagerError(
                code="bad_request",
                message=f"Unknown control {control!r}",
                retryable=False,
                http_status=400,
            )

    def _full_switch(self, name: str, candidate: str) -> None:
        """Make ``candidate`` the sole default, on a backend of either habit.

        A REPLACING backend drops any shadow or canary when only the primary is
        named; a MERGING one keeps the slot, so ``_surviving_roles`` names it.

        The 409 retry covers the race where a shadow appears between that read and
        the write. Only a TERMINAL 409 earns it: ``MODEL_NOT_LOADED`` and
        ``UPDATE_IN_FLIGHT`` are retryable and unrelated to roles, and a second
        write would turn a transient error terminal and take an auto-rollback with
        it. Scoped on ``retryable``, not the code, which is folded into the
        message and unconstrained by the spec.
        """
        policy = {"model": _ref(name, candidate)}
        stranded = self._surviving_roles(name, candidate)
        try:
            self.client.put_routing({**policy, **stranded} if stranded else policy)
        except ModelManagerError as exc:
            if exc.http_status != 409 or exc.retryable:
                raise
            logger.info(
                "full_switch to %s@%s refused as a role conflict; retrying with the "
                "shadow cleared explicitly",
                name,
                candidate,
            )
            self.client.put_routing({**policy, "shadow": None})

    def _surviving_roles(self, name: str, candidate: str) -> dict[str, None]:
        """Roles a merging backend would strand, named so the write clears them.

        A merging backend carries over any slot the write does not name, so a
        full switch to a third version settles as ``{default: 9, shadow: 10}`` --
        not exclusive, never converges. The 409 retry does not cover it: the
        backend only refuses when the target IS the shadow.

        Read from live state rather than sent unconditionally, so a backend with
        nothing in these roles never sees the field. Fails open to the bare policy
        plus the 409 retry.
        """
        try:
            live = self.serving_state(name)
        except ModelManagerError:
            logger.warning(
                "could not read routing before full_switch to %s@%s; sending the bare "
                "policy and relying on the conflict retry",
                name,
                candidate,
            )
            return {}
        if not live.routing_available:
            return {}
        # Any non-default role counts, even one held by the candidate itself: a
        # merging backend may accept ``{default: 2, shadow: 2}``.
        #
        # ``shadow: null`` is sent for an occupied CANARY too, because
        # ``canary: null`` is not expressible -- ``canary_percent`` is validated
        # whenever the canary key is present. That rests on a merging backend
        # carrying untouched slots over only when the write names NEITHER role.
        #
        # UNVERIFIED live for the canary case: RTVI-CV ships
        # ``deployment_stages.canary: false``, so no canary rung has run on a
        # merging backend. ``TestRoutingSemanticsGap`` pins the adapter half; the
        # backend half is the fake's assumption, not evidence.
        occupied = any(entry.role in ("shadow", "canary") for entry in live.routing)
        return {"shadow": None} if occupied else {}

    # ------------------------------------------------------- routing source
    #
    # Three places carry the routing policy, and which of them a backend fills
    # is not knowable up front:
    #
    #   1. ``model-status.routing``      flat, HAS ``mode``. Free. What AOI fills.
    #   2. ``GET /api/v1/model/routing`` authoritative. Costs a round trip.
    #   3. ``model-status.default``      the same policy object, no ``routes``.
    #
    # 2 and 3 carry the spec's ``DefaultRoutingPolicy``, whose keys match the flat
    # view apart from the absent ``mode``, which ``state.as_status_view``
    # synthesizes -- so all three converge on ONE shape.
    #
    # Cheapest-first, not most-authoritative-first: the three are the same policy
    # from the same backend, so preferring 2 would only put a round trip in front
    # of every AOI status read.
    _SOURCE_STATUS_ROUTING = "model-status.routing"
    _SOURCE_ROUTING_ENDPOINT = "GET /api/v1/model/routing"
    _SOURCE_STATUS_DEFAULT = "model-status.default"

    def _routing_view(
        self, status: dict, name: str, *, force_endpoint: bool = False
    ) -> _RoutingRead | None:
        """Resolve the routing policy, or ``None`` when no source can say.

        ``None`` means "could not read routing" and every consumer fails closed
        on it -- NOT the same as an EMPTY view, which means "read it, nothing is
        routed" and permits cleanup to proceed.

        An empty answer does not end the search: a backend can report an empty
        policy in one place while the one it enforces sits in another, and
        stopping early would empty ``serving_versions`` for a live version.
        Empties are held in reserve and returned only if nothing substantive
        turns up.

        NOT memoized. Only step 2 does IO, so remembering the winner saves
        nothing measurable, while one transient failure of step 2 would pin the
        adapter to step 3 for the life of the process -- and step 3 cannot see
        stream pins, silently, on any backend that has none.
        """
        readers = (
            (self._SOURCE_STATUS_ROUTING, lambda: self._from_status_routing(status)),
            (
                self._SOURCE_ROUTING_ENDPOINT,
                lambda: self._from_routing_endpoint(name, destructive=force_endpoint),
            ),
            (self._SOURCE_STATUS_DEFAULT, lambda: self._from_status_default(status)),
        )

        # Pins are a fact about the FETCH, not the winning view, so they survive
        # a source losing its bid -- but not a source never being asked. The loop
        # stops at the first substantive view and only step 2 sees stream routes,
        # so a backend filling ``model-status.routing`` substantively AND serving
        # ``routes[]`` would drop a stream-pinned version from
        # ``serving_versions``. That intersection is empty today, and closing it
        # would cost a round trip on every AOI status read. Pinned by a test.
        #
        # ``force_endpoint`` closes it on the one path worth the cost: the reclaim
        # read that may authorise an rmtree. There step 2 is consulted even when
        # step 1 answered, and also WINS -- once the round trip is paid, a
        # possibly stale flat view buys nothing.
        pinned: set[str] = set()
        empty_from: str | None = None
        winner: tuple[dict, str] | None = None
        endpoint_answered = False

        for source, reader in readers:
            read = reader()
            if read is None:
                continue
            if source == self._SOURCE_ROUTING_ENDPOINT:
                endpoint_answered = True
            pinned |= set(read.pinned)
            if not read.view:
                # First to say "nothing is routed" is the one reported, if no
                # source turns out to hold a policy.
                empty_from = empty_from or source
                continue
            # The ``force_endpoint and`` qualifier is not what keeps the unforced
            # path cheap -- the break below already stops the loop before step 2
            # is read. Defence in depth against that break being relaxed later.
            # No test pins it; it is deliberate, not guarded.
            if winner is None or (force_endpoint and source == self._SOURCE_ROUTING_ENDPOINT):
                winner = (read.view, source)
            if not (force_endpoint and source != self._SOURCE_ROUTING_ENDPOINT):
                break

        if force_endpoint and not endpoint_answered:
            # The endpoint is the only source that sees stream pins, so another
            # source's view would read as available with ``pinned`` incomplete.
            logger.warning(
                "the routing endpoint did not answer on a destructive read; reporting routing "
                "as unknown rather than trusting a view that cannot see stream pins"
            )
            return None

        if winner is not None:
            logger.debug("routing read from %s", winner[1])
            return _RoutingRead(view=winner[0], pinned=frozenset(pinned))
        if empty_from is not None:
            logger.debug("routing read from %s; nothing is routed", empty_from)
            return _RoutingRead(view={}, pinned=frozenset(pinned))
        return None

    def _from_status_routing(self, status: dict) -> _RoutingRead | None:
        """Step 1: the flat view already carried by model-status.

        An EMPTY dict counts as an ANSWER -- just not as a policy. A backend that
        fills this key and reports ``{}`` is saying "nothing is routed"; refusing
        it outright would make a quiet AOI backend's routing unreadable.
        ``_routing_view`` holds it in reserve rather than acting on it.
        """
        routing = status.get("routing")
        if not isinstance(routing, dict):
            return None
        return _RoutingRead(view=routing)

    def _from_routing_endpoint(
        self, name: str, *, destructive: bool = False
    ) -> _RoutingRead | None:
        """Step 2: the authoritative view, one round trip.

        A failure here is NOT terminal: step 3 reads a field of the status already
        in hand, and the endpoint is absent on any backend that predates it.

        ``get_routing`` returns ``None`` only when the response carried no routing
        object at all -- a 200 with an error page -- which is not an answer. A
        routing object that is EMPTY, or carries no ``default``, IS one for a
        SWAP. Stream routes travel back either way, being a fact about the fetch.

        ``destructive`` splits that reading in the refusing direction only. On the
        image whose model-filtered status carries no ``routing`` key this endpoint
        is the SOLE source, so a 200 merely shaped like an answer would empty
        ``serving_versions`` and every routing guard would pass vacuously over a
        live version. There a missing policy slot reads as unknown.
        """
        try:
            routing = self.client.get_routing()
        except ModelManagerError as exc:
            logger.info("routing endpoint did not answer (%s); trying model-status", exc.code)
            return None
        if routing is None:
            return None
        policy = routing.get("default")
        if destructive and not isinstance(policy, dict):
            logger.warning(
                "the routing endpoint answered with no default policy on a destructive read; "
                "reporting routing as unknown rather than as nothing routed"
            )
            return None
        view = state_mod.as_status_view(policy) if isinstance(policy, dict) else {}
        # Per-stream routes are visible HERE and nowhere else -- the only thing
        # stopping cleanup deleting a version that serves a named stream without
        # appearing in the default policy at all.
        return _RoutingRead(view=view, pinned=frozenset(state_mod.pinned_versions(routing, name)))

    def _from_status_default(self, status: dict) -> _RoutingRead | None:
        """Step 3: the policy as carried on model-status itself.

        Degraded on purpose: the default slot only. Convergence loses nothing,
        since every control writes the default policy, but per-stream routes are
        invisible here, so a version referenced ONLY by one is unseen.
        """
        policy = status.get("default")
        if not isinstance(policy, dict) or not policy:
            return None
        return _RoutingRead(view=state_mod.as_status_view(policy))

    # -------------------------------------------------------- serving_state
    def serving_state(self, name: str, *, destructive: bool = False) -> ServingState:
        """Compose the normalized serving state from one status read.

        One ``GET /api/v1/model/status`` carries both routing and per-version
        state, so the two availability flags move together: a failed read makes
        both unknown, which is stricter than reporting either as empty.
        """
        return self._serving_state_with_status(name, destructive=destructive)[0]

    def _serving_state_with_status(
        self, name: str, *, destructive: bool = False
    ) -> tuple[ServingState, dict]:
        """``serving_state`` plus the raw status it came from.

        Handed back so a caller needing a fact the normalized state drops --
        whether the payload identified the model at all -- avoids a second read.
        """
        try:
            status = self.client.get_status(name)
        except ModelManagerError:
            logger.warning("unified status unavailable; serving state is unknown")
            return ServingState(routing_available=False, loaded_available=False), {}

        rows = state_mod.version_rows(status, name)
        # Destructive reads only: there a row that does not literally say
        # ``unloaded`` counts as resident, because cleanup's residency root is the
        # last thing before an rmtree. The swap path wants the lenient default.
        known, ready, resident = state_mod.version_sets(rows, strict_residency=destructive)

        # The status snapshot is not a complete version floor, so the repo
        # listing closes it. Two ways a directory goes missing from it: a version
        # staged but never loaded has no row, and the contract lets an
        # implementation "omit unloaded versions instead of retaining entries with
        # state: unloaded" while a dry run or age floor withholds the rmtree.
        #
        # A shrinking floor is undetectable -- ``loaded_available`` stays True, so
        # DFW mints confidently onto an occupied directory whose spec.yaml and
        # metadata.json then survive under new weights.
        #
        # Widening only, and only here: leaking this into ``ready``/``resident``
        # would make a directory count as servable or block an unload forever.
        known = [*known, *sorted(inventory_mod.on_disk_versions(name).difference(known))]

        # Absence from every source is "unknown", not "nothing is routed":
        # claiming availability off the HTTP status alone would defeat cleanup's
        # fail-closed guard.
        read = self._routing_view(status, name, force_endpoint=destructive)
        routing_known = read is not None
        routing = read.view if read is not None else {}
        pinned = read.pinned if read is not None else frozenset()
        if routing_known and state_mod.routing_names_other_model(routing, name):
            logger.warning(
                "unified routing policy names a model other than %r; reporting routing as "
                "unknown so convergence and cleanup fail closed",
                name,
            )
            routing_known, routing, pinned = False, {}, frozenset()

        default_version, entries = state_mod.compose_routing(routing, name)
        return ServingState(
            default_version=default_version,
            routing=entries,
            serving_versions=state_mod.serving_versions(routing, name) | set(pinned),
            loaded_versions=known,
            # Derived, since the unified API has no service-wide readiness
            # endpoint. Observability only: convergence uses ``ready_versions``.
            ready=bool(default_version and default_version in ready),
            routing_available=routing_known,
            loaded_available=True,
            ready_versions=ready,
            resident_versions=resident,
        ), status

    # ---------------------------------------------------------------- unload
    def unload(self, name: str, version: str) -> EvictionAttestation:
        """Evict a version. Allowed by default; both shipped backends are per-version.

        Refused by default until an early ``deft-aoi-inference`` defect was fixed:
        a per-version request was validated, echoed in a 200, and took the routed
        version down with it (reproduced with an idle v1 and v2 the sole routed
        primary, leaving v2 ``default_for:["model"]`` while ``state:"unloaded"``).

        Measured at the wire against both backends this adapter is used with:

        * ``deft-aoi-inference`` -- fixed by ``0fce90f3`` (2026-08-05), which pins
          Triton's ``version_policy`` to ``specific:[primary, candidate]`` and
          reloads inside a quiesce barrier. On ``:v0.0.1``, retiring an idle
          version left the routed default ``ready`` under single, shadow and
          canary.
        * RTVI-CV ``metropolis-perception:3.3.0-...-172-with-api-fixes`` -- unload
          is per-version, and it *independently* refuses to evict a routed version
          at all (``409 MODEL_IN_USE``). Single and shadow verified; no canary in
          that profile.

        The setting remains, inverted, because granularity is a property of the
        BUILD, not the contract. Point this at an older image and set it ``false``.

        Gates memory only: ``cleanup`` no longer unloads, so a deployment that
        sets this false still reclaims directories.
        """
        if not settings.unified_allow_unload:
            raise ModelManagerError(
                code="conflict",
                message=(
                    f"refusing to unload {name}@{version}: unload is disabled for this "
                    "deployment (MODEL_MANAGER_UNIFIED_ALLOW_UNLOAD=false). Both shipped "
                    "backends unload per version; set it true unless this one is an older "
                    "build that evicts the whole model."
                ),
                retryable=False,
                http_status=409,
            )
        code, body = self.client.unload(name, version)
        # A 200 means the eviction ran, and so does any 2xx whose body reports the
        # state as unloaded -- RTVI-CV always answers ``202 Accepted`` while the
        # body says ``'ITS@3' unloaded``. Anything else is acceptance only.
        state = body.get("state") if isinstance(body, dict) else None
        affirmed = code == 200 or (
            isinstance(state, str) and state.lower() == state_mod.STATE_UNLOADED
        )
        return EvictionAttestation(version=version, evicted_affirmed=affirmed)

    # -------------------------------------------------------------- inventory
    def inventory(
        self, name: str, *, reclaiming: str | None = None, evicted_by_caller: bool = False
    ) -> VersionInventory:
        """Return the per-version inventory, flagged unavailable when unreadable.

        Never raises: the caller is about to delete something and must be able to
        tell "nothing is deletable" from "the backend did not answer".

        ``reclaiming`` is supplied only by ``cleanup``; without it nothing
        unpublished can be nominated, so an ordinary read authorises no delete.
        """
        try:
            status = self.client.get_status(name)
        except ModelManagerError:
            logger.warning("unified status unavailable; per-version inventory is unknown")
            return VersionInventory(model_name=name, available=False)
        return inventory_mod.compose(
            name,
            status,
            on_disk=inventory_mod.on_disk_versions(name),
            evidence=self._delete_evidence(name, reclaiming, evicted_by_caller),
        )

    def _delete_evidence(
        self, name: str, reclaiming: str | None, evicted_by_caller: bool
    ) -> DeleteEvidence | None:
        """A second, INDEPENDENT read of the backend, or None for an ordinary read.

        One extra status read, one routing read and -- with a builder configured --
        one build-status read, paid once per cleanup on the reclaim path only.

        Every probe is READ-ONLY, which is what lets a reclaim happen with no
        unload in front of it: routing is re-read as late as possible before the
        ``rmtree``, through the forced endpoint that alone sees per-stream pins.
        """
        if reclaiming is None:
            return None
        state, status = self._serving_state_with_status(name, destructive=True)
        return DeleteEvidence(
            model_name=name,
            version=reclaiming,
            evicted_by_caller=evicted_by_caller,
            model_entry_present=state_mod.model_entry_present(status, name),
            routing_readable=state.routing_available,
            serving_versions=frozenset(state.serving_versions),
            resident_versions=frozenset(state.resident_versions),
            build_settled=builder.build_settled(name, reclaiming),
        )

    # --------------------------------------------------------- delete_version
    def delete_version(self, name: str, version: str) -> None:
        """Remove a version's directory from the shared model repository."""
        self.activation.delete(name, version)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def _primary(control: str, name: str, candidate: str, from_version: str | None) -> str:
        """Return the primary version for a partial-traffic write, refusing a guess.

        ``from_version`` is ``None`` both for a service with no primary and after
        a failed status read. Falling back to the candidate turns a ``shadow``
        into ``{model: v2, shadow: v2}`` -- the untested candidate serving 100% of
        traffic -- and convergence re-derives the primary with the same fallback,
        so the swap records ``succeeded`` while DFW believes it is shadowing.

        A shadow or canary is relative to something already serving, so with
        nothing to be relative to there is no correct write. Retryable: the usual
        cause is a transient status read.
        """
        if from_version:
            return from_version
        raise ModelManagerError(
            code="registry_unreachable",
            message=(
                f"refusing to apply {control} for {name}@{candidate}: the current default "
                "version is unknown, so this would install the candidate as the live primary "
                "instead of alongside it"
            ),
            retryable=True,
            http_status=503,
        )

    def _reject_unservable_target(self, name: str, version: str) -> None:
        """Refuse a name/version the backend would reject, before staging costs.

        Not the path-safety check: ``_version_dir`` proves a name cannot escape
        the model repository, and neither guard subsumes the other. This asks the
        weaker question of whether the backend will accept a safe value.
        """
        if not _BACKEND_NAME_RE.fullmatch(name):
            raise ModelManagerError(
                code="bad_request",
                message=(
                    f"model_name {name!r} is not usable by the serving backend: it must be "
                    "1-128 characters of letters, digits, '.', '_' or '-', and must start "
                    "with a letter or digit"
                ),
                retryable=False,
                http_status=400,
            )
        if not _VERSION_RE.fullmatch(version):
            raise ModelManagerError(
                code="bad_request",
                message=(
                    f"version {version!r} is not usable: it must be a positive integer with no "
                    "leading zeros, since it names a version directory in the shared model "
                    "repository"
                ),
                retryable=False,
                http_status=400,
            )


def _ref(name: str, version: str) -> dict:
    """Build a unified ModelRef for a routing write."""
    return {"name": name, "version": version}


def _canary_percent(name: str, candidate: str, canary_weight: float | None) -> float:
    """Convert a canary fraction to the wire percentage, rejecting the endpoints.

    The wire field is exclusive, ``0 < p < 100``, so the degenerate fractions
    have no representation: 0 is ``shadow`` and 1 is ``full_switch``.
    """
    if canary_weight is None or not (0 < canary_weight < 1):
        raise ModelManagerError(
            code="bad_request",
            message=(
                f"canary control for {name}@{candidate} needs a canary_weight strictly "
                f"between 0 and 1 (got {canary_weight!r}); use shadow for no traffic and "
                "full_switch for all of it"
            ),
            retryable=False,
            http_status=400,
        )
    # The fraction arrives from a percent/100 division upstream, and 0.05 * 100
    # is 5.000000000000001 in binary floating point.
    return round(canary_weight * 100, 6)

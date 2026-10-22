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

"""AOI serving adapter: maps Model Promotion verbs onto the AOI stack.

Verb -> AOI mapping (Explicit mode):

    stage(version)      -> activation.stage_and_load -> AOI Inference load
    apply(shadow)       -> aoi_client.put_routing(mode="shadow", ...)
    apply(canary, pct)  -> aoi_client.put_routing(mode="canary", ...)
    apply(full_switch)  -> aoi_client.put_routing(mode="single", primary=candidate)
    apply(unload)       -> AOI Inference unload
    unload(version)     -> AOI Inference unload
    serving_state()     -> aoi_client.get_routing() + aoi_inference.list_models() + ready
    inventory()         -> aoi_inference.list_versions()
    delete_version()    -> activation.delete

The adapter normalizes the AOI routing/registry payloads into a client-agnostic
``ServingState``/``VersionInventory`` so ``ModelManager`` never sees an AOI-shaped
dict.

Routing refs are read tolerantly and written canonically: the canonical ref is
``{model_name, model_version}``, the older ``{name, version}`` is accepted on
input. A reader that understood only the legacy keys would resolve every version
to None against a canonical response, and an empty routing view reads as "nothing
is serving" -- so a swap waits on a convergence it can never observe and cleanup
believes a live version is idle.
"""

import logging
import re

from src.adapters import activation as activation_mod
from src.adapters.aoi.activation import ActivationMode, get_activation
from src.adapters.aoi.client import AOIClientClient
from src.adapters.aoi.inference import AOIInferenceClient
from src.adapters.base import (
    EvictionAttestation,
    ServingAdapter,
    ServingState,
    VersionInventory,
    VersionState,
    deletable_with_rows,
    usable_deletable,
)
from src.api.schemas import RoutingEntry
from src.clients.http import list_field
from src.core.errors import ModelManagerError

logger = logging.getLogger(__name__)

# Triton reports an evicted version as UNAVAILABLE: it keeps its directory (and so
# stays in the version floor) but is no longer held in memory, which is exactly
# what makes an unload observably complete.
_EVICTED_STATE = "UNAVAILABLE"

# Copied from the AOI service's own ``_MODEL_NAME_RE`` (app.py, v0.0.0) so a name is
# refused here for the same reason it would be refused there.
_BACKEND_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
# ASCII digits only, and narrower than the server's ``isdigit()`` on purpose; see
# ``_reject_unservable_target``.
_BACKEND_VERSION_RE = re.compile(r"[0-9]+")


class AOIServingAdapter(ServingAdapter):
    """``ServingAdapter`` backed by AOI Inference + AOI Client."""

    def __init__(
        self,
        activation: ActivationMode | None = None,
        aoi_client: AOIClientClient | None = None,
        aoi_inference: AOIInferenceClient | None = None,
    ):
        self.inference = aoi_inference or AOIInferenceClient()
        self.activation = activation or get_activation(self.inference)
        self.aoi_client = aoi_client or AOIClientClient()

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
        """Stage a version (Explicit mode performs the AOI Inference load).

        Validated against the backend's naming rules first: everything after this
        line downloads and verifies hundreds of megabytes before the load POST
        would be the first to notice the name was never usable.
        """
        _reject_unservable_target(name, version)
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
        """Map a Model Promotion control verb onto AOI Client / Inference calls.

        Every routing target must already be READY: the routing PUT is rejected
        outright otherwise, which is why the load and its engine build complete
        during ``stage`` rather than being left for this call to wait out.
        """
        if control == "shadow":
            self._put_routing(
                {
                    "mode": "shadow",
                    "primary_model": _ref(name, from_version or candidate),
                    "shadow_model": _ref(name, candidate),
                },
                idempotency_key,
            )
        elif control == "canary":
            self._put_routing(
                {
                    "mode": "canary",
                    "primary_model": _ref(name, from_version or candidate),
                    "canary_model": _ref(name, candidate),
                    "canary_percent": _canary_percent(name, candidate, canary_weight),
                },
                idempotency_key,
            )
        elif control == "full_switch":
            self._put_routing(
                {"mode": "single", "primary_model": _ref(name, candidate)}, idempotency_key
            )
        elif control == "unload":
            self.inference.unload(name, candidate, idempotency_key=idempotency_key)
        else:  # pragma: no cover - guarded by the Control literal
            raise ModelManagerError(
                code="bad_request",
                message=f"Unknown control {control!r}",
                retryable=False,
                http_status=400,
            )

    # -------------------------------------------------------------- inventory
    def inventory(
        self, name: str, *, reclaiming: str | None = None, evicted_by_caller: bool = False
    ) -> VersionInventory:
        """Return the per-version inventory, flagged unavailable when unreadable.

        ``reclaiming`` and ``evicted_by_caller`` are accepted and IGNORED: this
        backend publishes its own ``deletable`` list, and second-guessing it
        locally reintroduces the races it exists to close.

        A failed lookup is reported rather than raised: the caller is about to
        delete something and must tell "nothing is deletable" from "no answer".

        A malformed ``versions`` or ``deletable`` field reports UNAVAILABLE, not
        empty. ``cleanup``'s guards are written "if the row says it is building /
        routed, refuse", so a version with no row passes them silently -- reading
        an unusable envelope as an empty inventory would clear the guards for the
        version that is mid-build or serving live traffic.

        Element-level is the opposite: a malformed ROW inside a well-formed array
        is skipped while the inventory stays available. Both are pinned by tests.
        """
        try:
            payload = self.inference.list_versions(name)
        except ModelManagerError:
            logger.warning("list_versions unavailable; per-version inventory is unknown")
            return VersionInventory(model_name=name, available=False)

        rows = list_field(payload, "versions")
        deletable = list_field(payload, "deletable")
        if rows is None or deletable is None:
            logger.warning(
                "list_versions for %s returned a malformed versions/deletable field; "
                "treating the per-version inventory as unknown",
                name,
            )
            return VersionInventory(model_name=name, available=False)

        # PRESENT, not merely truthy: a numeric ``0`` is falsy but real, and
        # ``usable_deletable`` coerces it to ``"0"``, so dropping the row would
        # leave a deletable entry with no row -- clearing every guard written
        # ``row is not None and ...``. This filter must never be narrower than
        # ``usable_deletable``'s; wider is inert.
        versions = [
            _version_state(row)
            for row in rows
            if isinstance(row, dict) and row.get("version") is not None
        ]
        reported = {row.version for row in versions}
        return VersionInventory(
            model_name=name,
            versions=versions,
            # Verbatim from the backend's own list -- the contract's single
            # delete authority -- minus anything with no row in the same payload.
            # Shared with the unified path.
            deletable=deletable_with_rows(
                usable_deletable(deletable, name, "list_versions"),
                reported,
                name,
                "list_versions",
            ),
            routing_mode=payload.get("routing_mode"),
        )

    # --------------------------------------------------------- delete_version
    def delete_version(self, name: str, version: str) -> None:
        """Remove a version's directory from the shared model repository."""
        self.activation.delete(name, version)

    # --------------------------------------------------------- serving_state
    def serving_state(self, name: str, *, destructive: bool = False) -> ServingState:
        """Compose the normalized serving state from the AOI stack.

        ``destructive`` applies the unified adapter's completeness rule:
        ``GET /v1/model-routing`` answers 200 with whatever it holds, and one
        naming no primary leaves ``serving_versions`` empty while
        ``routing_available`` stays True, so every ``cleanup`` guard passes
        vacuously. A deployment that never wrote a policy has nothing to retire.
        """
        routing, routing_available = self._safe_get_routing()
        if destructive and routing_available and not _default_version_of(routing):
            logger.warning(
                "the AOI routing state names no primary model on a destructive read; "
                "reporting routing as unknown rather than as nothing routed"
            )
            routing, routing_available = {}, False
        loaded, loaded_available, ready_versions, resident = self._loaded_versions(name)

        # ``GET /v1/models`` lists what is RESIDENT, so an evicted or
        # never-loaded version has a directory and no row. This floor must not
        # shrink: DFW mints its next version above ``loaded_versions``, so one
        # that forgets an occupied directory mints straight onto it, leaving the
        # old spec.yaml and metadata.json under new weights.
        #
        # Widening only: leaking this into ``ready``/``resident`` would make a
        # directory count as servable or block an unload forever.
        if loaded_available:
            loaded = [*loaded, *sorted(activation_mod.repo_versions(name).difference(loaded))]

        default_version, entries = _compose_routing(routing, name)
        return ServingState(
            default_version=default_version,
            routing=entries,
            serving_versions=_serving_versions(routing),
            loaded_versions=loaded,
            ready=self.inference.ready(),
            routing_available=routing_available,
            loaded_available=loaded_available,
            ready_versions=ready_versions,
            resident_versions=resident,
        )

    # ---------------------------------------------------------------- unload
    def unload(self, name: str, version: str) -> EvictionAttestation:
        """Unload a version from AOI Inference.

        The attestation is returned for contract symmetry and unused here: this
        backend publishes its own ``deletable`` list, so ``inventory`` ignores it.
        """
        self.inference.unload(name, version)
        return EvictionAttestation(version=version, evicted_affirmed=True)

    # --------------------------------------------------------------- helpers
    def _put_routing(self, config: dict, idempotency_key: str | None) -> None:
        """PUT a routing config, carrying the caller's idempotency key when set."""
        if idempotency_key:
            config = {**config, "idempotency_key": idempotency_key}
        self.aoi_client.put_routing(config)

    def _safe_get_routing(self) -> tuple[dict, bool]:
        """Return ``(routing, available)`` for the current AOI routing state.

        ``available`` is False when the routing lookup fails. Read-only consumers
        degrade on an empty routing dict; destructive ones must read
        ``available=False`` as "unknown" and fail closed.
        """
        try:
            return self.aoi_client.get_routing(), True
        except ModelManagerError:
            logger.warning("get_routing unavailable; routing state is unknown")
            return {}, False

    def _loaded_versions(self, name: str) -> tuple[list[str], bool, list[str], list[str]]:
        """Return ``(known, available, ready, resident)`` from list_models.

        Three sets because three different questions are asked of one payload:

        * ``known`` — every version reported, non-READY included. The version floor
          DFW mints above; an UNAVAILABLE version still owns a directory.
        * ``ready`` — the versions actually servable; what convergence is judged on.
        * ``resident`` — still held in memory. An UNAVAILABLE version is evicted and
          deliberately absent, since that is the only observable signal an unload
          took effect. Judged against ``known`` an unload would never converge.

        ``available`` is False when the lookup fails, so callers can tell "unknown"
        from "nothing loaded".

        A malformed ROW is skipped: the envelope is validated upstream but the
        array can still hold a non-dict entry, and turning that into a 500 would
        break ``apply``, ``status`` and ``cleanup`` alike. The whole COLLECTION not
        being a list takes the unavailable path instead -- reporting "nothing is
        loaded" would clear ``cleanup``'s fail-closed guard for a live version.
        """
        try:
            models = self.inference.list_models()
        except ModelManagerError:
            logger.warning("list_models unavailable; loaded versions are unknown")
            return [], False, [], []
        if not isinstance(models, list):
            logger.warning("list_models returned a non-list; loaded versions are unknown")
            return [], False, [], []
        mine = [
            m
            for m in models
            if isinstance(m, dict) and m.get("name") == name and isinstance(m.get("version"), str)
        ]
        known = [m["version"] for m in mine]
        # ``state`` is the newer /v1/models field ("READY" | "UNAVAILABLE"), and
        # an older payload omits it, so missing reads as READY.
        #
        # NOT shared with ``ExplicitActivation._is_ready``, which requires the
        # field: there a missing state withholds build confirmation, the safe
        # direction; here it would empty ``ready_versions`` and no swap would
        # converge. Same field, opposite failure modes, opposite defaults.
        ready = [m["version"] for m in mine if m.get("state", "READY") == "READY"]
        # Anything not evicted counts as resident, so a version mid-load (neither
        # READY nor UNAVAILABLE) is still in memory and not safe to unload.
        resident = [m["version"] for m in mine if m.get("state", "READY") != _EVICTED_STATE]
        return known, True, ready, resident


def _ref(name: str, version: str) -> dict:
    """Build a canonical routing ref for a PUT body."""
    return {"model_name": name, "model_version": version}


def _ref_version(model: object) -> str | None:
    """Return a routing ref's version, accepting canonical or legacy keys.

    The canonical field is ``model_version``; ``version`` is the legacy spelling
    the API still accepts on input and which older deployments answer with.
    """
    if not isinstance(model, dict):
        return None
    version = model.get("model_version") or model.get("version")
    return version if isinstance(version, str) else None


def _canary_percent(name: str, candidate: str, canary_weight: float | None) -> float:
    """Convert a canary fraction to the wire percentage, rejecting the endpoints.

    The wire field is exclusive, ``0 < p < 100``, so the degenerate fractions
    have no representation: 0 is ``shadow``, 1 is ``full_switch``. A missing
    weight used to collapse to 0% and produce a PUT the backend always rejected,
    with nothing in the message to say why.
    """
    if canary_weight is None or not (0 < canary_weight < 1):
        raise ModelManagerError(
            code="bad_request",
            message=(
                f"canary control for {name}@{candidate} needs a canary_weight strictly between "
                f"0 and 1 (got {canary_weight!r}); use shadow for no traffic and full_switch "
                "for all of it"
            ),
            retryable=False,
            http_status=400,
        )
    # The fraction arrives from a percent/100 division upstream, and 0.05 * 100
    # is 5.000000000000001 in binary floating point.
    return round(canary_weight * 100, 6)


def _reject_unservable_target(name: str, version: str) -> None:
    """Refuse a name/version the AOI backend would reject, before staging costs.

    NOT the path-safety check: ``_version_dir`` proves a name cannot escape the
    model repository, and neither subsumes the other -- ``v2`` is a safe directory
    name Triton cannot serve, ``../etc`` is a conforming-looking string that must
    never reach the filesystem.

    In the AOI adapter rather than ``api/schemas.py`` because "a version must be a
    non-negative integer" is a Triton rule, and the schema is the frozen,
    backend-agnostic contract. ``stage`` is the seam because it is the only verb
    that spends real resources before the backend sees the name.

    Mirrors ``_validate_model_target`` (``app.py``, v0.0.0) for the name. The
    version check is deliberately NARROWER than the server's
    ``str(version).isdigit()``, which is True for non-ASCII digits like ``²`` --
    ``int()`` rejects those and Triton needs an integer-named directory.
    """
    if not _BACKEND_NAME_RE.fullmatch(name):
        raise ModelManagerError(
            code="bad_request",
            message=(
                f"model_name {name!r} is not usable by the AOI serving backend: it must be "
                "1-128 characters of letters, digits, '.', '_' or '-', and must start with a "
                "letter or digit"
            ),
            retryable=False,
            http_status=400,
        )
    if not _BACKEND_VERSION_RE.fullmatch(version):
        raise ModelManagerError(
            code="bad_request",
            message=(
                f"version {version!r} is not usable by the AOI serving backend: it must be a "
                "non-negative integer, since it names a Triton version directory"
            ),
            retryable=False,
            http_status=400,
        )


def _version_state(row: dict) -> VersionState:
    """Normalize one ``/v1/models/versions`` row.

    ``version`` is coerced rather than required to be a string, so rows keyed by
    a number join the ``deletable`` list ``usable_deletable`` normalizes; every
    caller matches the two by string equality.
    """
    routed_as = row.get("routed_as")
    state = row.get("state")
    return VersionState(
        version=str(row["version"]),
        on_disk=bool(row.get("on_disk")),
        loaded=bool(row.get("loaded")),
        ready=bool(row.get("ready")),
        state=state if isinstance(state, str) else None,
        routed_as=routed_as if isinstance(routed_as, str) else None,
        building=bool(row.get("building")),
        deletable=bool(row.get("deletable")),
    )


def _default_version_of(routing: dict) -> str | None:
    """Return the primary (default) version from a ModelRoutingState, if any."""
    if not isinstance(routing, dict):
        return None
    return _ref_version(routing.get("primary_model"))


def _serving_versions(routing: dict) -> set[str]:
    """Return the set of versions currently referenced by the routing state."""
    versions: set[str] = set()
    for key in ("primary_model", "canary_model", "shadow_model"):
        version = _ref_version(routing.get(key))
        if version:
            versions.add(version)
    return versions


def _compose_routing(routing: dict, name: str) -> tuple[str | None, list[RoutingEntry]]:
    """Compose (default_version, RoutingEntry[]) from a ModelRoutingState.

    full_switch v2  -> default v2, [{v2, default, 1.0}]
    canary p% v2    -> default v1, [{v1, default, 1-p}, {v2, canary, p}]
    shadow v2       -> default v1, [{v1, default, 1.0}, {v2, shadow, 0.0}]
    unload v2       -> default v1, [{v1, default, 1.0}] (single routing remains)
    """
    mode = routing.get("mode") if isinstance(routing, dict) else None
    primary = _default_version_of(routing)

    if mode == "single" and primary:
        return primary, [RoutingEntry(version=primary, role="default", weight=1.0)]

    if mode == "shadow" and primary:
        shadow = _ref_version(routing.get("shadow_model"))
        entries = [RoutingEntry(version=primary, role="default", weight=1.0)]
        if shadow:
            entries.append(RoutingEntry(version=shadow, role="shadow", weight=0.0))
        return primary, entries

    if mode == "canary" and primary:
        canary = _ref_version(routing.get("canary_model"))
        pct = routing.get("canary_percent", 0) or 0
        fraction = pct / 100.0
        entries = [RoutingEntry(version=primary, role="default", weight=round(1.0 - fraction, 6))]
        if canary:
            entries.append(RoutingEntry(version=canary, role="canary", weight=round(fraction, 6)))
        return primary, entries

    return primary, ([RoutingEntry(version=primary, role="default", weight=1.0)] if primary else [])

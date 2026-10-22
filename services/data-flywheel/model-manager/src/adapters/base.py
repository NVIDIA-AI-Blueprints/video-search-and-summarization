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

"""The ``ServingAdapter`` seam between invariant orchestration and serving.

``ModelManager`` owns the invariant promotion orchestration; an adapter owns the
client-specific mechanics: staging/load, translating control verbs into backend
calls, and reading serving/routing state back.

The adapter never leaks a backend-shaped payload upward -- it returns a normalized
``ServingState`` -- so ``ModelManager`` knows nothing about the serving stack.
"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from src.api.schemas import RoutingEntry

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServingState:
    """Normalized, client-agnostic view of a model's serving state.

    Every adapter must map its backend's routing/registry payloads onto this
    shape; ``ModelManager`` composes its responses from nothing else.
    """

    default_version: str | None = None
    routing: list[RoutingEntry] = field(default_factory=list)
    serving_versions: set[str] = field(default_factory=set)
    loaded_versions: list[str] = field(default_factory=list)
    ready: bool = False
    # False when the backend's routing/serving lookup failed, so ``routing`` and
    # ``serving_versions`` reflect "unknown", not "nothing serving". Destructive
    # callers (cleanup) must fail closed rather than trust an empty set here.
    routing_available: bool = True
    # False when the backend's loaded-model lookup failed, so ``loaded_versions``
    # is "unknown", not "nothing loaded". Unload convergence must not treat an
    # unknown set as proof that a version is gone.
    loaded_available: bool = True
    # The subset the backend reports as actually servable. Separate from
    # ``loaded_versions``, which is the wider "owns a directory" set DFW mints
    # its next version above and may include the repo listing.
    ready_versions: list[str] = field(default_factory=list)
    # Resident in the serving process's memory. Narrower than ``loaded_versions``
    # (an unloaded version keeps its directory) and wider than ``ready_versions``
    # (a version mid-load is resident without being servable), so an unload is
    # not complete just because the version stopped being READY.
    resident_versions: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class VersionState:
    """One version's row in the backend's per-version inventory."""

    version: str
    on_disk: bool = False
    loaded: bool = False
    ready: bool = False
    state: str | None = None
    routed_as: str | None = None
    building: bool = False
    deletable: bool = False
    # Streams attached to this version; None where the backend publishes no
    # refcount (AOI publishes none). A stream can outlive the routing entry that
    # attached it. Only zero permits a destructive verb, and an unparsable value
    # normalizes to 1 -- "said something I could not read" is not "none".
    streams: int | None = None
    # The config and engine each ``instances[]`` entry is actually reading. A
    # version can be ``ready`` while serving out of ANOTHER version's directory
    # (seen live: ITS@4 with both under ITS/1/), so a delete must check these.
    artifact_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvictionAttestation:
    """A backend's own statement that it has just evicted one version.

    Authorises no delete: ``cleanup`` never unloads, so it never holds one.

    ``evicted_affirmed`` means DONE, not merely accepted -- true for a ``200``,
    and for any other 2xx whose body says ``state: "unloaded"``. RTVI-CV needs
    that second form: every reply there is ``202 Accepted`` while the body says
    ``'ITS@3' unloaded`` and the row is gone from the next status read.
    """

    version: str
    evicted_affirmed: bool = True


@dataclass(frozen=True)
class DeleteEvidence:
    """Facts about ONE version a caller is trying to reclaim, gathered read-only.

    Assembled by the adapter, not the inventory composer: the composer's only
    routing signal is the per-row ``default_for``, whose enum has no value for a
    per-stream route pin. Only the adapter's routing read sees pins.

    ``evicted_by_caller`` is the one field this process did not read -- it is the
    caller's assertion, and satisfies exactly one clause of ``infer_deletable``.
    """

    model_name: str
    version: str
    evicted_by_caller: bool
    model_entry_present: bool
    routing_readable: bool
    serving_versions: frozenset[str]
    resident_versions: frozenset[str]
    build_settled: bool


def usable_deletable(deletable: list, name: str, source: str) -> list[str]:
    """Normalize a backend's ``deletable`` list to version strings.

    Integers are coerced, not dropped: rows are keyed ``str(version)``, so a
    numeric payload leaves ``"3" in deletable`` never matching a list holding
    ``3``. ``bool`` is excluded despite subclassing ``int``: ``"True"`` is not a
    version.

    Element-level, and the opposite of the envelope-level rule: one bad ENTRY
    says nothing about the rest, so it is dropped and LOGGED and the inventory
    stays available -- but a ``deletable`` that is not an array AT ALL is no
    answer.
    """
    versions: list[str] = []
    dropped: list[object] = []
    for entry in deletable:
        if isinstance(entry, str):
            versions.append(entry)
        elif isinstance(entry, int) and not isinstance(entry, bool):
            versions.append(str(entry))
        else:
            dropped.append(entry)
    if dropped:
        logger.warning(
            "%s for %s reported %d deletable entr%s not usable as a version and "
            "%s ignored (%r); no directory will be reclaimed for them",
            source,
            name,
            len(dropped),
            "y" if len(dropped) == 1 else "ies",
            "was" if len(dropped) == 1 else "were",
            dropped,
        )
    return versions


def deletable_with_rows(
    published: list[str], reported: set[str], name: str, source: str
) -> list[str]:
    """Drop published ``deletable`` entries the same payload reports no row for.

    Load-bearing: ``cleanup``'s per-version guards are each written
    ``row is not None and ...``, so an entry with no row clears all four in
    silence and reaches the ``rmtree`` having passed nothing.

    The cost falls on a backend that BOTH publishes a list AND drops unloaded
    rows: it can never reclaim through this path. Neither shipped one does both
    (AOI keeps the row; RTVI-CV publishes no list), so the drop is logged loudly
    -- its only other symptom is a filling volume.
    """
    kept, dropped = [], []
    for version in published:
        (kept if version in reported else dropped).append(version)
    for version in dropped:
        logger.warning(
            "refusing to reclaim %s@%s: %s published it as deletable but reports no row "
            "for it, and every per-version guard (building, routed_as, streams, loaded) "
            "reads a missing row as permission. A backend that both publishes a deletable "
            "list and drops unloaded rows can reclaim nothing through this path",
            name,
            version,
            source,
        )
    return kept


@dataclass(frozen=True)
class VersionInventory:
    """The backend's answer to "which version directories are safe to remove?".

    ``deletable`` is the backend's TERM in the delete decision; no version
    outside it is ever removed. A published list is used verbatim, never
    recomputed from the per-version rows -- the backend unions state this service
    cannot see, closing three windows: a conversion running before its version
    reaches the loaded-model index, a loaded shadow/canary target that is not the
    primary, and a version still referenced while missing on disk.

    Where the backend publishes nothing, an adapter may nominate AT MOST the one
    version named in ``reclaiming``, on its own reads. Still never recomputed from
    the rows: a payload that forgot a model looks exactly like one in which
    everything is idle.

    Neither source is the whole decision: a published list is measured to include
    a directory that exists and has never been loaded -- an in-flight pull -- so
    a caller must still apply its own roots (``ModelManager.cleanup``).

    ``available`` is False when the lookup failed, so ``deletable`` reads
    "unknown" rather than "nothing is safe to delete".

    ``routing_mode`` is DIAGNOSTIC ONLY: nothing branches on it, it is in no API
    response model, and it is a leftover of the legacy
    ``GET /v1/models/versions`` payload. Anything needing the live routing policy
    must read ``ServingState``.
    """

    model_name: str
    versions: list[VersionState] = field(default_factory=list)
    deletable: list[str] = field(default_factory=list)
    routing_mode: str | None = None
    available: bool = True

    def version(self, version: str) -> VersionState | None:
        """Return the row for ``version``, or None when the backend reports none."""
        return next((row for row in self.versions if row.version == version), None)


class ServingAdapter(ABC):
    """Client-specific serving mechanics behind a common interface."""

    @abstractmethod
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
        """Stage (and, where applicable, load) a version; return its repo path.

        ``idempotency_key`` lets a backend deduplicate a repeated stage/load so a
        retry re-attaches to the work already in flight instead of starting it
        again.
        """
        ...

    @abstractmethod
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
        """Translate a Model Promotion control verb onto backend calls."""
        ...

    @abstractmethod
    def serving_state(self, name: str, *, destructive: bool = False) -> ServingState:
        """Return the normalized serving state for ``name``.

        ``destructive`` changes how an incomplete read is SCORED, not what is
        read. A backend answering 200 while stating no routing policy leaves
        ``serving_versions`` empty; a destructive read calls that UNKNOWN
        (``routing_available`` goes False) so the caller fails closed, while a
        swap reads it as "nothing is routed".

        Set by ``cleanup`` and ``_evict`` and nothing else. On the contract rather
        than one adapter, because those callers hold an adapter they cannot name.
        """
        ...

    @abstractmethod
    def unload(self, name: str, version: str) -> EvictionAttestation:
        """Remove a version from the serving stack.

        Returns the backend's own attestation of the eviction. Nothing may forge
        one.
        """
        ...

    @abstractmethod
    def inventory(
        self, name: str, *, reclaiming: str | None = None, evicted_by_caller: bool = False
    ) -> VersionInventory:
        """Return the backend's per-version inventory for ``name``.

        Must never raise for an unreadable backend: report ``available=False``
        instead, so a destructive caller can tell "nothing is deletable" from "I
        could not find out" and fail closed on the latter.

        ``reclaiming`` names the ONE version ``cleanup`` is considering removing.
        An adapter whose backend publishes its own ``deletable`` list MUST ignore
        it; one whose backend publishes nothing MAY nominate that version and no
        other. Omitting it must stay safe.

        ``evicted_by_caller`` is the caller's assertion of a recorded eviction,
        not a backend fact; it may satisfy only the one clause described in
        ``CleanupRequest.evicted_by_caller``.
        """
        ...

    @abstractmethod
    def delete_version(self, name: str, version: str) -> None:
        """Remove a version's artifacts from the model repository.

        Callers must have established from ``inventory`` that the version is
        deletable; this performs the removal and makes no safety judgement of its
        own.
        """
        ...

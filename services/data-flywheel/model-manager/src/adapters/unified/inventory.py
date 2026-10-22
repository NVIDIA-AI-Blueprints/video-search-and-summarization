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

"""Per-version inventory for a unified-API backend.

``VersionInventory`` was designed around the legacy ``GET /v1/models/versions``,
which answered "which version directories are safe to remove?" in one
authoritative call. **The unified API has no equivalent**, so this module
assembles what it can from three sources and is explicit about the one field it
cannot supply:

===============  ==========================================================
field            source
===============  ==========================================================
version, state,  ``GET /api/v1/model/status``
ready, loaded
routed_as        the same payload's ``default_for``
routing_mode     the same payload's ``routing.mode`` — best effort, see below
on_disk          a listing of the shared model repository
building         ``GET /v1/models/build-status`` (NOT part of the unified
                 surface; absent on a backend that does not expose it)
deletable        the model's own top-level ``deletable`` where the backend
                 publishes one; otherwise inferred, and then only for the one
                 version the caller named in ``reclaiming`` — see below
===============  ==========================================================

``deletable`` has two sources, in strict precedence.

A published top-level ``deletable`` is authoritative and used verbatim; it unions
state this process cannot observe from outside. Narrowed one way only: an entry
the same payload reports no ROW for is dropped at WARNING, because every
per-version guard downstream is written ``row is not None and ...``.

Where the backend publishes nothing, ``infer_deletable`` may nominate at most the
ONE version named in ``reclaiming``, from read-only probes. Every clause is a
refusal, so the result is ``[version]`` or ``[]``.

One clause is not a read: a backend that DROPS an unloaded version's row leaves
an absence byte-identical for an evicted, a never-loaded and an unknown version,
so that branch alone rests on the caller's ``evicted_by_caller``. A row that is
PRESENT must still say ``state: "unloaded"``.

The hazard: synthesising deletability from absence ("not routed and not
resident") reports the live primary as deletable the moment a model-granular
unload evicts it.

``routing_mode`` is best effort and reads ``None`` wherever the flat
``routing.mode`` key is unfilled -- permanently so on serving image
``3.3.0-amd64-26.08.1-172-with-api-fixes``, whose model-filtered status carries
neither ``routing`` nor ``default``.

Do NOT wire it through ``UnifiedServingAdapter._routing_view`` to close that.
The routing policy is service-GLOBAL and the resolved view is not filtered by
model -- ``serving_state`` only gets away with it by screening through
``routing_names_other_model`` first -- so unscreened, a service rolling a
different model through the shared policy would have THAT model's mode reported
as this one's. Nothing branches on the field anyway; the routing facts
``cleanup`` gates on come from ``serving_state``.
"""

import logging

from src.adapters import activation as activation_mod
from src.adapters.base import (
    DeleteEvidence,
    VersionInventory,
    VersionState,
    deletable_with_rows,
    usable_deletable,
)
from src.adapters.unified.state import (
    _SERVABLE_STATES,
    affirms_unloaded,
    artifact_paths,
    routed_roles,
    state_of,
    stream_refcount,
    version_rows,
)

logger = logging.getLogger(__name__)


def on_disk_versions(name: str) -> set[str]:
    """The version directories on disk for ``name``.

    Shared with the legacy adapter: two copies of a listing that carries a
    containment proof would drift.
    """
    return activation_mod.repo_versions(name)


_ABSENT = object()
_MALFORMED = object()


def published_deletable(status: dict, name: str) -> object:
    """Return the model's own ``deletable`` value, ``_ABSENT``, or ``_MALFORMED``.

    Three-valued, not two: a published ``[]`` means "nothing", an absent key
    means silence, and only the second selects inference.

    Read from the model's OWN entry, never the container's: a service-level array
    carries bare version strings and DFW mints small integers per model, so an
    entry ``"1"`` meaning ``modelB@1`` would authorise deleting ``modelA@1``.

    ``additionalProperties: true`` makes reading an undeclared key conformant
    everywhere, so the branch is on THE RESPONSE, not the backend's identity.
    """
    models = status.get("models")
    if isinstance(models, list):
        for model in models:
            if not isinstance(model, dict) or model.get("name") != name:
                continue
            if "deletable" not in model:
                break
            value = model["deletable"]
            if value is None:
                # The usual serialisation of an empty optional.
                logger.debug(
                    "unified status for %s carried a null deletable; reading as absent", name
                )
                return _ABSENT
            return value if isinstance(value, list) else _MALFORMED
    if isinstance(status.get("deletable"), list):
        logger.warning(
            "unified status carries a service-level 'deletable' with no model scoping for "
            "%s; ignoring it, because its entries name no model and version labels collide "
            "across models",
            name,
        )
    return _ABSENT


def _refuse(name: str, version: str, reason: str) -> list[str]:
    logger.info("not reclaiming %s@%s: %s", name, version, reason)
    return []


def infer_deletable(
    rows: list[dict],
    roles: dict[str, str],
    disk: set[str],
    evidence: DeleteEvidence | None,
) -> list[str]:
    """At most one version: the one the caller asked to reclaim.

    Every clause is a refusal, and nothing is added on the strength of something
    NOT being said -- ``compose`` manufactures fully-idle synthetic rows out of
    absence, so a payload that merely forgot the model would read as "all safe to
    remove". Any version other than ``reclaiming`` would enter having passed no
    guard, since ``cleanup``'s guards are scoped to that one.
    """
    if evidence is None:
        return []
    name, version = evidence.model_name, evidence.version
    if not evidence.model_entry_present:
        return _refuse(name, version, "the status did not identify the model at all")
    row = next((r for r in rows if r["version"] == version), None)
    if row is None:
        if not evidence.evicted_by_caller:
            return _refuse(
                name,
                version,
                "no row for it, and the caller asserted no recorded eviction -- on a "
                "backend that drops the row, an absent row is byte-identical for an "
                "evicted, a never-loaded and an unknown version",
            )
    elif not affirms_unloaded(row):
        # ``affirms_unloaded``, never ``state_of``: see its docstring.
        return _refuse(
            name, version, f"its row does not affirm eviction (state={row.get('state')!r})"
        )
    if roles.get(version):
        return _refuse(name, version, "its row still carries default_for")
    if not evidence.routing_readable:
        return _refuse(name, version, "routing was unreadable after the unload")
    if version in evidence.serving_versions:
        return _refuse(name, version, "routing or a stream pin still names it")
    if version in evidence.resident_versions:
        return _refuse(name, version, "it is still resident")
    if not evidence.build_settled:
        return _refuse(name, version, "a build is in flight, unknown, or unreadable")
    if version not in disk:
        return _refuse(name, version, "there is no directory on disk to reclaim")
    logger.warning(
        "reclaiming %s@%s: the backend published no deletable list, and its own row (or, "
        "where it drops the row, the caller's recorded eviction) affirms the version is "
        "evicted; a second read finds routing readable and naming it nowhere, nothing "
        "resident, and no build in flight",
        name,
        version,
    )
    return [version]


def compose(
    name: str,
    status: dict,
    *,
    on_disk: set[str] | None = None,
    building: str | None = None,
    evidence: DeleteEvidence | None = None,
) -> VersionInventory:
    """Build a ``VersionInventory`` from a status payload plus local knowledge.

    ``building`` is the single version reported mid-build: the build-status
    endpoint is service-wide and takes no filter, so at most one can be flagged.

    ``evidence`` is present only on a reclaim read. Without it and without a
    published list this returns ``deletable=[]`` and synthesises nothing.
    """
    rows = version_rows(status, name)
    roles = routed_roles(rows)
    disk = on_disk or set()
    reported = {row["version"] for row in rows}

    versions = [
        VersionState(
            version=row["version"],
            on_disk=row["version"] in disk,
            # The unified surface draws no "loaded" vs "resident" distinction.
            # ``affirms_unloaded``, not ``state_of``: cleanup's residency root is
            # the only reader, and an unreadable state must refuse there.
            loaded=not affirms_unloaded(row),
            ready=state_of(row) in _SERVABLE_STATES,
            state=state_of(row),
            routed_as=roles.get(row["version"]),
            building=building is not None and row["version"] == building,
            # Per the module docstring: never synthesized.
            deletable=False,
            # Carried up verbatim; the reclaim roots in ``ModelManager`` decide,
            # since they narrow a published list this module may not second-guess.
            streams=stream_refcount(row),
            artifact_paths=artifact_paths(row),
        )
        for row in rows
    ]

    # A directory the serving process never heard of still occupies disk and
    # still has to appear in the version floor.
    versions.extend(
        VersionState(
            version=version,
            on_disk=True,
            loaded=False,
            ready=False,
            state=None,
            routed_as=None,
            building=building == version,
            deletable=False,
        )
        for version in sorted(disk - reported)
    )

    published = published_deletable(status, name)
    if published is _MALFORMED and evidence is not None:
        # An unusable list is no answer. Safe to read as empty on a read that
        # names no version; the one that could authorise an rmtree must not.
        logger.warning(
            "unified status for %s carried a deletable that is not an array; reporting the "
            "per-version inventory as unknown",
            name,
        )
        return VersionInventory(model_name=name, available=False)
    if isinstance(published, list):
        # Verbatim, minus anything the same payload reports no row for.
        # Intersected against ``reported``, not ``versions``, which already holds
        # the synthetic disk-only rows.
        deletable = deletable_with_rows(
            usable_deletable(published, name, "model-status"), reported, name, "model-status"
        )
    else:
        deletable = infer_deletable(rows, roles, disk, evidence)

    routing = status.get("routing")
    routing_mode = routing.get("mode") if isinstance(routing, dict) else None
    return VersionInventory(
        model_name=name,
        versions=versions,
        deletable=deletable,
        routing_mode=routing_mode if isinstance(routing_mode, str) else None,
        available=True,
    )

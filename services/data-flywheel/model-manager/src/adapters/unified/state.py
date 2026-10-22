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

"""Pure normalization of the unified ``model-status`` payload.

Nothing here does IO, so every rule below is directly testable as a table.

The payload shape is::

    {"routing": {"mode": "canary",
                 "model":  {"name": "m", "version": "1"},
                 "canary": {"name": "m", "version": "2"},
                 "canary_percent": 10},
     "models": [{"name": "m",
                 "versions": [{"version": "1", "state": "ready",
                               "default_for": ["model"]}]}]}

The status view above is flat and carries a ``mode``. ``GET /api/v1/model/routing``
returns the same information nested one level down, under ``default``, with no
mode at all::

    {"routing": {"default": {"model":  {"name": "m", "version": "1"},
                             "shadow": {"name": "m", "version": "2"}},
                 "routes": [], "routing_revision": 4}}

The nested view's inner object -- the spec's ``DefaultRoutingPolicy`` -- uses the
SAME key names as the flat view and differs only by the absent ``mode``, so
``as_status_view`` synthesizes it and every rule below applies to both.

That synthesis is exact, not a guess: the schema declares ``shadow`` and
``canary`` mutually exclusive (``allOf: not: required: [shadow, canary]``), so
the occupied slot names the mode outright.
"""

import logging

from src.api.schemas import RoutingEntry

logger = logging.getLogger(__name__)

# Lifecycle states from the unified spec, lowercase. AOI emits only
# ready/warming/unloading/unloaded (from Triton's READY/LOADING/UNLOADING/
# UNAVAILABLE) and passes anything unmapped through lowercased, so unknown values
# take the conservative branch -- including the contract's ``updating``.
STATE_READY = "ready"
STATE_SERVING = "serving"
STATE_UNLOADED = "unloaded"

# A version is servable in exactly these states. ``serving`` is strictly more
# than ``ready`` -- it is ready AND routed -- so excluding it would make a live
# version read as unservable and no swap would ever converge.
_SERVABLE_STATES = frozenset({STATE_READY, STATE_SERVING})

# The service renders a missing stable version as the literal string "None" (an
# f-string over a None), which would otherwise be reported as the default.
_NULL_VERSION_SENTINELS = frozenset({"None", "none", "null", ""})


def _version_of(ref: object) -> str | None:
    """Return a unified ModelRef's version, or None when it is absent/degenerate."""
    if not isinstance(ref, dict):
        return None
    version = ref.get("version")
    if not isinstance(version, str) or version in _NULL_VERSION_SENTINELS:
        return None
    return version


def _name_of(ref: object) -> str | None:
    """Return a unified ModelRef's model name, if present."""
    if not isinstance(ref, dict):
        return None
    name = ref.get("name")
    return name if isinstance(name, str) else None


def _local_version(ref: object, name: str) -> str | None:
    """Return a ref's version only when the ref belongs to ``name``.

    Applied to the shadow and canary slots so ``compose_routing`` cannot disagree
    with ``serving_versions``: an entry the latter does not list would make
    convergence match on a version ``cleanup`` considers idle.

    The match is EXACT, so a ref with no name at all counts as foreign. ``name``
    is required on the spec's ModelRef, and the safe reading of "I cannot tell
    whose version this is" is "not ours".
    """
    if _name_of(ref) != name:
        return None
    return _version_of(ref)


def model_entry_present(status: dict, name: str) -> bool:
    """Does ``models[]`` contain an entry for ``name`` at all?

    Distinct from "has rows": ``version_rows`` returns ``[]`` both for a backend
    that knows the model and has nothing loaded and for one that never heard of
    it, and ``inventory.compose`` turns the latter into fully-idle synthetic rows.
    """
    models = status.get("models")
    if not isinstance(models, list):
        return False
    return any(isinstance(m, dict) and m.get("name") == name for m in models)


def version_rows(status: dict, name: str) -> list[dict]:
    """Return the ``versions`` rows for ``name`` from a ``model-status`` payload.

    A malformed row is skipped while the rest are kept: one bad entry says
    nothing about the others, and this feeds every read path, so turning the
    whole lookup into a failure over a single unparseable row would be a far
    larger outage than the row itself.
    """
    models = status.get("models")
    if not isinstance(models, list):
        return []
    rows: list[dict] = []
    for model in models:
        if not isinstance(model, dict) or model.get("name") != name:
            continue
        versions = model.get("versions")
        if not isinstance(versions, list):
            continue
        for row in versions:
            if isinstance(row, dict) and isinstance(row.get("version"), str):
                rows.append(row)
    return rows


def version_sets(
    rows: list[dict], *, strict_residency: bool = False
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(known, ready, resident)`` from a model's version rows.

    Three sets because three different questions are asked of one payload:

    * ``known`` — every version reported, ``unloaded`` included. FEEDS the version
      floor DFW mints above, but is not the whole of it: the contract lets an
      implementation omit an unloaded row entirely, so the adapter unions the
      on-disk listing on top.
    * ``ready`` — the versions actually servable; what convergence is judged on.
    * ``resident`` — still held in memory. Only ``unloaded`` is excluded, so a
      version mid-load or mid-unload still counts: an unload that has not
      finished must not read as complete.

    ``strict_residency`` is set by the destructive read and nothing else, and only
    refuses more: a row that does not say ``unloaded`` counts as resident.
    """
    known = [row["version"] for row in rows]
    ready = [row["version"] for row in rows if state_of(row) in _SERVABLE_STATES]
    # The strict read is a superset: a row whose state parses to something other
    # than ``unloaded`` never affirms eviction, so the second clause only narrows.
    resident = [
        row["version"]
        for row in rows
        if not affirms_unloaded(row) and (strict_residency or state_of(row) != STATE_UNLOADED)
    ]
    return known, ready, resident


def state_of(row: dict) -> str:
    """Return a row's lifecycle state, lowercased, defaulting to unloaded.

    An absent or non-string state reads as ``unloaded``, which keeps the version
    out of ``ready_versions`` so a swap waits. That is the weaker direction for
    residency -- an unload would look complete -- but no destructive decision
    rests on it: the reclaim path uses ``affirms_unloaded``, whose default is the
    opposite.
    """
    state = row.get("state")
    return state.lower() if isinstance(state, str) else STATE_UNLOADED


def affirms_unloaded(row: dict) -> bool:
    """Does the row say, in the BACKEND's own words, that it holds nothing?

    Needs the literal "unloaded". A missing state means "do not know", which must
    not clear the residency check before an ``rmtree`` -- which is what
    ``state_of``'s default of ``unloaded`` would do.
    """
    state = row.get("state")
    return isinstance(state, str) and state.lower() == STATE_UNLOADED


# ``shadow_streams`` is in the contract's vocabulary but on no shipped build
# (measured 2026-09-15). Read where present, never required.
_STREAM_KEYS = ("streams", "shadow_streams")

# The per-instance keys naming a file the instance is actually reading.
_ARTIFACT_KEYS = ("config", "engine")


def _count(value: object) -> int | None:
    """Read one published stream field as a refcount, or None when it is absent.

    Two shapes for one fact: RTVI-CV's ``serving.streams`` is a count, a route's
    ``streams`` is the list of ids.

    Anything else PRESENT reads as 1 -- unparseable means "attachments I cannot
    see", and the only caller is a reclaim. Do NOT add a ``bool`` branch
    returning 1: ``bool`` subclasses ``int``, so it would fire first and refuse
    an idle version forever.
    """
    if value is None:
        return None
    if isinstance(value, int):
        # ``int(value)`` so a bool leaves as 0/1: the count is echoed into a
        # refusal message.
        return max(int(value), 0)
    if isinstance(value, list | tuple | set | dict):
        return len(value)
    logger.warning(
        "a version row published a stream refcount this service cannot read (%r); "
        "counting it as attached",
        value,
    )
    return 1


def stream_refcount(row: dict) -> int | None:
    """How many streams a version row reports attached, or None if it reports none.

    Sums ``serving`` and every ``instances[]`` entry: a version can be attached
    through either, and the contract does not say they move together.

    None means no refcount was published at all -- the AOI shape, where gating on
    a field it never emits would refuse every reclaim. None permits.
    """
    counts = [
        _count(block.get(key))
        for block in (row.get("serving"), *_instances(row))
        if isinstance(block, dict)
        for key in _STREAM_KEYS
    ]
    published = [count for count in counts if count is not None]
    return sum(published) if published else None


def _instances(row: dict) -> list[dict]:
    """The row's ``instances[]`` entries, skipping anything not an object."""
    instances = row.get("instances")
    if not isinstance(instances, list):
        return []
    return [entry for entry in instances if isinstance(entry, dict)]


def artifact_paths(row: dict) -> tuple[str, ...]:
    """Every filesystem path this version is published as reading.

    ``config`` and ``engine`` are RTVI-CV extensions holding absolute paths, and
    the only statement of WHICH directory a live instance reads from -- not always
    its own, since a load whose ``config_file`` names another version's directory
    is accepted.

    Read from the ROW as well as each ``instances[]`` entry: a summarised instance
    list would hide an alias, and a missed alias is an ``rmtree`` under a live
    instance while a redundant one costs only a refused reclaim.
    """
    return tuple(
        dict.fromkeys(
            value
            for entry in (row, *_instances(row))
            for key in _ARTIFACT_KEYS
            if isinstance(value := entry.get(key), str) and value
        )
    )


def compose_routing(routing: dict, name: str) -> tuple[str | None, list[RoutingEntry]]:
    """Compose ``(default_version, RoutingEntry[])`` from the status routing view.

    single v2     -> default v2, [{v2, default, 1.0}]
    shadow v2     -> default v1, [{v1, default, 1.0}, {v2, shadow, 0.0}]
    canary p% v2  -> default v1, [{v1, default, 1-p}, {v2, canary, p}]

    Entries are emitted only for refs naming *this* model: the unified routing
    policy is service-global, so a service routing a different model would
    otherwise have that model's version reported as this one's default.
    """
    if not isinstance(routing, dict):
        return None, []
    primary_ref = routing.get("model")
    if primary_ref is None:
        # Legal: the schema's anyOf admits a policy with only a shadow slot, and
        # ``serving_versions`` still retains the shadow so cleanup stays closed.
        return None, []
    if _name_of(primary_ref) != name:
        logger.warning(
            "unified routing names model %r, not %r; reporting no routing for this model",
            _name_of(primary_ref),
            name,
        )
        return None, []

    primary = _version_of(primary_ref)
    if primary is None:
        return None, []

    mode = routing.get("mode")

    if mode == "shadow":
        entries = [RoutingEntry(version=primary, role="default", weight=1.0)]
        shadow = _local_version(routing.get("shadow"), name)
        if shadow:
            entries.append(RoutingEntry(version=shadow, role="shadow", weight=0.0))
        return primary, entries

    if mode == "canary":
        canary = _local_version(routing.get("canary"), name)
        percent = routing.get("canary_percent") or 0
        try:
            fraction = float(percent) / 100.0
        except (TypeError, ValueError):
            fraction = 0.0
        entries = [RoutingEntry(version=primary, role="default", weight=round(1.0 - fraction, 6))]
        if canary:
            entries.append(RoutingEntry(version=canary, role="canary", weight=round(fraction, 6)))
        return primary, entries

    # "single", and anything unrecognized that still names a primary.
    return primary, [RoutingEntry(version=primary, role="default", weight=1.0)]


def routing_names_other_model(routing: dict, name: str) -> bool:
    """True when any ref in the service-global policy names a different model.

    The unified policy has ONE primary/shadow/canary slot for the whole service,
    so a foreign ref means the policy in force is not ours, not that the payload
    is malformed.

    Convergence matches on a bare version NUMBER, so another model's canary at v2
    satisfies ``_control_reflected("canary", candidate="2", ...)`` exactly and the
    swap records ``succeeded``. Dropping the foreign entry is no safer: a lone
    ``[{v2, default, 0.9}]`` then satisfies ``full_switch``, which checks roles
    and never weights. The only correct answer is "routing is unknown".
    """
    if not isinstance(routing, dict):
        return False
    return any(
        routing.get(key) is not None and _name_of(routing.get(key)) != name
        for key in ("model", "shadow", "canary")
    )


def serving_versions(routing: dict, name: str) -> set[str]:
    """Return every version the routing policy references for ``name``.

    What ``cleanup`` refuses to delete, so deliberately wider than the routing
    entries: it keeps a shadow or canary target the entry composition dropped.
    """
    if not isinstance(routing, dict):
        return set()
    versions: set[str] = set()
    for key in ("model", "shadow", "canary"):
        ref = routing.get(key)
        if _name_of(ref) != name:
            continue
        version = _version_of(ref)
        if version:
            versions.add(version)
    return versions


def routed_roles(rows: list[dict]) -> dict[str, str]:
    """Map version -> routing role from each row's ``default_for``.

    The unified wire value for the primary is ``model``; the Model Manager's
    vocabulary calls it ``default``. Renaming here keeps that spelling difference
    out of every consumer.
    """
    roles: dict[str, str] = {}
    for row in rows:
        default_for = row.get("default_for")
        if not isinstance(default_for, list):
            continue
        for role in default_for:
            if not isinstance(role, str):
                continue
            roles[row["version"]] = "default" if role == "model" else role
            break
    return roles


# Whichever optional slot is filled names the mode. The schema makes them
# exclusive, so order only matters on NON-conformant input filling both, where
# canary-first keeps the weighted reading.
_MODE_SLOTS = (("canary", "canary"), ("shadow", "shadow"))


def infer_mode(policy: dict) -> str:
    """Return the routing mode a ``DefaultRoutingPolicy`` is in.

    ``GET /api/v1/model/routing`` and ``model-status.default`` both carry the
    policy WITHOUT a mode, so it is recovered from the occupied slots.

    A ``null`` slot reads as absent: the contract uses JSON ``null`` to CLEAR a
    shadow, so a backend echoing the cleared slot back describes no shadow.
    """
    if not isinstance(policy, dict):
        return "single"
    for key, mode in _MODE_SLOTS:
        if policy.get(key) is not None:
            return mode
    return "single"


def as_status_view(policy: dict) -> dict:
    """Adapt a ``DefaultRoutingPolicy`` to the flat ``model-status.routing`` shape.

    The whole adaptation is the synthesized ``mode``; every other key matches.
    Done here so ``compose_routing``, ``serving_versions`` and
    ``routing_names_other_model`` never learn which endpoint the policy came from.
    """
    if not isinstance(policy, dict):
        return {}
    # LAST on purpose: a real ``mode`` inside a nested policy must not beat the
    # occupied slots, which are what the other rules read.
    return {**policy, "mode": infer_mode(policy)}


def pinned_versions(routing_state: dict, name: str) -> set[str]:
    """Return versions held by explicit per-stream routes, for ``name`` only.

    ``GET /api/v1/model/routing`` is the ONE place these are visible -- an RTVI-CV
    extension, absent from ``model-status``. Each ``StreamRoute`` carries its own
    refs, so a version can serve real traffic on a named stream while the default
    policy has never heard of it.

    Feeds ``serving_versions`` and nothing else, since deleting such a version
    would cut the streams routed to it. Kept OUT of the routing entries, where a
    per-stream pin has no honest role or weight.
    """
    routes = routing_state.get("routes")
    if not isinstance(routes, list):
        return set()
    versions: set[str] = set()
    for route in routes:
        if not isinstance(route, dict):
            continue
        for key in ("model", "shadow", "canary"):
            version = _local_version(route.get(key), name)
            if version:
                versions.add(version)
    return versions

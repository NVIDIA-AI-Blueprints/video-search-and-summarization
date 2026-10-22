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

"""Freeze the convergence predicate as golden vectors.

Run this against the current implementation before a refactor:

    uv run python tests/vectors/generate.py

It writes ``convergence.json`` next to itself: ``(reflected, servable)`` for
every pruned point of the ``_control_reflected`` / ``_candidate_servable`` input
cross-product. ``tests/test_convergence_vectors.py`` replays it.

Do not regenerate the vectors to make a failing test pass; that erases the proof.
Regenerate only for a deliberate, separately reviewed behaviour change.

Most of the raw cross-product is redundant because some axes are not read on the
branch they land on. ``_canonicalize`` collapses those inert axes and the result
is deduplicated; every raw point is checked against its canonical form so the
pruning is proven output-preserving. ``_probe_vectors`` then re-adds the inert
axes on one converged vector per control, so the file still shows that those
fields are ignored.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import logging
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.adapters.base import ServingState  # noqa: E402
from src.api.schemas import RoutingEntry  # noqa: E402
from src.core.manager import (  # noqa: E402
    _candidate_servable,
    _control_reflected,
)

SCHEMA_VERSION = 1

HERE = Path(__file__).resolve().parent
CONVERGENCE_PATH = HERE / "convergence.json"

# ------------------------------------------------------------------ constants
CANDIDATE = "7"  # the version being rolled out
PREVIOUS = "6"  # the plausible ``from_version``
THIRD = "9"  # an unrelated version: "a third version" / "foreign entry"

CANARY_W = 0.25  # the weight carried by the canary entry in every routing shape

CONTROLS = ("shadow", "canary", "full_switch", "unload")
DEFAULT_VERSIONS = (CANDIDATE, PREVIOUS, THIRD, None)
FROM_VERSIONS = (None, PREVIOUS)
# None (weight unchecked), a mismatch, an exact match, inside the 1e-6 tolerance,
# and outside it.
CANARY_WEIGHTS = (None, 0.05, CANARY_W, CANARY_W + 1e-7, CANARY_W + 1e-3)
ROUTING_SHAPES = (
    "empty",
    "default",
    "default_canary",
    "default_shadow",
    "default_canary_shadow",
    "canary_only",
    "foreign",
)

# Fields no branch of either predicate reads. Held constant in the base product
# and varied only in _probe_vectors.
INERT_READY = False
INERT_LOADED_VERSIONS = [PREVIOUS, CANDIDATE]
INERT_SERVING_VERSIONS: list[str] = []


def _routing(shape: str, default_version: str | None) -> list[dict[str, Any]]:
    """Materialize a routing shape against ``default_version``.

    The primary follows ``default_version`` so the shapes stay self-consistent.
    ``foreign`` deliberately does not, to cover a routing view that describes an
    unrelated version.
    """
    primary = default_version if default_version is not None else CANDIDATE
    if shape == "empty":
        return []
    if shape == "default":
        return [{"version": primary, "role": "default", "weight": 1.0}]
    if shape == "default_canary":
        return [
            {"version": primary, "role": "default", "weight": 1.0 - CANARY_W},
            {"version": CANDIDATE, "role": "canary", "weight": CANARY_W},
        ]
    if shape == "default_shadow":
        return [
            {"version": primary, "role": "default", "weight": 1.0},
            {"version": CANDIDATE, "role": "shadow", "weight": 0.0},
        ]
    if shape == "default_canary_shadow":
        return [
            {"version": primary, "role": "default", "weight": 1.0 - CANARY_W},
            {"version": CANDIDATE, "role": "canary", "weight": CANARY_W},
            {"version": CANDIDATE, "role": "shadow", "weight": 0.0},
        ]
    if shape == "canary_only":
        return [{"version": CANDIDATE, "role": "canary", "weight": CANARY_W}]
    if shape == "foreign":
        return [
            {"version": THIRD, "role": "default", "weight": 1.0 - CANARY_W},
            {"version": THIRD, "role": "canary", "weight": CANARY_W},
        ]
    raise ValueError(f"unknown routing shape: {shape}")


def _state_dict(
    *,
    default_version: str | None,
    routing: list[dict[str, Any]],
    routing_available: bool,
    loaded_available: bool,
    candidate_ready: bool,
    candidate_resident: bool,
    ready: bool = INERT_READY,
    loaded_versions: list[str] | None = None,
    serving_versions: list[str] | None = None,
) -> dict[str, Any]:
    """A ServingState as plain JSON. Vectors construct ServingState from this."""
    return {
        "default_version": default_version,
        "routing": routing,
        "serving_versions": (
            list(INERT_SERVING_VERSIONS) if serving_versions is None else serving_versions
        ),
        "loaded_versions": (
            list(INERT_LOADED_VERSIONS) if loaded_versions is None else loaded_versions
        ),
        "ready": ready,
        "routing_available": routing_available,
        "loaded_available": loaded_available,
        # ``ready_versions``/``resident_versions`` always carry PREVIOUS so that
        # "candidate absent" is never confused with "the whole set is empty".
        "ready_versions": [PREVIOUS] + ([CANDIDATE] if candidate_ready else []),
        "resident_versions": [PREVIOUS] + ([CANDIDATE] if candidate_resident else []),
    }


def build_state(state: dict[str, Any]) -> ServingState:
    """Rebuild a frozen ServingState. Shared with the replay test."""
    return ServingState(
        default_version=state["default_version"],
        routing=[RoutingEntry(**entry) for entry in state["routing"]],
        serving_versions=set(state["serving_versions"]),
        loaded_versions=list(state["loaded_versions"]),
        ready=state["ready"],
        routing_available=state["routing_available"],
        loaded_available=state["loaded_available"],
        ready_versions=list(state["ready_versions"]),
        resident_versions=list(state["resident_versions"]),
    )


# -------------------------------------------------------------------- product
def _raw_product() -> list[dict[str, Any]]:
    """The full cross-product, before pruning."""
    vectors = []
    for (
        control,
        default_version,
        shape,
        routing_available,
        loaded_available,
        candidate_ready,
        candidate_resident,
        canary_weight,
        from_version,
    ) in itertools.product(
        CONTROLS,
        DEFAULT_VERSIONS,
        ROUTING_SHAPES,
        (True, False),
        (True, False),
        (True, False),
        (True, False),
        CANARY_WEIGHTS,
        FROM_VERSIONS,
    ):
        vectors.append(
            {
                "control": control,
                "candidate": CANDIDATE,
                "from_version": from_version,
                "canary_weight": canary_weight,
                "state": _state_dict(
                    default_version=default_version,
                    routing=_routing(shape, default_version),
                    routing_available=routing_available,
                    loaded_available=loaded_available,
                    candidate_ready=candidate_ready,
                    candidate_resident=candidate_resident,
                ),
            }
        )
    return vectors


def _canonicalize(vector: dict[str, Any]) -> dict[str, Any]:
    """Collapse axes the taken branch does not read.

    ``main`` asserts every collapse is output-preserving before writing, so this
    is only a size optimization.
    """
    v = json.loads(json.dumps(vector))
    state = v["state"]
    control = v["control"]

    # ``canary_weight`` is read only in the canary branch.
    if control != "canary":
        v["canary_weight"] = None
    # ``from_version`` is read only by shadow/canary (the expected primary).
    if control not in ("shadow", "canary"):
        v["from_version"] = None
    # ``resident_versions`` is read only by unload.
    if control != "unload":
        state["resident_versions"] = [PREVIOUS]

    if control == "unload":
        # Unload is judged only on ``loaded_available`` and ``resident_versions``;
        # the routing view and the default are never consulted.
        state["default_version"] = None
        state["routing"] = []
        state["routing_available"] = True
        return v

    if not state["routing_available"]:
        # An unreadable routing view short-circuits _control_reflected, so
        # nothing downstream of it is read. ``loaded_available`` and
        # ``ready_versions`` stay free because _candidate_servable still runs.
        state["default_version"] = None
        state["routing"] = []
        v["canary_weight"] = None
        v["from_version"] = None
        return v

    if control == "canary":
        has_entry = any(
            e["role"] == "canary" and e["version"] == v["candidate"] for e in state["routing"]
        )
        if not has_entry:
            # No matching canary entry: the weight comparison is never reached.
            v["canary_weight"] = None

    return v


def _probe_vectors() -> list[dict[str, Any]]:
    """Vectors that keep an inert axis varying, on an otherwise converged state.

    Canonicalization would erase these, and with them the evidence that the
    predicate ignores ``ready``, ``loaded_versions``, ``serving_versions``,
    ``resident_versions`` outside unload, and the routing view under unload.
    A refactor that starts reading one of them fails here.
    """
    probes: list[dict[str, Any]] = []
    converged = {
        "shadow": ("default_shadow", PREVIOUS, PREVIOUS, None),
        "canary": ("default_canary", PREVIOUS, PREVIOUS, CANARY_W),
        "full_switch": ("default", CANDIDATE, None, None),
        "unload": ("default", PREVIOUS, None, None),
    }
    axes = list(
        itertools.product(
            (False, True),  # ready (service-wide)
            ([PREVIOUS, CANDIDATE], []),  # loaded_versions
            ([], [CANDIDATE]),  # serving_versions
            (True, False),  # candidate resident
            (True, False),  # routing_available
        )
    )
    for control, (shape, default_version, from_version, canary_weight) in converged.items():
        for ready, loaded_versions, serving_versions, resident, routing_available in axes:
            probes.append(
                {
                    "control": control,
                    "candidate": CANDIDATE,
                    "from_version": from_version,
                    "canary_weight": canary_weight,
                    "state": _state_dict(
                        default_version=default_version,
                        routing=_routing(shape, default_version),
                        routing_available=routing_available,
                        loaded_available=True,
                        candidate_ready=True,
                        candidate_resident=resident,
                        ready=ready,
                        loaded_versions=list(loaded_versions),
                        serving_versions=list(serving_versions),
                    ),
                }
            )
    return probes


# ------------------------------------------------------------------- evaluate
def evaluate(vector: dict[str, Any]) -> dict[str, bool]:
    """Run the predicate pair. Shared with the replay test."""
    state = build_state(vector["state"])
    reflected = _control_reflected(
        vector["control"],
        vector["candidate"],
        state,
        from_version=vector["from_version"],
        canary_weight=vector["canary_weight"],
    )
    servable = _candidate_servable(vector["control"], vector["candidate"], state)
    return {
        "reflected": bool(reflected),
        "servable": bool(servable),
        # The composite the convergence read branches on, frozen explicitly.
        "converged": bool(reflected and servable),
    }


# --------------------------------------------------------------------- output
def canonical_key(vector: dict[str, Any]) -> str:
    """Stable serialization of a vector's inputs. Also the dedup key."""
    return json.dumps(vector, sort_keys=True, separators=(",", ":"))


def vector_id(vector: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_key(vector).encode()).hexdigest()[:16]


def _dump(path: Path, payload: dict[str, Any], *, rows_key: str) -> None:
    """Write stable JSON: a scalar header, then one line per row.

    One compact line per row keeps ``git diff`` readable when a single vector
    changes.
    """
    header = {k: v for k, v in payload.items() if k != rows_key}
    lines = [json.dumps(row, sort_keys=True, separators=(",", ":")) for row in payload[rows_key]]
    body = ",\n    ".join(lines)
    head = ",\n  ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in header.items())
    text = "{\n  " + head + f",\n  {json.dumps(rows_key)}: [\n    " + body + "\n  ]\n}\n"
    json.loads(text)  # never emit a file the replay test cannot read back
    path.write_text(text, encoding="utf-8")


def main() -> int:
    # The predicate logs as it goes; that would bury the summary.
    logging.disable(logging.CRITICAL)

    raw = _raw_product()

    # Check the pruning: a canonicalized point must answer exactly as the raw
    # point it stands in for.
    pruned: dict[str, dict[str, Any]] = {}
    for vector in raw:
        canonical = _canonicalize(vector)
        raw_out = evaluate(vector)
        canonical_out = evaluate(canonical)
        if raw_out != canonical_out:
            print(
                "PRUNE UNSOUND -- canonicalization changed the answer:\n"
                f"  raw       {canonical_key(vector)}\n  ->        {raw_out}\n"
                f"  canonical {canonical_key(canonical)}\n  ->        {canonical_out}",
                file=sys.stderr,
            )
            return 1
        pruned[canonical_key(canonical)] = canonical

    for probe in _probe_vectors():
        pruned.setdefault(canonical_key(probe), probe)

    ordered = sorted(pruned.values(), key=lambda v: (v["control"], canonical_key(v)))
    vectors = []
    seen_ids: set[str] = set()
    for index, vector in enumerate(ordered):
        vid = vector_id(vector)
        if vid in seen_ids:  # pragma: no cover - 64-bit prefix collision
            print(f"vector id collision: {vid}", file=sys.stderr)
            return 1
        seen_ids.add(vid)
        vectors.append({"id": vid, "index": index, "inputs": vector, "expected": evaluate(vector)})

    _dump(
        CONVERGENCE_PATH,
        {
            "schema_version": SCHEMA_VERSION,
            "frozen_from": "src/core/manager.py::_control_reflected,_candidate_servable",
            "raw_product_size": len(raw),
            "count": len(vectors),
            "vectors": vectors,
        },
        rows_key="vectors",
    )

    print(f"raw product    : {len(raw)}")
    print(f"frozen vectors : {len(vectors)} -> {CONVERGENCE_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

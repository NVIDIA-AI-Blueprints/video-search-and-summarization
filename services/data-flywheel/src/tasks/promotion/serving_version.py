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

"""DFW-owned serving-version counter (F3).

The serving/Triton version is a monotonic **integer** minted by DFW in Mongo —
NOT the MLflow version number. This keeps the version authority inside DFW's
system of record (Mongo), so a promotion can proceed even when MLflow is
disabled or unreachable (MLflow stays best-effort; see MR !550's promotion-record
fallback). Each model has its own counter document keyed by ``_id == model_name``
in the ``serving_version_counters`` collection.

``find_one_and_update`` with ``$inc`` is atomic server-side, so concurrent
allocations never collide. Gaps are acceptable (a rare retry of the register
stage may skip an integer): the only invariant is that versions are monotonic
and unique per model.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pymongo import ReturnDocument
from pymongo.database import Database

from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.promotion.serving_version")

#: Day-0 floor, used ONLY when the serving stack is reachable and reports it
#: holds nothing. A fresh AOI deployment seeds the incumbent as version 1 (the
#: model-repo seed writes ``changenet_classify/1/``), so the first candidate is
#: 2. This is never used to paper over an unknown stack — see
#: :class:`ServingVersionUnknownError`.
_BASELINE_SERVING_VERSION = 1


class ServingVersionUnknownError(RuntimeError):
    """Raised when no floor can be established from any authority.

    Only possible when DFW has never minted for this model AND the serving stack
    could not be read. Minting a guess there is how a candidate lands on top of a
    live version: the stage silently overwrites the incumbent's weights on disk
    and the routing apply is then rejected ``model_routing_duplicate_target``.

    Over-estimating a version costs a gap in the sequence, which nothing cares
    about. Under-estimating costs the incumbent's weights. The asymmetry is why
    this fails closed rather than defaulting.
    """


@dataclass(frozen=True)
class ObservedServing:
    """What the serving stack reports it holds, when it could be asked.

    ``floor`` is the highest version present in the model repository, or ``None``
    when the stack is reachable and genuinely holds nothing. The distinction that
    matters is at the call site: passing ``None`` *instead of* this object means
    "could not find out", which is not the same as "holds nothing".
    """

    floor: int | None


def next_serving_version(
    db: Database, model_name: str, *, observed: ObservedServing | None = None
) -> int:
    """Atomically mint the next serving/Triton integer for a model.

    Returns a version strictly greater than every version any authority can show
    is already taken. Two authorities are combined, and the higher wins:

    * the Mongo counter — what DFW has already handed out, including versions
      minted but not yet staged;
    * ``observed`` — what the live serving stack reports it holds, including
      versions that exist in the model repo but are currently unloaded.

    **Why the counter alone is not enough.** It records what *this* DFW minted,
    not what the stack holds. Point a fresh DFW at an already-promoted stack — a
    new install, a restored or wiped Mongo, a second DFW instance, a hand-seeded
    model repo — and the counter is absent while the incumbent is already at
    version N.

    **Why the live read alone is not enough.** The stack may be unreachable, and
    a version may be minted before it is staged, so the counter remains the
    durable allocator. Taking the max is monotonic from either side.

    **Why not read the routed version.** Routing names at most three versions
    (primary, shadow/canary); the repo can hold many. After a rollback the routed
    version goes *down* while the retired candidate stays on disk — a floor taken
    from routing would then re-mint straight onto it. Only the repo-wide view is
    safe, which is what ``observed`` carries.

    Args:
        db: the flywheel database.
        model_name: the serving model whose counter is being advanced.
        observed: what the stack reports, or ``None`` for "could not find out".

    Raises:
        ServingVersionUnknownError: nothing has been minted for this model and the
            stack could not be read, so no floor is trustworthy.
    """
    floor = _BASELINE_SERVING_VERSION
    if observed is not None and observed.floor is not None:
        floor = max(floor, int(observed.floor))

    pipeline = [{"$set": {"seq": {"$add": [{"$max": [{"$ifNull": ["$seq", floor]}, floor]}, 1]}}}]

    if observed is None:
        # The stack is unknown, so only an existing counter can justify a number.
        # No upsert: if there is no counter this must fail, not invent one.
        doc = db.serving_version_counters.find_one_and_update(
            {"_id": model_name}, pipeline, return_document=ReturnDocument.AFTER
        )
        if doc is None:
            raise ServingVersionUnknownError(
                f"No serving-version counter for {model_name!r} and the serving stack "
                "could not be read; refusing to guess a version that may already be "
                "live. Fix connectivity to the deployment backend, or seed the "
                "counter with the incumbent version "
                f'(serving_version_counters: {{"_id": "{model_name}", "seq": <N>}}).'
            )
        return doc["seq"]

    doc = db.serving_version_counters.find_one_and_update(
        {"_id": model_name}, pipeline, upsert=True, return_document=ReturnDocument.AFTER
    )
    return doc["seq"]


def observed_serving_floor(versions: Iterable[Any] | None) -> int | None:
    """Highest numeric version among ``versions``, or ``None`` if there is none.

    ``versions`` is every version the serving stack reports it holds for a model
    — the *whole* model repo, not just what is loaded. A version directory that
    exists but is unloaded still owns its slot: staging into it overwrites the
    weights that are there.

    Versions are strings on the wire and a deployment is free to use ones that
    are not integers (``"1.0.0"``, ``"prod"``). Those are skipped rather than
    guessed at: they cannot participate in integer arithmetic, and coercing them
    would invent a floor. When nothing numeric is present the answer is ``None``
    — "I could not find out" — which leaves the caller on its other floors
    rather than lowering them.
    """
    best: int | None = None
    for raw in versions or ():
        try:
            value = int(str(raw).strip())
        except (TypeError, ValueError):
            logger.debug("Ignoring non-integer serving version %r for the floor", raw)
            continue
        if best is None or value > best:
            best = value
    return best

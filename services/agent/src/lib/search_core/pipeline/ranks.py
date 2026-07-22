# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Frozen ``Ranks``/``Hit`` values and the ``|``/``.pipe()`` chaining seam.

``Ranks`` is the value that flows between pipeline stages. Every stage is a
pure function ``Ranks -> Ranks``: it never mutates its input and returns a new
value, so any intermediate result can be held, inspected, or re-piped safely.

A :class:`Hit` carries a *map of named scores* rather than a single number
(``{"embed": .83, "attribute": .42, "fusion": .031}``). Scorers append keys;
rankers reorder using them. ``Ranks.score_key`` names the score that currently
defines the ordering — the one exported as ``SearchResult.similarity`` at the
facade boundary.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field
from dataclasses import replace
from typing import TYPE_CHECKING
from typing import Any

if TYPE_CHECKING:
    from collections.abc import Iterable
    from collections.abc import Mapping

# Identity of a hit inside one Ranks value: the same clip window from the same
# source — carrying the same tracked objects — is the same candidate regardless
# of which retrieval leg found it. Object ids participate so distinct objects
# observed in one shared window (object-mode behavior hits) never collapse.
HitKey = tuple[str, str, str, str, tuple[str, ...]]

Stage = Callable[["Ranks"], "Ranks"]


@dataclass(frozen=True)
class Hit:
    """One ranked candidate: a clip window plus its named scores.

    Frozen by contract: stages build modified copies via :meth:`with_scores` /
    :func:`dataclasses.replace`, never assignment. ``scores`` is stored as a
    plain dict for ergonomics but must be treated as immutable — always pass a
    fresh dict when constructing.
    """

    video_name: str
    description: str = ""
    start_time: str = ""
    end_time: str = ""
    sensor_id: str = ""
    screenshot_url: str = ""
    object_ids: tuple[str, ...] = ()
    scores: Mapping[str, float] = field(default_factory=dict)

    def key(self) -> HitKey:
        """Union-by-identity key: same source + window + objects == same candidate."""
        return (self.video_name, self.sensor_id, self.start_time, self.end_time, self.object_ids)

    def score(self, key: str, default: float = 0.0) -> float:
        """Read one named score, defaulting instead of raising."""
        return float(self.scores.get(key, default))

    def with_scores(self, **scores: float) -> Hit:
        """Return a copy with the given named scores added.

        Existing keys are kept at their **maximum** — score maps are
        append-only from the chain's point of view, and when two retrievals of
        the same leg kind disagree the stronger evidence wins deterministically.
        """
        merged = dict(self.scores)
        for name, value in scores.items():
            merged[name] = max(float(value), merged[name]) if name in merged else float(value)
        return replace(self, scores=merged)


def merge_hits(existing: Hit, incoming: Hit) -> Hit:
    """Merge two hits that share an identity key (union-by-identity).

    Score maps merge via :meth:`Hit.with_scores` (append-only, max on
    conflict); ``object_ids`` union preserving first-seen order; the existing
    hit's metadata wins, with the incoming hit filling gaps (empty
    ``screenshot_url``/``description``).
    """
    merged = existing.with_scores(**dict(incoming.scores))
    seen = set(existing.object_ids)
    object_ids = existing.object_ids + tuple(oid for oid in incoming.object_ids if oid not in seen)
    return replace(
        merged,
        object_ids=object_ids,
        screenshot_url=existing.screenshot_url or incoming.screenshot_url,
        description=existing.description or incoming.description,
    )


@dataclass(frozen=True)
class Ranks:
    """The pipeline value: ordered hits, per-leg provenance, diagnostics.

    ``legs`` records each retrieval leg's own ordering (identity keys, best
    first) — rank-based fusion needs per-leg *ranks*, not just scores.
    ``messages`` accumulates operator-visible degradation notes (append-only);
    it is a fixed diagnostic channel, not an open context bag.
    ``score_key`` names the score that currently defines ``hits`` ordering.
    """

    hits: tuple[Hit, ...] = ()
    legs: Mapping[str, tuple[HitKey, ...]] = field(default_factory=dict)
    messages: tuple[str, ...] = ()
    score_key: str = ""

    @staticmethod
    def empty() -> Ranks:
        """The chain seed: no hits, no legs, no messages."""
        return Ranks()

    def pipe(self, fn: Callable[..., Ranks], /, *args: Any, **kwargs: Any) -> Ranks:
        """Apply one stage: ``ranks.pipe(stage)`` == ``stage(ranks)``."""
        return fn(self, *args, **kwargs)

    def __or__(self, fn: Callable[[Ranks], Ranks]) -> Ranks:
        """Chain sugar: ``ranks | stage`` == ``stage(ranks)``."""
        return fn(self)

    # ------------------------------------------------------------- helpers

    def with_hits(self, hits: Iterable[Hit], *, score_key: str | None = None) -> Ranks:
        """Copy with a new hit tuple (and optionally a new current score key)."""
        return replace(
            self,
            hits=tuple(hits),
            score_key=self.score_key if score_key is None else score_key,
        )

    def with_message(self, message: str) -> Ranks:
        """Copy with one diagnostic message appended."""
        return replace(self, messages=(*self.messages, message))

    def union(self, name: str, incoming: Iterable[Hit]) -> Ranks:
        """Union-append one retrieval leg's hits (the ``retrieve`` semantics).

        New candidates append in leg order; a candidate already present (same
        :meth:`Hit.key`) merges via :func:`merge_hits` instead of duplicating.
        The leg's own ordering is recorded under ``legs[name]``.
        """
        by_key: dict[HitKey, int] = {hit.key(): idx for idx, hit in enumerate(self.hits)}
        merged: list[Hit] = list(self.hits)
        leg_order: list[HitKey] = []
        for hit in incoming:
            key = hit.key()
            leg_order.append(key)
            if key in by_key:
                merged[by_key[key]] = merge_hits(merged[by_key[key]], hit)
            else:
                by_key[key] = len(merged)
                merged.append(hit)
        legs = dict(self.legs)
        legs[name] = tuple(leg_order)
        return replace(self, hits=tuple(merged), legs=legs)

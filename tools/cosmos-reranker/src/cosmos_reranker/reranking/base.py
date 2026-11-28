# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reranker interface and the no-op baseline reranker.

A *reranker* is the optional stage-2 component. It receives the stage-1
candidate list for a query (segments + their embedder scores, plus enough
context to re-examine the underlying video) and returns a new ordering.

For the embedder-only baseline we use :class:`IdentityReranker`, which preserves
the stage-1 ordering. Adding a real reranker (e.g. a cross-encoder or a VLM
judge) means implementing :meth:`Reranker.rerank` and registering it -- nothing
else in the pipeline changes.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path

from tqdm import tqdm

from .data import Segment


@dataclass
class Candidate:
    """A single stage-1 retrieval result handed to the reranker."""

    segment: Segment
    stage1_score: float
    #: Path to the source video, so media-based rerankers can re-read the
    #: segment's frames (the embedder discards them after encoding).
    video_path: Path | None = None
    #: Optional direct image path for image-retrieval tasks such as PAS. When
    #: present, image-capable rerankers can score this image directly instead
    #: of reading the video_path/segment.
    image_path: Path | None = None
    #: Stage-2 score (ranking key); None until scored. Higher = more relevant.
    rerank_score: float | None = None
    #: Free-form stage-2 diagnostics for analysis (raw text, trace, logprobs);
    #: never used for ranking.
    rerank_meta: dict | None = None


@dataclass
class CandidateScore:
    """A stage-2 score for one candidate. ``score=None`` marks a scoring failure
    (the candidate falls back to its stage-1 position). ``meta`` is logged only."""

    score: float | None
    meta: dict | None = None


class Reranker(abc.ABC):
    """Re-orders stage-1 candidates for a query."""

    name: str = "reranker"

    @abc.abstractmethod
    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        """Return ``candidates`` in the new (best-first) order."""

    def rerank_batch(
        self, queries: list[str], candidates_list: list[list[Candidate]]
    ) -> list[list[Candidate]]:
        """Rerank many queries. Default: per-query; overridden for batched scoring."""
        return [self.rerank(q, c) for q, c in zip(queries, candidates_list)]


class IdentityReranker(Reranker):
    """Pass-through reranker: keeps the embedder ordering (baseline)."""

    name = "identity"

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        return candidates


class ScoringReranker(Reranker):
    """Pointwise reranker: score each candidate, then reorder the top-N.

    Only the top ``rerank_depth`` stage-1 candidates are scored; the rest keep
    their stage-1 order appended after, so metrics beyond the depth (e.g.
    Recall@50 with depth 20) are unchanged. Ordering is deterministic, breaking
    ties by stage-1 score then segment id. Failed candidates (score None) keep
    their stage-1 position below the scored ones; failures are counted in
    :attr:`last_failures`.

    Subclasses implement :meth:`score_pairs` over ``(query, candidate)`` pairs;
    :meth:`rerank_batch` flattens pairs across queries and scores them in chunks
    so GPU backends (vLLM) stay saturated instead of seeing one query at a time.
    """

    name = "scoring"

    def __init__(self, rerank_depth: int = 50, score_chunk_size: int = 256) -> None:
        self.rerank_depth = rerank_depth
        self.score_chunk_size = score_chunk_size
        self.last_failures = 0

    @abc.abstractmethod
    def score_pairs(self, pairs: list[tuple[str, Candidate]]) -> list[CandidateScore]:
        """Return one :class:`CandidateScore` per ``(query, candidate)`` pair."""

    @staticmethod
    def _order(head: list[Candidate], results: list[CandidateScore]) -> tuple[list[Candidate], int]:
        if len(results) != len(head):
            raise ValueError(f"got {len(results)} scores for {len(head)} candidates")
        for cand, res in zip(head, results):
            cand.rerank_score = None if res.score is None else float(res.score)
            cand.rerank_meta = res.meta
        fails = sum(1 for c in head if c.rerank_score is None)

        # Scored first (by score), failed after (stage-1 order); deterministic ties.
        def key(c: Candidate) -> tuple[bool, float, float, int]:
            return (
                c.rerank_score is not None,
                c.rerank_score or 0.0,
                c.stage1_score,
                -c.segment.segment_id,
            )

        return sorted(head, key=key, reverse=True), fails

    def _split(self, candidates: list[Candidate]) -> tuple[list[Candidate], list[Candidate]]:
        depth = self.rerank_depth if self.rerank_depth > 0 else len(candidates)
        return candidates[:depth], candidates[depth:]

    def rerank(self, query: str, candidates: list[Candidate]) -> list[Candidate]:
        self.last_failures = 0
        if not candidates:
            return candidates
        head, tail = self._split(candidates)
        results = self.score_pairs([(query, c) for c in head])
        ordered, self.last_failures = self._order(head, results)
        return ordered + tail

    def rerank_batch(
        self, queries: list[str], candidates_list: list[list[Candidate]]
    ) -> list[list[Candidate]]:
        heads, tails = (
            zip(*(self._split(c) for c in candidates_list)) if candidates_list else ([], [])
        )
        pairs = [(q, c) for q, head in zip(queries, heads) for c in head]

        scores: list[CandidateScore] = []
        chunk_size = max(1, int(self.score_chunk_size))
        chunk_starts = range(0, len(pairs), chunk_size)
        total_chunks = (len(pairs) + chunk_size - 1) // chunk_size if pairs else 0
        for i in tqdm(
            chunk_starts,
            total=total_chunks,
            desc=f"{self.name} scoring",
            unit="chunk",
            leave=False,
        ):
            scores.extend(self.score_pairs(pairs[i : i + chunk_size]))

        out, total_fail, idx = [], 0, 0
        for head, tail in zip(heads, tails):
            ordered, fails = self._order(list(head), scores[idx : idx + len(head)])
            idx += len(head)
            total_fail += fails
            out.append(ordered + list(tail))
        self.last_failures = total_fail
        return out

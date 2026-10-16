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

"""The scoring-backend contract.

A scoring backend turns a :class:`~flows.artifacts.RetrievalArtifact` into a
:class:`~flows.artifacts.ScoredResult`. Two models exist:

* ``segment-overlap`` -- the eval's existing model: ground truth is a list of
  ``{video_name, start_time, end_time}`` segments per query; a hit scores by
  half-open timestamp overlap (``flows.metrics``).
* ``clip-relevance`` -- the drive script's model (Phase 4): ground truth is a
  gallery of ``chunk_id`` clips plus per-query ``relevant_clip_ids``; a hit
  scores by rank against that relevance.

Scoring reads only the artifact and the ground truth -- never the deployment --
so the same artifact can be re-scored under either model. Each backend declares
the ground truth it needs via :meth:`load_ground_truth`, and ``run_score`` reads
``scoring.model`` off the result to know which shape the summary is in.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from flows.artifacts import RetrievalArtifact, RunConfig, ScoredResult


@runtime_checkable
class ScoringBackend(Protocol):
    """How a retrieval artifact becomes a scored result."""

    #: The value ``--scoring`` and ``scoring.model`` carry.
    name: str

    def load_ground_truth(self, cfg: RunConfig, artifact: RetrievalArtifact) -> Any:
        """Load this model's ground truth.

        ``segment-overlap`` reads the eval dataset the artifact ran against;
        ``clip-relevance`` reads a separate gallery GT (``--gallery-gt``).
        """
        ...

    def score(
        self, artifact: RetrievalArtifact, ground_truth: Any, cfg: RunConfig
    ) -> ScoredResult:
        """Score every answered query in the artifact against ``ground_truth``.

        Returns the :class:`ScoredResult` (summary / config / flow /
        query_results); writing the file is the stage's job, not the backend's.
        """
        ...


def report_unanswered(
    unanswered: list[dict[str, Any]],
    total_queries: int,
    tolerate_unanswered: int,
    scored_count: int,
) -> None:
    """Warn loudly about unanswered queries and abort when they exceed the tolerance.

    Shared by every scoring backend, so a partial run can never publish
    full-looking numbers quietly, whichever metric model produced them. This is
    the same contract the segment-overlap runner always had.
    """
    if not unanswered:
        return
    print("\n" + "!" * 60)
    print(f"WARNING: {len(unanswered)}/{total_queries} queries were never answered.")
    for entry in unanswered[:10]:
        print(f'  [{entry["index"]}] {entry["query"][:60]!r}: {entry["reason"][:120]}')
    if len(unanswered) > 10:
        print(f"  ... and {len(unanswered) - 10} more")
    print("!" * 60 + "\n")
    if len(unanswered) > tolerate_unanswered:
        raise SystemExit(
            f"ABORTED: {len(unanswered)} unanswered query/queries exceeds "
            f"--tolerate-unanswered {tolerate_unanswered}.\n"
            "  They are excluded from the metrics either way, so the numbers "
            "below would describe\n"
            f"  {scored_count} queries under a {total_queries}-query label. "
            "Raise the tolerance to accept that."
        )

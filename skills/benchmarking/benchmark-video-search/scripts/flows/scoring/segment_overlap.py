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

"""The eval's existing scoring model, as a pluggable backend.

Ground truth is a list of ``{video_name, start_time, end_time}`` segments per
query; a retrieved window scores by half-open timestamp overlap. All the metric
math delegates to :mod:`flows.metrics` (the vendored, drift-guarded module), so
this backend changes no numbers -- it is the same scorer the monolith ran,
exposed behind the :class:`~flows.scoring.base.ScoringBackend` contract.

This backend leaves ``ScoredResult.scoring`` as ``None``: its output is the
legacy shape byte-for-byte, and a missing ``scoring.model`` is what tells a
reader "this is a segment-overlap result". ``clip-relevance`` sets it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import flows
from flows.artifacts import RetrievalArtifact, RunConfig, ScoredResult

from .base import report_unanswered
from .summarize import _print_summary, _summarize


class SegmentOverlapScoring:
    """Score retrieved windows by timestamp overlap against segment ground truth."""

    name = "segment-overlap"

    def load_ground_truth(
        self, cfg: RunConfig, artifact: RetrievalArtifact
    ) -> dict[str, Any]:
        """Read the eval dataset the artifact ran against (its segment annotations).

        The dataset identity is intrinsic to the artifact, so it is the source of
        truth; only ``--data-dir`` may legitimately differ on the scoring host.
        """
        if artifact.query_source == "gallery":
            raise SystemExit(
                "This retrieval artifact was produced against a clip gallery "
                "(--gallery-gt), not an eval dataset -- segment-overlap has no "
                "segment ground truth for it. Score it with --scoring clip-relevance "
                "or --scoring drive."
            )
        dataset = cfg.dataset or artifact.dataset
        subset = cfg.subset if cfg.subset is not None else artifact.subset
        data_dir = Path(cfg.data_dir) if cfg.data_dir else Path(artifact.data_dir)
        data = flows.load_dataset_file(data_dir, dataset, subset)
        annotations, _decompositions = flows.unpack_dataset(data)
        return {"annotations": annotations, "dataset": dataset, "subset": subset}

    def score(
        self, artifact: RetrievalArtifact, ground_truth: dict[str, Any], cfg: RunConfig
    ) -> ScoredResult:
        annotations = ground_truth["annotations"]
        dataset = ground_truth["dataset"]
        subset = ground_truth["subset"]

        print(f"\n{'=' * 60}")
        print("SCORING")
        print(f"{'=' * 60}")

        results: list[dict[str, Any]] = []
        answered = [r for r in artifact.queries if r.status == "answered"]
        # A3 transparency: a dense / full-gallery artifact returns more windows than
        # the scoring top_k. Slicing down must be visible, or the numbers read as if
        # the full retrieved set was scored.
        max_retrieved = max((len(r.hits) for r in answered), default=0)
        sliced_queries = sum(1 for r in answered if cfg.top_k and len(r.hits) > cfg.top_k)
        if sliced_queries:
            print(
                f"  note: segment-overlap scored the top {cfg.top_k} of up to "
                f"{max_retrieved} retrieved windows per query ({sliced_queries} sliced)"
            )
        for i, record in enumerate(answered, 1):
            expected = annotations[record.query]
            latency_s = record.search_latency_s
            # A3: cap the candidate set at the scoring top_k. A dense / full-gallery
            # artifact (clip-relevance retrieval) returns far more windows than the
            # baseline's top_k; scoring them all would collapse precision and make
            # the run incomparable. Slicing is a no-op for a normal top_k artifact.
            normalized = record.hits[: cfg.top_k] if cfg.top_k else record.hits
            result = flows.evaluate_query(
                record.query, flows.for_scoring(normalized), expected, latency_s
            )
            if record.decompose_s is not None:
                # Kept apart from latency_s: one is the LLM deciding what to search
                # for, the other is the search. Summing them silently would hide
                # which half a regression came from.
                result["decompose_s"] = round(record.decompose_s, 4)
                result["total_latency_s"] = round(record.decompose_s + latency_s, 4)
                result["decomposition"] = record.decomposition
            if record.timings:
                result["timings"] = record.timings
            kept, num_rejected = flows.filter_rejected(normalized)
            result["num_rejected"] = num_rejected
            result["critic_filtered"] = flows.evaluate_query(
                record.query, flows.for_scoring(kept), expected, latency_s
            )
            results.append(result)

            print(
                f'[{i}/{len(artifact.queries)}] "{record.query}"  ({len(expected)} ground truth segments)'
            )
            print(f"  {flows.format_inline(result, latency_s=latency_s)}")
            if num_rejected:
                print(
                    f"  [critic-filtered] rejected={num_rejected}  "
                    f"{flows.format_inline(result['critic_filtered'])}"
                )
            if result["missed_gt_indices"]:
                print(f"  Missed: {len(result['missed_gt_indices'])} segment(s)")
            print()

        if not results:
            raise SystemExit("ABORTED: no query produced a result.")

        # `total_queries` counts what was scored, so dropping failures quietly makes
        # a partial run look complete -- 50 unanswered out of 673 reports
        # `total_queries: 623` and reads as clean. Say it, loudly, and stop unless
        # the caller has said how many they will accept.
        unanswered = artifact.unanswered
        report_unanswered(
            artifact.unanswered, len(artifact.queries), artifact.tolerate_unanswered, len(results)
        )

        summary = _summarize(
            results,
            dataset=dataset,
            subset=subset,
            wall_clock_s=artifact.wall_clock_s,
            concurrency=cfg.concurrency,
            sources_seen=set(artifact.sources_seen),
            verdicts=artifact.verdicts,
            upload_stats=artifact.ingest.upload_stats,
            unanswered=unanswered,
            path_counts=(
                flows.path_distribution(artifact.executed_plans)
                if artifact.executed_plans
                else None
            ),
        )
        if sliced_queries:
            summary["candidate_budget"] = {
                "scored_top_k": cfg.top_k,
                "max_retrieved": max_retrieved,
                "queries_sliced": sliced_queries,
            }
        _print_summary(summary)

        described = artifact.described
        flow: dict[str, Any] = {
            "query": described,
            "ingest": artifact.ingest.ingest_description,
            "readiness": artifact.ingest.readiness,
            # None when the ingest flow proved indexing itself (a chunk count),
            # so a reader can tell "not checked here" from "checked, and empty".
            "index_probe": artifact.ingest.index_probe,
            "cleared": artifact.ingest.cleared,
            "inventory": artifact.inventory,
            # Phoenix is absent by construction on the CLI query path:
            # search_core and vss_cli carry no OpenTelemetry instrumentation,
            # and Phoenix spans came only from NAT's telemetry config.
            "phoenix": {
                "collected": False,
                "reason": (
                    "not collected by this script; unavailable on the CLI query "
                    "path because search_core/vss_cli emit no OTel spans"
                ),
            },
        }
        if artifact.ingest.upload_stats:
            flow["upload_stats"] = artifact.ingest.upload_stats

        config = {
            "dataset": dataset,
            "subset": subset or "default",
            "concurrency": cfg.concurrency,
            **{k: v for k, v in described.items() if k != "backend"},
        }

        return ScoredResult(
            summary=summary, config=config, flow=flow, query_results=results
        )

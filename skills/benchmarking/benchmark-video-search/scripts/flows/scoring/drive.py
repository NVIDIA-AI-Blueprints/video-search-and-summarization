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

"""Drive scoring backend -- the original Drive ``evaluate_domain_test.py`` metrics
applied to a full-gallery retrieval artifact.

This is the drive-script variant the eval set out to support: rather than
reimplement the metric math, it loads the original Drive scorer module
(``--drive-scorer-dir``, fingerprinted) and applies its exact functions, so the
numbers are comparable with the Drive release's own baselines.

Preconditions (enforced loudly, not silently graded):
* the retrieval artifact must be full-gallery (dense): every gallery clip returned
  exactly once per query -- a bounded top-k artifact raises instead.
* ``--gallery-gt`` (the release) and ``--ingestion-manifest`` (clip -> sensor) are
  required, and the manifest must map the gallery exactly.
"""

from __future__ import annotations

from typing import Any

from flows.artifacts import RetrievalArtifact, RunConfig, ScoredResult

from . import drive_scoring
from .base import effective_tolerance, report_unanswered


class DriveScoring:
    """Score a full-gallery retrieval artifact with the original Drive metrics."""

    name = "drive"

    def load_ground_truth(
        self, cfg: RunConfig, artifact: RetrievalArtifact
    ) -> dict[str, Any]:
        if artifact.query_source != "gallery":
            raise SystemExit(
                "--scoring drive needs a retrieval artifact produced against a clip "
                "gallery (run retrieve with --gallery-gt --full-gallery). This artifact "
                "is an eval-dataset (segment-overlap) run."
            )
        if not cfg.gallery_gt or not cfg.ingestion_manifest:
            raise SystemExit(
                "ERROR: --scoring drive requires --gallery-gt <release> and "
                "--ingestion-manifest <clip->sensor map>."
            )
        if cfg.pas_frame_supported:
            raise SystemExit(
                "ERROR: --pas-frame-supported is a clip-relevance ground-truth "
                "derivation; --scoring drive uses the Drive script's own eligibility "
                "and does not apply it."
            )
        # The Drive scorer module is loaded from --drive-scorer-dir (the directory
        # holding evaluate_domain_test.py); its own load_gt reads the release GT.
        # prepare() validates the gallery<->manifest identity mapping.
        context = drive_scoring.prepare(
            cfg.gallery_gt, cfg.ingestion_manifest, cfg.drive_scorer_dir
        )
        ids = [q.get("query_id") for q in context["gt"]["queries"]]
        if any(not isinstance(qid, str) or not qid for qid in ids) or len(ids) != len(
            set(ids)
        ):
            raise SystemExit("ERROR: gallery query IDs must be nonempty and unique")
        return context

    def score(
        self, artifact: RetrievalArtifact, ground_truth: dict[str, Any], cfg: RunConfig
    ) -> ScoredResult:
        gt = ground_truth["gt"]
        gallery = ground_truth["gallery"]
        scorer = ground_truth["scorer"]

        print(f"\n{'=' * 60}")
        print("DRIVE SCORING")
        print(f"{'=' * 60}")
        print(f"gallery:            {len(gallery)} clips")
        print(
            f"scorer:             {ground_truth['script']} (sha256 {ground_truth['script_sha256'][:12]}...)"
        )

        # Map the artifact's query text onto the GT query index.
        id_to_qi = {q["query_id"]: i for i, q in enumerate(gt["queries"])}
        from collections import Counter

        counts = Counter(q["query"] for q in gt["queries"])
        text_to_qi = {
            q["query"]: i
            for i, q in enumerate(gt["queries"])
            if counts[q["query"]] == 1
        }
        eligible, exclusions = drive_scoring.eligible_indices(gt, gallery)
        eligible_set = set(eligible)

        rows: list[dict[str, Any]] = []
        unanswered = 0
        eligible_failures = []
        excluded_failures = []
        excluded_count = 0
        for record in artifact.queries:
            query_id = getattr(record, "query_id", None)
            qi = (
                id_to_qi.get(query_id)
                if query_id is not None
                else text_to_qi.get(record.query)
            )
            if qi is None or gt["queries"][qi]["query"] != record.query:
                raise SystemExit(
                    f"ERROR: unknown or ambiguous artifact query {query_id or record.query!r}; stable query_id required for repeated texts"
                )
            if qi not in eligible_set:
                excluded_count += 1
                if record.status != "answered":
                    excluded_failures.append(
                        {
                            "index": record.index,
                            "query": record.query,
                            "reason": record.error or "unanswered",
                        }
                    )
                continue
            if record.status != "answered":
                unanswered += 1
                eligible_failures.append(
                    {
                        "index": record.index,
                        "query": record.query,
                        "reason": record.error or "unanswered",
                    }
                )
                continue
            row = drive_scoring.score_query(
                ground_truth, qi, record.hits, record.search_latency_s or 0.0
            )
            rows.append(row)
            print(
                f"  [{len(rows)}/{len(eligible)}] AP={row['ap']:.4f} rank={row['rank']}"
            )

        if not rows:
            raise SystemExit("ABORTED: no answered query could be scored.")
        report_unanswered(
            eligible_failures,
            len(artifact.queries) - excluded_count,
            effective_tolerance(cfg, artifact),
            len(rows),
        )

        agg = drive_scoring.aggregate_report(rows, gt, gallery, scorer, exclusions)
        drive_scoring.verify_scorer_unchanged(ground_truth)

        summary: dict[str, Any] = {
            "scorer": "drive",
            "scorer_sha256": ground_truth["script_sha256"],
            "gallery_size": len(gallery),
            "queries_scored": len(rows),
            "queries_unanswered": unanswered,
            "excluded_failures": excluded_failures,
            "tolerate_unanswered": effective_tolerance(cfg, artifact),
            **agg["overall"],
            "slices": agg["metrics"]["slices"],
        }
        _print_drive_summary(summary)

        config = {
            "dataset": artifact.dataset,
            "subset": artifact.subset or "default",
            "scoring": self.name,
            "gallery_gt": cfg.gallery_gt,
            "ingestion_manifest": cfg.ingestion_manifest,
            "drive_scorer_dir": cfg.drive_scorer_dir,
            "concurrency": cfg.concurrency,
            "ranking": "CLI response order",
        }
        flow = {
            "query": artifact.described,
            "ingest": artifact.ingest.ingest_description,
            "inventory": artifact.inventory,
        }
        return ScoredResult(
            summary=summary,
            config=config,
            flow=flow,
            query_results=rows,
            scoring={
                "model": self.name,
                "scorer_sha256": ground_truth["script_sha256"],
                "gallery_size": len(gallery),
                "note": (
                    "Original Drive evaluate_domain_test.py metrics (loaded + "
                    "fingerprinted) over a full-gallery CLI ranking, in returned "
                    "order. Comparable to the Drive release's baselines only when "
                    "the deployed embed model matches the gallery's encoder."
                ),
            },
        )


def _print_drive_summary(summary: dict[str, Any]) -> None:
    print(f"\n{'=' * 60}\nDRIVE SUMMARY (full gallery)\n{'=' * 60}")
    print(f"Gallery:            {summary['gallery_size']} clips")
    print(
        f"Queries scored:     {summary['queries_scored']}  (unanswered: {summary['queries_unanswered']})"
    )
    for key in (
        "mAP",
        "recall@1",
        "recall@5",
        "hit@1",
        "hit@5",
        "hit@10",
        "ndcg@10",
        "median_rank",
        "auc",
    ):
        if summary.get(key) is not None:
            print(f"{key + ':':<20}{summary[key]:.4f}")
    print(f"{'=' * 60}\n")

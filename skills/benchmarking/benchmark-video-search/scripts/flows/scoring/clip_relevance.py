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

"""Clip-relevance scoring backend -- the bundle's ``ClipScorer`` as a stage backend.

Ground truth is a clip gallery with per-query ``relevant_clip_ids`` (the Drive
release format), not segment timestamps. Each retrieved hit is resolved to a
gallery clip (by sensor_id / video_name), windows of one clip collapse to its
first occurrence, and relevance is binary. The scorer tracks gallery coverage
and ``mAP_scope`` (``full-gallery`` vs ``observed-ranking``) so a capped or
partial retrieval is reported as such, never silently graded as complete.

Needs ``--gallery-gt`` (the release) and ``--ingestion-manifest`` (the clip ->
sensor mapping recorded at ingest). The retrieval artifact should come from a
full-gallery (dense) run -- ``--full-gallery`` -- or coverage will be partial.
"""

from __future__ import annotations

from typing import Any

from flows.artifacts import RetrievalArtifact, RunConfig, ScoredResult

from . import gallery as gallery_mod
from .base import effective_tolerance, report_unanswered
from .clip_scoring import VERSION, ClipScorer, aggregate


class ClipRelevanceScoring:
    """Score a retrieval artifact by clip-level relevance against a gallery."""

    name = "clip-relevance"

    def load_ground_truth(
        self, cfg: RunConfig, artifact: RetrievalArtifact
    ) -> dict[str, Any]:
        if artifact.query_source != "gallery":
            raise SystemExit(
                "--scoring clip-relevance needs a retrieval artifact produced against "
                "a clip gallery (run retrieve with --gallery-gt --full-gallery). This "
                "artifact is an eval-dataset (segment-overlap) run."
            )
        if not cfg.gallery_gt or not cfg.ingestion_manifest:
            raise SystemExit(
                "ERROR: --scoring clip-relevance requires --gallery-gt <release> and "
                "--ingestion-manifest <clip->sensor map>."
            )
        return gallery_mod.build_context(
            cfg.gallery_gt,
            cfg.ingestion_manifest,
            pas_frame_supported=cfg.pas_frame_supported,
        )

    def score(
        self, artifact: RetrievalArtifact, ground_truth: dict[str, Any], cfg: RunConfig
    ) -> ScoredResult:
        gt = ground_truth
        scorer = ClipScorer(gt["mapping"], gt["queries"])

        print(f"\n{'=' * 60}")
        print("CLIP-RELEVANCE SCORING")
        print(f"{'=' * 60}")
        print(f"gallery:            {len(gt['gallery'])} clips")
        print(f"queries in GT:      {len(gt['queries'])}")
        if gt.get("cohort") is not None:
            cohort = gt["cohort"]
            print(f"PAS protocol:       {cohort['protocol']}")
            print(f"cohort input:       {cohort['input']}")
            print(f"cohort eligible:    {cohort['eligible']}")
            print(f"cohort excluded:    {len(cohort['excluded'])} PAS query/queries")

        pairs: list[dict[str, Any]] = []
        unanswered = 0
        excluded_count = 0
        excluded_failures = []
        eligible_failures = []
        for record in artifact.queries:
            q = gallery_mod.resolve_query(record, gt)
            if q is None:
                excluded_count += 1
                if record.status != "answered":
                    excluded_failures.append(
                        {
                            "index": record.index,
                            "query": record.query,
                            "query_id": getattr(record, "query_id", None),
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
                        "query_id": q["query_id"],
                        "reason": record.error or "unanswered",
                    }
                )
                continue
            pair = scorer.pair(q, record.hits, record.search_latency_s or 0.0)
            if record.selected_hits is not None:
                selected = scorer.pair(q, record.selected_hits, record.search_latency_s or 0.0)
                pair["raw_returned"] = scorer.score(q, record.hits[:record.selection["requested_top_k"]], record.search_latency_s or 0.0)
                pair["source_selected"] = selected
                pair["selection"] = record.selection
            pairs.append(pair)
            raw = pair["raw"]
            print(
                f"  {q.get('query_id', record.query)}: AP={raw['average_precision']:.4f} "
                f"clips={raw['unique_clips']}/{len(gt['gallery'])} "
                f"coverage={raw['gallery_coverage']:.2f}"
            )

        if not pairs:
            raise SystemExit("ABORTED: no answered query could be scored.")
        report_unanswered(
            eligible_failures,
            len(artifact.queries) - excluded_count,
            effective_tolerance(cfg, artifact),
            len(pairs),
        )

        raws = [p["raw"] for p in pairs]
        filtereds = [p["critic_filtered"] for p in pairs]
        any_critic = any(p["verification_coverage"]["attempted"] for p in pairs)

        def grouped(field, rows):
            groups = {}
            for row in rows:
                groups.setdefault(str(row.get(field, "unknown")), []).append(row)
            return {
                key: {"queries_scored": len(group), **aggregate(group)}
                for key, group in sorted(groups.items())
            }

        summary: dict[str, Any] = {
            "scorer": VERSION,
            "scenario": artifact.described.get("scenario", "legacy-single-run"),
            "coverage": {
                "annotation_gallery_size": len(gt["gallery"]),
                "annotation_complete_rankings": sum(
                    r["ranking_complete"] for r in raws
                ),
                "indexed_inventory": artifact.inventory,
                "note": "gallery coverage measures returned clip identities; index inventory is separate evidence, not an exhaustiveness claim",
            },
            "gallery_size": len(gt["gallery"]),
            "queries_scored": len(pairs),
            "queries_unanswered": unanswered,
            "excluded_failures": excluded_failures,
            "tolerate_unanswered": effective_tolerance(cfg, artifact),
            "raw": aggregate(raws),
            "groups": {
                "domain": grouped("query_domain", raws),
                "event_slice": grouped(
                    "slice", [r for r in raws if r["query_domain"] == "event"]
                ),
                "pas_difficulty": grouped(
                    "difficulty", [r for r in raws if r["query_domain"] == "pas"]
                ),
            },
            "denominators": {
                "artifact_queries": len(artifact.queries),
                "eligible_queries": len(gt["queries"]),
                "answered_scored": len(pairs),
                "unanswered": unanswered,
                "excluded_artifact_queries": excluded_count,
            },
            "verification_coverage": {
                key: sum(p["verification_coverage"][key] for p in pairs)
                for key in ("candidate_hits", "attempted", "decided", "unverified")
            },
            "candidate_pool": "unchanged saved retrieval hits; filter declared pool then take metric cutoff; no further retrieval",
        }
        if gt.get("cohort") is not None:
            summary["cohort"] = gt["cohort"]
            summary["queries_excluded"] = excluded_count
        if any_critic:
            summary["critic_filtered"] = aggregate(filtereds)
            summary["critic_filtered_groups"] = {
                "domain": grouped("query_domain", filtereds),
                "event_slice": grouped(
                    "slice", [r for r in filtereds if r["query_domain"] == "event"]
                ),
                "pas_difficulty": grouped(
                    "difficulty", [r for r in filtereds if r["query_domain"] == "pas"]
                ),
            }
            summary["critic_effect"] = {
                "mAP_delta": summary["critic_filtered"]["mAP"] - summary["raw"]["mAP"],
                "mAP@10_delta": summary["critic_filtered"]["mAP@10"]
                - summary["raw"]["mAP@10"],
                "relevant_clips_removed": sum(
                    p["critic_effect"]["relevant_clips_removed"] for p in pairs
                ),
                "irrelevant_clips_removed": sum(
                    p["critic_effect"]["irrelevant_clips_removed"] for p in pairs
                ),
            }
        selections = [pair for pair in pairs if "source_selected" in pair]
        if selections:
            # Raw retrieval remains the complete original pool. The critic saw
            # only selected candidates, so do not label raw-pool metrics filtered.
            for key in ("critic_filtered", "critic_filtered_groups", "critic_effect"):
                summary.pop(key, None)
            summary["candidate_pool"] = "raw is the complete original ranking; source_selected is the separately declared verification pool; no retrieval or backfill after rejection"
            summary["raw_returned"] = aggregate([pair["raw_returned"] for pair in selections])
            def selection_groups(rows):
                return {
                    "domain": grouped("query_domain", rows),
                    "event_slice": grouped("slice", [row for row in rows if row["query_domain"] == "event"]),
                    "pas_difficulty": grouped("difficulty", [row for row in rows if row["query_domain"] == "pas"]),
                }
            summary["raw_returned_groups"] = selection_groups([pair["raw_returned"] for pair in selections])
            selected_pairs = [pair["source_selected"] for pair in selections]
            selected_summary = {
                "queries_scored": len(selections),
                "raw": aggregate([pair["raw"] for pair in selected_pairs]),
                "groups": selection_groups([pair["raw"] for pair in selected_pairs]),
                "verification_coverage": {
                    key: sum(pair["verification_coverage"][key] for pair in selected_pairs)
                    for key in ("candidate_hits", "attempted", "decided", "unverified")
                },
                "comparison_note": "Raw first-R and diverse first-R share one original ranking; only the diverse pool is verified. No matched-budget critic-effect claim.",
            }
            if any(pair["verification_coverage"]["attempted"] for pair in selected_pairs):
                selected_summary["critic_filtered"] = aggregate([pair["critic_filtered"] for pair in selected_pairs])
                selected_summary["critic_filtered_groups"] = selection_groups([pair["critic_filtered"] for pair in selected_pairs])
                selected_summary["critic_effect"] = {
                    "mAP_delta": selected_summary["critic_filtered"]["mAP"] - selected_summary["raw"]["mAP"],
                    "mAP@10_delta": selected_summary["critic_filtered"]["mAP@10"] - selected_summary["raw"]["mAP@10"],
                    "relevant_clips_removed": sum(pair["critic_effect"]["relevant_clips_removed"] for pair in selected_pairs),
                    "irrelevant_clips_removed": sum(pair["critic_effect"]["irrelevant_clips_removed"] for pair in selected_pairs),
                }
            summary["source_selected"] = selected_summary
            for pair in selections:
                pair["verification_coverage"] = pair["source_selected"]["verification_coverage"]
                pair["verification_pool"] = "source_selected"
                for key in ("critic_filtered", "critic_effect", "critic"):
                    pair.pop(key, None)
                if not pair["source_selected"]["verification_coverage"]["attempted"]:
                    pair["source_selected"].pop("critic_filtered", None)
                    pair["source_selected"].pop("critic_effect", None)
            summary["verification_coverage"] = {
                key: sum(pair["verification_coverage"][key] for pair in pairs)
                for key in ("candidate_hits", "attempted", "decided", "unverified")
            }
            summary["verification_pool"] = "source_selected" if len(selections) == len(pairs) else "per-query selected or original"
        _print_clip_summary(summary)

        config = {
            "dataset": artifact.dataset,
            "subset": artifact.subset or "default",
            "scoring": self.name,
            "gallery_gt": cfg.gallery_gt,
            "ingestion_manifest": cfg.ingestion_manifest,
            "pas_frame_supported": cfg.pas_frame_supported,
            "concurrency": cfg.concurrency,
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
            query_results=pairs,
            scoring={
                "model": self.name,
                "scorer_version": VERSION,
                "gallery_size": len(gt["gallery"]),
                "mAP_scope": summary["raw"].get("mAP_scope"),
                "note": (
                    "Clip-relevance ranks gallery clips by the retrieval's returned "
                    "order and scores binary relevance. Comparable to a Drive-script "
                    "run only when the deployed embed model matches the one the "
                    "gallery was embedded with."
                ),
            },
        )


def _print_clip_summary(summary: dict[str, Any]) -> None:
    raw = summary["raw"]
    print(f"\n{'=' * 60}\nCLIP-RELEVANCE SUMMARY\n{'=' * 60}")
    print(f"Gallery:            {summary['gallery_size']} clips")
    print(
        f"Queries scored:     {summary['queries_scored']}  (unanswered: {summary['queries_unanswered']})"
    )
    print(f"mAP scope:          {raw.get('mAP_scope')}")
    for key in ("mAP", "mAP@10", "nDCG", "nDCG@10", "MRR", "precision", "recall", "F1"):
        if raw.get(key) is not None:
            print(f"{key + ':':<20}{raw[key]:.4f}")
    for k in (1, 3, 5, 10):
        if raw.get(f"HIT@{k}") is not None:
            print(f"{'HIT@' + str(k) + ':':<20}{raw[f'HIT@{k}']:.4f}")
    if "critic_filtered" in summary:
        cf = summary["critic_filtered"]
        print("\nCritic-filtered:")
        for key in ("mAP", "MRR", "recall"):
            if cf.get(key) is not None:
                print(f"  {key + ':':<18}{cf[key]:.4f}")
    print(f"{'=' * 60}\n")

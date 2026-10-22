#!/usr/bin/env python3
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

"""Search eval against the new (and old) VSS search flows.

Standalone: this script and ``flows/`` import nothing from ``run_eval.py``, so
that script can be deleted without taking anything here with it. The metric
definitions were vendored unchanged rather than rewritten, and a test asserts
they still agree with ``run_eval.py`` for as long as both exist -- so numbers
stay comparable with baselines captured by the old runner.

    ingest:  legacy-put | agent-3step | vst-direct
    query:   cli                           (openclaw pending -- GAP-3)

Several defaults here look arbitrary and are load-bearing:

* ``--no-merge-adjacent`` is ON by default in this script, because upstream
  merging averages the scores of merged windows and changes the precision
  denominator. The historical baseline has unmerged windows; keeping them
  unmerged is what makes the comparison mean anything.
* Critic-filtered metrics are suppressed rather than zeroed when no
  verification block is present, so an unfiltered run can never be mistaken
  for a filtered one.
* CLI exits 2/3/4/5 abort the run instead of scoring 0.0.

Examples
--------
    # Defaults: vst-direct ingest + live decomposition + CLI queries.
    # Stored decompositions can pin routing for repeatable comparisons.
    python run_eval_flows.py --endpoint http://HOST:8000 --data-dir ~/datasets \
        --dataset warehouse --skip-download

    # Query only, against what is already indexed
    python run_eval_flows.py --endpoint http://HOST:8000 --data-dir ~/datasets \
        --dataset warehouse --skip-download --skip-ingest

    # Shared deployment: do not re-upload what is already registered
    python run_eval_flows.py --endpoint http://HOST:8000 \
        --skip-download --skip-existing

    # Show what would run, touching nothing
    python run_eval_flows.py --endpoint http://HOST:8000 --dry-run
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time  # noqa: F401  (re-exported patch target: tests monkeypatch rf.time.sleep)
from pathlib import Path
from typing import Any

import requests  # noqa: F401  (re-exported patch target: tests monkeypatch rf.requests.delete)

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import flows
from flows import pipeline
from flows.artifacts import RESULTS_DIR, IngestManifest, RunConfig
from flows.pipeline import (
    _probe_backend,
    clear_all_videos,
    describe_query_flow,
    expected_sources_from_upload,
    ingest_videos,
    probe_index_coverage,
    prune_foreign_videos,
)
from flows.preflight import (
    check_inputs_before_wipe,
    check_reused_index,
    dataset_video_names,
    decomposition_sources,
    file_sources,
    local_videos,
)
from flows.scoring import SCORING_BACKENDS
from flows.scoring.summarize import _print_summary, _summarize

# Backward-compatibility surface. The stage helpers moved to flows.pipeline (and
# the summary aggregation to flows.scoring.summarize) but are re-exported here
# because the test-suite both calls them on this module (rf.probe_index_coverage,
# rf._summarize, rf.describe_query_flow, ...) and monkeypatches the shared module
# objects it reaches through here (rf.time, rf.requests, rf.flows) -- those are
# the same module objects the moved code imports, so patching them here still
# takes effect. run_evaluation (below) keeps the pre-split single-call signature
# working. Listing the re-exports in __all__ marks them used; time/requests carry
# a noqa because they are patch targets rather than names this module calls.
__all__ = [
    "_print_summary",
    "_probe_backend",
    "_summarize",
    "check_inputs_before_wipe",
    "check_reused_index",
    "clear_all_videos",
    "dataset_video_names",
    "decomposition_sources",
    "describe_query_flow",
    "expected_sources_from_upload",
    "file_sources",
    "ingest_videos",
    "local_videos",
    "probe_index_coverage",
    "prune_foreign_videos",
    "run_evaluation",
]

# =============================================================================
# Backend construction
# =============================================================================


def build_ingest_backend(args: argparse.Namespace) -> Any:
    if args.ingest_flow == "legacy-put":
        return flows.LegacyPutIngest(args.endpoint)
    if args.ingest_flow == "agent-3step":
        return flows.AgentThreeStepIngest(
            args.endpoint,
            upload_timestamp=args.upload_timestamp,
            complete_retries=args.complete_retries,
            complete_backoff_s=args.complete_backoff,
        )
    if args.ingest_flow == "vst-direct":
        # Same VST origin the readiness poll and --only-dataset prune use, so a
        # run cannot upload to one host and check another.
        return flows.VstDirectIngest(
            args.vst_url or flows.vst_url_for(args.endpoint, args.vst_port),
            upload_timestamp=args.upload_timestamp,
        )
    raise SystemExit(f"Unknown ingest flow: {args.ingest_flow}")


def build_query_backend(args: argparse.Namespace) -> Any:
    if args.query_flow == "cli":
        # Cheapest check first: this one needs no subprocess and no network.
        # `attribute` and `fusion` declare --attribute as mandatory upstream,
        # so without it the run would reach the first query and abort on a
        # usage error (exit 2) -- correct, but only after building the CLI
        # environment and reconfiguring the deployment.
        if (
            args.search_path in ("attribute", "fusion")
            and not args.attribute
            and not args.decompositions
            and not getattr(args, "attribute_input_explicit", False)
        ):
            raise SystemExit(
                f"ERROR: --search-path {args.search_path} requires at least one --attribute.\n"
                "  Attributes are detectable properties ('white jacket', 'red hard hat'),\n"
                "  not generic nouns or actions -- those belong in the query.\n"
                "  Use --search-path embed for a text-only query."
            )

        # Discovery order: --vss-cmd, --vss-repo-root, the vendored submodule,
        # then `vss` on PATH. Both flags are optional when one of the last two
        # works, so the common case needs no CLI-location argument at all.
        try:
            vss_cmd, how = flows.resolve_vss_cmd(args.vss_cmd, args.vss_repo_root)
        except FileNotFoundError as e:
            raise SystemExit(f"ERROR: {e}") from e
        print(
            f"vss CLI:        {' '.join(vss_cmd)}\n                (resolved from {how})"
        )

        version = None
        if not args.skip_vss_preflight and not args.dry_run:
            # One subprocess now, instead of discovering a broken CLI on query
            # 1 of N. A stale venv exits 1 on every invocation.
            try:
                version = flows.preflight_vss_cmd(vss_cmd)
            except RuntimeError as e:
                raise SystemExit(f"ERROR: {e}") from e
            print(f"                {version}")

        if not args.skip_vss_configure and not args.dry_run:
            # The CLI reads ~/.vss/config.json, which is separate state from
            # --endpoint and points at a DIFFERENT port: it discovers services
            # by path prefix on the unified origin, which the agent port does
            # not route. Getting this wrong yields exit 4 on every query.
            base_url = args.vss_base_url or flows.vss_origin_for(
                args.endpoint, args.vss_origin_port
            )
            try:
                flows.ensure_vss_configured(vss_cmd, base_url)
            except Exception as e:
                raise SystemExit(
                    f"ERROR: vss configure failed for {base_url}: {e}"
                ) from e
        decompositions: dict[str, dict[str, Any]] = {}
        if args.decompositions:
            decompositions = flows.load_decompositions(args.decompositions)
            print(
                f"decompositions: {len(decompositions)} loaded from {args.decompositions}"
            )

        # `--original-query` is newer than most deployed CLIs, and an unknown
        # option exits 2 -- fatal, on query 1. Probe rather than assume: one
        # --help beats aborting a run that would otherwise have completed with
        # slightly worse verification.
        pass_original_query = not args.no_original_query
        if (
            pass_original_query
            and not args.dry_run
            and not flows.cli_supports_flag(vss_cmd, "--original-query")
        ):
            pass_original_query = False
            print(
                "WARNING: this vss CLI has no --original-query, so the critic will "
                "verify a question\n"
                "         reconstructed from the retrieval arguments rather than the "
                "one the dataset asked.\n"
                "         Critic rejections are not comparable with the REST flow's. "
                "Update the CLI to fix."
            )

        # Dense / full-gallery retrieval: every clip in one pass, no cosine floor,
        # no adjacent merging. Exact scanning fails on budget overflow;
        # clip-level scorers separately report annotation-gallery coverage.
        full_gallery = getattr(args, "full_gallery", False)
        top_k = (
            (args.candidate_limit or 100000)
            if full_gallery
            else (args.retrieval_pool_size or args.top_k)
        )
        min_cosine_similarity = -1.0 if full_gallery else args.min_cosine_similarity
        merge_adjacent = False if full_gallery else args.merge_adjacent
        if full_gallery:
            print(
                "retrieval depth:  bounded exact gallery scan (cosine floor -1, no merge; overflow fails)"
            )

        backend = flows.CliQueryBackend(
            vss_cmd=vss_cmd,
            search_path=args.search_path,
            top_k=top_k,
            min_cosine_similarity=min_cosine_similarity,
            source_type=args.source_type,
            attributes=args.attribute or [],
            merge_adjacent=merge_adjacent,
            cwd=args.vss_repo_root,
            decompositions=decompositions,
            pass_original_query=pass_original_query,
            full_gallery=full_gallery,
            candidate_limit=args.candidate_limit or (100000 if full_gallery else None),
            attribute_min_similarity=args.attribute_min_similarity,
            critic_enabled=args.critic,
            critic_eval_count=args.critic_eval_count,
            critic_frame_budget=args.critic_frame_budget,
            critic_input_width=args.critic_input_width,
            critic_input_height=args.critic_input_height,
            critic_diagnostic_dir=args.critic_diagnostic_dir,
            retrieval_pool_size=args.retrieval_pool_size,
            frame_scoring=getattr(args, "frame_scoring", "auto"),
            frame_lookup_transport=getattr(args, "frame_lookup_transport", "auto"),
            attribute_input=args.attribute_input
            if args.search_path == "attribute" and args.attribute_input_explicit
            else None,
        )
        backend.cli_version = version
        backend.decomposition_source = args.decompositions
        return backend
    raise SystemExit(f"Unknown query flow: {args.query_flow}")


# =============================================================================
# Backward-compatible single-call entrypoint
# =============================================================================


def run_evaluation(
    query_backend: Any,
    data_dir: Path,
    dataset: str,
    subset: str,
    output_file: str | None = None,
    run_name: str | None = None,
    concurrency: int = 1,
    upload_stats: dict[str, Any] | None = None,
    ingest_description: dict[str, Any] | None = None,
    readiness: dict[str, Any] | None = None,
    index_probe: dict[str, Any] | None = None,
    cleared: dict[str, Any] | None = None,
    vst_url: str | None = None,
    decomposer: Any = None,
    decompose_fallback: dict[str, Any] | None = None,
    tolerate_unanswered: int = 0,
    use_dataset_decompositions: bool = True,
) -> dict[str, Any]:
    """Backward-compatible adapter for the pre-split single-call entrypoint.

    Chains the retrieve and score stages and returns the legacy output dict
    (``summary`` / ``config`` / ``flow`` / ``query_results``). New code should
    drive the stages directly via :func:`flows.pipeline.run_retrieve` and
    ``run_score`` (or the ``--stage`` CLI selector). Kept so existing callers
    and the test-suite keep working unchanged.
    """
    cfg = RunConfig(
        endpoint="",
        data_dir=Path(data_dir),
        dataset=dataset,
        subset=subset,
        concurrency=concurrency,
        vst_url=vst_url,
        use_dataset_decompositions=use_dataset_decompositions,
        tolerate_unanswered=tolerate_unanswered,
        output_file=output_file,
        run_name=run_name,
    )
    manifest = IngestManifest(
        ingest_description=ingest_description,
        upload_stats=upload_stats,
        readiness=readiness,
        index_probe=index_probe,
        cleared=cleared,
    )
    artifact = pipeline.run_retrieve(
        cfg, query_backend, decomposer, decompose_fallback, manifest
    )
    return pipeline.run_score(cfg, artifact).to_output()


# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--endpoint",
        default=None,
        help="VSS agent base URL (e.g. http://localhost:8000). Required unless --stage score.",
    )

    stage = p.add_argument_group("pipeline stages")
    stage.add_argument(
        "--stage",
        default="run",
        choices=("run", "ingest", "retrieve", "score"),
        help=(
            "Which stage to run (default: run = the full ingest->retrieve->score "
            "chain). 'ingest' writes an ingest manifest and stops; 'retrieve' "
            "writes a retrieval artifact and stops; 'score' reads a retrieval "
            "artifact and writes the scored result -- it needs no endpoint."
        ),
    )
    stage.add_argument(
        "--ingest-out",
        help="Where to write the ingest manifest (--stage ingest). "
        f"Default: {RESULTS_DIR}/<name>.ingest.json",
    )
    stage.add_argument(
        "--ingest-in",
        help="Read a prior ingest manifest for the flow block (--stage retrieve).",
    )
    stage.add_argument(
        "--retrieval-out",
        help=(
            "Where to write the retrieval artifact. Used by --stage retrieve "
            "(which stops after) and by the default run (so one retrieval can be "
            "re-scored later without re-querying). "
            f"Default: {RESULTS_DIR}/<name>.retrieval.json"
        ),
    )
    stage.add_argument(
        "--resume-from",
        help="Resume a compatible retrieval artifact or checkpoint directory; answered queries are reused.",
    )
    stage.add_argument(
        "--retrieval-in",
        help="Read a retrieval artifact to score (--stage score; required).",
    )

    flow = p.add_argument_group("flow selection")
    flow.add_argument(
        "--ingest-flow",
        default="vst-direct",
        choices=sorted(flows.INGEST_BACKENDS),
        help=(
            "How fixtures are uploaded (default: vst-direct, what the UI does "
            "-- upload to VIOS and let its webhooks drive perception). "
            "Requires --webhooks-confirmed. 'agent-3step' triggers perception explicitly. "
            "Every strategy receives embedding and attribute readiness checks."
        ),
    )
    flow.add_argument(
        "--webhooks-confirmed",
        action="store_true",
        help="Confirm deployed VIOS add webhooks and model names were checked before vst-direct ingestion. Otherwise select --ingest-flow agent-3step.",
    )
    flow.add_argument(
        "--query-flow",
        default="cli",
        choices=sorted(flows.QUERY_BACKENDS),
        help=(
            "How queries are issued. Only 'cli' exists today -- the path the "
            "new UI flow reaches through the vss-search-archive skill."
        ),
    )
    flow.add_argument(
        "--scenarios",
        nargs="+",
        choices=("embed-only", "attribute-only", "live-routed"),
        help="Gallery comparisons (default: all three); use --fixed-search-path for a legacy single run.",
    )
    flow.add_argument(
        "--critic",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Verify candidates; gallery scenarios default off, legacy commands preserve deployment defaults.",
    )
    flow.add_argument("--critic-eval-count", type=int)
    flow.add_argument("--critic-frame-budget", type=int)
    flow.add_argument("--critic-input-width", type=int)
    flow.add_argument("--critic-input-height", type=int)
    flow.add_argument("--critic-diagnostic-dir")
    flow.add_argument("--frame-scoring", choices=("auto", "source", "indexed"), default="auto",
                      help="Frame cosine kernel; effective compatibility profile is recorded before retrieval.")
    flow.add_argument("--frame-lookup-transport", choices=("auto", "single", "msearch"), default="auto",
                      help="Frame refinement transport; no timestamp bounds are added.")
    flow.add_argument(
        "--candidate-limit",
        type=int,
        help="Bound indexed vector documents scanned for exact embed/attribute search (1..100000); overflow fails, never truncates silently.",
    )
    flow.add_argument(
        "--retrieval-pool-size",
        type=int,
        help="Cap returned candidates before critic filtering, independently of --candidate-limit and metric --top-k.",
    )
    flow.add_argument(
        "--ingestion-validation-contract",
        help="JSON embedding contract for complete indexed-content checks after gallery ingestion; requires ffprobe.",
    )
    flow.add_argument(
        "--attribute-min-similarity",
        type=float,
        help="Attribute shifted-score floor, (1+cosine)/2; reference floor 0.",
    )
    flow.add_argument(
        "--attribute-input", choices=("original", "decomposed"), default="original"
    )
    flow.add_argument(
        "--query-domain",
        help="Select query domain (for example pas), preserving the gallery.",
    )
    flow.add_argument(
        "--preprocessing-provenance",
        help="JSON with effective ingestion preprocessing settings; changed preprocessing requires re-ingestion before comparison.",
    )
    flow.add_argument(
        "--decomposition-out",
        help="Directory for durable per-query decomposition evidence.",
    )
    flow.add_argument(
        "--search-path",
        default="embed",
        choices=flows.SEARCH_PATHS,
        help="CLI retrieval path (--query-flow cli only). Fixed, not agent-chosen.",
    )
    flow.add_argument(
        "--attribute",
        action="append",
        help="Attribute term for attribute/fusion paths; repeatable.",
    )

    cli = p.add_argument_group("vss CLI (--query-flow cli)")
    cli.add_argument(
        "--vss-repo-root",
        help=(
            "Checkout containing libs/vss/cli. Optional -- "
            "falls back to the vendored submodule, then `vss` on PATH."
        ),
    )
    cli.add_argument(
        "--vss-cmd", help="Override the whole vss invocation (shell-quoted)"
    )
    cli.add_argument(
        "--vss-base-url",
        help=(
            "Origin for `vss configure`. Defaults to --endpoint's host on port "
            f"{flows.DEFAULT_VSS_ORIGIN_PORT}, which is where the unified "
            "path-prefix routes live (the agent port does not serve them)."
        ),
    )
    cli.add_argument(
        "--vss-origin-port",
        type=int,
        default=flows.DEFAULT_VSS_ORIGIN_PORT,
        help=f"Port for the derived configure origin (default: {flows.DEFAULT_VSS_ORIGIN_PORT}).",
    )
    cli.add_argument(
        "--skip-vss-configure",
        action="store_true",
        help="Use ~/.vss/config.json as-is instead of pointing it at this deployment.",
    )
    cli.add_argument(
        "--decompositions",
        help=(
            "Replay stored decompositions instead of calling the LLM. This does "
            "NOT enable decomposition -- that is the default -- it REPLACES the "
            "live decomposer, so no LLM runs and routing is fixed by the file. "
            "Use it to pin routing across two runs whose retrieval you are "
            "comparing: the decomposer is an LLM and its path split moves "
            "between model versions even at temperature 0, which would show up "
            "as a retrieval difference. The result file records "
            "live_decomposition: false so a reader can tell which ran."
        ),
    )
    cli.add_argument(
        "--skip-vss-preflight",
        action="store_true",
        help="Skip the `vss --version` check that catches a broken CLI install.",
    )

    dataset = p.add_argument_group("dataset")
    dataset.add_argument(
        "--dataset",
        default=None,
        choices=sorted(flows.DATASETS),
        help="Eval dataset (default: warehouse). Resolved from the artifact for --stage score.",
    )
    dataset.add_argument(
        "--subset",
        default=None,
        help="Dataset subset (default: ''). Resolved from the artifact for --stage score.",
    )
    dataset.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=f"Dataset root (default: {flows.DEFAULT_DATA_DIR}). Overridable for --stage score.",
    )
    dataset.add_argument(
        "--skip-download", action="store_true", help="Use the local dataset as-is"
    )
    dataset.add_argument(
        "--skip-ingest", action="store_true", help="Assume videos are already indexed"
    )
    dataset.add_argument(
        "--skip-existing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "Do not re-upload fixtures VST already lists (default: enabled). "
            "Use --no-skip-existing only on an isolated deployment."
        ),
    )

    query = p.add_argument_group("query parameters")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument(
        "--scoring",
        default="segment-overlap",
        choices=sorted(SCORING_BACKENDS),
        help=(
            "Scoring model for the score stage (default: segment-overlap, the eval's "
            "existing segment-timestamp-overlap metrics). 'clip-relevance' scores "
            "against a clip gallery (drive-style); see flows/scoring/. The result "
            "records which model ran."
        ),
    )
    query.add_argument("--min-cosine-similarity", type=float, default=0.0)
    query.add_argument(
        "--source-type", default="video_file", choices=["video_file", "rtsp"]
    )
    query.add_argument("--concurrency", type=int, default=1)
    query.add_argument(
        "--merge-adjacent",
        action="store_true",
        help=(
            "Let the CLI merge contiguous same-sensor windows (upstream default). "
            "OFF here by default: merging averages scores and changes the precision "
            "denominator, breaking comparability with the REST baseline."
        ),
    )

    gallery = p.add_argument_group(
        "clip-level scoring (--scoring clip-relevance|drive)"
    )
    gallery.add_argument(
        "--full-gallery",
        action="store_true",
        help=(
            "Request an exact indexed ranking within --candidate-limit (embed path, "
            "cosine floor -1, no adjacent merging). The precondition the clip-level "
            "scorers grade against; pair with --scoring clip-relevance|drive."
        ),
    )
    gallery.add_argument(
        "--gallery-gt",
        help="Drive release root (gt/queries_gt.json + additional_gt/clips_gt.json) "
        "or a merged {gallery, queries} json.",
    )
    gallery.add_argument(
        "--ingestion-manifest",
        help="Clip->sensor mapping recorded at ingest time (required by clip-level scorers).",
    )
    gallery.add_argument(
        "--drive-scorer-dir",
        help="Directory holding the original evaluate_domain_test.py (loaded for --scoring drive).",
    )
    gallery.add_argument(
        "--pas-frame-supported",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Clip-relevance only: derive PAS queries' relevant_clip_ids from the "
            "released positive tracks whose representative frame supports the "
            "query's resolved attributes (PhysicalAI v1.1.2). Needs the release-"
            "directory layout (additional_gt/tracks_pas_attributes.json). PAS "
            "queries with no supported positive are excluded and listed in the "
            "cohort; event queries are scored unchanged."
        ),
    )
    ingest = p.add_argument_group("ingest")
    ingest.add_argument("--upload-timestamp", default=flows.DEFAULT_UPLOAD_TIMESTAMP)
    ingest.add_argument(
        "--complete-retries",
        type=int,
        default=3,
        help=(
            "Attempts for POST /videos/{id}/complete (default: 3). The call is "
            "flaky -- observed 502 on the first attempt and 200 on the second."
        ),
    )
    ingest.add_argument(
        "--complete-backoff",
        type=float,
        default=5.0,
        help="Base seconds between /complete attempts; multiplied by attempt number.",
    )
    ingest.add_argument(
        "--vst-port",
        type=int,
        default=30888,
        help=(
            "VST port on the same host as --endpoint (default: 30888). The agent "
            "origin does not necessarily proxy /vst -- on 10.86.12.161 it does "
            "not, and VST answers on 7777 and 30888 instead."
        ),
    )
    ingest.add_argument(
        "--vst-url",
        help="Full VST origin, overriding the --vst-port derivation entirely.",
    )
    ingest.add_argument(
        "--readiness-timeout",
        type=int,
        default=1200,
        help="Seconds to wait for VST to register every source after ingest (default: 1200)",
    )
    ingest.add_argument("--skip-readiness-wait", action="store_true")
    ingest.add_argument(
        "--skip-index-probe",
        action="store_true",
        help=(
            "Skip embedding coverage and attribute pipeline readiness checks for every "
            "ingest strategy. Unsafe for evaluation campaigns; duplicate completions "
            "cannot be accepted with this option."
        ),
    )
    ingest.add_argument("--index-probe-attempts", type=int, default=10)
    ingest.add_argument(
        "--index-probe-top-k",
        type=int,
        default=20,
        help=(
            "Results requested per expected source when probing coverage "
            "(default: 20, capped at the CLI's 1000). Embedding search returns "
            "the nearest top_k, so on a large dataset one query cannot reach "
            "every video and full coverage becomes unreachable -- raise this, "
            "or use --skip-index-probe."
        ),
    )
    ingest.add_argument(
        "--index-probe-backoff",
        type=float,
        default=15.0,
        help="Seconds between index probes (default: 15). Webhook-driven perception is async.",
    )
    destructive = ingest.add_mutually_exclusive_group()
    destructive.add_argument(
        "--only-dataset",
        action="store_true",
        help=(
            "Delete every source that is NOT one of this dataset's videos, then "
            "ingest. Leaves the dataset's own sources alone. Safer than --clear "
            "on a shared deployment, and enough to make a run reproducible."
        ),
    )
    destructive.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Delete ALL videos on the endpoint before ingest -- including any "
            "you did not upload. Do not use on a shared deployment."
        ),
    )
    ingest.add_argument(
        "--confirm-delete",
        action="store_true",
        help="Required with --only-dataset or --clear to acknowledge the printed deletion inventory.",
    )

    p.add_argument(
        "--output-file",
        help=f"Exact path for the results JSON. Default: {RESULTS_DIR}/<name>.json",
    )
    p.add_argument(
        "--llm-url",
        default=os.environ.get("VSS_LLM_URL"),
        help="LLM origin for live query decomposition (env: VSS_LLM_URL). "
        "Derived from --endpoint and --llm-port when unset.",
    )
    p.add_argument(
        "--llm-port",
        type=int,
        default=30081,
        help="NIM port on the --endpoint host, used to derive --llm-url (default: 30081).",
    )
    p.add_argument(
        "--no-decompose",
        action="store_true",
        help=(
            "Skip live decomposition. Replay stored per-query routes from the "
            "dataset or --decompositions when present; otherwise use --search-path. "
            "For a true single-path baseline use --fixed-search-path."
        ),
    )
    p.add_argument(
        "--fixed-search-path",
        action="store_true",
        help="Use --search-path for every query, ignoring live and dataset decompositions (baseline only).",
    )
    p.add_argument(
        "--no-original-query",
        action="store_true",
        help=(
            "Do not send --original-query. The critic then verifies a question "
            "the host rebuilds from the retrieval arguments instead of the one "
            "the dataset asked, which is the pre-fix behaviour and the only "
            "reason to pass this: A/B against an older result file."
        ),
    )
    p.add_argument(
        "--tolerate-unanswered",
        type=int,
        default=0,
        help=(
            "How many queries may go unanswered (timeout, unparseable CLI "
            "output, or an exception) before the run aborts. Default 0. They "
            "are excluded from the metrics either way -- this only decides "
            "whether a partial run is allowed to publish numbers."
        ),
    )
    p.add_argument(
        "--llm-chat-template-kwargs",
        type=json.loads,
        help="Explicit JSON object of chat-template controls, saved with decomposition evidence.",
    )
    p.add_argument(
        "--llm-model", help="Decomposition model id (default: ask the endpoint)."
    )
    p.add_argument(
        "--name",
        help="Name this run's results file, instead of <dataset>_<subset>_<timestamp>. "
        "Ignored when --output-file gives an exact path.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved backends and a sample CLI invocation, then exit",
    )
    args = p.parse_args(argv)
    explicit = [
        token.split("=", 1)[0]
        for token in (list(argv) if argv is not None else sys.argv[1:])
    ]
    args.tolerate_unanswered_override = (
        args.tolerate_unanswered if "--tolerate-unanswered" in explicit else None
    )
    if args.resume_from and args.stage not in ("run", "retrieve"):
        p.error("--resume-from requires --stage run or retrieve")
    if args.resume_from and (
        args.clear
        or args.only_dataset
        or not (args.skip_ingest or args.stage == "retrieve")
    ):
        p.error(
            "--resume-from requires --skip-ingest or --stage retrieve and cannot modify ingestion"
        )
    args.attribute_input_explicit = "--attribute-input" in explicit
    if args.gallery_gt and "--scoring" not in explicit:
        args.scoring = "clip-relevance"
    # --stage score never touches the deployment, so --endpoint is the one
    # argument whose required-ness depends on the stage.
    if args.endpoint is None and args.stage != "score":
        p.error("--endpoint is required unless --stage score")
    if args.stage == "score" and not args.retrieval_in:
        p.error("--stage score requires --retrieval-in <artifact.json>")
    if args.stage == "score" and args.dry_run:
        p.error(
            "--dry-run is not meaningful with --stage score (scoring is already offline)"
        )
    if args.retrieval_in and args.stage != "score":
        p.error("--retrieval-in is only meaningful with --stage score")
    if args.ingest_in and args.stage != "retrieve":
        p.error("--ingest-in is only meaningful with --stage retrieve")
    if args.stage == "ingest" and args.skip_ingest:
        p.error("--stage ingest cannot be combined with --skip-ingest")
    if args.stage in ("retrieve", "score") and (
        args.clear or args.only_dataset or args.confirm_delete
    ):
        p.error(
            "--clear/--only-dataset/--confirm-delete only apply to --stage run or --stage ingest"
        )
    if args.scoring in ("clip-relevance", "drive") and not (
        args.gallery_gt
        and (
            args.ingestion_manifest
            or (args.stage in ("run", "ingest") and not args.skip_ingest)
        )
    ):
        p.error(
            f"--scoring {args.scoring} requires --gallery-gt and --ingestion-manifest"
        )
    if args.scoring == "drive" and not args.drive_scorer_dir:
        p.error(
            "--scoring drive requires --drive-scorer-dir <dir holding evaluate_domain_test.py>"
        )
    if args.pas_frame_supported and args.scoring != "clip-relevance":
        p.error(
            "--pas-frame-supported is only valid with --scoring clip-relevance "
            "(drive uses the Drive script's own eligibility)"
        )
    if (
        args.gallery_gt
        and args.stage in ("run", "ingest", "retrieve")
        and not (
            args.fixed_search_path
            or args.full_gallery
            or "--search-path" in explicit
            or args.no_decompose
        )
        and args.scenarios is None
    ):
        args.scenarios = ["embed-only", "attribute-only", "live-routed"]
    if args.scenarios and (
        args.fixed_search_path
        or args.full_gallery
        or "--search-path" in explicit
        or args.no_decompose
    ):
        p.error(
            "--scenarios conflicts with legacy fixed-path/full-gallery/decomposition-off options"
        )
    if args.candidate_limit is not None and not 1 <= args.candidate_limit <= 100000:
        p.error("--candidate-limit must be between 1 and 100000")
    if args.retrieval_pool_size is not None:
        if not 1 <= args.retrieval_pool_size <= 100000:
            p.error("--retrieval-pool-size must be between 1 and 100000")
        if args.full_gallery or args.stage not in ("run", "retrieve"):
            p.error(
                "--retrieval-pool-size requires run/retrieve and conflicts with --full-gallery"
            )
    if args.ingestion_validation_contract and (
        not args.gallery_gt
        or args.stage not in ("run", "ingest")
        or args.skip_ingest
        or args.skip_index_probe
    ):
        p.error(
            "--ingestion-validation-contract requires gallery ingestion with index probes enabled"
        )
    if "--llm-chat-template-kwargs" in explicit:
        from flows.decompose import validate_chat_template_kwargs

        try:
            if args.llm_chat_template_kwargs is None:
                raise ValueError("must be a JSON object, not null")
            args.llm_chat_template_kwargs = validate_chat_template_kwargs(
                args.llm_chat_template_kwargs
            )
        except ValueError as exc:
            p.error(f"invalid --llm-chat-template-kwargs: {exc}")
    if args.resume_from and args.scenarios and len(args.scenarios) != 1:
        p.error(
            "--resume-from requires selecting the saved scenario with --scenarios <scenario>"
        )
    if args.scenarios and args.attribute:
        p.error(
            "--attribute conflicts with scenarios, which use the original query or saved extracted attributes"
        )
    if args.scenarios and len(set(args.scenarios)) != len(args.scenarios):
        p.error("--scenarios cannot contain duplicates")
    if args.critic_eval_count is not None and args.critic_eval_count < 1:
        p.error("--critic-eval-count must be at least 1")
    if args.critic_frame_budget is not None and not 1 <= args.critic_frame_budget <= 60:
        p.error("--critic-frame-budget must be between 1 and 60")
    if any(
        value is not None and value < 16
        for value in (args.critic_input_width, args.critic_input_height)
    ):
        p.error("critic input width/height must be at least 16")
    if args.scenarios and not args.gallery_gt:
        p.error("--scenarios requires --gallery-gt")
    if args.attribute_input == "decomposed" and not args.decompositions:
        p.error("--attribute-input decomposed requires --decompositions")
    if args.attribute_input == "decomposed" and not (
        args.scenarios or args.search_path == "attribute"
    ):
        p.error("--attribute-input decomposed requires attribute search or scenarios")
    if args.full_gallery:
        # Dense retrieval is a fixed embed pass over every clip: no routing, no floor,
        # no merging. Routing a query to attribute/fusion would filter the gallery and
        # silently break full coverage, so force the fixed embed path.
        if args.search_path != "embed":
            p.error(
                "--full-gallery retrieves every clip; it requires the embed path (--search-path embed)"
            )
        if args.decompositions:
            p.error(
                "--full-gallery is a fixed dense embed retrieval; it does not decompose/route (--decompositions)"
            )
        args.no_decompose = True
    if (
        args.fixed_search_path
        and args.decompositions
        and args.attribute_input != "decomposed"
    ):
        p.error("--fixed-search-path cannot be combined with --decompositions")
    if args.fixed_search_path:
        args.no_decompose = True
    if (
        args.source_type == "rtsp"
        and args.stage in ("run", "ingest")
        and not args.skip_ingest
    ):
        p.error(
            "--source-type rtsp requires --skip-ingest: fresh uploads are video files"
        )
    if (args.only_dataset or args.clear) and not args.confirm_delete:
        p.error("--only-dataset and --clear require --confirm-delete")
    if (args.only_dataset or args.clear) and args.skip_ingest:
        p.error("destructive cleanup cannot be combined with --skip-ingest")
    if args.confirm_delete and not (args.only_dataset or args.clear):
        p.error("--confirm-delete requires --only-dataset or --clear")
    if (args.scenarios and "live-routed" not in args.scenarios) or (
        not args.scenarios
        and args.search_path == "attribute"
        and args.attribute_input_explicit
    ):
        args.no_decompose = True
    return args


def main() -> None:
    args = parse_args()
    stage = args.stage

    # --stage score is fully offline: it reads a retrieval artifact and the
    # dataset, never an endpoint, a backend, or a decomposer.
    if stage == "score":
        cfg = RunConfig.from_args(args, vst_url=None)
        pipeline.run_pipeline(cfg, None, None, None, None)
        return

    # Resolve the dataset identity for the deployment-touching stages (score
    # resolves it from the artifact instead). args keeps these None-able only
    # so --stage score can fall back to the artifact.
    if args.dataset is None:
        args.dataset = "warehouse"
    if args.subset is None:
        args.subset = ""
    if args.data_dir is None:
        args.data_dir = flows.DEFAULT_DATA_DIR

    # Decomposition is the default, not an opt-in. The deployed agent decomposes
    # every query and that choice picks the retrieval path, so an eval that
    # skips it measures a flow the product never runs -- and nothing in the
    # metrics reveals it. Opting out has to be deliberate.
    if (
        stage in ("run", "retrieve")
        and not args.llm_url
        and not args.no_decompose
        and not args.decompositions
    ):
        args.llm_url = flows.llm_url_for(args.endpoint, args.llm_port)

    decomposer = None
    decompose_fallback: dict[str, Any] | None = None
    # Only the query-issuing stages (run / retrieve) build a decomposer; ingest
    # and score never call the LLM.
    if stage in ("run", "retrieve") and args.no_decompose:
        if not args.dry_run:
            if args.fixed_search_path:
                print(
                    f"decomposition: OFF (--fixed-search-path) -- every query uses --search-path {args.search_path}"
                )
            else:
                print(
                    "decomposition: live OFF (--no-decompose) -- stored routes replay when present"
                )
    elif stage in ("run", "retrieve") and args.decompositions:
        if not args.dry_run:
            print(f"decomposition: from {args.decompositions} -- not live")
    elif stage in ("run", "retrieve") and args.llm_url and not args.dry_run:
        try:
            decomposer = flows.LiveDecomposer(
                args.llm_url,
                repo_root=flows.REPO_ROOT,
                model=args.llm_model,
                source_type=args.source_type,
                **(
                    {"chat_template_kwargs": args.llm_chat_template_kwargs}
                    if args.llm_chat_template_kwargs is not None
                    else {}
                ),
            )
            print(f"decomposition: live via {args.llm_url}  model={decomposer.model}")
        except flows.DecompositionError as e:
            # An unreachable NIM must not end the run, but it must not quietly
            # collapse it to one path either. Fall back to the dataset's own
            # answer key when it ships one -- that still routes per query, so
            # every path is exercised -- and only then to --search-path.
            decompose_fallback = {"attempted": args.llm_url, "error": str(e)}
            print(f"\n  WARNING: decomposition LLM unreachable at {args.llm_url}")
            print(f"           {e}")
            # Fall back to --search-path, which defaults to `embed`: the one
            # path that needs nothing but the query text. An explicit
            # --search-path is honoured rather than overridden, but note what
            # that means here -- `attribute` and `fusion` both
            # require a per-query attribute that only decomposition produces,
            # and handing them the whole query as its attribute is the failure
            # this eval already measured -- mAP -25%, HIT@1 halved, +59% latency.
            #
            # A dataset that carries its own decompositions still routes per
            # query; that is the dataset's explicit intent and unpack_dataset
            # applies it further down. Nothing else is inferred -- the answer key
            # in devset_provenance.json is reachable only via --decompositions,
            # because a gold-routed run measures retrieval under perfect routing
            # and should not be mistaken for a live-routing eval.
            carried = 0
            with contextlib.suppress(Exception):
                carried = len(
                    flows.unpack_dataset(
                        flows.load_dataset_file(
                            args.data_dir, args.dataset, args.subset
                        )
                    )[1]
                )
            if carried:
                decompose_fallback["fell_back_to"] = (
                    f"{carried} decomposition(s) carried by the dataset"
                )
                print(
                    f"           the dataset carries {carried} decomposition(s) -- routing still per-query"
                )
            else:
                decompose_fallback["fell_back_to"] = f"--search-path {args.search_path}"
                print(
                    f"           every query will use --search-path {args.search_path}."
                )
                print("           ONE PATH IS MEASURED -- this is not a routing eval.")
                print("           Report it alongside the metrics.")
                # Record that an answer key exists; do NOT suggest using it.
                # This used to print `--decompositions <path>` as a ready-to-run
                # line, which is a suggestion shaped like an instruction: an
                # agent driving this skill copies it, and the run silently
                # becomes a gold-routing experiment scored under a live-routing
                # label. Only physicalai-dev ships one, so following it also
                # makes that dataset incomparable with every other.
                sidecar = flows.sidecar_decompositions_for(args.data_dir, args.dataset)
                if sidecar:
                    decompose_fallback["answer_key_available"] = str(sidecar)
                    decompose_fallback["answer_key_used"] = False
                print()

    ingest_backend = build_ingest_backend(args) if stage in ("run", "ingest") else None
    query_backend = build_query_backend(args)

    if args.dry_run:
        if ingest_backend is not None:
            print("Ingest backend:")
            print(json.dumps(ingest_backend.describe(), indent=2))
        print("\nQuery backend:")
        print(
            json.dumps(
                describe_query_flow(
                    query_backend,
                    decomposer,
                    None if args.no_decompose else args.llm_url,
                ),
                indent=2,
            )
        )
        if isinstance(query_backend, flows.CliQueryBackend):
            import shlex

            print("\nSample invocation:")
            print(
                "  "
                + shlex.join(query_backend.build_argv("a person in a white jacket"))
            )
        print("\nDry run - nothing executed.")
        return

    print(f"\n{'=' * 60}")
    print("SEARCH EVAL - PLUGGABLE FLOWS")
    print(f"{'=' * 60}")
    print(f"Endpoint:       {args.endpoint}")
    print(f"Ingest flow:    {args.ingest_flow}")
    print(f"Query flow:     {args.query_flow}")
    print(f"Stage:          {stage}")
    # Deliberately no search-path line here. This banner prints before the
    # dataset is read, so it cannot know whether the dataset carries
    # decompositions -- it could only report the fallback, which reads as
    # "every query used this". The SEARCH PROFILE EVALUATION header below runs
    # after the dataset loads and reports routing, fallback_path and the
    # planned per-path split accurately.
    print(f"{'=' * 60}\n")

    if not args.skip_download and not args.gallery_gt:
        print("Downloading dataset from DSS...")
        flows.download_from_dss(args.data_dir, args.dataset)

    # VST is a separate origin from the agent. run_eval.py's convention (swap
    # the port on the same host) is reused so both scripts resolve it the same
    # way; --vst-url overrides it for deployments that route differently.
    # Resolved BEFORE --clear so the destructive path uses the same origin.
    vst_url = args.vst_url or flows.vst_url_for(args.endpoint, args.vst_port)
    print(f"VST origin:     {vst_url}")

    # The heavy lifting now lives in flows.pipeline as separable stages
    # (ingest -> retrieve -> score). This default invocation chains them, which
    # is byte-for-byte the old behaviour; --stage (Phase 2) makes each separately
    # runnable against an on-disk artifact.
    cfg = RunConfig.from_args(args, vst_url=vst_url)
    if isinstance(query_backend, flows.CliQueryBackend):
        query_backend.capture_deployment_provenance()
    pipeline.run_pipeline(
        cfg,
        ingest_backend=ingest_backend,
        query_backend=query_backend,
        decomposer=decomposer,
        decompose_fallback=decompose_fallback,
    )


if __name__ == "__main__":
    main()

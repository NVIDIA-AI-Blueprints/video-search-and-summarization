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
    # Defaults: agent-3step ingest + CLI queries. A dataset carrying
    # decompositions routes per query; otherwise everything uses --search-path.
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
import collections
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
import contextlib
from datetime import datetime
import json
import os
from pathlib import Path
import statistics
import sys
import threading
import time
from typing import Any

import requests

#: Stages whose own time is below this are wrappers or noise; the console hides
#: them so the table reads as "where did the time go" without a nesting caveat.
_STAGE_FLOOR_S = 0.001

SCRIPT_DIR = Path(__file__).resolve().parent

#: Results land beside the script, not under the caller's cwd, so a run is
#: findable regardless of where it was started from.
RESULTS_DIR = SCRIPT_DIR / "cli_eval_result"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import flows  # noqa: E402  (imported after SCRIPT_DIR joins sys.path, above)

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
        if args.search_path in ("attribute", "fusion") and not args.attribute and not args.decompositions:
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
        print(f"vss CLI:        {' '.join(vss_cmd)}\n                (resolved from {how})")

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
            base_url = args.vss_base_url or flows.vss_origin_for(args.endpoint, args.vss_origin_port)
            try:
                flows.ensure_vss_configured(vss_cmd, base_url)
            except Exception as e:
                raise SystemExit(f"ERROR: vss configure failed for {base_url}: {e}") from e
        decompositions: dict[str, dict[str, Any]] = {}
        if args.decompositions:
            decompositions = flows.load_decompositions(args.decompositions)
            print(f"decompositions: {len(decompositions)} loaded from {args.decompositions}")

        # `--original-query` is newer than most deployed CLIs, and an unknown
        # option exits 2 -- fatal, on query 1. Probe rather than assume: one
        # --help beats aborting a run that would otherwise have completed with
        # slightly worse verification.
        pass_original_query = not args.no_original_query
        if pass_original_query and not args.dry_run and not flows.cli_supports_flag(vss_cmd, "--original-query"):
            pass_original_query = False
            print(
                "WARNING: this vss CLI has no --original-query, so the critic will "
                "verify a question\n"
                "         reconstructed from the retrieval arguments rather than the "
                "one the dataset asked.\n"
                "         Critic rejections are not comparable with the REST flow's. "
                "Update the CLI to fix."
            )

        return flows.CliQueryBackend(
            vss_cmd=vss_cmd,
            search_path=args.search_path,
            top_k=args.top_k,
            min_cosine_similarity=args.min_cosine_similarity,
            source_type=args.source_type,
            attributes=args.attribute or [],
            merge_adjacent=args.merge_adjacent,
            cwd=args.vss_repo_root,
            decompositions=decompositions,
            pass_original_query=pass_original_query,
        )
    raise SystemExit(f"Unknown query flow: {args.query_flow}")


# =============================================================================
# Ingest
# =============================================================================


def clear_all_videos(agent_endpoint: str, vst_url: str) -> dict[str, Any]:
    """Delete every video registered on the deployment.

    Lists and deletes against the SAME resolved origins the rest of the run
    uses, so ``--vst-url`` cannot list one host and delete from another.
    Prints the full inventory first: ``--clear`` is destructive and shared
    deployments carry other people's fixtures.
    """
    try:
        streams = flows.list_sensor_streams(vst_url)
    except Exception as e:
        raise SystemExit(f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})") from e

    if not streams:
        print("  No videos found to delete.")
        return {"found": 0, "deleted": 0, "names": []}

    print(f"  Deleting {len(streams)} video(s) from {agent_endpoint}:")
    for stream_id, name in streams.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in streams.items():
        try:
            resp = requests.delete(f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60)
            resp.raise_for_status()
            deleted += 1
            print(f"    deleted {name} -> {resp.status_code}")
        except Exception as e:
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    print(f"  Deleted {deleted}/{len(streams)} video(s)")
    return {"found": len(streams), "deleted": deleted, "failed": failed, "names": list(streams.values())}


#: What the probe asks. Any text works -- embedding search returns the nearest
#: `top_k` regardless of how poorly they match, so what comes back is a sample
#: of what is indexed, not a judgement about relevance. Deliberately generic so
#: it carries no assumption about which dataset was ingested.
PROBE_QUERY = "a person"


def _probe_backend(query_backend: Any, top_k: int, source: str | None = None) -> Any:
    """A throwaway embed backend, optionally scoped to one registered source.

    Never the run's own: a ``--min-cosine-similarity`` floor or a non-embed
    ``--search-path`` would filter a healthy index into a false alarm.

    Scoping rides on the decomposition machinery rather than a new argv branch --
    ``{"video_sources": [name]}`` routes to embed (no attributes, no object ids)
    and ``plan_for`` turns it into ``--video-source``.
    """
    decompositions = {PROBE_QUERY: {"video_sources": [source]}} if source else None
    return flows.CliQueryBackend(
        vss_cmd=query_backend.vss_cmd,
        search_path="embed",
        top_k=top_k,
        cwd=query_backend.cwd,
        pass_original_query=False,
        decompositions=decompositions,
    )


def probe_index_coverage(
    query_backend: Any,
    expected_sources: list[str],
    vst_url: str | None = None,
    attempts: int = 10,
    backoff_s: float = 15.0,
    top_k_per_source: int = 20,
) -> dict[str, Any]:
    """Confirm every ingested video is searchable before scoring a whole run.

    The three-step ingest flow proves this for free: ``/complete`` runs the
    embedding leg synchronously per video and returns ``chunks_processed``, so a
    zero fails that upload. ``vst-direct`` has no such step -- VIOS fans out to
    the perception services by webhook and never reports the outcome -- and the
    readiness poll does not close the gap, because it asks VST which sources are
    *registered*. VST registers at upload time, synchronously, so that poll
    returns in 0.0s against an index that has not been built yet.

    Two stages, because one broad query is cheap but not conclusive:

    1. One unscoped embed query. Embedding search returns the nearest ``top_k``
       whatever they are, so what comes back samples what is indexed. Usually
       every source appears and the probe is done in one call.
    2. Anything still absent is queried again **scoped to that source**. A fully
       indexed video can be crowded out of stage 1 by closer chunks from other
       videos, and aborting a healthy run on that would be worse than the bug
       this guards. Absence only counts once the source has been asked about
       directly.

    Scoping needs the name VST actually registered, which is not always the
    dataset's spelling (VST keeps the extension for some sources and not
    others), so ``vst_url`` is read once to resolve them. Without it, stage 2 is
    skipped and stage 1 alone decides -- weaker, and said so in the result.
    """
    if not expected_sources:
        return {"checked": False, "reason": "no expected sources"}

    registered: dict[str, str] = {}
    if vst_url:
        try:
            names = flows.list_sensor_names(vst_url)
            for source in expected_sources:
                variants = flows.name_variants(source)
                match = next((n for n in names if n.lower() in variants), None)
                if match:
                    registered[source] = match
        except Exception as e:
            print(f"  (could not resolve VST source names, per-source probing off: {e})")

    broad = _probe_backend(query_backend, min(1000, max(50, top_k_per_source * len(expected_sources))))
    missing = list(expected_sources)

    for attempt in range(1, attempts + 1):
        try:
            hits, _latency = broad.search(PROBE_QUERY)
            seen = {h.get("video_name", "") for h in hits if h.get("video_name")}
            missing = [
                source
                for source in expected_sources
                if not any(flows.video_name_matches(name, source) for name in seen)
            ]

            # Stage 2: ask directly about whatever the broad query did not
            # surface, so ranking cannot be mistaken for absence.
            confirmed_missing = []
            for source in missing:
                name = registered.get(source)
                if not name:
                    confirmed_missing.append(source)
                    continue
                scoped_hits, _ = _probe_backend(query_backend, 1, source=name).search(PROBE_QUERY)
                if not scoped_hits:
                    confirmed_missing.append(source)
            missing = confirmed_missing
        except Exception as e:
            # An environment fault is worth reporting as itself rather than as
            # an empty index -- exit 4 means misconfigured, not unindexed.
            return {
                "checked": True,
                "covered": False,
                "attempts": attempt,
                "error": f"{type(e).__name__}: {e}",
            }

        found = len(expected_sources) - len(missing)
        if not missing:
            print(f"  Index covers all {len(expected_sources)} source(s) after {attempt} attempt(s).")
            return {
                "checked": True,
                "covered": True,
                "attempts": attempt,
                "sources": len(expected_sources),
                "per_source_verified": bool(registered),
            }
        if attempt < attempts:
            print(
                f"  Probe {attempt}/{attempts}: {found}/{len(expected_sources)} "
                f"source(s) indexed, retrying in {backoff_s:.0f}s"
            )
            time.sleep(backoff_s)

    return {
        "checked": True,
        "covered": False,
        "attempts": attempts,
        "sources": len(expected_sources),
        "missing": missing[:20],
        "missing_count": len(missing),
        "per_source_verified": bool(registered),
    }


def describe_query_flow(
    query_backend: Any,
    decomposer: Any,
    planned_llm_url: str | None = None,
    fallback: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """The query backend's own description, corrected for live decomposition.

    ``QueryBackend.describe()`` reports routing from ``self.decompositions``,
    which a *live* decomposer only fills as queries run. Called before the first
    query -- which is where both the banner and the saved flow block need it --
    it therefore says ``routing: fixed, decompositions: 0`` for a run that went
    on to decompose every query, and a reader of the result file concludes one
    path was measured. Take the decomposer's presence as the answer instead of
    inferring it from state that does not exist yet.

    ``planned_llm_url`` covers the dry run, where no decomposer is built at all
    because constructing one asks the LLM for its model list and a dry run
    contacts nothing. Reporting the intent keeps the preview from promising a
    fixed-path run that the real invocation would not perform.
    """
    described = query_backend.describe()
    if decomposer is not None:
        described["routing"] = "per-query (live decomposition)"
        described["live_decomposition"] = decomposer.describe()
    elif planned_llm_url:
        described["routing"] = "per-query (live decomposition)"
        described["live_decomposition"] = {
            "llm_url": planned_llm_url,
            "model": "(discovered at run time)",
        }
    elif fallback:
        # The run went ahead without the LLM. Say so in the artifact, or the
        # result reads as a deliberate choice of routing rather than a fallback.
        described["live_decomposition"] = fallback
    else:
        described["live_decomposition"] = False
    return described


def prune_foreign_videos(agent_endpoint: str, vst_url: str, video_dir: Path) -> dict[str, Any]:
    """Delete every registered source that is NOT one of this dataset's videos.

    The narrow form of ``--clear``. A run is only interpretable if the index
    holds what the dataset says it holds: a foreign source cannot be retrieved
    for any query in the dataset, so it contributes nothing but the chance of a
    false positive and a longer ingest. Removing exactly the foreign ones keeps
    the deployment reproducible without wiping fixtures the dataset does need.

    Membership uses :func:`flows.name_variants` -- the same normalisation
    ``--skip-existing`` uses to decide a video is already present. Sharing one
    matcher is what stops this deleting a video the very next step re-uploads.
    """
    try:
        streams = flows.list_sensor_streams(vst_url)
    except Exception as e:
        raise SystemExit(f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})") from e

    ours: set[str] = set()
    for vf in sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mkv")):
        ours |= flows.name_variants(vf.name)
    if not ours:
        raise SystemExit(
            f"ABORTED: no video files under {video_dir}, so every source looks foreign. "
            f"Refusing to delete the whole deployment -- pass --clear if that is what you meant."
        )

    foreign = {sid: n for sid, n in streams.items() if str(n).lower() not in ours}
    kept = len(streams) - len(foreign)
    print(f"  {len(streams)} source(s) registered: {kept} belong to this dataset, {len(foreign)} do not")
    if not foreign:
        print("  Nothing to prune.")
        return {"found": len(streams), "kept": kept, "deleted": 0, "names": []}

    for stream_id, name in foreign.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in foreign.items():
        try:
            resp = requests.delete(f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60)
            resp.raise_for_status()
            deleted += 1
            print(f"    deleted {name} -> {resp.status_code}")
        except Exception as e:
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    print(f"  Pruned {deleted}/{len(foreign)} foreign video(s); kept {kept}")
    return {
        "found": len(streams), "kept": kept, "deleted": deleted,
        "failed": failed, "names": list(foreign.values()),
    }


def ingest_videos(
    backend: Any,
    video_dir: Path,
    skip_existing_from: str | None = None,
) -> dict[str, Any]:
    """Upload every fixture through the selected ingest backend.

    ``skip_existing_from`` is a VST origin. When given, sources already
    registered there are left alone -- necessary on a shared deployment, where
    re-uploading someone else's fixture is at best wasteful and at worst
    creates a duplicate sensor for the same video.
    """
    video_files = sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mkv"))
    if not video_files:
        print(f"  No video files found in {video_dir}")
        return flows.aggregate_upload_stats([])

    skipped: list[str] = []
    if skip_existing_from:
        try:
            registered = flows.list_sensor_names(skip_existing_from)
            keep = []
            for vf in video_files:
                if flows.is_registered(vf.name, registered):
                    skipped.append(vf.stem)
                else:
                    keep.append(vf)
            video_files = keep
            if skipped:
                print(f"  Already registered, skipping ingest: {skipped}")
        except Exception as e:
            print(f"  WARNING: could not read VST sensor list ({type(e).__name__}: {e}); ingesting all")

    if not video_files:
        print("  Nothing to ingest -- every fixture is already registered.")
        stats = flows.aggregate_upload_stats([])
        stats["skipped_existing"] = skipped
        return stats

    print(f"  Ingesting {len(video_files)} video(s) via '{backend.name}'")
    per_file: list[dict[str, Any]] = []

    for i, vf in enumerate(video_files, 1):
        size = vf.stat().st_size / (1024 * 1024) if vf.exists() else 0.0
        print(f"  [{i}/{len(video_files)}] {vf.name} ({size:.2f} MB)")
        record = backend.upload(vf)
        per_file.append(record)

        if record.get("success"):
            extras = []
            if record.get("chunks_processed") is not None:
                extras.append(f"{record['chunks_processed']} chunks")
            if record.get("upload_speed_mbps") is not None:
                extras.append(f"{record['upload_speed_mbps']} Mbps")
            if record.get("phases"):
                extras.append(
                    " + ".join(f"{k.removesuffix('_s')}={v}s" for k, v in record["phases"].items())
                )
            suffix = f"  |  {'  |  '.join(extras)}" if extras else ""
            print(f"    OK  {record['upload_latency_s']}s{suffix}  (sensor: {record.get('sensor_id')})")
        else:
            print(f"    FAILED: {record.get('error')}")

    stats = flows.aggregate_upload_stats(per_file)
    stats["skipped_existing"] = skipped
    print(f"  Ingest complete: {stats['successful']}/{stats['total_uploads']} succeeded")
    return stats


# =============================================================================
# Evaluation
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
) -> dict[str, Any]:
    """Score every dataset query through ``query_backend``.

    Output JSON keeps ``run_eval.py``'s exact ``summary`` / ``config`` /
    ``query_results`` shape so existing readers (CI summary CSV, dashboards)
    keep working, plus a ``flow`` block recording how the numbers were obtained.
    """
    data = flows.load_dataset_file(data_dir, dataset, subset)
    # Decompositions live in the dataset next to the ground truth they belong
    # to. --decompositions stays available for A/B-ing a different set (say,
    # agent-captured vs hand-written) against the same annotations, so an
    # explicit flag wins over what the dataset carries.
    annotations, dataset_decompositions = flows.unpack_dataset(data)
    queries = list(annotations.keys())

    if dataset_decompositions and hasattr(query_backend, "decompositions"):
        if query_backend.decompositions:
            print(
                f"NOTE: --decompositions ({len(query_backend.decompositions)}) overrides the "
                f"{len(dataset_decompositions)} carried by the dataset"
            )
        else:
            query_backend.decompositions = dataset_decompositions
            print(f"Using {len(dataset_decompositions)} decomposition(s) from the dataset")

    # Compute the planned split over the ACTUAL query list, so the header
    # states what will run rather than only the fallback.
    planned_paths: dict[str, int] = {}
    if hasattr(query_backend, "plan_for_query"):
        planned_paths = flows.path_distribution([query_backend.plan_for_query(q) for q in queries])

    described = describe_query_flow(query_backend, decomposer, fallback=decompose_fallback)
    if planned_paths:
        described["planned_paths"] = "  ".join(f"{k}={v}" for k, v in planned_paths.items())

    print(f"\n{'=' * 60}")
    print("SEARCH PROFILE EVALUATION (pluggable flow)")
    print(f"{'=' * 60}")
    for key, value in described.items():
        print(f"{key + ':':<22}{value}")
    print(f"{'dataset:':<22}{dataset} / {subset or '(default)'}")
    print(f"{'queries:':<22}{len(queries)}")
    print(f"{'concurrency:':<22}{concurrency}")
    print(f"{'=' * 60}\n")

    # The prompt asks which sources exist so it can fill `video_sources`; the
    # dataset already knows, which beats asking VST for an inventory that may
    # include unrelated uploads.
    video_names = {s["video_name"] for segs in annotations.values() for s in segs if s.get("video_name")}
    all_results: list[dict[str, Any] | None] = [None] * len(queries)
    print_lock = threading.Lock()
    completed = [0]
    sources_seen: set[str] = set()
    verdicts: dict[str, int] = {}
    fatal: list[BaseException] = []

    def _run_single_query(qi: int, query: str) -> None:
        expected = annotations[query]

        # Decompose first, then search. The backend already routes from its
        # `decompositions` map, so writing this query's entry is the whole
        # integration -- path choice, attributes and top_k all follow. Distinct
        # keys per thread, so the shared dict needs no lock.
        decompose_s = 0.0
        if decomposer is not None:
            decomposition, decompose_s = decomposer.decompose(query, video_sources=sorted(video_names))
            query_backend.decompositions[query] = decomposition

        raw_results, latency_s = query_backend.search(query)
        normalized = flows.normalize_results(raw_results)
        # Both are read-modify-write on state shared across the pool, and the
        # verdict tally is what the no-opinion check reads at the end: lose
        # increments here and a healthy critic is reported as having formed no
        # opinion. `print_lock` already serialises the per-query output, and
        # this is the same few microseconds.
        with print_lock:
            sources_seen.update(flows.verification_sources(normalized))
            for verdict, count in flows.verdict_counts(normalized).items():
                verdicts[verdict] = verdicts.get(verdict, 0) + count

        result = flows.evaluate_query(query, flows.for_scoring(normalized), expected, latency_s)
        if decomposer is not None:
            # Kept apart from latency_s: one is the LLM deciding what to search
            # for, the other is the search. Summing them silently would hide
            # which half a regression came from.
            result["decompose_s"] = round(decompose_s, 4)
            result["total_latency_s"] = round(decompose_s + latency_s, 4)
            result["decomposition"] = query_backend.decompositions.get(query)

        # Present only when the deployment carries the search_core timings
        # change. Absent means nobody collected -- not that stages took no time.
        stage_timings = getattr(query_backend, "timings_by_query", {}).get(query)
        if stage_timings:
            result["timings"] = stage_timings

        kept, num_rejected = flows.filter_rejected(normalized)
        result["num_rejected"] = num_rejected
        result["critic_filtered"] = flows.evaluate_query(
            query, flows.for_scoring(kept), expected, latency_s
        )
        all_results[qi] = result

        with print_lock:
            completed[0] += 1
            print(f'[{completed[0]}/{len(queries)}] "{query}"  ({len(expected)} ground truth segments)')
            print(f"  {flows.format_inline(result, latency_s=latency_s)}")
            if num_rejected:
                print(
                    f"  [critic-filtered] rejected={num_rejected}  "
                    f"{flows.format_inline(result['critic_filtered'])}"
                )
            if result["missed_gt_indices"]:
                print(f"  Missed: {len(result['missed_gt_indices'])} segment(s)")
            print()

    inventory_before = flows.inventory_snapshot(vst_url) if vst_url else {"ok": False}

    #: Queries the backend never answered. Appended only from the single-
    #: threaded as_completed loop below, so no lock.
    unanswered: list[dict[str, Any]] = []

    wall_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {executor.submit(_run_single_query, qi, q): qi for qi, q in enumerate(queries)}
        for future in as_completed(futures):
            try:
                future.result()
            except flows.CliExitError as exc:
                # An environment fault, not a bad query. Scoring it as zero
                # recall would report an accuracy cliff that never happened.
                fatal.append(exc)
                for pending in futures:
                    pending.cancel()
                break
            except flows.QueryUnanswerableError as exc:
                # No answer is not no hits. Record it and leave it OUT of the
                # scored set rather than letting an empty result average in.
                qi = futures[future]
                unanswered.append({"index": qi, "query": queries[qi], "reason": str(exc)[:300]})
                print(f"  Query {qi} unanswerable: {exc}", file=sys.stderr)
            except Exception as exc:
                # Same rule for anything else that stopped this query from
                # producing a result: scoring is only valid for queries the
                # backend actually answered.
                qi = futures[future]
                unanswered.append(
                    {"index": qi, "query": queries[qi], "reason": f"{type(exc).__name__}: {exc}"[:300]}
                )
                print(f"  Query {qi} raised: {type(exc).__name__}: {exc}", file=sys.stderr)
    wall_clock_s = time.monotonic() - wall_start

    # A shared deployment can be wiped mid-run by someone else. If it was, the
    # numbers above describe an index that no longer exists.
    inventory_after = flows.inventory_snapshot(vst_url) if vst_url else {"ok": False}
    inventory = flows.compare_inventory(inventory_before, inventory_after)
    if inventory.get("stable") is False:
        print("\n" + "!" * 60)
        print("WARNING: the deployment changed DURING this run.")
        if inventory["disappeared"]:
            print(f"  disappeared: {inventory['disappeared']}")
        if inventory["appeared"]:
            print(f"  appeared:    {inventory['appeared']}")
        print("  Someone else is using this endpoint. Treat these metrics as unreliable.")
        print("!" * 60 + "\n")

    if fatal:
        raise SystemExit(f"\nABORTED: {fatal[0]}")

    results = [r for r in all_results if r is not None]
    if not results:
        raise SystemExit("ABORTED: no query produced a result.")

    # `total_queries` counts what was scored, so dropping failures quietly makes
    # a partial run look complete -- 50 unanswered out of 673 reports
    # `total_queries: 623` and reads as clean. Say it, loudly, and stop unless
    # the caller has said how many they will accept.
    if unanswered:
        print("\n" + "!" * 60)
        print(f"WARNING: {len(unanswered)}/{len(queries)} queries were never answered.")
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
                f"  {len(results)} queries under a {len(queries)}-query label. "
                "Raise the tolerance to accept that."
            )

    summary = _summarize(
        results,
        dataset=dataset,
        subset=subset,
        wall_clock_s=wall_clock_s,
        concurrency=concurrency,
        sources_seen=sources_seen,
        verdicts=verdicts,
        upload_stats=upload_stats,
        unanswered=unanswered,
        path_counts=(
            flows.path_distribution(query_backend.executed_plans)
            if getattr(query_backend, "executed_plans", None)
            else None
        ),
    )
    _print_summary(summary)

    if output_file is None:
        # Beside the script rather than relative to the caller's cwd: the old
        # default wrote to "eval/results/..." resolved from wherever you happened
        # to be, so the same command scattered results across directories.
        name = run_name or f"{dataset}_{subset or 'default'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_file = str(RESULTS_DIR / f"{name}.json")
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    output: dict[str, Any] = {
        "summary": summary,
        "config": {
            "dataset": dataset,
            "subset": subset or "default",
            "concurrency": concurrency,
            **{k: v for k, v in described.items() if k != "backend"},
        },
        "flow": {
            "query": described,
            "ingest": ingest_description,
            "readiness": readiness,
            # None when the ingest flow proved indexing itself (a chunk count),
            # so a reader can tell "not checked here" from "checked, and empty".
            "index_probe": index_probe,
            "cleared": cleared,
            "inventory": inventory,
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
        },
        "query_results": results,
    }
    if upload_stats:
        output["flow"]["upload_stats"] = upload_stats

    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
    print(f"Results saved to: {out_path.absolute()}")
    return output


def _summarize(
    results: list[dict[str, Any]],
    dataset: str,
    subset: str,
    wall_clock_s: float,
    concurrency: int,
    sources_seen: set[str],
    upload_stats: dict[str, Any] | None,
    verdicts: dict[str, int] | None = None,
    path_counts: dict[str, int] | None = None,
    unanswered: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Aggregate per-query metrics.

    Key names deliberately match ``run_eval.run_evaluation``'s summary block so
    baselines from either script line up field for field.
    """
    n = len(results)
    unanswered = unanswered or []
    def avg(key):
        return sum(r[key] for r in results) / n
    def avg_hit(k):
        return sum(r["hit_at_k"][k] for r in results) / n

    summary: dict[str, Any] = {
        "dataset": dataset,
        "subset": subset or "default",
        # `total_queries` is the SCORED count, matching run_eval.py's field so
        # baselines line up. When they differ, `queries_attempted` is what a
        # reader needs to know the metrics cover less than the dataset.
        "total_queries": n,
        "queries_attempted": n + len(unanswered),
        "mAP": round(avg("average_precision"), 4),
        "MRR": round(avg("reciprocal_rank"), 4),
        "avg_precision": round(avg("precision"), 4),
        "avg_recall": round(avg("recall"), 4),
        "avg_f1": round(avg("f1"), 4),
    }
    for k in flows.HIT_K_VALUES:
        summary[f"HIT@{k}"] = round(avg_hit(k), 4)

    # Absent, not zero, when every query was answered -- so its presence in a
    # result file is itself the signal that the metrics above are partial.
    if unanswered:
        summary["unanswered"] = {
            "count": len(unanswered),
            "reasons": dict(
                collections.Counter(e["reason"].split(":")[0] for e in unanswered)
            ),
            "queries": unanswered[:50],
        }

    # Only report critic-filtered metrics when a verification block was
    # actually present. Emitting them off an unverified response would publish
    # unfiltered numbers under a filtered label -- see flows.py for why that
    # failure mode is the one worth engineering against.
    # A verification block on every hit does not mean the critic formed an
    # opinion. Every critic failure path -- unreachable VLM, a 4xx on a clip URL
    # the VLM cannot resolve, a parse error -- degrades that candidate to
    # `unverified` and carries on, so a totally broken critic still attaches a
    # block to everything. Filtering then drops nothing and critic-filtered
    # metrics come out numerically identical to raw, which reads as "the critic
    # agreed with retrieval" when it never rendered an opinion at all.
    #
    # Observed in practice: a CLI configured with a loopback base_url hands
    # RT-VLM a `localhost` clip link, its SSRF guard returns 422, and all 121
    # queries came back unverified at exit 0.
    real_sources = sources_seen - {flows.VERIFICATION_ABSENT}
    verdicts = verdicts or {}
    opinions = verdicts.get("confirmed", 0) + verdicts.get("rejected", 0)
    if real_sources and not opinions:
        summary["critic"] = {
            "verdicts": dict(verdicts),
            "status": "no_opinion",
            "detail": (
                "Verification blocks were present but every verdict was 'unverified': "
                "the critic ran and formed no opinion. Critic-filtered metrics are "
                "suppressed because they would equal raw. Common cause: the clip URL "
                "handed to RT-VLM is not resolvable from inside its container -- check "
                "`vss configure show` is not a loopback address."
            ),
        }
        real_sources = set()
    elif real_sources:
        summary["critic"] = {"verdicts": dict(verdicts), "status": "ok"}
    if real_sources:
        def favg(key):
            return sum(r["critic_filtered"][key] for r in results) / n
        def favg_hit(k):
            return sum(r["critic_filtered"]["hit_at_k"][k] for r in results) / n
        critic_filtered: dict[str, Any] = {
            "mAP": round(favg("average_precision"), 4),
            "MRR": round(favg("reciprocal_rank"), 4),
            "avg_precision": round(favg("precision"), 4),
            "avg_recall": round(favg("recall"), 4),
            "avg_f1": round(favg("f1"), 4),
        }
        for k in flows.HIT_K_VALUES:
            critic_filtered[f"HIT@{k}"] = round(favg_hit(k), 4)
        summary["critic_filtered"] = critic_filtered
        summary["total_rejected"] = sum(r.get("num_rejected", 0) for r in results)
        summary["queries_with_rejections"] = sum(1 for r in results if r.get("num_rejected", 0) > 0)

    # A routed run mixes retrieval paths, so the headline metric averages over
    # different mechanisms. The split has to sit next to it or the number is
    # uninterpretable.
    if path_counts:
        summary["search_paths"] = path_counts

    summary["verification"] = {
        "field_sources": sorted(sources_seen),
        "available": bool(real_sources),
    }

    latencies = [r["latency_s"] for r in results if r.get("latency_s") is not None]
    if latencies:
        sorted_lat = sorted(latencies)
        summary["latency"] = {
            "mean_s": round(statistics.mean(sorted_lat), 3),
            "median_s": round(statistics.median(sorted_lat), 3),
            "min_s": round(min(sorted_lat), 3),
            "max_s": round(max(sorted_lat), 3),
            "p90_s": round(sorted_lat[min(int(len(sorted_lat) * 0.9), len(sorted_lat) - 1)], 3),
            "p95_s": round(sorted_lat[min(int(len(sorted_lat) * 0.95), len(sorted_lat) - 1)], 3),
            "std_dev_s": round(statistics.stdev(sorted_lat), 3) if len(sorted_lat) > 1 else 0.0,
        }

    # Per-stage breakdown, when the deployment reports it. This is the CLI-path
    # replacement for the Phoenix span breakdown, which does not exist here
    # because search_core emits no OTel spans.
    stage_rows: dict[str, dict[str, list[float]]] = {}
    reported = 0
    for r in results:
        stages = (r.get("timings") or {}).get("stages") or {}
        if stages:
            reported += 1
        for label, entry in stages.items():
            row = stage_rows.setdefault(label, {"total": [], "self": [], "calls": [], "conc": []})
            row["total"].append(float(entry.get("total_s", 0.0)))
            row["self"].append(float(entry.get("self_s", 0.0)))
            row["calls"].append(float(entry.get("calls", 0.0)))
            row["conc"].append(float(entry.get("concurrent_children", 0.0)))

    if stage_rows:
        # Ranked by SELF time: that is where the work actually happened. Ranking
        # by inclusive total puts wrappers on top, which says nothing.
        summary["stage_latency"] = {
            "queries_reporting": reported,
            "stages": {
                label: {
                    "self_total_s": round(sum(row["self"]), 4),
                    "self_mean_s": round(statistics.mean(row["self"]), 4),
                    "inclusive_total_s": round(sum(row["total"]), 4),
                    "inclusive_mean_s": round(statistics.mean(row["total"]), 4),
                    "queries": len(row["total"]),
                    "calls": int(sum(row["calls"])),
                    "concurrent_children": bool(any(row["conc"])),
                }
                for label, row in sorted(stage_rows.items(), key=lambda kv: -sum(kv[1]["self"]))
            },
        }

    # What the query cost outside search() itself: CLI process launch, imports
    # and the ~/.vss/config.json read. Only derivable when the deployment
    # reports timings, since it is latency minus the search's own total.
    overheads = [
        r["latency_s"] - (r.get("timings") or {}).get("total_s", 0.0)
        for r in results
        if r.get("latency_s") is not None and (r.get("timings") or {}).get("total_s")
    ]
    if overheads and "latency" in summary:
        summary["latency"]["cli_startup_mean_s"] = round(statistics.mean(overheads), 3)
        summary["latency"]["search_internal_mean_s"] = round(
            statistics.mean(
                [(r.get("timings") or {}).get("total_s", 0.0) for r in results if (r.get("timings") or {}).get("total_s")]
            ),
            3,
        )

    decompose_times = [r["decompose_s"] for r in results if r.get("decompose_s") is not None]
    if decompose_times:
        # Reported next to, not inside, the search latency: decomposition is an
        # LLM call the CLI never makes, so folding it into latency_s would make
        # runs with and without it incomparable.
        summary["decomposition"] = {
            "queries": len(decompose_times),
            "mean_s": round(statistics.mean(decompose_times), 3),
            "median_s": round(statistics.median(decompose_times), 3),
            "max_s": round(max(decompose_times), 3),
            "total_s": round(sum(decompose_times), 3),
            "share_of_query_pct": round(
                100 * sum(decompose_times) / max(sum(r["latency_s"] + r["decompose_s"] for r in results if r.get("decompose_s") is not None), 1e-9), 1
            ),
            "routed_to": flows.path_distribution(
                [flows.plan_for(r["query"], r.get("decomposition")) for r in results if r.get("decomposition")]
            ),
        }

    summary["throughput"] = {
        "wall_clock_s": round(wall_clock_s, 3),
        "qps": round(n / wall_clock_s, 3) if wall_clock_s > 0 else 0.0,
        "successful_qps": round(len(latencies) / wall_clock_s, 3) if wall_clock_s > 0 else 0.0,
        "concurrency": concurrency,
    }

    if upload_stats and upload_stats.get("total_uploads"):
        summary["upload"] = upload_stats

    summary["timestamp"] = datetime.now().isoformat()
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(f"{'=' * 60}")
    print("RESULTS SUMMARY")
    print(f"{'=' * 60}")
    print(f"Dataset:            {summary['dataset']} / {summary['subset']}")
    print(f"Queries:            {summary['total_queries']}")

    if summary.get("search_paths"):
        split = "  ".join(f"{k}={v}" for k, v in summary["search_paths"].items())
        print(f"Search paths:       {split}")

    cf = summary.get("critic_filtered")
    if cf:
        print(
            f"Rejections:         {summary['total_rejected']} across "
            f"{summary['queries_with_rejections']} queries"
        )
    else:
        print(
            "Rejections:         n/a -- no verification block in the response "
            "(critic-filtered metrics suppressed)"
        )

    def _cf(key: str) -> str:
        return f"{cf[key]:.4f}" if cf else "NA"

    print()
    print(f"{'':<20}{'Raw':<12}Critic Filtered")
    for label, key in (
        ("mAP", "mAP"),
        ("MRR", "MRR"),
        ("Avg Precision", "avg_precision"),
        ("Avg Recall", "avg_recall"),
        ("Avg F1", "avg_f1"),
    ):
        print(f"{label + ':':<20}{summary[key]:<12.4f}{_cf(key)}")
    for k in flows.HIT_K_VALUES:
        print(f"HIT@{k}:".ljust(20) + f"{summary[f'HIT@{k}']:<12.4f}{_cf(f'HIT@{k}')}")

    if "latency" in summary:
        lat = summary["latency"]
        print("\nLatency (client-observed):")
        for label, key in (
            ("Mean", "mean_s"), ("Median", "median_s"), ("Min", "min_s"),
            ("Max", "max_s"), ("P90", "p90_s"), ("P95", "p95_s"), ("Std Dev", "std_dev_s"),
        ):
            print(f"  {label + ':':<18}{lat[key]:.3f}s")
        if "cli_startup_mean_s" in lat:
            print(f"  {'  of which:':<18}")
            print(f"  {'    search:':<18}{lat['search_internal_mean_s']:.3f}s")
            print(f"  {'    CLI startup:':<18}{lat['cli_startup_mean_s']:.3f}s")

    crit = summary.get("critic")
    if crit and crit.get("status") == "no_opinion":
        print(f"\n!! CRITIC RENDERED NO OPINION  {crit['verdicts']}")
        for line in crit["detail"].split(". "):
            if line.strip():
                print(f"   {line.strip().rstrip('.')}.")

    dec = summary.get("decomposition")
    if dec:
        print(f"\nQuery decomposition ({dec['queries']} queries, live)")
        print(f"  {'Mean:':<18}{dec['mean_s']:.3f}s")
        print(f"  {'Median:':<18}{dec['median_s']:.3f}s")
        print(f"  {'Max:':<18}{dec['max_s']:.3f}s")
        print(f"  {'Share of query:':<18}{dec['share_of_query_pct']:.1f}%")
        print(f"  {'Routed to:':<18}{dec['routed_to']}")

    stage = summary.get("stage_latency")
    if stage:
        # One number per stage: time spent IN it, children excluded. Wrappers
        # like "search: embed search" contribute nothing of their own, so they
        # are hidden rather than shown at ~0 next to real work. Full detail --
        # inclusive totals, call counts, overlap flags -- stays in the JSON.
        # Filter on the MEAN, not the total: a total grows with query count, so
        # a wrapper contributing microseconds per query would reappear on a
        # large dataset.
        rows = [(k, v) for k, v in stage["stages"].items() if v["self_mean_s"] >= _STAGE_FLOOR_S]

        total_queries = stage["queries_reporting"]
        # `queries` is a column rather than a parenthetical: a stage that runs
        # only on fusion queries has its mean taken over those, and the count
        # is what stops a 5.9s mean over 3 reading like a 7.8s mean over 9.
        width = max((len(label) for label, _ in rows), default=20)
        print(f"\nStage latency ({total_queries} queries reported)")
        print(f"  {'stage':<{width}}  {'mean':>10}  {'total':>10}  {'queries':>7}")
        print(f"  {'-' * width}  {'-' * 10}  {'-' * 10}  {'-' * 7}")
        for label, v in rows:
            print(
                f"  {label:<{width}}  {v['self_mean_s']:9.3f}s  "
                f"{v['self_total_s']:9.3f}s  {v['queries']:>7}"
            )

    tp = summary["throughput"]
    print("\nThroughput:")
    print(f"  Wall clock:       {tp['wall_clock_s']:.3f}s")
    print(f"  QPS:              {tp['qps']:.3f}")
    print(f"  Successful QPS:   {tp['successful_qps']:.3f}  (excludes failed queries)")
    print(f"  Concurrency:      {tp['concurrency']}")

    if summary.get("upload"):
        flows.print_upload_summary(summary["upload"])
    print(f"{'=' * 60}\n")


# =============================================================================
# CLI
# =============================================================================


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--endpoint", required=True, help="VSS agent base URL (e.g. http://localhost:8000)")

    flow = p.add_argument_group("flow selection")
    flow.add_argument(
        "--ingest-flow",
        default="vst-direct",
        choices=sorted(flows.INGEST_BACKENDS),
        help=(
            "How fixtures are uploaded (default: vst-direct, what the UI does "
            "-- upload to VIOS and let its webhooks drive perception). "
            "'agent-3step' is the older agent-mediated flow; it returns a chunk "
            "count, which vst-direct replaces with a post-ingest index probe."
        ),
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
            "Checkout containing services/agent/packages/vss_cli. Optional -- "
            "falls back to the vendored submodule, then `vss` on PATH."
        ),
    )
    cli.add_argument("--vss-cmd", help="Override the whole vss invocation (shell-quoted)")
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
    dataset.add_argument("--dataset", default="warehouse", choices=sorted(flows.DATASETS))
    dataset.add_argument("--subset", default="", help="Dataset subset (default: '')")
    dataset.add_argument("--data-dir", type=Path, default=flows.DEFAULT_DATA_DIR)
    dataset.add_argument("--skip-download", action="store_true", help="Use the local dataset as-is")
    dataset.add_argument("--skip-ingest", action="store_true", help="Assume videos are already indexed")
    dataset.add_argument(
        "--skip-existing",
        action="store_true",
        help=(
            "Do not re-upload fixtures VST already lists. Use on shared "
            "deployments, where re-uploading creates a duplicate sensor."
        ),
    )

    query = p.add_argument_group("query parameters")
    query.add_argument("--top-k", type=int, default=5)
    query.add_argument("--min-cosine-similarity", type=float, default=0.0)
    query.add_argument("--source-type", default="video_file", choices=["video_file", "rtsp"])
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
            "Do not check that anything is searchable before scoring. Only the "
            "ingest flows that report no chunk count are probed at all, and "
            "skipping it means an unindexed deployment scores 0.0 across the "
            "board and reads as a retrieval collapse."
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
    ingest.add_argument(
        "--only-dataset",
        action="store_true",
        help=(
            "Delete every source that is NOT one of this dataset's videos, then "
            "ingest. Leaves the dataset's own sources alone. Safer than --clear "
            "on a shared deployment, and enough to make a run reproducible."
        ),
    )
    ingest.add_argument(
        "--clear",
        action="store_true",
        help=(
            "Delete ALL videos on the endpoint before ingest -- including any "
            "you did not upload. Do not use on a shared deployment."
        ),
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
            "Do not decompose. Every query then takes --search-path, which "
            "measures one retrieval path rather than the routing the product "
            "performs -- correct for a baseline, wrong for an eval."
        ),
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
    p.add_argument("--llm-model", help="Decomposition model id (default: ask the endpoint).")
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
    return p.parse_args(argv)


def main() -> None:
    args = parse_args()

    # Decomposition is the default, not an opt-in. The deployed agent decomposes
    # every query and that choice picks the retrieval path, so an eval that
    # skips it measures a flow the product never runs -- and nothing in the
    # metrics reveals it. Opting out has to be deliberate.
    if not args.llm_url and not args.no_decompose and not args.decompositions:
        args.llm_url = flows.llm_url_for(args.endpoint, args.llm_port)

    decomposer = None
    decompose_fallback: dict[str, Any] | None = None
    if args.no_decompose:
        if not args.dry_run:
            print(f"decomposition: OFF (--no-decompose) -- every query uses --search-path {args.search_path}")
    elif args.decompositions:
        if not args.dry_run:
            print(f"decomposition: from {args.decompositions} -- not live")
    elif args.llm_url and not args.dry_run:
        try:
            decomposer = flows.LiveDecomposer(
                args.llm_url, repo_root=flows.REPO_ROOT, model=args.llm_model
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
            # Fall back to --search-path, which is `embed`: the one path that
            # needs nothing but the query text. `attribute` and `fusion` both
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
                carried = len(flows.unpack_dataset(
                    flows.load_dataset_file(args.data_dir, args.dataset, args.subset)
                )[1])
            if carried:
                decompose_fallback["fell_back_to"] = f"{carried} decomposition(s) carried by the dataset"
                print(f"           the dataset carries {carried} decomposition(s) -- routing still per-query")
            else:
                decompose_fallback["fell_back_to"] = f"--search-path {args.search_path}"
                print(f"           every query will use --search-path {args.search_path}.")
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

    ingest_backend = build_ingest_backend(args)
    query_backend = build_query_backend(args)

    if args.dry_run:
        print("Ingest backend:")
        print(json.dumps(ingest_backend.describe(), indent=2))
        print("\nQuery backend:")
        print(json.dumps(describe_query_flow(
            query_backend, decomposer, None if args.no_decompose else args.llm_url
        ), indent=2))
        if isinstance(query_backend, flows.CliQueryBackend):
            import shlex

            print("\nSample invocation:")
            print("  " + shlex.join(query_backend.build_argv("a person in a white jacket")))
        print("\nDry run - nothing executed.")
        return

    print(f"\n{'=' * 60}")
    print("SEARCH EVAL - PLUGGABLE FLOWS")
    print(f"{'=' * 60}")
    print(f"Endpoint:       {args.endpoint}")
    print(f"Ingest flow:    {args.ingest_flow}")
    print(f"Query flow:     {args.query_flow}")
    # Deliberately no search-path line here. This banner prints before the
    # dataset is read, so it cannot know whether the dataset carries
    # decompositions -- it could only report the fallback, which reads as
    # "every query used this". The SEARCH PROFILE EVALUATION header below runs
    # after the dataset loads and reports routing, fallback_path and the
    # planned per-path split accurately.
    print(f"{'=' * 60}\n")

    if not args.skip_download:
        print("Downloading dataset from DSS...")
        flows.download_from_dss(args.data_dir, args.dataset)

    # VST is a separate origin from the agent. run_eval.py's convention (swap
    # the port on the same host) is reused so both scripts resolve it the same
    # way; --vst-url overrides it for deployments that route differently.
    # Resolved BEFORE --clear so the destructive path uses the same origin.
    vst_url = args.vst_url or flows.vst_url_for(args.endpoint, args.vst_port)
    print(f"VST origin:     {vst_url}")

    cleared: dict[str, Any] | None = None
    video_dir = args.data_dir / args.dataset / "videos"
    if args.clear:
        print("\nClearing ALL existing videos (--clear)...")
        cleared = clear_all_videos(args.endpoint, vst_url)
    elif args.only_dataset:
        print("\nPruning sources that are not in this dataset (--only-dataset)...")
        cleared = prune_foreign_videos(args.endpoint, vst_url, video_dir)

    # A source that would not delete is still searchable, so it still answers
    # queries and still counts as a false positive. Continuing would score a run
    # that claims to isolate this dataset against an index that does not.
    if cleared and cleared.get("failed"):
        raise SystemExit(
            f"ABORTED: {len(cleared['failed'])} source(s) could not be deleted: "
            f"{cleared['failed'][:10]}\n"
            "  They remain searchable and would be scored as false positives, so "
            "this run cannot claim\n"
            "  to measure the selected dataset in isolation. Delete them by hand, "
            "or use --skip-existing."
        )

    upload_stats: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None
    index_probe: dict[str, Any] | None = None

    if args.skip_ingest:
        print("Skipping ingest (--skip-ingest)")
    else:
        print(f"\nIngesting videos from {video_dir}")
        upload_stats = ingest_videos(
            ingest_backend,
            video_dir,
            skip_existing_from=vst_url if args.skip_existing else None,
        )
        flows.print_upload_summary(upload_stats)

        if upload_stats.get("failed"):
            raise SystemExit(
                f"ABORTED: {upload_stats['failed']} upload(s) failed. "
                "Scoring against a partial index would misreport retrieval quality."
            )

        if not args.skip_readiness_wait:
            # Every fixture must be queryable, not just the ones uploaded this
            # run -- with --skip-existing the rest were skipped precisely
            # because they are already there, and they still get searched.
            expected = [
                r["video_name"] for r in upload_stats.get("per_file", []) if r.get("success")
            ] + list(upload_stats.get("skipped_existing") or [])
            print(f"\nWaiting for {len(expected)} source(s) to register in VST...")
            readiness = flows.wait_for_sources(
                vst_url, expected, timeout_s=args.readiness_timeout
            )
            if readiness["ready"]:
                print(f"  All sources registered after {readiness['attempts']} poll(s).")
            else:
                raise SystemExit(
                    f"ABORTED: {len(readiness['missing'])} source(s) never registered within "
                    f"{args.readiness_timeout}s: {readiness['missing']}\n"
                    "  Querying now would score an indexing delay as a retrieval regression."
                )

        # Registered in VST is not the same as indexed in Elasticsearch. The
        # three-step flow already proved the difference with a chunk count; a
        # flow that reports no proof has to be asked.
        # The anchor is the other half of "is this searchable": ground truth is
        # matched by absolute-timestamp overlap against 2025-01-01, so media
        # indexed at any other instant scores every query zero while looking
        # perfectly healthy to the coverage probe. One timeline read on one
        # uploaded video is the whole check, and it was defined and tested but
        # never called until now.
        anchor_check = getattr(ingest_backend, "verify_anchor", None)
        first_sensor = next(
            (r.get("sensor_id") for r in upload_stats.get("per_file", []) if r.get("sensor_id")),
            None,
        )
        if anchor_check and first_sensor and not args.skip_index_probe:
            anchor = anchor_check(first_sensor)
            if anchor.get("found") and not anchor.get("matches_expected_anchor"):
                raise SystemExit(
                    f"ABORTED: {first_sensor} is indexed at {anchor.get('start_time')!r}, not at "
                    f"the {anchor.get('expected_anchor')!r} anchor the ground truth is written "
                    "against.\n"
                    "  Every query would score 0.0 and read as a retrieval collapse. VIOS did not "
                    "honour the\n"
                    "  upload's metadata.timestamp -- check --upload-timestamp and the VIOS "
                    "storage config."
                )
            if anchor.get("checked") and anchor.get("found"):
                print(f"  Anchor confirmed at {anchor.get('start_time')}.")
            else:
                print(f"  (anchor unverified: {anchor})")

        if not ingest_backend.proves_indexing and not args.skip_index_probe:
            print(f"\nProbing index coverage for {len(expected)} source(s)...")
            index_probe = probe_index_coverage(
                query_backend,
                expected,
                vst_url=vst_url,
                attempts=args.index_probe_attempts,
                backoff_s=args.index_probe_backoff,
                top_k_per_source=args.index_probe_top_k,
            )
            if index_probe.get("checked") and not index_probe.get("covered"):
                detail = (
                    f" ({index_probe['error']})"
                    if index_probe.get("error")
                    else f"; {index_probe.get('missing_count')} still missing, e.g. "
                    f"{index_probe.get('missing', [])[:5]}"
                )
                raise SystemExit(
                    "ABORTED: uploads succeeded and every source registered in VST, but the "
                    f"index does not cover them after {index_probe['attempts']} attempt(s)"
                    + detail
                    + ".\n"
                    "  Registered is not indexed: a source registered by an EARLIER run "
                    "satisfies the readiness poll\n"
                    "  while this run's re-ingest is still being embedded, and querying now "
                    "scores that as a retrieval regression.\n"
                    "  Raise --index-probe-attempts if perception is merely slow. Otherwise "
                    "check webhooks.enabled in\n"
                    "  the VIOS notification config (false in the Helm chart), and that "
                    "RTVI_EMBED_MODEL matches the webhook's\n"
                    "  model string -- RT-Embed answers a mismatch with 200 and "
                    "inference=false."
                )

    run_evaluation(
        query_backend=query_backend,
        data_dir=args.data_dir,
        dataset=args.dataset,
        subset=args.subset,
        decomposer=decomposer,
        decompose_fallback=decompose_fallback,
        tolerate_unanswered=args.tolerate_unanswered,
        output_file=args.output_file,
        run_name=args.name,
        concurrency=args.concurrency,
        upload_stats=upload_stats,
        ingest_description=ingest_backend.describe(),
        readiness=readiness,
        index_probe=index_probe,
        cleared=cleared,
        vst_url=vst_url,
    )


if __name__ == "__main__":
    main()

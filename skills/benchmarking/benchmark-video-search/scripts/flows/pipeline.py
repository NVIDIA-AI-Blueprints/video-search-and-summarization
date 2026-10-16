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

"""The eval pipeline as separable stages: ingest -> retrieve -> score.

These are the bodies of the old monolithic ``run_eval_flows.main`` /
``run_evaluation``, split at the retrieve/score seam so each stage can run (and
later, be serialized) independently. The seam is the
:class:`~flows.artifacts.RetrievalArtifact`: retrieve records the RAW normalized
hits and the run-level aggregates; score turns them into metrics. Because scoring
reads only the artifact and the dataset, a run can be re-scored without touching
the deployment.

The default ``run_pipeline`` chains all three, which is byte-for-byte the old
behaviour -- the parity fixture pins that.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

import flows
from flows.artifacts import (
    RESULTS_DIR,
    IngestManifest,
    QueryRecord,
    RetrievalArtifact,
    RunConfig,
    ScoredResult,
    read_ingest_manifest,
    read_retrieval_artifact,
    write_ingest_manifest,
    write_retrieval_artifact,
)
from flows.scoring import SCORING_BACKENDS
from flows.scoring.gallery import load_gallery_gt

#: Stages whose own time is below this are wrappers or noise; the console hides
#: them so the table reads as "where did the time go" without a nesting caveat.
_STAGE_FLOOR_S = 0.001

#: What the probe asks. Any text works -- embedding search returns the nearest
#: `top_k` regardless of how poorly they match, so what comes back is a sample
#: of what is indexed, not a judgement about relevance. Deliberately generic so
#: it carries no assumption about which dataset was ingested.
PROBE_QUERY = "a person"


# =============================================================================
# Ingest helpers (moved verbatim from run_eval_flows.py)
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
        raise SystemExit(
            f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})"
        ) from e

    if not streams:
        print("  No videos found to delete.")
        return {"found": 0, "deleted": 0, "names": []}

    print(f"  Deleting {len(streams)} video(s) from {agent_endpoint}:")
    for stream_id, name in streams.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in streams.items():
        try:
            resp = requests.delete(
                f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60
            )
            resp.raise_for_status()
            deleted += 1
            print(f"    deleted {name} -> {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    print(f"  Deleted {deleted}/{len(streams)} video(s)")
    return {
        "found": len(streams),
        "deleted": deleted,
        "failed": failed,
        "names": list(streams.values()),
    }


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
        critic_eval_count=1,
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
            # `list_sensor_streams`, NOT `list_sensor_names`: the latter
            # lowercases for case-insensitive membership tests, and that value
            # would go on to `--video-source`, where `should_clauses_for_source`
            # matches it with `term`/`wildcard` against ES `.keyword` fields.
            # Those are case-sensitive, so a lowercased "assault036_x264" never
            # matches the stored "Assault036_x264" -- every scoped probe comes
            # back empty and aborts a fully indexed run. physicalai-dev is named
            # exactly that way.
            names = list(flows.list_sensor_streams(vst_url).values())
            for source in expected_sources:
                # Use the same complete-identity rule as scoring. VST may add
                # an upload suffix, and the source-scoped query must use VST's
                # original spelling for case-sensitive ES keyword fields.
                match = next(
                    (
                        n
                        for n in names
                        if flows.video_name_matches(n.lower(), source.lower())
                    ),
                    None,
                )
                if match:
                    registered[source] = match
        except Exception as e:  # noqa: BLE001
            print(
                f"  (could not resolve VST source names, per-source probing off: {e})"
            )

    broad = _probe_backend(
        query_backend, min(1000, max(50, top_k_per_source * len(expected_sources)))
    )
    missing = list(expected_sources)
    last_index_error: str | None = None

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
                scoped_hits, _ = _probe_backend(query_backend, 1, source=name).search(
                    PROBE_QUERY
                )
                if not any(
                    flows.video_name_matches(hit.get("video_name", ""), source)
                    for hit in scoped_hits
                ):
                    confirmed_missing.append(source)
            missing = confirmed_missing
            last_index_error = None
        except flows.CliExitError as e:
            if e.returncode != 5:
                return {
                    "checked": True,
                    "covered": False,
                    "attempts": attempt,
                    "error": f"{type(e).__name__}: {e}",
                }
            # VIOS registration can precede creation of the embedding index.
            # Exit 5 is a readiness state for this probe, not a zero-hit result.
            last_index_error = f"{type(e).__name__}: {e}"
            missing = list(expected_sources)
            if attempt < attempts:
                print(
                    f"  Probe {attempt}/{attempts}: search index not ready; retrying in {backoff_s:.0f}s"
                )
                time.sleep(backoff_s)
            continue
        except Exception as e:  # noqa: BLE001
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
            print(
                f"  Index covers all {len(expected_sources)} source(s) after {attempt} attempt(s)."
            )
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

    result = {
        "checked": True,
        "covered": False,
        "attempts": attempts,
        "sources": len(expected_sources),
        "missing": missing[:20],
        "missing_count": len(missing),
        "per_source_verified": bool(registered),
    }
    if last_index_error:
        result["error"] = last_index_error
    return result


def expected_sources_from_upload(upload_stats: dict[str, Any]) -> list[str]:
    """Sources post-ingest probes cover even when the VST wait is skipped."""
    return [
        row["video_name"]
        for row in upload_stats.get("per_file", [])
        if row.get("success")
    ] + list(upload_stats.get("skipped_existing") or [])


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


def prune_foreign_videos(
    agent_endpoint: str, vst_url: str, video_dir: Path
) -> dict[str, Any]:
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
        raise SystemExit(
            f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})"
        ) from e

    ours: set[str] = set()
    for vf in sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mkv")):
        ours |= flows.name_variants(vf.name)
    if not ours:
        raise SystemExit(
            f"ABORTED: no video files (.mp4 or .mkv) under {video_dir}, so every source looks foreign. "
            f"Refusing to delete the whole deployment -- pass --clear if that is what you meant."
        )

    foreign = {sid: n for sid, n in streams.items() if str(n).lower() not in ours}
    kept = len(streams) - len(foreign)
    print(
        f"  {len(streams)} source(s) registered: {kept} belong to this dataset, {len(foreign)} do not"
    )
    if not foreign:
        print("  Nothing to prune.")
        return {"found": len(streams), "kept": kept, "deleted": 0, "names": []}

    for stream_id, name in foreign.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in foreign.items():
        try:
            resp = requests.delete(
                f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60
            )
            resp.raise_for_status()
            deleted += 1
            print(f"    deleted {name} -> {resp.status_code}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    print(f"  Pruned {deleted}/{len(foreign)} foreign video(s); kept {kept}")
    return {
        "found": len(streams),
        "kept": kept,
        "deleted": deleted,
        "failed": failed,
        "names": list(foreign.values()),
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
        except Exception as e:  # noqa: BLE001
            print(
                f"  WARNING: could not read VST sensor list ({type(e).__name__}: {e}); ingesting all"
            )

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
                    " + ".join(
                        f"{k.removesuffix('_s')}={v}s"
                        for k, v in record["phases"].items()
                    )
                )
            suffix = f"  |  {'  |  '.join(extras)}" if extras else ""
            print(
                f"    OK  {record['upload_latency_s']}s{suffix}  (sensor: {record.get('sensor_id')})"
            )
        else:
            print(f"    FAILED: {record.get('error')}")

    stats = flows.aggregate_upload_stats(per_file)
    stats["skipped_existing"] = skipped
    print(
        f"  Ingest complete: {stats['successful']}/{stats['total_uploads']} succeeded"
    )
    return stats


# =============================================================================
# Stage: ingest
# =============================================================================


def run_ingest(
    cfg: RunConfig, ingest_backend: Any, query_backend: Any
) -> IngestManifest:
    """Clear/prune, upload fixtures, then prove the index before any scoring.

    Returns an :class:`IngestManifest`; on ``--skip-ingest`` all fields but the
    backend description are ``None``. Aborts (``SystemExit``) on a failed delete,
    a failed upload, a readiness timeout, a wrong anchor, or an unindexed
    deployment -- the same hard stops the monolith had, so a partial index can
    never be scored as a retrieval collapse.
    """
    manifest = IngestManifest(ingest_description=ingest_backend.describe())
    video_dir = cfg.data_dir / cfg.dataset / "videos"

    cleared: dict[str, Any] | None = None
    if cfg.clear:
        print("\nClearing ALL existing videos (--clear)...")
        cleared = clear_all_videos(cfg.endpoint, cfg.vst_url)
    elif cfg.only_dataset:
        print("\nPruning sources that are not in this dataset (--only-dataset)...")
        cleared = prune_foreign_videos(cfg.endpoint, cfg.vst_url, video_dir)

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
    manifest.cleared = cleared

    if cfg.gallery_gt:
        # Clip-level gallery ingest: upload the gallery's own clips and write the
        # clip->sensor manifest the clip scorers need, so a single skill run
        # produces both the indexed gallery and its identity mapping.
        gallery_gt = load_gallery_gt(cfg.gallery_gt)
        video_dir = next(
            (
                Path(row["video_path"]).parent
                for row in gallery_gt["gallery"]
                if row.get("video_path")
            ),
            Path(cfg.gallery_gt) / "clips",
        )
        upload_stats = ingest_videos(
            ingest_backend,
            video_dir,
            skip_existing_from=cfg.vst_url if cfg.skip_existing else None,
        )
        flows.print_upload_summary(upload_stats)
        # Map each uploaded clip (by filename stem) to its gallery chunk_id + sensor.
        by_stem = {Path(row["video"]).stem: row for row in gallery_gt["gallery"]}
        clips = []
        for record in upload_stats.get("per_file", []):
            if not record.get("success"):
                continue
            row = by_stem.get(Path(record["video_name"]).stem)
            if row is None:
                continue
            clips.append(
                {
                    "dataset_id": row["chunk_id"],
                    "sensor_id": record.get("sensor_id"),
                    "filename": Path(row.get("video") or record["video_name"]).name,
                }
            )
        manifest_path = cfg.ingest_out or str(
            RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.ingestion.json"
        )
        out_path = Path(manifest_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps({"clips": clips}, indent=2) + "\n")
        print(f"Ingestion manifest saved to: {out_path.absolute()}")
        manifest.upload_stats = upload_stats
        expected = expected_sources_from_upload(upload_stats)
    elif cfg.skip_ingest:
        print("Skipping ingest (--skip-ingest)")
        return manifest
    else:
        print(f"\nIngesting videos from {video_dir}")
        upload_stats = ingest_videos(
            ingest_backend,
            video_dir,
            skip_existing_from=cfg.vst_url if cfg.skip_existing else None,
        )
        flows.print_upload_summary(upload_stats)
        manifest.upload_stats = upload_stats
        expected = expected_sources_from_upload(upload_stats)

    if upload_stats.get("failed"):
        raise SystemExit(
            f"ABORTED: {upload_stats['failed']} upload(s) failed. "
            "Scoring against a partial index would misreport retrieval quality."
        )

    # Needed by both the optional VST wait and the mandatory index probe.
    # Defining it inside the wait made --skip-readiness-wait crash later.
    if not cfg.skip_readiness_wait:
        # Every fixture must be queryable, not just the ones uploaded this
        # run -- with --skip-existing the rest were skipped precisely
        # because they are already there, and they still get searched.
        print(f"\nWaiting for {len(expected)} source(s) to register in VST...")
        readiness = flows.wait_for_sources(
            cfg.vst_url, expected, timeout_s=cfg.readiness_timeout
        )
        if readiness["ready"]:
            print(f"  All sources registered after {readiness['attempts']} poll(s).")
        else:
            raise SystemExit(
                f"ABORTED: {len(readiness['missing'])} source(s) never registered within "
                f"{cfg.readiness_timeout}s: {readiness['missing']}\n"
                "  Querying now would score an indexing delay as a retrieval regression."
            )
        manifest.readiness = readiness

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
        (
            r.get("sensor_id")
            for r in upload_stats.get("per_file", [])
            if r.get("sensor_id")
        ),
        None,
    )
    if anchor_check and first_sensor and not cfg.skip_index_probe:
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

    if not ingest_backend.proves_indexing and not cfg.skip_index_probe:
        print(f"\nProbing index coverage for {len(expected)} source(s)...")
        index_probe = probe_index_coverage(
            query_backend,
            expected,
            vst_url=cfg.vst_url,
            attempts=cfg.index_probe_attempts,
            backoff_s=cfg.index_probe_backoff,
            top_k_per_source=cfg.index_probe_top_k,
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
                "  the deployed VIOS notification config, and that "
                "RTVI_EMBED_MODEL matches the webhook's\n"
                "  model string -- RT-Embed answers a mismatch with 200 and "
                "inference=false."
            )
        manifest.index_probe = index_probe

    return manifest


# =============================================================================
# Stage: retrieve
# =============================================================================


def run_retrieve(
    cfg: RunConfig,
    query_backend: Any,
    decomposer: Any,
    decompose_fallback: dict[str, Any] | None,
    ingest: IngestManifest,
) -> RetrievalArtifact:
    """Decompose + search + normalize every dataset query; record raw hits.

    No scoring happens here. The returned artifact carries the normalized hits
    and the run-level aggregates (``sources_seen``, ``verdicts``,
    ``wall_clock_s``) so the score stage -- or a later re-score -- sees exactly
    what retrieval observed.

    Per-query console lines report retrieval progress (hits, latency), not
    scores; the scored per-query table is printed by :func:`run_score`.
    """
    if cfg.data_dir is None or cfg.dataset is None or cfg.subset is None:
        raise SystemExit(
            "run_retrieve needs a concrete dataset/subset/data_dir; they are "
            "resolved for the run/retrieve/ingest stages and only None for "
            "--stage score (which never retrieves). This stage was reached "
            "without them."
        )

    if cfg.gallery_gt:
        # Clip-level scoring (clip-relevance / drive) runs the gallery's own
        # queries against the ingested gallery, not an eval dataset. There is no
        # segment ground truth here; scoring reads relevance from the gallery GT.
        gallery_gt = load_gallery_gt(cfg.gallery_gt)
        queries = [q["query"] for q in gallery_gt["queries"]]
        annotations = {q: [] for q in queries}
        dataset_decompositions = {}
        gallery_sources = {Path(row["video"]).stem for row in gallery_gt["gallery"]}
        # Record what this artifact actually is, so a scorer can refuse the wrong kind.
        query_source = "gallery"
        artifact_dataset = (
            gallery_gt.get("meta", {}).get("set")
            or gallery_gt.get("meta", {}).get("derived_from")
            or Path(cfg.gallery_gt).stem
        )
        artifact_gallery_gt = cfg.gallery_gt
    else:
        data = flows.load_dataset_file(cfg.data_dir, cfg.dataset, cfg.subset)
        # Decompositions live in the dataset next to the ground truth they belong
        # to. --decompositions stays available for A/B-ing a different set (say,
        # agent-captured vs hand-written) against the same annotations, so an
        # explicit flag wins over what the dataset carries.
        annotations, dataset_decompositions = flows.unpack_dataset(data)
        queries = list(annotations.keys())
        query_source = "eval-dataset"
        artifact_dataset = cfg.dataset
        artifact_gallery_gt = None

    if (
        cfg.use_dataset_decompositions
        and dataset_decompositions
        and hasattr(query_backend, "decompositions")
    ):
        if query_backend.decompositions:
            print(
                f"NOTE: --decompositions ({len(query_backend.decompositions)}) overrides the "
                f"{len(dataset_decompositions)} carried by the dataset"
            )
        else:
            query_backend.decompositions = dataset_decompositions
            print(
                f"Using {len(dataset_decompositions)} decomposition(s) from the dataset"
            )

    # Compute the planned split over the ACTUAL query list, so the header
    # states what will run rather than only the fallback.
    planned_paths: dict[str, int] = {}
    if hasattr(query_backend, "plan_for_query"):
        planned_paths = flows.path_distribution(
            [query_backend.plan_for_query(q) for q in queries]
        )

    described = describe_query_flow(
        query_backend, decomposer, fallback=decompose_fallback
    )
    if planned_paths:
        described["planned_paths"] = "  ".join(
            f"{k}={v}" for k, v in planned_paths.items()
        )

    print(f"\n{'=' * 60}")
    print("SEARCH PROFILE EVALUATION (pluggable flow)")
    print(f"{'=' * 60}")
    for key, value in described.items():
        print(f"{key + ':':<22}{value}")
    print(f"{'dataset:':<22}{cfg.dataset} / {cfg.subset or '(default)'}")
    print(f"{'queries:':<22}{len(queries)}")
    print(f"{'concurrency:':<22}{cfg.concurrency}")
    print(f"{'=' * 60}\n")

    # The prompt asks which sources exist so it can fill `video_sources`; the
    # dataset already knows, which beats asking VST for an inventory that may
    # include unrelated uploads.
    if cfg.gallery_gt:
        video_names = gallery_sources
    else:
        video_names = {
            s["video_name"]
            for segs in annotations.values()
            for s in segs
            if s.get("video_name")
        }
    records: list[QueryRecord | None] = [None] * len(queries)
    print_lock = threading.Lock()
    completed = [0]
    sources_seen: set[str] = set()
    verdicts: dict[str, int] = {}
    fatal: list[BaseException] = []

    def _retrieve_single(qi: int, query: str) -> None:
        # Decompose first, then search. The backend already routes from its
        # `decompositions` map, so writing this query's entry is the whole
        # integration -- path choice, attributes and top_k all follow. Distinct
        # keys per thread, so the shared dict needs no lock.
        decompose_s = 0.0
        decomposition: dict[str, Any] | None = None
        if decomposer is not None:
            decomposition, decompose_s = decomposer.decompose(
                query, video_sources=sorted(video_names)
            )
            query_backend.decompositions[query] = decomposition
        elif hasattr(query_backend, "decompositions"):
            decomposition = query_backend.decompositions.get(query)

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

        # Present only when the deployment carries the search_core timings
        # change. Absent means nobody collected -- not that stages took no time.
        stage_timings = getattr(query_backend, "timings_by_query", {}).get(query)
        plan = (
            query_backend.plan_for_query(query)
            if hasattr(query_backend, "plan_for_query")
            else None
        )
        records[qi] = QueryRecord(
            index=qi,
            query=query,
            status="answered",
            plan=plan,
            decomposition=decomposition,
            decompose_s=decompose_s if decomposer is not None else None,
            search_latency_s=latency_s,
            timings=stage_timings or None,
            # _raw (the original wire hit) duplicates the normalized fields and
            # roughly doubles the artifact; scoring never reads it (for_scoring
            # strips all _-prefixed keys), so keep only the normalized form.
            hits=[{k: v for k, v in hit.items() if k != "_raw"} for hit in normalized],
        )

        with print_lock:
            completed[0] += 1
            print(
                f'[{completed[0]}/{len(queries)}] "{query}"  ({len(normalized)} hits, {latency_s:.2f}s)'
            )

    inventory_before = (
        flows.inventory_snapshot(cfg.vst_url) if cfg.vst_url else {"ok": False}
    )

    #: Queries the backend never answered. Appended only from the single-
    #: threaded as_completed loop below, so no lock.
    unanswered: list[dict[str, Any]] = []

    wall_start = time.monotonic()
    with ThreadPoolExecutor(max_workers=cfg.concurrency) as executor:
        futures = {
            executor.submit(_retrieve_single, qi, q): qi for qi, q in enumerate(queries)
        }
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
                reason = str(exc)[:300]
                unanswered.append({"index": qi, "query": queries[qi], "reason": reason})
                records[qi] = QueryRecord(
                    index=qi, query=queries[qi], status="unanswerable", error=reason
                )
                print(f"  Query {qi} unanswerable: {exc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                # Same rule for anything else that stopped this query from
                # producing a result: scoring is only valid for queries the
                # backend actually answered.
                qi = futures[future]
                reason = f"{type(exc).__name__}: {exc}"[:300]
                unanswered.append({"index": qi, "query": queries[qi], "reason": reason})
                records[qi] = QueryRecord(
                    index=qi, query=queries[qi], status="unanswerable", error=reason
                )
                print(
                    f"  Query {qi} raised: {type(exc).__name__}: {exc}", file=sys.stderr
                )
    wall_clock_s = time.monotonic() - wall_start

    # A shared deployment can be wiped mid-run by someone else. If it was, the
    # numbers above describe an index that no longer exists.
    inventory_after = (
        flows.inventory_snapshot(cfg.vst_url) if cfg.vst_url else {"ok": False}
    )
    inventory = flows.compare_inventory(inventory_before, inventory_after)
    if inventory.get("stable") is False:
        print("\n" + "!" * 60)
        print("WARNING: the deployment changed DURING this run.")
        if inventory["disappeared"]:
            print(f"  disappeared: {inventory['disappeared']}")
        if inventory["appeared"]:
            print(f"  appeared:    {inventory['appeared']}")
        print(
            "  Someone else is using this endpoint. Treat these metrics as unreliable."
        )
        print("!" * 60 + "\n")

    if fatal:
        raise SystemExit(f"\nABORTED: {fatal[0]}")

    return RetrievalArtifact(
        dataset=artifact_dataset,
        subset=cfg.subset,
        data_dir=str(cfg.data_dir),
        concurrency=cfg.concurrency,
        described=described,
        queries=[r for r in records if r is not None],
        unanswered=unanswered,
        executed_plans=list(getattr(query_backend, "executed_plans", []) or []),
        wall_clock_s=wall_clock_s,
        sources_seen=sorted(sources_seen),
        verdicts=dict(verdicts),
        inventory=inventory,
        ingest=ingest,
        tolerate_unanswered=cfg.tolerate_unanswered,
        run_id=cfg.run_name,
        query_source=query_source,
        gallery_gt=artifact_gallery_gt,
    )


# =============================================================================
# Stage: score
# =============================================================================


def run_score(cfg: RunConfig, artifact: RetrievalArtifact) -> ScoredResult:
    """Turn the retrieval artifact into metrics via the selected scoring backend.

    Reads only the artifact and the ground truth the backend loads -- never the
    deployment. The backend owns the metric model (``--scoring``); this stage owns
    writing the result file. ``scoring.model`` on the output tells a reader which
    shape the summary is in (absent means segment-overlap).
    """
    backend_cls = SCORING_BACKENDS.get(cfg.scoring)
    if backend_cls is None:
        raise SystemExit(
            f"Unknown scoring model: {cfg.scoring!r} (have: {sorted(SCORING_BACKENDS)})"
        )
    backend = backend_cls()
    ground_truth = backend.load_ground_truth(cfg, artifact)
    result = backend.score(artifact, ground_truth, cfg)

    # Writing the result file is the stage's job, shared by every backend. The
    # default name comes from the artifact's own dataset identity, so a
    # score-from-artifact run is named for the run that produced it.
    if cfg.output_file is None:
        output_file = str(
            RESULTS_DIR / f"{_run_id(cfg, artifact.dataset, artifact.subset)}.json"
        )
    else:
        output_file = cfg.output_file
    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(result.to_output(), f, indent=2)
    print(f"Results saved to: {out_path.absolute()}")
    return result


# =============================================================================
# Orchestration
# =============================================================================


def _run_id(cfg: RunConfig, dataset: str, subset: str) -> str:
    """The base name shared by a run's artifact and result files."""
    return (
        cfg.run_name
        or f"{dataset}_{subset or 'default'}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"
    )


def run_pipeline(
    cfg: RunConfig,
    ingest_backend: Any,
    query_backend: Any,
    decomposer: Any,
    decompose_fallback: dict[str, Any] | None,
) -> ScoredResult | None:
    """Dispatch on ``cfg.stage``.

    ``run`` (default) chains ingest -> retrieve -> score in memory and is the
    byte-identical old behaviour; it additionally dumps the retrieval artifact
    when ``--retrieval-out`` is given, so that one retrieval can be re-scored
    later. The other stages run one step against an on-disk artifact and stop:

        ingest    -> write the ingest manifest (``--ingest-out``)
        retrieve  -> read ``--ingest-in`` (optional), write the retrieval artifact
        score     -> read ``--retrieval-in`` (required), write the scored result
    """
    stage = cfg.stage

    if stage == "ingest":
        manifest = run_ingest(cfg, ingest_backend, query_backend)
        out = cfg.ingest_out or str(
            RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.ingest.json"
        )
        if cfg.gallery_gt:
            # run_ingest already wrote the clip->sensor manifest to --ingest-out;
            # the IngestManifest (flow context) rides in the retrieval artifact, not a file.
            print(f"Ingestion manifest saved to: {out}")
        else:
            write_ingest_manifest(out, manifest)
            print(f"Ingest manifest saved to: {out}")
        return None

    if stage == "retrieve":
        manifest = (
            read_ingest_manifest(cfg.ingest_in) if cfg.ingest_in else IngestManifest()
        )
        artifact = run_retrieve(
            cfg, query_backend, decomposer, decompose_fallback, manifest
        )
        out = cfg.retrieval_out or str(
            RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.retrieval.json"
        )
        write_retrieval_artifact(out, artifact)
        print(f"Retrieval artifact saved to: {out}")
        return None

    if stage == "score":
        artifact = read_retrieval_artifact(cfg.retrieval_in)
        return run_score(cfg, artifact)

    manifest = run_ingest(cfg, ingest_backend, query_backend)
    artifact = run_retrieve(
        cfg, query_backend, decomposer, decompose_fallback, manifest
    )
    if cfg.retrieval_out:
        write_retrieval_artifact(cfg.retrieval_out, artifact)
        print(f"Retrieval artifact saved to: {cfg.retrieval_out}")
    return run_score(cfg, artifact)

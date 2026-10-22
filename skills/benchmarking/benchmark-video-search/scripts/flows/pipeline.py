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

import copy
import hashlib
import inspect
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests

import flows
from flows.artifacts import (
    RESULTS_DIR,
    IngestManifest,
    QueryRecord,
    RetrievalArtifact,
    RunConfig,
    ScoredResult,
    atomic_write_json,
    read_ingest_manifest,
    read_retrieval_artifact,
    write_ingest_manifest,
    write_retrieval_artifact,
)
from flows.preflight import (
    check_inputs_before_wipe,
    check_reused_index,
    dataset_video_names,
    decomposition_sources,
    file_sources,
)
from flows.scoring import SCORING_BACKENDS
from flows.scoring.gallery import load_gallery_gt, validate_gallery_queries

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


def _delete_video(agent_endpoint: str, stream_id: str) -> None:
    """HTTP success alone does not establish that every deletion leg succeeded."""
    resp = requests.delete(
        f"{agent_endpoint.rstrip('/')}/api/v1/videos/{stream_id}", timeout=60
    )
    resp.raise_for_status()
    payload = resp.json()
    if not isinstance(payload, dict) or payload.get("status") != "success":
        raise RuntimeError(f"video cleanup did not succeed: {payload!r}")


def _verify_deleted(
    vst_url: str, targets: dict[str, str], *, require_empty: bool = False
) -> list[str]:
    """Verify targeted registrations disappeared; index reset is a separate operation."""
    remaining, _ = file_sources(vst_url)
    target_names = set(targets.values())
    return [
        name
        for sid, name in remaining.items()
        if require_empty or sid in targets or name in target_names
    ]


def clear_all_videos(agent_endpoint: str, vst_url: str) -> dict[str, Any]:
    """Delete every video registered on the deployment.

    Lists and deletes against the SAME resolved origins the rest of the run
    uses, so ``--vst-url`` cannot list one host and delete from another.
    Prints the full inventory first: ``--clear`` is destructive and shared
    deployments carry other people's fixtures.
    """
    try:
        streams, kept_rtsp = file_sources(vst_url)
    except Exception as e:
        raise SystemExit(
            f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})"
        ) from e

    if not streams:
        print("  No videos found to delete.")
        return {"found": 0, "deleted": 0, "names": [], "kept_rtsp": kept_rtsp}

    print(f"  Deleting {len(streams)} video(s) from {agent_endpoint}:")
    for stream_id, name in streams.items():
        print(f"    - {name}  ({stream_id})")

    deleted, failed = 0, []
    for stream_id, name in streams.items():
        try:
            _delete_video(agent_endpoint, stream_id)
            deleted += 1
            print(f"    deleted {name}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    try:
        remaining = _verify_deleted(vst_url, streams, require_empty=True)
    except (Exception, SystemExit) as exc:
        raise SystemExit(f"ABORTED: could not verify cleanup inventory: {exc}") from exc
    failed = list(dict.fromkeys([*failed, *remaining]))
    deleted = sum(name not in failed for name in streams.values())
    print(f"  Deleted and verified {deleted}/{len(streams)} video(s)")
    return {
        "found": len(streams),
        "deleted": deleted,
        "failed": failed,
        "names": list(streams.values()),
        "kept_rtsp": kept_rtsp,
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
        critic_enabled=False,
        min_cosine_similarity=-1.0,
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

    Every ingest strategy is probed, including synchronous completion: its
    chunk count cannot prove coverage for skipped-existing uploads or a
    duplicate-camera response. VST registration alone does not establish
    searchability.

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


def resolve_probe_sources(vst_url: str, expected: list[str]) -> dict[str, str]:
    """Resolve complete dataset identities to VST's case-sensitive source names."""
    registered = flows.list_sensor_streams(vst_url)
    resolved = {}
    for source in expected:
        matches = [
            name
            for name in registered.values()
            if flows.video_name_matches(name.casefold(), source.casefold())
        ]
        if len(matches) != 1:
            raise ValueError(
                f"Expected one registered source for {source!r}, found {matches!r}"
            )
        resolved[source] = matches[0]
    if len(set(resolved.values())) != len(expected):
        raise ValueError(
            "Expected sources do not resolve to distinct registered identities"
        )
    return resolved


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
    agent_endpoint: str,
    vst_url: str,
    video_dir: Path,
    video_files: list[Path] | None = None,
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
        streams, kept_rtsp = file_sources(vst_url)
    except Exception as e:
        raise SystemExit(
            f"ABORTED: could not list sensors at {vst_url} ({type(e).__name__}: {e})"
        ) from e

    ours: set[str] = set()
    for vf in (
        video_files
        if video_files is not None
        else sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mkv"))
    ):
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
            _delete_video(agent_endpoint, stream_id)
            deleted += 1
            print(f"    deleted {name}")
        except Exception as e:  # noqa: BLE001
            failed.append(name)
            print(f"    FAILED {name}: {e}")

    try:
        remaining = _verify_deleted(vst_url, foreign)
    except (Exception, SystemExit) as exc:
        raise SystemExit(f"ABORTED: could not verify pruned inventory: {exc}") from exc
    failed = list(dict.fromkeys([*failed, *remaining]))
    deleted = sum(name not in failed for name in foreign.values())
    print(
        f"  Pruned and verified {deleted}/{len(foreign)} foreign video(s); kept {kept}"
    )
    return {
        "found": len(streams),
        "kept": kept,
        "deleted": deleted,
        "failed": failed,
        "names": list(foreign.values()),
        "kept_rtsp": kept_rtsp,
    }


def ingest_videos(
    backend: Any,
    video_dir: Path,
    skip_existing_from: str | None = None,
    video_files: list[Path] | None = None,
) -> dict[str, Any]:
    """Upload every fixture through the selected ingest backend.

    ``skip_existing_from`` is a VST origin. When given, sources already
    registered there are left alone -- necessary on a shared deployment, where
    re-uploading someone else's fixture is at best wasteful and at worst
    creates a duplicate sensor for the same video.
    """
    video_files = (
        list(video_files)
        if video_files is not None
        else sorted(video_dir.glob("*.mp4")) + sorted(video_dir.glob("*.mkv"))
    )
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
    if cfg.ingestion_validation_contract and (
        not cfg.gallery_gt
        or cfg.stage not in ("run", "ingest")
        or cfg.skip_ingest
        or cfg.skip_index_probe
    ):
        raise SystemExit(
            "ABORTED: indexed-content validation requires gallery ingestion with index probes enabled"
        )
    if cfg.skip_ingest:
        print("Skipping ingest (--skip-ingest)")
        if cfg.vst_url:
            names = (
                [
                    Path(row["video"]).stem
                    for row in load_gallery_gt(cfg.gallery_gt)["gallery"]
                ]
                if cfg.gallery_gt
                else dataset_video_names(
                    cfg.data_dir / cfg.dataset / "videos",
                    cfg.data_dir,
                    cfg.dataset,
                    cfg.subset,
                )
            )
            manifest.readiness = {
                "reused_index": check_reused_index(cfg.vst_url, names, cfg.source_type)
            }
        return manifest
    if cfg.source_type == "rtsp":
        raise SystemExit(
            "ABORTED: --source-type rtsp requires --skip-ingest; fresh uploads are video files"
        )
    if (
        getattr(ingest_backend, "name", None) == "vst-direct"
        and not cfg.webhooks_confirmed
    ):
        raise SystemExit(
            "ABORTED: vst-direct requires --webhooks-confirmed after checking deployed "
            "VIOS add webhooks/model settings; otherwise use --ingest-flow agent-3step"
        )
    video_dir = cfg.data_dir / cfg.dataset / "videos"
    gallery_gt = load_gallery_gt(cfg.gallery_gt) if cfg.gallery_gt else None
    gallery_files = None
    pending_gallery_mapping = None
    if gallery_gt is None and (cfg.clear or cfg.only_dataset):
        check_inputs_before_wipe(video_dir, cfg.data_dir, cfg.dataset, cfg.subset)
    if gallery_gt is not None:
        validate_gallery_queries(gallery_gt)
        if cfg.query_domain and not any(
            str(row.get("query_domain", row.get("domain", ""))).casefold()
            == cfg.query_domain.casefold()
            for row in gallery_gt["queries"]
        ):
            raise SystemExit("ABORTED: no queries matched the selected gallery domain")
        base = Path(cfg.gallery_gt)
        base = base if base.is_dir() else base.parent
        gallery_files = [
            Path(row.get("video_path") or base / row["video"])
            for row in gallery_gt["gallery"]
        ]
        if not gallery_files or any(
            not path.is_file() or path.suffix.lower() not in (".mp4", ".mkv")
            for path in gallery_files
        ):
            raise SystemExit(
                "ABORTED: every declared gallery clip must exist as an MP4/MKV file before ingestion or cleanup"
            )
        stems = [path.stem.casefold() for path in gallery_files]
        ids = [row["chunk_id"] for row in gallery_gt["gallery"]]
        if len(set(stems)) != len(stems) or len(set(ids)) != len(ids):
            raise SystemExit(
                "ABORTED: gallery clip IDs and source filenames must be unique"
            )
        video_dir = gallery_files[0].parent

    prepared_content = None
    content_provenance = None
    if cfg.ingestion_validation_contract:
        from flows.ingestion_validation import prepare_contract

        try:
            prepared_content, content_provenance = prepare_contract(
                cfg.ingestion_validation_contract, gallery_files
            )
        except Exception as exc:
            raise SystemExit(
                f"ABORTED: content-validation preflight failed before cleanup/ingestion: {exc}"
            ) from exc

    cleared: dict[str, Any] | None = None
    if cfg.clear:
        print("\nClearing ALL existing videos (--clear)...")
        cleared = clear_all_videos(cfg.endpoint, cfg.vst_url)
    elif cfg.only_dataset:
        print("\nPruning sources that are not in this dataset (--only-dataset)...")
        cleared = (
            prune_foreign_videos(
                cfg.endpoint, cfg.vst_url, video_dir, video_files=gallery_files
            )
            if gallery_files is not None
            else prune_foreign_videos(cfg.endpoint, cfg.vst_url, video_dir)
        )

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
        upload_stats = ingest_videos(
            ingest_backend,
            video_dir,
            skip_existing_from=cfg.vst_url if cfg.skip_existing else None,
            video_files=gallery_files,
        )
        flows.print_upload_summary(upload_stats)
        # Map each uploaded clip (by filename stem) to its gallery chunk_id + sensor.
        by_stem = {
            Path(row["video"]).stem.casefold(): row for row in gallery_gt["gallery"]
        }
        clips = []
        for record in upload_stats.get("per_file", []):
            if not record.get("success"):
                continue
            row = by_stem.get(Path(record["video_name"]).stem.casefold())
            if row is None:
                continue
            clips.append(
                {
                    "dataset_id": row["chunk_id"],
                    "sensor_id": record.get("sensor_id"),
                    "filename": Path(row.get("video") or record["video_name"]).name,
                }
            )
        # Skipped uploads still belong to the gallery. Read their identities
        # from the deployment inventory rather than deriving IDs from names.
        skipped = set(upload_stats.get("skipped_existing") or [])
        if skipped:
            streams = flows.list_sensor_streams(cfg.vst_url)
            by_name: dict[str, list[str]] = {}
            for sensor_id, name in streams.items():
                by_name.setdefault(Path(name).stem.casefold(), []).append(sensor_id)
            for stem in sorted(skipped):
                row = by_stem.get(stem.casefold())
                identities = by_name.get(stem.casefold(), [])
                if row is None or len(identities) != 1:
                    raise SystemExit(
                        f"ABORTED: cannot uniquely resolve existing gallery source {stem!r}"
                    )
                clips.append(
                    {
                        "dataset_id": row["chunk_id"],
                        "sensor_id": identities[0],
                        "filename": Path(row["video"]).name,
                    }
                )
        manifest_path = cfg.ingest_out or str(
            RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.ingestion.json"
        )
        out_path = Path(manifest_path)
        if not upload_stats.get("failed"):
            if (
                {row["dataset_id"] for row in clips} != set(ids)
                or len(clips) != len(ids)
                or any(not row.get("sensor_id") for row in clips)
                or len({row["sensor_id"] for row in clips}) != len(clips)
            ):
                raise SystemExit(
                    "ABORTED: uploaded identities must map the exact gallery uniquely; previous manifest preserved"
                )
            pending_gallery_mapping = (out_path, {"clips": clips})
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

    if cfg.skip_index_probe and any(
        row.get("complete_outcome") == flows.COMPLETE_ALREADY_REGISTERED
        for row in upload_stats.get("per_file", [])
    ):
        raise SystemExit(
            "ABORTED: duplicate-camera completions require index readiness verification"
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

    # Registered in VST is not the same as indexed in Elasticsearch. Even a
    # synchronous ingest needs verification for skipped and duplicate uploads.
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

    if not cfg.skip_index_probe:
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
        require_attributes = (
            (
                bool(cfg.scenarios)
                and any(scenario != "embed-only" for scenario in cfg.scenarios)
            )
            or getattr(query_backend, "search_path", "embed") in ("attribute", "fusion")
            or any(
                isinstance(decomposition, dict)
                and bool(decomposition.get("attributes"))
                for decomposition in getattr(
                    query_backend, "decompositions", {}
                ).values()
            )
        )
        readiness_probe = getattr(query_backend, "readiness", None)
        if readiness_probe is None:
            raise SystemExit(
                "ABORTED: query backend cannot verify pipeline readiness; update the benchmark CLI"
            )
        try:
            resolved_sources = resolve_probe_sources(cfg.vst_url, expected)
        except Exception as exc:
            raise SystemExit(
                f"ABORTED: cannot resolve readiness identities: {exc}"
            ) from exc
        index_probe["registered_sources"] = resolved_sources
        readiness_options = {"require_attributes": require_attributes}
        if prepared_content is not None:
            decoded = {
                name.casefold(): count
                for name, count in prepared_content["frame_counts"].items()
            }
            resolved_contract = {
                **prepared_content,
                "frame_counts": {
                    registered: decoded[Path(source).stem.casefold()]
                    for source, registered in resolved_sources.items()
                },
            }
            # An unsuccessful re-ingest must not invalidate the receipt linked
            # by the previous successfully published gallery manifest.
            contract_path = (
                pending_gallery_mapping[0]
                .with_suffix(f".{uuid4().hex}.content-contract.json")
                .resolve()
            )
            atomic_write_json(contract_path, resolved_contract)
            readiness_options["content_contract"] = contract_path
            index_probe["content_contract"] = {
                "path": str(contract_path),
                "sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                **content_provenance,
            }

        def save_validation_receipt(body):
            if prepared_content is None:
                return
            receipt_path = contract_path.with_suffix(".validation.json")
            atomic_write_json(
                receipt_path,
                {
                    "schema_version": 1,
                    "contract": resolved_contract,
                    "contract_identity": index_probe["content_contract"],
                    "pipeline_readiness": body,
                },
            )
            index_probe["content_validation_receipt"] = {
                "path": str(receipt_path),
                "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
            }

        pipeline_readiness = None
        for attempt in range(1, cfg.index_probe_attempts + 1):
            try:
                pipeline_readiness = readiness_probe(
                    list(resolved_sources.values()),
                    **readiness_options,
                )
            except Exception as exc:
                save_validation_receipt(
                    {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
                )
                raise SystemExit(
                    f"ABORTED: pipeline readiness check failed: {exc}"
                ) from exc
            pipeline_readiness["attempts"] = attempt
            save_validation_receipt(pipeline_readiness)
            if pipeline_readiness.get("ready"):
                break
            content = pipeline_readiness.get("content_validation", {})
            if pipeline_readiness.get("error") or any(
                isinstance(value, dict) and value.get("violation_count", 0)
                for value in content.values()
            ):
                break
            if attempt < cfg.index_probe_attempts:
                time.sleep(cfg.index_probe_backoff)
        index_probe["pipeline_readiness"] = pipeline_readiness
        if not pipeline_readiness or not pipeline_readiness.get("ready"):
            raise SystemExit(
                f"ABORTED: embedding/attribute pipeline not ready: {pipeline_readiness}"
            )
        if require_attributes:
            attribute_probe = flows.CliQueryBackend(
                vss_cmd=query_backend.vss_cmd,
                cwd=query_backend.cwd,
                search_path="attribute",
                attributes=[PROBE_QUERY],
                decompositions={
                    PROBE_QUERY: {
                        "attributes": [PROBE_QUERY],
                        "has_action": False,
                        "video_sources": list(resolved_sources.values()),
                    }
                },
                top_k=1,
                attribute_min_similarity=0.0,
                critic_enabled=False,
                pass_original_query=False,
            )
            try:
                hits, _ = attribute_probe.search(PROBE_QUERY)
            except Exception as exc:
                raise SystemExit(
                    f"ABORTED: attribute search readiness check failed: {exc}"
                ) from exc
            index_probe["attribute_search"] = {"checked": True, "hits": len(hits)}
            if not hits:
                raise SystemExit(
                    "ABORTED: attribute indices contain data but attribute search returned no hits at score floor 0"
                )

    if pending_gallery_mapping is not None:
        out_path, mapping = pending_gallery_mapping
        if manifest.index_probe and manifest.index_probe.get(
            "content_validation_receipt"
        ):
            mapping["content_validation_receipt"] = manifest.index_probe[
                "content_validation_receipt"
            ]
        atomic_write_json(out_path, mapping)
        print(f"Ingestion manifest saved to: {out_path.absolute()}")
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
        validate_gallery_queries(gallery_gt)
        selected_rows = [
            q
            for q in gallery_gt["queries"]
            if cfg.query_domain is None
            or str(q.get("query_domain", q.get("domain", ""))).lower()
            == cfg.query_domain.lower()
        ]
        if cfg.scoring in ("clip-relevance", "drive"):
            # Validate the full scoring contract before any model request.
            from flows.scoring.gallery import build_context

            context = build_context(
                cfg.gallery_gt,
                cfg.ingestion_manifest,
                pas_frame_supported=cfg.pas_frame_supported
                if cfg.scoring == "clip-relevance"
                else False,
            )
            excluded = {row.get("query_id") for row in context.get("excluded", [])}
            selected_rows = [
                row for row in selected_rows if row.get("query_id") not in excluded
            ]
        queries = [q["query"] for q in selected_rows]
        query_ids = [q.get("query_id") for q in selected_rows]
        if not queries:
            raise SystemExit("No queries matched the selected gallery domain")
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
        query_ids = [None] * len(queries)
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

    from collections import Counter

    query_counts = (
        Counter(row["query"] for row in gallery_gt["queries"])
        if cfg.gallery_gt
        else Counter(queries)
    )
    query_backend.ambiguous_query_texts = {
        text for text, count in query_counts.items() if count > 1
    }

    def backend_call(name, query, query_id=None):
        method = getattr(query_backend, name)
        if query_id is not None and "query_id" in inspect.signature(method).parameters:
            return method(query, query_id=query_id)
        return method(query)

    # Compute the planned split over the ACTUAL query list, so the header
    # states what will run rather than only the fallback.
    planned_paths: dict[str, int] = {}
    if hasattr(query_backend, "plan_for_query"):
        try:
            planned_paths = flows.path_distribution(
                [
                    backend_call("plan_for_query", q, query_id)
                    for q, query_id in zip(queries, query_ids)
                ]
            )
        except flows.QueryUnanswerableError:
            pass  # Invalid attribute replay is recorded per query below.

    if hasattr(query_backend, "capture_frame_capabilities"):
        # Only frame-capable plans need a live mapping/transport probe. Embed
        # and unbounded attribute baselines must not depend on frame services.
        source_types = set()
        if decomposer is not None:
            source_types.update({"video_file", "rtsp"})
        elif hasattr(query_backend, "plan_for_query"):
            for query, query_id in zip(queries, query_ids):
                try:
                    plan = backend_call("plan_for_query", query, query_id)
                except flows.QueryUnanswerableError:
                    continue
                if plan["path"] == "fusion" or (
                    plan["path"] == "attribute"
                    and plan.get("timestamp_start") and plan.get("timestamp_end")
                ):
                    source_types.add(plan["source_type"])
        query_backend.frame_profiles = {}
        if source_types:
            query_backend.capture_frame_capabilities(source_types)

    described = describe_query_flow(
        query_backend, decomposer, fallback=decompose_fallback
    )
    from flows.provenance import capture_provenance

    described["provenance"] = capture_provenance(cfg, queries, query_ids=query_ids)
    if hasattr(query_backend, "deployment_models"):
        described["provenance"]["deployed_models"] = query_backend.deployment_models
    if getattr(query_backend, "attribute_input", None) is not None:
        described["attribute_input"] = query_backend.attribute_input
    if cfg.query_domain:
        described["query_domain"] = cfg.query_domain
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
    evidence_dir = Path(
        cfg.decomposition_out
        or str(RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.decompositions")
    )
    gallery_rows = selected_rows if cfg.gallery_gt else [{} for _ in queries]
    sources_seen: set[str] = set()
    verdicts: dict[str, int] = {}
    fatal: list[BaseException] = []

    checkpoint_base = Path(
        cfg.retrieval_out
        or (
            str(Path(cfg.output_file).with_suffix(".retrieval.json"))
            if cfg.output_file
            else str(
                RESULTS_DIR
                / f"{_run_id(cfg, artifact_dataset, cfg.subset)}.retrieval.json"
            )
        )
    )
    checkpoint_dir = Path(str(checkpoint_base) + ".checkpoint")
    prompt_sources = (
        sorted(video_names)
        if cfg.gallery_gt
        else decomposition_sources(
            annotations,
            cfg.vst_url,
            getattr(query_backend, "source_type", cfg.source_type),
        )
        if decomposer is not None
        else []
    )
    signature_data = {
        "endpoint": cfg.endpoint,
        "vst_url": cfg.vst_url,
        "concurrency": cfg.concurrency,
        "search_timeout_s": getattr(query_backend, "timeout", None),
        "cli_cwd": getattr(query_backend, "cwd", None),
        "source_identity": described["provenance"]["source_identity"],
        "cli_version": getattr(query_backend, "cli_version", None),
        "cohort": list(zip(query_ids, queries)),
        "query_settings": query_backend.describe(),
        "live_decomposition": decomposer.describe() if decomposer else None,
        "prompt_sources": prompt_sources if decomposer is not None else None,
        "saved_decompositions": getattr(query_backend, "decompositions", {}),
        "dataset_identity": described["provenance"]["dataset_identity"],
        "ingestion_identity": described["provenance"]["ingestion_identity"],
        "deployed_models": described["provenance"].get("deployed_models"),
        "preprocessing": described["provenance"]["preprocessing"],
        "scoring": cfg.scoring,
        "pas_frame_supported": cfg.pas_frame_supported,
    }
    serialized_identity = json.dumps(signature_data, sort_keys=True).encode()
    resume_identity = hashlib.sha256(serialized_identity).hexdigest()
    # Freeze the initial settings; a live run later mutates its decomposition map.
    signature_data = json.loads(serialized_identity)
    described["resume_identity"] = resume_identity
    described["checkpoint_directory"] = str(checkpoint_dir.resolve())
    reuse_checkpoint_destination = False
    if cfg.resume_from:
        if "sha256" not in described["provenance"]["ingestion_identity"]:
            raise SystemExit(
                "ABORTED: resume requires a readable --ingestion-manifest or --ingest-in identity file; unchanged indexed data cannot be verified without one"
            )
        resume_path = Path(cfg.resume_from)
        if resume_path.is_dir():
            header = json.loads((resume_path / "checkpoint.json").read_text())
            previous_identity = header.get("resume_identity")
            previous = []
            for path in sorted(resume_path.glob("query-*.json")):
                try:
                    payload = json.loads(path.read_text())
                    if payload.get("_resume_identity") != previous_identity:
                        raise ValueError("query checkpoint identity mismatch")
                    previous.append(QueryRecord.from_dict(payload))
                except (OSError, ValueError, KeyError, TypeError, SystemExit) as exc:
                    print(
                        f"WARNING: checkpoint {path} cannot be reused; its query will rerun: {exc}",
                        file=sys.stderr,
                    )
        else:
            previous_artifact = read_retrieval_artifact(resume_path)
            previous_identity = previous_artifact.described.get("resume_identity")
            previous = previous_artifact.queries
        if checkpoint_dir.exists():
            destination_header = json.loads(
                (checkpoint_dir / "checkpoint.json").read_text()
            )
            if destination_header.get("resume_identity") != resume_identity:
                raise SystemExit(
                    "ABORTED: destination checkpoint belongs to another run; choose a new output path"
                )
            reuse_checkpoint_destination = True
        if previous_identity != resume_identity:
            raise SystemExit(
                "ABORTED: resume settings, cohort, models, preprocessing, or ingestion identity differ from the saved run"
            )
        for record in previous:
            if record.status != "answered":
                continue
            if (
                not isinstance(record.index, int)
                or record.index < 0
                or record.index >= len(queries)
                or (record.query_id, record.query)
                != (query_ids[record.index], queries[record.index])
            ):
                raise SystemExit(
                    "ABORTED: resume query identity does not match the selected cohort"
                )
            records[record.index] = record
    elif checkpoint_dir.exists():
        raise SystemExit(
            f"ABORTED: checkpoint already exists: {checkpoint_dir}; use --resume-from or a new output name"
        )
    described["resume_reused_queries"] = sum(record is not None for record in records)
    if not reuse_checkpoint_destination:
        try:
            checkpoint_dir.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise SystemExit(
                f"ABORTED: checkpoint already exists: {checkpoint_dir}; choose a new output path or use --resume-from"
            ) from None
    atomic_write_json(
        checkpoint_dir / "checkpoint.json",
        {
            "resume_identity": resume_identity,
            "cohort": signature_data["cohort"],
            "identity_components": signature_data,
            "status": "running",
        },
    )

    def checkpoint(qi):
        atomic_write_json(
            checkpoint_dir / f"query-{qi:08d}.json",
            {**records[qi].to_dict(), "_resume_identity": resume_identity},
        )

    for qi, record in enumerate(records):
        if record is not None:
            checkpoint(qi)
            verification_pool = record.selected_hits if record.selected_hits is not None else record.hits
            sources_seen.update(flows.verification_sources(verification_pool))
            for verdict, count in flows.verdict_counts(verification_pool).items():
                verdicts[verdict] = verdicts.get(verdict, 0) + count
    completed[0] = sum(record is not None for record in records)
    resumed_plans = [
        record.plan for record in records if record is not None and record.plan
    ]
    stop_retrieval = threading.Event()
    failures = [0]

    def _retrieve_single(qi: int, query: str) -> None:
        query_id = query_ids[qi]
        identity = query_id or query
        # Decompose first, then search. The backend already routes from its
        # `decompositions` map, so writing this query's entry is the whole
        # integration -- path choice, attributes and top_k all follow. Distinct
        # keys per thread, so the shared dict needs no lock.
        decompose_s = 0.0
        decomposition: dict[str, Any] | None = None
        evidence_path = None
        latest_evidence = {}
        evidence_write_error = None
        if decomposer is not None and isinstance(decomposer, flows.LiveDecomposer):
            evidence_path = evidence_dir / (
                hashlib.sha256(str(identity).encode()).hexdigest() + ".json"
            )
            records[qi] = QueryRecord(
                index=qi,
                query_id=query_ids[qi],
                query=query,
                status="unanswerable",
                evidence={
                    "decomposition_file": str(evidence_path),
                    "decomposition_mode": "fresh",
                },
            )

            def save_evidence(evidence: dict[str, Any]) -> None:
                nonlocal evidence_write_error
                latest_evidence.clear()
                latest_evidence.update(evidence)
                try:
                    atomic_write_json(evidence_path, evidence)
                except OSError as exc:
                    evidence_write_error = f"{type(exc).__name__}: {exc}"
                    records[qi] = replace(
                        records[qi],
                        evidence={
                            **(records[qi].evidence or {}),
                            "decomposition_diagnostic_error": evidence_write_error,
                            "decomposition_evidence": dict(latest_evidence),
                        },
                    )
                    print(
                        f"WARNING: decomposition evidence could not be published: {exc}",
                        file=sys.stderr,
                    )

            started = time.perf_counter()
            try:
                decomposition, decompose_s = decomposer.decompose(
                    query,
                    video_sources=prompt_sources,
                    evidence_sink=save_evidence,
                    query_id=query_id,
                )
            except BaseException:
                elapsed = time.perf_counter() - started
                records[qi] = replace(
                    records[qi],
                    decompose_s=elapsed,
                    evidence={
                        **(records[qi].evidence or {}),
                        "fresh_inference_s": elapsed,
                        "decomposition_evidence": dict(latest_evidence),
                    },
                )
                raise
            query_backend.decompositions[identity] = decomposition
        elif decomposer is not None:
            started = time.perf_counter()
            try:
                decomposition, decompose_s = decomposer.decompose(
                    query, video_sources=prompt_sources
                )
            except BaseException:
                records[qi] = QueryRecord(
                    index=qi,
                    query_id=query_id,
                    query=query,
                    status="unanswerable",
                    decompose_s=time.perf_counter() - started,
                )
                raise
            query_backend.decompositions[identity] = decomposition
        elif hasattr(query_backend, "decompositions"):
            replay_start = time.perf_counter()
            decomposition = query_backend.decompositions.get(identity)
            if (
                decomposition is None
                and query not in query_backend.ambiguous_query_texts
            ):
                decomposition = query_backend.decompositions.get(query)
            replay_s = time.perf_counter() - replay_start

        if stop_retrieval.is_set():
            return

        if cfg.gallery_gt:
            records[qi] = QueryRecord(
                index=qi,
                query_id=query_ids[qi],
                query=query,
                status="unanswerable",
                decomposition=decomposition,
                evidence={
                    "decomposition_mode": "fresh"
                    if decomposer
                    else "replay"
                    if decomposition is not None
                    else "none",
                    "decomposition_file": str(evidence_path)
                    if evidence_path
                    else getattr(query_backend, "decomposition_source", None),
                },
            )
        plan_before = (
            backend_call("plan_for_query", query, query_id)
            if hasattr(query_backend, "plan_for_query")
            else None
        )
        if evidence_path is not None:
            evidence = dict(latest_evidence)
            evidence["selected_route"] = plan_before
            evidence["effective_argv"] = query_backend.build_argv(query, plan_before)
            save_evidence(evidence)
        query_evidence = None
        if cfg.gallery_gt or evidence_path is not None:
            row = gallery_rows[qi]
            query_evidence = {
                "decomposition_mode": "fresh"
                if decomposer
                else "replay"
                if decomposition is not None
                else "none",
                "decomposition_diagnostic_error": evidence_write_error,
                "decomposition_evidence": dict(latest_evidence)
                if evidence_write_error
                else None,
                "fresh_inference_s": decompose_s if decomposer else None,
                "replay_s": replay_s
                if decomposer is None and decomposition is not None
                else None,
                "decomposition_file": str(evidence_path) if evidence_path else None,
                "released_pas_requirements": row.get(
                    "resolved_attributes",
                    row.get("attributes", row.get("requirements")),
                ),
                "extracted_attributes": decomposition.get("attributes")
                if isinstance(decomposition, dict)
                else None,
            }
        if plan_before is not None and hasattr(query_backend, "build_argv"):
            plan_before["cli_subcommand"] = f"search run {plan_before['path']}"
            plan_before["effective_argv"] = query_backend.build_argv(query, plan_before)
        records[qi] = QueryRecord(
            index=qi,
            query_id=query_ids[qi],
            query=query,
            status="unanswerable",
            decomposition=decomposition,
            plan=plan_before,
            evidence=query_evidence,
        )
        if stop_retrieval.is_set():
            return
        try:
            raw_results, latency_s = backend_call("search", query, query_id)
        except flows.QueryUnanswerableError:
            metadata = getattr(query_backend, "metadata_by_query", {}).get(identity)
            if metadata:
                records[qi] = replace(records[qi], evidence={**(query_evidence or {}), **metadata})
            raise
        metadata = dict(getattr(query_backend, "metadata_by_query", {}).get(identity) or {})
        selected_wire = metadata.pop("_selected_results", None)
        selection = metadata.pop("selection", None)
        if metadata:
            query_evidence = {
                **(query_evidence or {}),
                **metadata,
                "post_aggregation_hit_count": len(raw_results),
            }
        normalized = flows.normalize_results(raw_results)
        selected_normalized = None
        if selected_wire is not None:
            selected_normalized = []
            for index, wire in zip(selection["selected_indices"], selected_wire, strict=True):
                hit = dict(normalized[index])
                raw_verification = wire.get("verification", wire.get("critic_result"))
                verification = raw_verification if isinstance(raw_verification, dict) else {}
                hit.update(
                    critic_result=wire.get("critic_result"),
                    verification={**verification, "result": verification.get("result", "unverified"),
                                  "criteria_met": verification.get("criteria_met") or {}},
                    _verification_source=("verification" if "verification" in wire else "critic_result")
                        if isinstance(raw_verification, dict) else "absent",
                )
                selected_normalized.append(hit)
        verification_pool = selected_normalized if selected_normalized is not None else normalized
        # Both are read-modify-write on state shared across the pool, and the
        # verdict tally is what the no-opinion check reads at the end: lose
        # increments here and a healthy critic is reported as having formed no
        # opinion. `print_lock` already serialises the per-query output, and
        # this is the same few microseconds.
        with print_lock:
            sources_seen.update(flows.verification_sources(verification_pool))
            for verdict, count in flows.verdict_counts(verification_pool).items():
                verdicts[verdict] = verdicts.get(verdict, 0) + count

        # Present only when the deployment carries the search_core timings
        # change. Absent means nobody collected -- not that stages took no time.
        stage_timings = getattr(query_backend, "timings_by_query", {}).get(identity)
        plan = (
            backend_call("plan_for_query", query, query_id)
            if hasattr(query_backend, "plan_for_query")
            else None
        )
        records[qi] = QueryRecord(
            index=qi,
            query_id=query_ids[qi],
            query=query,
            status="answered",
            plan=plan_before or plan,
            evidence=query_evidence,
            decomposition=decomposition,
            decompose_s=decompose_s if decomposer is not None else None,
            search_latency_s=latency_s,
            timings=stage_timings or None,
            # _raw (the original wire hit) duplicates the normalized fields and
            # roughly doubles the artifact; scoring never reads it (for_scoring
            # strips all _-prefixed keys), so keep only the normalized form.
            hits=[{k: v for k, v in hit.items() if k != "_raw"} for hit in normalized],
            selected_hits=[{k: v for k, v in hit.items() if k != "_raw"} for hit in selected_normalized]
                if selected_normalized is not None else None,
            selection=selection,
        )

        checkpoint(qi)
        with print_lock:
            completed[0] += 1
            print(
                f'[{completed[0]}/{len(queries)}] "{query}"  ({len(normalized)} hits, {latency_s:.2f}s)'
            )

    def _retrieve_guarded(qi: int, query: str) -> None:
        if stop_retrieval.is_set():
            return
        try:
            _retrieve_single(qi, query)
        except BaseException as exc:
            with print_lock:
                failures[0] += 1
                if isinstance(
                    exc, (flows.CliExitError, KeyboardInterrupt, SystemExit)
                ) or (
                    isinstance(exc, flows.DecompositionError)
                    and failures[0] > cfg.tolerate_unanswered
                ):
                    stop_retrieval.set()
                    fatal.append(exc)
            raise

    inventory_before = (
        flows.inventory_snapshot(cfg.vst_url) if cfg.vst_url else {"ok": False}
    )

    #: Queries the backend never answered. Appended only from the single-
    #: threaded as_completed loop below, so no lock.
    unanswered: list[dict[str, Any]] = []

    wall_start = time.monotonic()
    executor = ThreadPoolExecutor(max_workers=cfg.concurrency)
    futures = {}
    try:
        futures = {
            executor.submit(_retrieve_guarded, qi, q): qi
            for qi, q in enumerate(queries)
            if records[qi] is None
        }
        for future in as_completed(futures):
            try:
                future.result()
            except (flows.CliExitError, KeyboardInterrupt, SystemExit) as exc:
                # An environment fault, not a bad query. Scoring it as zero
                # recall would report an accuracy cliff that never happened.
                fatal.append(exc)
                qi = futures[future]
                reason = f"{type(exc).__name__}: {exc}"[:1000]
                records[qi] = (
                    replace(records[qi], status="unanswerable", error=reason)
                    if records[qi]
                    else QueryRecord(
                        index=qi,
                        query_id=query_ids[qi],
                        query=queries[qi],
                        status="unanswerable",
                        error=reason,
                    )
                )
                checkpoint(qi)
                for pending in futures:
                    pending.cancel()
                break
            except flows.QueryUnanswerableError as exc:
                # No answer is not no hits. Record it and leave it OUT of the
                # scored set rather than letting an empty result average in.
                qi = futures[future]
                reason = str(exc)[:300]
                unanswered.append({"index": qi, "query": queries[qi], "reason": reason})
                records[qi] = (
                    replace(records[qi], status="unanswerable", error=reason)
                    if records[qi]
                    else QueryRecord(
                        index=qi,
                        query_id=query_ids[qi],
                        query=queries[qi],
                        status="unanswerable",
                        error=reason,
                    )
                )
                checkpoint(qi)
                print(f"  Query {qi} unanswerable: {exc}", file=sys.stderr)
            except Exception as exc:  # noqa: BLE001
                # Same rule for anything else that stopped this query from
                # producing a result: scoring is only valid for queries the
                # backend actually answered.
                qi = futures[future]
                reason = f"{type(exc).__name__}: {exc}"[:300]
                unanswered.append({"index": qi, "query": queries[qi], "reason": reason})
                records[qi] = (
                    replace(records[qi], status="unanswerable", error=reason)
                    if records[qi]
                    else QueryRecord(
                        index=qi,
                        query_id=query_ids[qi],
                        query=queries[qi],
                        status="unanswerable",
                        error=reason,
                    )
                )
                checkpoint(qi)
                print(
                    f"  Query {qi} raised: {type(exc).__name__}: {exc}", file=sys.stderr
                )
    except KeyboardInterrupt as exc:
        stop_retrieval.set()
        fatal.append(exc)
        for pending in futures:
            pending.cancel()
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
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
        for qi, record in enumerate(records):
            if record is None:
                records[qi] = QueryRecord(
                    index=qi,
                    query_id=query_ids[qi],
                    query=queries[qi],
                    status="unanswerable",
                    error="not completed before retrieval aborted",
                )
        unanswered = [
            {
                "index": row.index,
                "query_id": row.query_id,
                "query": row.query,
                "reason": row.error or "retrieval aborted",
            }
            for row in records
            if row.status != "answered"
        ]
    described["retrieval_status"] = "aborted" if fatal else "completed"
    if fatal:
        described["retrieval_error"] = f"{type(fatal[0]).__name__}: {fatal[0]}"[:1000]
    artifact = RetrievalArtifact(
        dataset=artifact_dataset,
        subset=cfg.subset,
        data_dir=str(cfg.data_dir),
        concurrency=cfg.concurrency,
        described=described,
        queries=[r for r in records if r is not None],
        unanswered=unanswered,
        executed_plans=resumed_plans
        + list(getattr(query_backend, "executed_plans", []) or []),
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
    for qi, record in enumerate(records):
        if record is not None:
            checkpoint(qi)
    atomic_write_json(
        checkpoint_dir / "checkpoint.json",
        {
            "resume_identity": resume_identity,
            "cohort": signature_data["cohort"],
            "identity_components": signature_data,
            "status": described["retrieval_status"],
        },
    )
    if fatal:
        write_retrieval_artifact(checkpoint_base, artifact)
        raise SystemExit(
            f"\nABORTED: {fatal[0]}\nPartial retrieval saved to {checkpoint_base}; resume with --resume-from {checkpoint_dir}"
        )
    return artifact


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
    if artifact.described.get("retrieval_status") == "aborted":
        raise SystemExit(
            "ABORTED: this retrieval was interrupted; resume its checkpoint before scoring"
        )
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
    atomic_write_json(out_path, result.to_output())
    print(f"Results saved to: {out_path.absolute()}")
    return result


# =============================================================================
# Orchestration
# =============================================================================


def _run_id(cfg: RunConfig, dataset: str, subset: str) -> str:
    """The base name shared by a run's artifact and result files."""
    if cfg.run_name:
        return cfg.run_name
    if not getattr(cfg, "_generated_run_id", None):
        cfg._generated_run_id = f"{dataset}_{subset or 'default'}_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}"
    return cfg._generated_run_id


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
    if cfg.max_hits_per_source is not None and (
        type(cfg.max_hits_per_source) is not int or not 1 <= cfg.max_hits_per_source <= 100000
        or not cfg.gallery_gt or not cfg.scenarios or "attribute-only" not in cfg.scenarios
        or cfg.source_type != "video_file" or cfg.scoring not in ("clip-relevance", "drive")
        or cfg.stage not in ("run", "retrieve")
    ):
        raise SystemExit("Source diversity requires an attribute-only file gallery scenario and clip scoring")
    stage = cfg.stage
    if cfg.resume_from and (
        cfg.clear or cfg.only_dataset or (stage == "run" and not cfg.skip_ingest)
    ):
        raise SystemExit(
            "ABORTED: resume requires unchanged ingestion (--skip-ingest or --stage retrieve)"
        )
    if cfg.gallery_gt and stage in ("run", "ingest") and not cfg.skip_ingest:
        mapping_path = (
            cfg.ingestion_manifest
            or cfg.ingest_out
            or str(
                RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.ingestion.json"
            )
        )
        cfg = replace(cfg, ingestion_manifest=mapping_path, ingest_out=mapping_path)

    if cfg.scenarios and stage in ("run", "retrieve"):
        return run_scenarios(
            cfg, ingest_backend, query_backend, decomposer, decompose_fallback
        )

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


def run_scenarios(cfg, ingest_backend, query_backend, decomposer, fallback):
    """Use the existing stages once per scenario, sharing one ingestion."""
    if cfg.stage == "run" and cfg.gallery_gt and not cfg.skip_ingest:
        cfg = replace(
            cfg,
            ingestion_manifest=cfg.ingestion_manifest
            or cfg.ingest_out
            or str(
                RESULTS_DIR / f"{_run_id(cfg, cfg.dataset, cfg.subset)}.ingestion.json"
            ),
        )
        cfg = replace(cfg, ingest_out=cfg.ingestion_manifest)
    manifest = (
        run_ingest(cfg, ingest_backend, query_backend)
        if cfg.stage == "run"
        else read_ingest_manifest(cfg.ingest_in)
        if cfg.ingest_in
        else IngestManifest()
    )
    base = Path(
        cfg.retrieval_out
        or cfg.output_file
        or str(RESULTS_DIR / _run_id(cfg, cfg.dataset, cfg.subset))
    )
    if base.suffix == ".json":
        base = base.with_suffix("")
    comparison = {
        "scenarios": {},
        "query_domain": cfg.query_domain,
        "routing_consistency": {
            "meaning": "agreement with saved decomposition route; not semantic correctness"
        },
    }
    last = None
    for scenario in cfg.scenarios:
        backend = copy.copy(query_backend)
        backend.retrieval_pool_size = cfg.retrieval_pool_size
        backend.max_hits_per_source = cfg.max_hits_per_source if scenario == "attribute-only" else None
        backend.decompositions = dict(query_backend.decompositions)
        backend.executed_plans = []
        backend.timings_by_query = {}
        backend.metadata_by_query = {}
        backend.critic_enabled = (
            backend.critic_enabled if backend.critic_enabled is not None else False
        )
        if backend.critic_enabled and backend.critic_eval_count is None:
            backend.critic_eval_count = 5
        child = replace(
            cfg,
            scenarios=None,
            use_dataset_decompositions=False,
            run_name=f"{cfg.run_name or base.name}.{scenario}",
            output_file=f"{base}.{scenario}.json",
            retrieval_out=f"{base}.{scenario}.retrieval.json",
        )
        active_decomposer = None
        if scenario == "live-routed":
            active_decomposer = decomposer
            if active_decomposer is None and not backend.decompositions:
                raise SystemExit(
                    "live-routed requires a live decomposer or --decompositions; no fixed-path fallback"
                )
            backend.attribute_input = None
            backend.require_decomposition = True
        else:
            backend.search_path = "embed" if scenario == "embed-only" else "attribute"
            backend.top_k = cfg.retrieval_pool_size or cfg.candidate_limit
            backend.candidate_limit = cfg.candidate_limit
            backend.min_cosine_similarity = -1.0
            backend.attribute_min_similarity = 0.0
            backend.merge_adjacent = False
            backend.attribute_input = (
                cfg.attribute_input if scenario == "attribute-only" else None
            )
            if scenario == "embed-only" or cfg.attribute_input == "original":
                backend.decompositions = {}
        artifact = run_retrieve(child, backend, active_decomposer, fallback, manifest)
        artifact.described["scenario"] = scenario
        artifact.described["critic"] = {
            "enabled": backend.critic_enabled,
            "eval_count": backend.critic_eval_count,
            "frame_budget": backend.critic_frame_budget,
            "input_width": backend.critic_input_width,
            "input_height": backend.critic_input_height,
            "diagnostic_dir": backend.critic_diagnostic_dir,
        }
        if scenario != "live-routed":
            artifact.described["ranking_protocol"] = (
                "bounded exact indexed-vector scan; overflow fails"
            )
            artifact.described["candidate_limit"] = cfg.candidate_limit
            artifact.described["metric_top_k"] = cfg.top_k
        artifact.described["returned_pool_limit"] = cfg.retrieval_pool_size
        if cfg.retrieval_pool_size is not None:
            artifact.described["returned_ranking"] = (
                "bounded; annotation coverage is reported separately"
            )
        artifact.described["decomposition_mode"] = (
            "fresh"
            if active_decomposer
            else "replay"
            if backend.decompositions
            else "none"
        )
        path = f"{base}.{scenario}.retrieval.json"
        write_retrieval_artifact(path, artifact)
        entry = {
            "retrieval_artifact": path,
            "paths": flows.path_distribution(artifact.executed_plans),
            "queries": len(artifact.queries),
            "unanswered": len(artifact.unanswered),
            "indexed_candidate_coverage": [
                {
                    "query": r.query,
                    "post_aggregation_hit_count": len(r.hits),
                    "candidate_coverage": (r.evidence or {}).get("candidate_coverage"),
                }
                for r in artifact.queries
            ],
        }
        if scenario == "live-routed":
            routed = [r for r in artifact.queries if r.decomposition and r.plan]
            from flows.routing import route

            entry["routing_consistency"] = {
                "compared": len(routed),
                "consistent": sum(
                    route(r.decomposition) == r.plan["path"] for r in routed
                ),
            }
        if cfg.stage == "run":
            last = run_score(child, artifact)
            entry["summary"] = last.summary
        comparison["scenarios"][scenario] = entry
        atomic_write_json(f"{base}.comparison.json", comparison)
    return last

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

"""The intermediate artifacts that connect the eval's stages.

The pipeline is ``ingest -> retrieve -> score -> report``. Each stage reads the
previous artifact (or runs the previous stage inline) and writes its own. The
:class:`RetrievalArtifact` is the load-bearing seam: it records the RAW
normalized hits plus everything the scorer needs, so a run can be re-scored --
under a different metric model, or with different flags -- without touching the
deployment again.

``RESULTS_DIR`` lives here (not in ``run_eval_flows.py``) so both the CLI module
and the pipeline can reference it without a circular import.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dataset import DEFAULT_DATA_DIR

#: Bump when the serialized artifact layout changes; readers migrate on load.
SCHEMA_VERSION = 1

#: Results land beside the script (``flows/`` -> ``scripts/``), not under the
#: caller's cwd, so a run is findable regardless of where it was started from.
SCRIPT_DIR = Path(__file__).resolve().parents[1]
RESULTS_DIR = SCRIPT_DIR / "cli_eval_result"


@dataclass
class RunConfig:
    """The resolved, stage-relevant slice of the CLI arguments.

    Built once in ``main`` and threaded through every stage so the stages share
    one source of truth instead of re-reading ``argparse.Namespace`` piecemeal.
    ``vst_url`` is the *resolved* origin (caller applies the port derivation).

    ``dataset``/``subset``/``data_dir`` are ``None`` only for ``--stage score``,
    where they are resolved from the retrieval artifact (the dataset the queries
    actually ran against) instead of the CLI defaults.
    """

    endpoint: str | None
    data_dir: Path | None
    dataset: str | None
    subset: str | None
    concurrency: int
    vst_url: str | None

    # stage selection
    stage: str = "run"
    resume_from: str | None = None
    retrieval_in: str | None = None
    retrieval_out: str | None = None
    ingest_in: str | None = None
    ingest_out: str | None = None

    # ingest
    webhooks_confirmed: bool = False
    source_type: str = "video_file"
    skip_ingest: bool = False
    skip_existing: bool = True
    clear: bool = False
    only_dataset: bool = False
    skip_readiness_wait: bool = False
    readiness_timeout: int = 1200
    skip_index_probe: bool = False
    index_probe_attempts: int = 10
    index_probe_backoff: float = 15.0
    index_probe_top_k: int = 20

    # retrieve / score
    use_dataset_decompositions: bool = True
    tolerate_unanswered: int = 0
    tolerate_unanswered_override: int | None = None
    scoring: str = "segment-overlap"
    # The candidate budget segment-overlap scores against. Matches the baseline's
    # top_k; slicing a dense/full-gallery artifact down to it keeps the run
    # comparable (see flows/scoring/segment_overlap.py).
    top_k: int = 5

    # clip-level scoring (clip-relevance / drive) inputs
    gallery_gt: str | None = None
    ingestion_manifest: str | None = None
    drive_scorer_dir: str | None = None
    # full_gallery makes the retrieve stage request every clip (dense mode),
    # the precondition the clip-level scorers grade against.
    full_gallery: bool = False
    # Frame-supported PAS ground-truth derivation (--scoring clip-relevance
    # only): rewrites PAS queries' relevant_clip_ids from the released
    # positive tracks whose representative frame supports the resolved
    # attributes. See flows/scoring/pas_ground_truth.py.
    pas_frame_supported: bool | None = None
    scenarios: list[str] | None = None
    frame_scoring: str = "auto"
    frame_lookup_transport: str = "auto"
    candidate_limit: int = 100000
    retrieval_pool_size: int | None = None
    ingestion_validation_contract: str | None = None
    attribute_input: str = "original"
    query_domain: str | None = None
    decomposition_out: str | None = None
    preprocessing_provenance: str | None = None

    # output
    output_file: str | None = None
    run_name: str | None = None

    @classmethod
    def from_args(cls, args: Any, vst_url: str | None) -> RunConfig:
        stage = getattr(args, "stage", "run")
        is_score = stage == "score"
        return cls(
            endpoint=args.endpoint,
            # For --stage score the dataset identity comes from the artifact (the
            # queries it ran against); --dataset/--subset/--data-dir are optional
            # overrides and stay None here so run_score falls back to the artifact.
            # Every other stage needs a concrete dataset to read right now.
            dataset=args.dataset if is_score else (args.dataset or "warehouse"),
            subset=args.subset if is_score else (args.subset or ""),
            data_dir=args.data_dir if is_score else (args.data_dir or DEFAULT_DATA_DIR),
            concurrency=args.concurrency,
            vst_url=vst_url,
            stage=stage,
            retrieval_pool_size=getattr(args, "retrieval_pool_size", None),
            ingestion_validation_contract=getattr(args, "ingestion_validation_contract", None),
            resume_from=getattr(args, "resume_from", None),
            tolerate_unanswered_override=getattr(
                args, "tolerate_unanswered_override", None
            ),
            retrieval_in=getattr(args, "retrieval_in", None),
            retrieval_out=getattr(args, "retrieval_out", None),
            ingest_in=getattr(args, "ingest_in", None),
            ingest_out=getattr(args, "ingest_out", None),
            webhooks_confirmed=getattr(args, "webhooks_confirmed", False),
            source_type=getattr(args, "source_type", "video_file"),
            skip_ingest=args.skip_ingest,
            skip_existing=args.skip_existing,
            clear=args.clear,
            only_dataset=args.only_dataset,
            skip_readiness_wait=args.skip_readiness_wait,
            readiness_timeout=args.readiness_timeout,
            skip_index_probe=args.skip_index_probe,
            index_probe_attempts=args.index_probe_attempts,
            index_probe_backoff=args.index_probe_backoff,
            index_probe_top_k=args.index_probe_top_k,
            use_dataset_decompositions=not args.fixed_search_path,
            tolerate_unanswered=args.tolerate_unanswered,
            scoring=getattr(args, "scoring", "segment-overlap"),
            top_k=args.top_k,
            gallery_gt=getattr(args, "gallery_gt", None),
            ingestion_manifest=getattr(args, "ingestion_manifest", None),
            drive_scorer_dir=getattr(args, "drive_scorer_dir", None),
            full_gallery=getattr(args, "full_gallery", False),
            pas_frame_supported=getattr(args, "pas_frame_supported", False),
            scenarios=getattr(args, "scenarios", None),
            frame_scoring=getattr(args, "frame_scoring", "auto"),
            frame_lookup_transport=getattr(args, "frame_lookup_transport", "auto"),
            candidate_limit=getattr(args, "candidate_limit", None) or 100000,
            attribute_input=getattr(args, "attribute_input", "original"),
            query_domain=getattr(args, "query_domain", None),
            decomposition_out=getattr(args, "decomposition_out", None),
            preprocessing_provenance=getattr(args, "preprocessing_provenance", None),
            output_file=args.output_file,
            run_name=args.name,
        )


@dataclass
class IngestManifest:
    """What the ingest stage established, for the retrieval artifact's flow block.

    A ``--skip-ingest`` run records the backend description and, when a VST
    origin is supplied, a non-destructive reused-source inventory in readiness.
    """

    ingest_description: dict[str, Any] | None = None
    upload_stats: dict[str, Any] | None = None
    readiness: dict[str, Any] | None = None
    index_probe: dict[str, Any] | None = None
    cleared: dict[str, Any] | None = None
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "ingest_description": self.ingest_description,
            "upload_stats": self.upload_stats,
            "readiness": self.readiness,
            "index_probe": self.index_probe,
            "cleared": self.cleared,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> IngestManifest:
        migrate(d, "ingest manifest")
        return cls(
            ingest_description=d.get("ingest_description"),
            upload_stats=d.get("upload_stats"),
            readiness=d.get("readiness"),
            index_probe=d.get("index_probe"),
            cleared=d.get("cleared"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
        )


@dataclass
class QueryRecord:
    """One query's retrieval outcome -- the raw material for scoring.

    ``hits`` are the NORMALIZED hits (with ``_verification_source``), so the
    scorer can produce both raw and critic-filtered metrics from the same record.
    ``status`` is the no-silent-zero contract: only ``answered`` records score.
    """

    index: int
    query: str
    status: str  # "answered" | "unanswerable"
    query_id: str | None = None
    error: str | None = None
    plan: dict[str, Any] | None = None
    decomposition: dict[str, Any] | None = None
    decompose_s: float | None = None
    evidence: dict[str, Any] | None = None
    search_latency_s: float | None = None
    timings: dict[str, Any] | None = None
    hits: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = {
            "index": self.index,
            "query": self.query,
            "status": self.status,
            "error": self.error,
            "plan": self.plan,
            "decomposition": self.decomposition,
            "decompose_s": self.decompose_s,
            "search_latency_s": self.search_latency_s,
            "timings": self.timings,
            "hits": self.hits,
        }
        if self.query_id is not None:
            result["query_id"] = self.query_id
        if self.evidence is not None:
            result["evidence"] = self.evidence
        return result

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> QueryRecord:
        _require(
            d,
            ["index", "query", "status"],
            "retrieval artifact query record",
        )
        return cls(
            index=d["index"],
            query_id=d.get("query_id"),
            query=d["query"],
            status=d["status"],
            error=d.get("error"),
            plan=d.get("plan"),
            decomposition=d.get("decomposition"),
            decompose_s=d.get("decompose_s"),
            evidence=d.get("evidence"),
            search_latency_s=d.get("search_latency_s"),
            timings=d.get("timings"),
            hits=d.get("hits") or [],
        )


@dataclass
class RetrievalArtifact:
    """The full result of the retrieve stage, ready to score.

    Carries the run-level aggregates the summary needs (``sources_seen``,
    ``verdicts``, ``wall_clock_s``) so scoring never has to re-derive them and
    drift from what retrieval actually observed.
    """

    dataset: str
    subset: str
    data_dir: str
    concurrency: int
    described: dict[
        str, Any
    ]  # the query-flow description; becomes flow.query and config
    queries: list[QueryRecord]
    unanswered: list[dict[str, Any]]
    executed_plans: list[dict[str, Any]]
    wall_clock_s: float
    sources_seen: list[str]  # sorted set of verification-field spellings seen
    verdicts: dict[str, int]
    inventory: dict[str, Any]
    ingest: IngestManifest
    tolerate_unanswered: int = 0
    run_id: str | None = None
    schema_version: int = SCHEMA_VERSION
    # Which ground-truth space the queries live in. "gallery" for a clip-level
    # (clip-relevance / drive) retrieval over --gallery-gt; "eval-dataset" for a
    # normal segment-overlap run. Scorers refuse the wrong kind of artifact.
    query_source: str = "eval-dataset"
    gallery_gt: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "dataset": self.dataset,
            "subset": self.subset,
            "data_dir": self.data_dir,
            "concurrency": self.concurrency,
            "described": self.described,
            "queries": [q.to_dict() for q in self.queries],
            "unanswered": self.unanswered,
            "executed_plans": self.executed_plans,
            "wall_clock_s": self.wall_clock_s,
            "sources_seen": self.sources_seen,
            "verdicts": self.verdicts,
            "inventory": self.inventory,
            "ingest": self.ingest.to_dict(),
            "tolerate_unanswered": self.tolerate_unanswered,
            "query_source": self.query_source,
            "gallery_gt": self.gallery_gt,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> RetrievalArtifact:
        migrate(d, "retrieval artifact")
        _require(
            d,
            [
                "dataset",
                "subset",
                "data_dir",
                "concurrency",
                "described",
                "queries",
                "wall_clock_s",
            ],
            "retrieval artifact",
        )
        return cls(
            dataset=d["dataset"],
            subset=d["subset"],
            data_dir=d["data_dir"],
            concurrency=d["concurrency"],
            described=d["described"],
            queries=[QueryRecord.from_dict(q) for q in d["queries"]],
            unanswered=d.get("unanswered") or [],
            executed_plans=d.get("executed_plans") or [],
            wall_clock_s=d["wall_clock_s"],
            sources_seen=d.get("sources_seen") or [],
            verdicts=d.get("verdicts") or {},
            inventory=d.get("inventory") or {},
            ingest=IngestManifest.from_dict(d.get("ingest") or {}),
            tolerate_unanswered=d.get("tolerate_unanswered", 0),
            run_id=d.get("run_id"),
            schema_version=d.get("schema_version", SCHEMA_VERSION),
            query_source=d.get("query_source", "eval-dataset"),
            gallery_gt=d.get("gallery_gt"),
        )


@dataclass
class ScoredResult:
    """The score stage's output. ``to_output`` reproduces the legacy shape exactly."""

    summary: dict[str, Any]
    config: dict[str, Any]
    flow: dict[str, Any]
    query_results: list[dict[str, Any]]
    # Present only on a non-default scoring model (e.g. clip-relevance). Absent on
    # a segment-overlap result, so that output stays byte-identical to the legacy
    # runner -- a missing ``scoring.model`` is what marks a result as segment-overlap.
    scoring: dict[str, Any] | None = None

    def to_output(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "summary": self.summary,
            "config": self.config,
            "flow": self.flow,
            "query_results": self.query_results,
        }
        if self.scoring is not None:
            out["scoring"] = self.scoring
        return out


# =============================================================================
# On-disk serialization
# =============================================================================
#
# The retrieval artifact is the seam that lets stages run on different days:
# retrieve writes it, score reads it. ``SCHEMA_VERSION`` rides in every payload
# and ``migrate`` is the single dispatch point, so an artifact written by an
# older runner still loads after the format evolves (and a newer one fails
# loudly instead of being silently misread).


def migrate(doc: dict[str, Any], kind: str) -> None:
    """Validate + upgrade an artifact payload in place to the current schema.

    v1 is the current version, so today this only guards the two failure modes
    that matter: a payload newer than this runner understands (abort, do not
    guess) and a payload with no version at all (treated as v1). Future schema
    bumps chain ``v1 -> v2`` transforms here.
    """
    version = doc.get("schema_version", 1)
    if not isinstance(version, int) or version > SCHEMA_VERSION:
        raise SystemExit(
            f"ERROR: {kind} schema_version {version!r} is newer than this runner "
            f"understands ({SCHEMA_VERSION}). Update the runner."
        )


def _require(doc: dict[str, Any], keys: list[str], kind: str) -> None:
    """Fail loudly, naming what is missing, rather than a bare KeyError deep in
    ``from_dict`` when an artifact is malformed or hand-edited."""
    missing = [k for k in keys if k not in doc]
    if missing:
        raise SystemExit(
            f"ERROR: {kind} is missing required field(s) {missing} "
            f"(schema_version={doc.get('schema_version', 'none')!r}). "
            "Is this a valid artifact, and was it written by a compatible runner?"
        )


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    """Publish a complete JSON document; a failed write preserves the old file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=p.parent, prefix=f".{p.name}.", suffix=".tmp", delete=False
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, p)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return p


_write_json = atomic_write_json


def _read_json(path: str | Path, kind: str) -> dict[str, Any]:
    p = Path(path)
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        raise SystemExit(f"ERROR: {kind} not found: {p}") from None
    except json.JSONDecodeError as e:
        raise SystemExit(f"ERROR: {kind} is not valid JSON: {p}: {e}") from None


def write_ingest_manifest(path: str | Path, manifest: IngestManifest) -> Path:
    return _write_json(path, manifest.to_dict())


def read_ingest_manifest(path: str | Path) -> IngestManifest:
    return IngestManifest.from_dict(_read_json(path, "ingest manifest"))


def write_retrieval_artifact(path: str | Path, artifact: RetrievalArtifact) -> Path:
    return _write_json(path, artifact.to_dict())


def read_retrieval_artifact(path: str | Path) -> RetrievalArtifact:
    return RetrievalArtifact.from_dict(_read_json(path, "retrieval artifact"))

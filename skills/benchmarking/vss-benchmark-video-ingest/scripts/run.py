#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""VSS ingest benchmark -- endpoint-only sweep runner.

Measures how much video a deployed VSS Search profile accepts per unit time,
and how long a single upload takes to become searchable at that load, using
nothing but public endpoints.

A sweep point is one video class at one concurrency. Every worker uploads the
whole class once, so::

    uploads = concurrency x videos in the class
    bytes   = concurrency x total bytes of the class

Three constraints define this benchmark:

* The deployed profile is a black box. Only what the client can time is measured.
* No in-cluster benchmark Job or internal deployment telemetry.
* The client generates all concurrency. A concurrency the endpoint refuses is a
  result, not an obstacle -- never raise a server-side stream, batch, or
  timeout limit to fit it.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from datetime import timezone
from dataclasses import asdict
import importlib.util
import json
import math
from pathlib import Path
import sys
import time
import uuid
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifacts import summarize_point  # noqa: E402
from artifacts import write_corpus_csv  # noqa: E402
from artifacts import write_errors_csv  # noqa: E402
from artifacts import write_requests_csv  # noqa: E402
from artifacts import write_summary_csv  # noqa: E402
from check_compatibility import add_arguments as add_compatibility_arguments  # noqa: E402
from check_compatibility import default_repo  # noqa: E402
from cleanup import wait_for_cleanup  # noqa: E402
from completion import EsReadinessMonitor  # noqa: E402
from completion import ReadinessError  # noqa: E402
from config import DEFAULT_CONFIG_PATH  # noqa: E402
from config import DEFAULT_RESULTS_DIR, DEFAULT_WARMUP  # noqa: E402
from config import ConfigError  # noqa: E402
from config import apply_lists  # noqa: E402
from config import describe as describe_config  # noqa: E402
from config import drop_profile_owned_lists  # noqa: E402
from config import load_config  # noqa: E402
from config import profile_given_on_cli  # noqa: E402
from config import resolve_defaults  # noqa: E402
from es_readiness import EsReadinessConfig  # noqa: E402
from corpus import CorpusError  # noqa: E402
from corpus import VideoItem  # noqa: E402
from corpus import USER_CLASS_NAME  # noqa: E402
from corpus import load_class  # noqa: E402
from corpus import load_user_videos  # noqa: E402
from corpus import projected_bytes  # noqa: E402
from httpio import auth_configured  # noqa: E402
from httpio import redact_url  # noqa: E402
from recover_cleanup import UploadLedger  # noqa: E402
from upload import UploadRecord  # noqa: E402
from upload import delete_asset  # noqa: E402
from upload import pending_sensor_id  # noqa: E402
from upload import video_inventory  # noqa: E402
from upload import upload_one  # noqa: E402
from validate import PROFILES  # noqa: E402
from validate import resolve_matrix  # noqa: E402
from validate import validate  # noqa: E402
from vss_cli import VssCli  # noqa: E402

DEFAULT_TRANSFER_CEILING_GB = 100.0
DEFAULT_STAGGER_SEC = 0.25
DEFAULT_MAX_RAMP_SEC = 60.0


def log(message: str = "") -> None:
    print(message, flush=True)


def gb(num_bytes: float) -> str:
    return f"{num_bytes / 1_000_000_000:.2f} GB"


def _load_config_defaults(argv: list[str] | None) -> tuple[Path | None, dict, dict, dict]:
    """Resolve --config before the real parse, so the file can supply defaults.

    Returns (path, flat_config, scalar_defaults_by_dest, list_values_by_dest).
    A missing default config file is not an error; a missing file the operator
    named with --config is.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path, default=None)
    pre.add_argument("--no-config", action="store_true")
    known, _ = pre.parse_known_args(argv)

    if known.no_config:
        return None, {}, {}, {}
    path = known.config or DEFAULT_CONFIG_PATH
    flat = load_config(path, explicit=known.config is not None)
    scalars, lists = resolve_defaults(flat, path.parent)
    return path, flat, scalars, lists


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    config_path, config_flat, config_scalars, config_lists = _load_config_defaults(argv)

    parser = argparse.ArgumentParser(
        description="Benchmark video ingestion for a deployed VSS Search profile, endpoint-only.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Benchmark config file. Supplies defaults; every flag still wins over it.",
    )
    parser.add_argument(
        "--no-config",
        action="store_true",
        help="Ignore the config file and use built-in defaults only.",
    )
    parser.add_argument(
        "--vss-repo",
        type=Path,
        default=default_repo(),
    )
    parser.add_argument(
        "--cli-config-home",
        type=Path,
        default=None,
        help="CLI config directory; otherwise use VSS_CONFIG_HOME or ~/.vss.",
    )
    parser.add_argument("--cli-executable", default=None, help="Installed vss executable; defaults to PATH.")
    add_compatibility_arguments(parser)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="Local ingest corpus root. Required unless every class comes from --video.",
    )
    parser.add_argument("--profile", default="smoke", choices=sorted(PROFILES), help="Benchmark profile.")
    parser.add_argument(
        "--video-class",
        action="append",
        dest="classes",
        help="Override the profile's classes. Repeatable. Either a predefined class "
        "(50MB, 500MB, 2GB, 10GB) or the name of any folder under --corpus.",
    )
    parser.add_argument(
        "--video",
        action="append",
        dest="videos",
        help="Benchmark your own video instead of the predefined corpus: a file or a "
        "directory of them, .mp4 or .mkv. Repeatable. Needs no corpus root and no "
        "size-class folder layout. On its own it replaces the profile's classes; "
        "with an explicit --video-class it is swept as one more class.",
    )
    parser.add_argument(
        "--video-class-name",
        default=USER_CLASS_NAME,
        help="Label the --video files carry in the CSVs, the summary, and the charts.",
    )
    parser.add_argument(
        "--concurrency",
        action="append",
        type=int,
        dest="concurrencies",
        help="Override the profile's concurrencies. Repeatable.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Use only the first N videos of each class.")
    parser.add_argument("--elasticsearch-url", default="")
    parser.add_argument("--readiness-poll-interval", type=float, default=5.0)
    parser.add_argument("--es-embed-index", default="mdx-embed-filtered-2025-01-01")
    parser.add_argument("--es-raw-index", default="mdx-raw-2025-01-01")
    parser.add_argument("--embed-chunk-duration", type=int, default=5)
    parser.add_argument("--frame-processing-time-ms", type=int, default=33)
    parser.add_argument("--raw-drop-grace-sec", type=int, default=120,
                        help="Failure-only stall timeout, scaled by concurrency; 0 disables this guard.")
    parser.add_argument(
        "--readiness-timeout",
        type=float,
        default=None,
        help="Ceiling on the readiness wait. Defaults to the duration/concurrency-derived budget.",
    )
    parser.add_argument(
        "--es-request-timeout", type=float, default=60.0, help="HTTP timeout for ES reads; CLI owns upload timeouts."
    )
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP, help="Warmup uploads, discarded.")
    parser.add_argument("--stagger-sec", type=float, default=DEFAULT_STAGGER_SEC)
    parser.add_argument("--max-ramp-sec", type=float, default=DEFAULT_MAX_RAMP_SEC)
    parser.add_argument(
        "--cleanup",
        default="always",
        choices=("always", "on-success", "never"),
        help="Delete run-owned media through VSS CLI and wait for raw/Embed removal in ES.",
    )
    parser.add_argument("--cleanup-timeout", type=float, default=300.0,
                        help="Maximum seconds to wait for downstream ES cleanup after CLI deletion.")
    parser.add_argument("--cleanup-settle-sec", type=float, default=30.0,
                        help="Seconds raw and Embed documents must remain absent before continuing.")
    parser.add_argument(
        "--transfer-ceiling-gb",
        type=float,
        default=DEFAULT_TRANSFER_CEILING_GB,
        help="Projected-bytes threshold requiring confirmation.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the projected-bytes confirmation.")
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--set", action="append", dest="overrides", help="key=value sweep override.")
    parser.add_argument("--dry-run", action="store_true", help="Validate, probe the corpus, project bytes, then stop.")

    # Config supplies defaults only: argparse still lets an explicit flag win.
    # The append-action options are excluded here on purpose -- argparse appends
    # to a list default instead of replacing it, so they are filled in after the
    # parse, and only when the flag was absent.
    parser.set_defaults(**config_scalars)
    args = parser.parse_args(argv)
    if args.warmup < 0 or (args.limit is not None and args.limit < 1):
        parser.error("warmup must be nonnegative and limit must be positive")
    if args.cleanup not in {"always", "on-success", "never"}:
        parser.error("cleanup must be always, on-success, or never")
    if args.stagger_sec < 0 or args.max_ramp_sec < 0 or args.transfer_ceiling_gb < 0:
        parser.error("stagger, ramp and transfer ceiling must be nonnegative")
    if args.embed_chunk_duration <= 0 or args.frame_processing_time_ms <= 0:
        parser.error("chunk duration and frame processing time must be positive")
    if args.raw_drop_grace_sec < 0:
        parser.error("raw stall grace must be nonnegative")
    if (not all(isinstance(value, (int, float)) and math.isfinite(value)
                for value in (args.cleanup_timeout, args.cleanup_settle_sec))
            or not 0 < args.cleanup_settle_sec < args.cleanup_timeout):
        parser.error("cleanup timing must be finite, with 0 < settle seconds < timeout")
    if profile_given_on_cli(argv):
        dropped = drop_profile_owned_lists(config_lists, args.profile)
        if dropped:
            args._profile_overrode = (args.profile, dropped)
    apply_lists(args, config_lists)
    args.config_path = config_path
    args.config_flat = config_flat
    return args


def confirm_volume(total_bytes: int, ceiling_gb: float, assume_yes: bool) -> bool:
    """Stop for confirmation when projected bytes exceed the configured ceiling."""
    ceiling_bytes = ceiling_gb * 1_000_000_000
    if total_bytes <= ceiling_bytes:
        return True
    log("")
    log(f"  Projected transfer {gb(total_bytes)} exceeds the ceiling of {ceiling_gb:.1f} GB.")
    if assume_yes:
        log("  --yes given; continuing.")
        return True
    if not sys.stdin.isatty():
        log("  Not a TTY and --yes was not given. Stopping.")
        return False
    answer = input("  Continue? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def run_sweep_point(
    *,
    items: list[VideoItem],
    video_class: str,
    concurrency: int,
    run_uuid: str,
    args: argparse.Namespace,
    readiness_monitor: EsReadinessMonitor,
) -> tuple[list[UploadRecord], float]:
    """Run one (class, concurrency) point. Returns (records, wall_clock_sec).

    ``wall_clock_sec`` spans the first request going out to the last outcome
    being recorded. Cleanup happens after the clock stops, so deletion time
    never inflates the point.

    Workers are staggered by ``--stagger-sec`` so stream admission spreads
    instead of arriving as one burst; the ramp is capped by ``--max-ramp-sec``
    so it cannot become a meaningful share of the point. This does not weaken
    the concurrency being measured -- large uploads overlap for the whole run
    regardless.
    """
    stagger = float(args.stagger_sec or 0.0)
    if stagger > 0 and concurrency > 1 and args.max_ramp_sec > 0:
        full_ramp = stagger * (concurrency - 1)
        if full_ramp > args.max_ramp_sec:
            stagger = args.max_ramp_sec / (concurrency - 1)
            full_ramp = args.max_ramp_sec
        log(f"    upload ramp: {stagger:.3f}s per worker ({full_ramp:.1f}s for {concurrency} workers)")
    else:
        stagger = 0.0

    def worker(worker_index: int) -> list[UploadRecord]:
        if stagger > 0 and worker_index > 1:
            time.sleep((worker_index - 1) * stagger)
        records: list[UploadRecord] = []
        for item_index, item in enumerate(items):
            records.append(
                upload_one(
                    item,
                    worker_index=worker_index,
                    upload_sequence=(worker_index - 1) * len(items) + item_index + 1,
                    concurrency=concurrency,
                    run_uuid=run_uuid,
                    cli=args.cli,
                    readiness_monitor=readiness_monitor,
                    record_identity=getattr(args, "record_identity", None),
                )
            )
        return records

    started = time.monotonic()
    records: list[UploadRecord] = []
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        for result in executor.map(worker, range(1, concurrency + 1)):
            records.extend(result)
    wall_clock_sec = time.monotonic() - started
    return records, wall_clock_sec


def cleanup_records(
    cli: VssCli, records: list[UploadRecord], policy: str, *, es_config: EsReadinessConfig,
    timeout_sec: float = 300.0, settle_sec: float = 30.0,
    record_identity: Callable[..., None] | None = None,
    verification: dict | None = None,
) -> dict[str, int]:
    """Delete owned media and verify ES removal outside measured windows."""
    stats = {"attempted": 0, "deleted": 0, "failed": 0, "no_handle": 0}
    if verification is not None:
        verification.update(status="skipped" if policy == "never" else "no_deletions", polls=0, elapsed_sec=0.0)
    if policy == "never":
        return stats
    inventory = None
    inventory_error = ""
    deleted_records: list[UploadRecord] = []
    for record in records:
        if policy == "on-success" and record.outcome != "confirmed":
            continue
        if not record.sensor_id:
            try:
                if not record.cleanup_intent_recorded or record_identity is None:
                    raise ValueError("No durable upload intent available for identity recovery")
                # A single public listing per cleanup batch; no upload retries or
                # extra reads inside CLI/ES latency or sweep throughput windows.
                if inventory is None and not inventory_error:
                    try:
                        inventory = video_inventory(cli)
                    except (OSError, ValueError) as exc:
                        inventory_error = str(exc)
                if inventory_error:
                    raise ValueError(inventory_error)
                sensor_id = pending_sensor_id(record.upload_filename, inventory)
                if not sensor_id:
                    raise ValueError("Pending upload not listed yet; preserve the ledger for later recovery")
                record_identity(sensor_id=sensor_id, upload_filename=record.upload_filename,
                                camera_name=record.upload_filename.rsplit(".", 1)[0],
                                request_sent_at=record.request_sent_at, cli_exit_code=record.cli_exit_code)
                record.sensor_id = sensor_id
                record.cleanup_identity_recorded = True
            except (OSError, ValueError) as exc:
                stats["no_handle"] += 1
                record.cleanup_detail = f"Cannot resolve cleanup identity: {exc}"
                continue
        if record.cleanup_intent_recorded and not record.cleanup_identity_recorded:
            # A failed append may have written a complete or partial row before
            # fsync failed. Retain the media for recovery; do not append blindly
            # or delete while the durable ledger may still describe an intent.
            stats["failed"] += 1
            record.cleanup_detail = "Cleanup identity persistence failed; media retained for recovery"
            continue
        stats["attempted"] += 1
        ok, detail = delete_asset(cli, record.sensor_id)
        if ok:
            deleted_records.append(record)
        else:
            record.cleanup_detail = detail
            stats["failed"] += 1
    if deleted_records:
        log(f"     Waiting for ES cleanup of {len(deleted_records)} upload(s)")
        result = wait_for_cleanup(
            es_config,
            [(record.upload_filename.rsplit(".", 1)[0], record.sensor_id) for record in deleted_records],
            timeout_sec=timeout_sec,
            settle_sec=settle_sec,
        )
        if verification is not None:
            verification.update(asdict(result), status="confirmed" if result.success else "incomplete")
        for record in deleted_records:
            record.cleanup_detail = (
                f"VIOS recordings removed; {result.detail} "
                f"({result.polls} cleanup polls, {result.elapsed_sec:.1f}s)"
            )
        stats["deleted" if result.success else "failed"] += len(deleted_records)
    return stats


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except ConfigError as exc:
        log(f"ERROR  {exc}")
        return 2
    run_id = f"vss-ingest-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"

    overrides: dict[str, str] = {}
    for item in args.overrides or []:
        key, _, value = item.partition("=")
        overrides[key.strip()] = value.strip()

    try:
        classes, concurrencies = resolve_matrix(
            args.profile,
            args.classes,
            args.concurrencies,
            user_videos=args.videos,
            user_class=args.video_class_name,
        )
    except ValueError as exc:
        log(f"ERROR  {exc}")
        return 2

    # Which class, if any, is fed by --video rather than by a corpus folder.
    user_class = args.video_class_name if args.videos else ""
    if args.corpus is None and set(classes) - {user_class}:
        log("ERROR  --corpus is required unless every class is supplied with --video")
        return 2

    log(f"VSS ingest benchmark -- run_id={run_id}")
    log(f"  profile={args.profile}  classes={','.join(classes)}  concurrency={','.join(map(str, concurrencies))}")
    override = getattr(args, "_profile_overrode", None)
    if override:
        log(f"  note: --profile {override[0]} overrides config sweep {', '.join(override[1])}")
    log(f"  vss_cli_repo={args.vss_repo}")
    log(describe_config(args.config_path, args.config_flat))
    log("")

    log("[1/8] Input and CLI validation")
    result = validate(
        vss_repo=args.vss_repo,
        cli_config_home=args.cli_config_home,
        cli_executable=args.cli_executable,
        version_url=args.version_url,
        version_timeout_sec=args.version_timeout,
        elasticsearch_url=args.elasticsearch_url,
        corpus_root=args.corpus or Path("."),
        profile=args.profile,
        classes=classes,
        concurrencies=concurrencies,
        readiness_poll_interval_sec=args.readiness_poll_interval,
        readiness_timeout_sec=args.readiness_timeout,
        es_request_timeout_sec=args.es_request_timeout,
        results_dir=args.results_dir,
        overrides=overrides,
        check_health=True,
        user_videos=args.videos,
        user_class=user_class,
        progress=lambda message: log(f"\n{message}"),
    )
    log(result.report())
    if not result.ok:
        log("\nStopping: configuration is invalid.")
        return result.error_exit_code

    if not args.dry_run and importlib.util.find_spec("matplotlib") is None:
        log("  ERROR  matplotlib is required for the final charts; install scripts/requirements.txt before benchmarking.")
        return 2

    args.cli = result.cli
    args.elasticsearch_url = result.elasticsearch_url
    deployment = result.deployment

    log("\n  Building the corpus CSV from validated ffprobe measurements")
    corpus: dict[str, list[VideoItem]] = {}
    try:
        for video_class in classes:
            if video_class == user_class:
                items = load_user_videos(args.videos, user_class, args.limit, measurements=result.measurements)
            else:
                items = load_class(args.corpus, video_class, args.limit, measurements=result.measurements)
            corpus[video_class] = items
            total = sum(i.duration_sec for i in items)
            log(
                f"  {video_class}: {len(items)} file(s), "
                f"{gb(sum(i.bytes for i in items))}, {total / 60:.2f} video-minutes"
            )
    except CorpusError as exc:
        log(f"  ERROR  {exc}")
        return 2

    all_items = [item for items in corpus.values() for item in items]
    csv_dir = args.results_dir / "csv"
    raw_dir = args.results_dir / "raw"
    if (raw_dir / "upload_ledger.jsonl").exists():
        log("  ERROR  Results directory already has a run ledger; use a fresh directory to preserve its evidence.")
        return 2
    csv_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    write_corpus_csv(csv_dir / "ingest_corpus.csv", all_items)

    log("\n  Projected transfer volume")
    point_projection: list[dict] = []
    run_bytes = 0
    for video_class in classes:
        for concurrency in concurrencies:
            point_bytes = projected_bytes(corpus[video_class], concurrency)
            run_bytes += point_bytes
            uploads = concurrency * len(corpus[video_class])
            point_projection.append(
                {
                    "video_class": video_class,
                    "concurrency": concurrency,
                    "uploads": uploads,
                    "projected_bytes": point_bytes,
                }
            )
            log(f"  {video_class} @ c{concurrency}: {uploads} upload(s), {gb(point_bytes)}")
    warmup_bytes = args.warmup * min((i.bytes for i in all_items), default=0)
    run_bytes += warmup_bytes
    log(f"  warmup: {args.warmup} upload(s), {gb(warmup_bytes)}")
    log(f"  TOTAL: {gb(run_bytes)} on the wire")

    if args.dry_run:
        log("\n--dry-run: stopping before any upload.")
        return 0

    if not confirm_volume(run_bytes, args.transfer_ceiling_gb, args.yes):
        log("Stopping at user request.")
        return 1

    log("\n[4/8] Elasticsearch readiness preflight")
    try:
        es_config = EsReadinessConfig(
            elasticsearch_url=args.elasticsearch_url or "",
            embed_index=args.es_embed_index,
            raw_index=args.es_raw_index,
            frame_processing_time_ms=args.frame_processing_time_ms,
            embed_chunk_duration_sec=args.embed_chunk_duration,
            poll_interval_sec=args.readiness_poll_interval,
            raw_drop_grace_sec=args.raw_drop_grace_sec,
            # 0 keeps the computed per-upload budget; an explicit
            # --readiness-timeout becomes a hard ceiling.
            timeout_override_sec=(float(args.readiness_timeout) if args.readiness_timeout is not None else 0.0),
        )
        readiness_monitor = EsReadinessMonitor(
            args.elasticsearch_url,
            args.readiness_poll_interval,
            args.es_request_timeout,
            es_config,
        )
        readiness_monitor.preflight()
    except (ReadinessError, OSError, ValueError) as exc:
        log(f"  ERROR  {exc}")
        return 2
    log(f"  ok      {readiness_monitor.label} answered")

    try:
        ledger = UploadLedger(raw_dir / "upload_ledger.jsonl", run_id, deployment)
    except (OSError, ValueError) as exc:
        log(f"  ERROR  Cannot create recovery ledger: {exc}; use a fresh results directory.")
        return 2
    args.record_identity = ledger.record

    warmup_records: list[UploadRecord] = []
    cleanup_checks: list[dict] = []
    warmup_cleanup: dict = {}
    all_records: list[UploadRecord] = []
    summary_rows: list[dict] = []
    cleanup_totals = {"attempted": 0, "deleted": 0, "failed": 0, "no_handle": 0}
    stop_reason = ""

    metadata = {
        "run_id": run_id,
        "version_compatibility": result.version_compatibility,
        "started_at_utc": run_id.split("-")[2],
        "blueprint": "vss-ingest",
        "phase": 1,
        "profile": args.profile,
        "video_classes": classes,
        "concurrencies": concurrencies,
        "corpus_root": str(args.corpus) if args.corpus else "",
        "corpus_limit": args.limit,
        "user_videos": list(args.videos or []),
        "user_video_class": user_class,
        "upload_flow": "vss-cli",
        "upload_route": "vss vios add --type video PATH --name UNIQUE_FILENAME",
        "cli": {"command": list(args.cli.command), "config_home": args.cli.config_home},
        "comparability_note": "Compare runs only with matching CLI, corpus, deployment and polling settings.",
        "byte_accounting": "Successful CLI upload payload bytes only; partial failures and wire overhead unknown.",
        "es_readiness": {
            "elasticsearch_url": redact_url(args.elasticsearch_url),
            "embed_index": args.es_embed_index,
            "raw_index": args.es_raw_index,
            "expect_embed": True,
            "expect_raw": True,
            "embed_chunk_duration_sec": args.embed_chunk_duration,
            "frame_processing_time_ms": args.frame_processing_time_ms,
            "raw_drop_grace_sec": args.raw_drop_grace_sec,
            "completion_rule": "raw_count >= expected_frames and embed_count >= expected_chunks",
            "poll_interval_sec": args.readiness_poll_interval,
            "timeout_sec": args.readiness_timeout,
            "frame_tolerance": 15,
            "clock_stops_at": "client observation of both expected ES counts",
            "timing_bias": "Polling and ES request latency delay observation of actual completion.",
        },
        "es_request_timeout_sec": args.es_request_timeout,
        "upload_timeout_owner": "VSS CLI (no benchmark retry or timeout wrapper)",
        "warmup_uploads": args.warmup,
        "warmup_cleanup": warmup_cleanup,
        "stagger_sec": args.stagger_sec,
        "max_ramp_sec": args.max_ramp_sec,
        "cleanup_policy": args.cleanup,
        "cleanup_verification": {
            "timeout_sec": args.cleanup_timeout,
            "settle_sec": args.cleanup_settle_sec,
            "scope": "CLI-confirmed recording deletion and sustained raw/Embed absence in public ES",
        },
        "cleanup_ledger": "raw/upload_ledger.jsonl",
        "cleanup_totals": cleanup_totals,
        "cleanup_checks": cleanup_checks,
        "stop_reason": stop_reason,
        "sweep_points_completed": len(summary_rows),
        "transfer_ceiling_gb": args.transfer_ceiling_gb,
        "projected_bytes_total": run_bytes,
        "projected_by_point": point_projection,
        "vss_service_url": redact_url(deployment.get("base_url", "")),
        "auth_token_provided": auth_configured(),
        "sweep_overrides": overrides,
        # Provenance: which config file supplied defaults, and what it set, so
        # a run can be reconstructed from the artifact alone. Any value whose
        # key names a URL goes through redact_url -- a config file should carry
        # no credentials, but an operator may still have embedded one.
        "config_file": str(args.config_path) if args.config_path else "",
        "config_keys": {
            key: (redact_url(str(value)) if key.endswith("url") and value else value)
            for key, value in sorted(args.config_flat.items())
        },
        "telemetry_collected": "none",
        "measurement_scope": (
            "All metrics are client-observed. No Kubernetes, Prometheus, GPU, pod, trace, "
            "log, or internal pipeline telemetry was collected."
        ),
    }

    def write_metadata() -> None:
        metadata.update(
            warmup_cleanup=warmup_cleanup, stop_reason=stop_reason,
            sweep_points_completed=len(summary_rows),
        )
        (args.results_dir / "run-metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    write_metadata()

    if args.warmup > 0:
        log(f"\n[5/8] Warmup ({args.warmup} upload(s), discarded)")
        warmup_item = min(all_items, key=lambda i: i.bytes)
        for index in range(args.warmup):
            record = upload_one(
                warmup_item,
                worker_index=0,
                upload_sequence=900_00 + index,
                concurrency=1,
                run_uuid=uuid.uuid4().hex,
                cli=args.cli,
                readiness_monitor=readiness_monitor,
                record_identity=args.record_identity,
            )
            warmup_records.append(record)
            log(f"  warmup {index + 1}: {record.outcome} in {record.latency_sec:.1f}s (discarded)")
        warmup_verification: dict = {}
        warmup_cleanup = cleanup_records(
            args.cli,
            warmup_records,
            args.cleanup,
            es_config=es_config,
            timeout_sec=args.cleanup_timeout,
            settle_sec=args.cleanup_settle_sec,
            record_identity=args.record_identity,
            verification=warmup_verification,
        )
        cleanup_checks.append({"phase": "warmup", "stats": warmup_cleanup, "es_wait": warmup_verification})
        (raw_dir / "warmup_details.jsonl").write_text(
            "\n".join(
                json.dumps(
                    {
                        **r.as_row(),
                        "sensor_id": r.sensor_id,
                        "error_detail": r.error_detail,
                        "cleanup_detail": r.cleanup_detail,
                    }
                )
                for r in warmup_records
            )
            + "\n",
            encoding="utf-8",
        )
        warmup_errors = []
        for index, record in enumerate(warmup_records, start=1):
            if record.outcome != "confirmed":
                warmup_errors.append(
                    f"Warmup ingestion {index} ({record.upload_filename}): {record.outcome}; "
                    f"{record.error_detail or 'ingestion was not confirmed'}"
                )
        if warmup_cleanup["failed"]:
            warmup_errors.append(f"Warmup cleanup failed for {warmup_cleanup['failed']} upload(s).")
        if warmup_cleanup["no_handle"]:
            warmup_errors.append(
                f"Warmup cleanup identity unresolved for {warmup_cleanup['no_handle']} upload(s); "
                "could not safely select media for deletion."
            )
        if warmup_errors:
            stop_reason = "; ".join(warmup_errors)
            write_metadata()
            for error in warmup_errors:
                log(f"  ERROR  {error}")
            if warmup_cleanup["failed"] or warmup_cleanup["no_handle"]:
                # Details can describe successful cleanup too; label them as
                # context, not additional failures in a mixed warmup batch.
                for index, record in enumerate(warmup_records, start=1):
                    if record.cleanup_detail:
                        log(f"  warmup {index} cleanup ({record.upload_filename}): {record.cleanup_detail}")
                log(f"  Recovery ledger: {raw_dir / 'upload_ledger.jsonl'}")
            log(f"Stopping before the measured sweep. Warmup details: {raw_dir / 'warmup_details.jsonl'}")
            return 1
        write_metadata()
    else:
        log("\n[5/8] Warmup skipped (--warmup 0)")

    log("\n[6/8] Sweep")
    for video_class in classes:
        for concurrency in concurrencies:
            items = corpus[video_class]
            log(f"\n  -- {video_class} @ c{concurrency}: {concurrency * len(items)} upload(s)")
            records, wall_clock_sec = run_sweep_point(
                items=items,
                video_class=video_class,
                concurrency=concurrency,
                run_uuid=uuid.uuid4().hex,
                args=args,
                readiness_monitor=readiness_monitor,
            )
            all_records.extend(records)
            row = summarize_point(
                video_class=video_class,
                concurrency=concurrency,
                records=records,
                wall_clock_sec=wall_clock_sec,
            )
            summary_rows.append(row)
            log(
                f"     confirmed={row['success_count']}/{row['upload_count']}  "
                f"video_min_per_sec={row['video_min_per_sec']}  "
                f"p95={row['p95_latency_sec']}s  "
                f"aggregate={row['aggregate_mb_per_sec']} MB/s"
            )
            if row["cli_exit_statuses"]:
                log(f"     CLI exit histogram: {row['cli_exit_statuses']}")

            verification: dict = {}
            stats = cleanup_records(
                args.cli, records, args.cleanup, es_config=es_config,
                timeout_sec=args.cleanup_timeout, settle_sec=args.cleanup_settle_sec,
                record_identity=args.record_identity,
                verification=verification,
            )
            cleanup_checks.append({
                "phase": "sweep", "video_class": video_class, "concurrency": concurrency,
                "stats": stats, "es_wait": verification,
            })
            for key in cleanup_totals:
                cleanup_totals[key] += stats[key]
            if stats["attempted"]:
                log(f"     cleanup: deleted {stats['deleted']}/{stats['attempted']}")
            if args.cleanup != "never" and (stats["failed"] or stats["no_handle"]):
                stop_reason = (
                    f"Cleanup incomplete at {video_class}@c{concurrency}: "
                    f"{stats['failed']} cleanup operation(s) failed, {stats['no_handle']} missing handle(s); "
                    "remaining sweep points were not started."
                )
                log(f"     Stopping: {stop_reason}")
                break
            write_metadata()
        if stop_reason:
            break

    log("\n[7/8] Writing artifacts")
    write_requests_csv(csv_dir / "ingest_requests.csv", all_records)
    write_summary_csv(csv_dir / "ingest_summary.csv", summary_rows)
    write_errors_csv(csv_dir / "ingest_errors.csv", all_records)
    (raw_dir / "upload_details.jsonl").write_text(
        "\n".join(
            json.dumps(
                {
                    **record.as_row(),
                    "sensor_id": record.sensor_id,
                    "cli_response": record.cli_response,
                    "error_detail": record.error_detail,
                    "cleanup_detail": record.cleanup_detail,
                    "transmitted_bytes": record.transmitted_bytes,
                    "readiness_polls": record.readiness_polls,
                },
                sort_keys=True,
            )
            for record in all_records
        )
        + "\n",
        encoding="utf-8",
    )

    write_metadata()

    log("\n[8/8] Generating and validating summaries and charts")
    # Generate from the persisted CSVs, keeping all reporting outside timing.
    try:
        import charts
        import summarize
        from validate_artifacts import validate_artifacts

        output_args = ["--results-dir", str(args.results_dir)]
        if summarize.main(output_args) or charts.main(output_args):
            log("  ERROR  Artifact generation failed; recorded CSVs and raw results are retained.")
            return 2
        errors = validate_artifacts(args.results_dir)
    except (ImportError, OSError, ValueError) as exc:
        log(f"  ERROR  Artifact generation or validation failed: {exc}")
        return 2
    if errors:
        for error in errors:
            log(f"  ERROR  {error}")
        return 2
    log("  ok      Required artifacts validated; interpretations remain client-observed.")
    log(f"\nArtifacts: {args.results_dir}")
    return 0 if all(row["result_valid"] for row in summary_rows) and not stop_reason else 1


if __name__ == "__main__":
    sys.exit(main())

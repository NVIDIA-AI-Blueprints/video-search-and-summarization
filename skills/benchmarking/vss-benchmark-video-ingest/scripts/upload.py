# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""One CLI upload followed by Elasticsearch readiness.

VIOS dispatches the deployed webhooks; this client does not invoke the Agent or
register consumers itself. CLI exit 0 confirms VIOS media availability, not ES.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from pathlib import Path
import re
import time
from typing import Any
from typing import Callable

from completion import EsReadinessMonitor
from completion import UploadContext
from completion import ReadinessResult
from corpus import VideoItem
from vss_cli import VssCli

#: Terminal outcomes recorded in ``ingest_requests.csv``.
OUTCOMES = ("confirmed", "unconfirmed", "failed", "timed_out")
UPLOAD_NAME = re.compile(r"[0-9a-f]{32}-[0-9]{5,}\.[a-z0-9]+\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iso_delta(started_at: str, completed_at: str) -> float:
    """Seconds between two ISO timestamps, or 0.0 when either is unusable."""
    if not started_at or not completed_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (end - start).total_seconds())


@dataclass
class UploadRecord:
    """One row of ``ingest_requests.csv``."""

    worker_index: int
    upload_sequence: int
    video_id: str
    video_class: str
    concurrency: int
    upload_filename: str
    bytes: int
    duration_sec: float
    request_sent_at: str
    ingest_confirmed_at: str
    latency_sec: float
    es_indexed_latency_sec: float
    http_status: str
    outcome: str
    #: Not a CSV column -- carried so cleanup can delete what the run created.
    sensor_id: str = field(default="", repr=False)
    cleanup_intent_recorded: bool = field(default=False, repr=False)
    cleanup_identity_recorded: bool = field(default=False, repr=False)
    cli_exit_code: int | str = ""
    cli_duration_sec: float = 0.0
    cli_response: dict[str, Any] = field(default_factory=dict, repr=False)
    cleanup_detail: str = field(default="", repr=False)
    error_detail: str = field(default="", repr=False)
    transmitted_bytes: int = field(default=0, repr=False)
    readiness_polls: int = field(default=0, repr=False)
    readiness_metrics: dict[str, Any] = field(default_factory=dict, repr=False)

    @property
    def expected_frames(self) -> int:
        return int(self.readiness_metrics.get("expected_frames") or 0)

    @property
    def es_frame_count(self) -> int:
        return int(self.readiness_metrics.get("es_frame_count") or 0)

    @property
    def expected_chunks(self) -> int:
        return int(self.readiness_metrics.get("expected_chunks") or 0)

    @property
    def es_chunk_count(self) -> int:
        return int(self.readiness_metrics.get("es_chunk_count") or 0)

    @property
    def raw_completion_ratio(self) -> float | str:
        value = self.readiness_metrics.get("raw_completion_ratio")
        return value if isinstance(value, (int, float)) else ""

    @property
    def rt_cv_dropped(self) -> bool | str:
        value = self.readiness_metrics.get("rt_cv_dropped")
        return value if isinstance(value, bool) else ""

    #: Columns of ``ingest_requests.csv``, in order.
    CSV_FIELDS = (
        "worker_index",
        "upload_sequence",
        "video_id",
        "video_class",
        "concurrency",
        "upload_filename",
        "bytes",
        "duration_sec",
        "request_sent_at",
        "ingest_confirmed_at",
        "latency_sec",
        "es_indexed_latency_sec",
        "http_status",
        "cli_exit_code",
        "cli_duration_sec",
        "outcome",
        "expected_frames",
        "es_frame_count",
        "expected_chunks",
        "es_chunk_count",
        "raw_completion_ratio",
        "rt_cv_dropped",
    )

    def as_row(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in self.CSV_FIELDS}


def generate_upload_filename(run_uuid: str, upload_sequence: int, suffix: str) -> str:
    """``<run_uuid>-<seq>.<ext>`` -- the harness naming scheme.

    The caller supplies a fresh UUID for every point and warmup. Short, unique, and free of any source-file stem, so a class
    of one clip uploaded at concurrency 20 produces 20 distinct assets.
    """
    return f"{run_uuid}-{upload_sequence:05d}{suffix or '.mp4'}"


def _sensor_id_from(body: dict[str, Any]) -> str:
    for key in ("sensor_id", "sensorId", "vst_sensor_id", "id"):
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def video_inventory(cli: VssCli) -> list[dict[str, Any]]:
    """Read concrete VIOS handles once; a failed listing is not an empty one."""
    result = cli.call("vios", "list", "--type", "video")
    sensors = result.body.get("sensors")
    if result.exit_code or not isinstance(sensors, list):
        raise ValueError(f"Cannot verify VIOS inventory: CLI exit {result.exit_code}")
    # The CLI includes unclassifiable/error rows even with --type video.
    # Keep those rows so an unresolved target cannot look absent.
    for sensor in sensors:
        if (not isinstance(sensor, dict)
                or not isinstance(sensor.get("sensor_id"), str)
                or not isinstance(sensor.get("name"), str)):
            raise ValueError("Invalid VIOS video inventory")
    return sensors


def pending_sensor_id(upload_filename: str, sensors: list[dict[str, Any]]) -> str:
    """Resolve a persisted UUID upload intent, never a prefix or an invented ID."""
    if not UPLOAD_NAME.fullmatch(upload_filename):
        raise ValueError("Invalid pending upload filename")
    camera_name = upload_filename.rsplit(".", 1)[0]
    matches = [sensor for sensor in sensors if sensor["name"] == camera_name]
    if not matches:
        return ""
    if len(matches) != 1:
        raise ValueError("Ambiguous pending upload name; refusing cleanup")
    sensor_id = matches[0]["sensor_id"]
    if not sensor_id or sensor_id != sensor_id.strip() or matches[0].get("type") != "video":
        raise ValueError("Pending upload has no valid video identity; refusing cleanup")
    if sum(sensor["sensor_id"] == sensor_id for sensor in sensors) != 1:
        raise ValueError("Ambiguous pending upload sensor ID; refusing cleanup")
    return sensor_id


def upload_one(
    item: VideoItem,
    *,
    worker_index: int,
    upload_sequence: int,
    concurrency: int,
    run_uuid: str,
    cli: VssCli,
    readiness_monitor: EsReadinessMonitor,
    record_identity: Callable[..., None] | None = None,
) -> UploadRecord:
    """Send one upload and confirm it. Never raises -- every failure is a row.

    ``latency_sec`` is the harness ``upload_duration_sec``: client monotonic
    time from CLI process launch to readiness being established. Under
    ``es_readiness`` that covers upload transfer, media store write, storage
    read-back, detection, embedding, and the Kafka to Logstash to
    Elasticsearch indexing tail, as the client sees them. It is not transfer
    time alone, and it is not any server-side stage.
    """
    upload_filename = generate_upload_filename(run_uuid, upload_sequence, Path(item.source_path).suffix.lower())
    camera_name = upload_filename.rsplit(".", 1)[0]

    request_sent_at = utc_now()
    body: dict[str, Any] = {}
    transmitted = 0
    detail = ""
    outcome = ""
    readiness_metrics: dict[str, Any] = {}
    server_completed_at = ""

    cli_exit_code: int | str = ""
    cli_duration_sec = 0.0
    intent_recorded = False
    identity_recorded = False
    # Persist the exact UUID name before the mutation: the CLI may be killed or
    # time out after VIOS accepted the media but before returning its sensor ID.
    if record_identity is not None:
        try:
            record_identity(sensor_id="", upload_filename=upload_filename, camera_name=camera_name,
                            request_sent_at=request_sent_at, cli_exit_code="")
            intent_recorded = True
        except (OSError, ValueError) as exc:
            outcome, detail = "failed", f"Cannot persist upload intent; upload not started: {exc}"

    # Intent persistence is preparation, outside CLI/upload latency timing.
    request_sent_at = utc_now()
    started = time.monotonic()
    if not outcome:
        try:
            response = cli.call(
                "vios", "add", "--type", "video", str(Path(item.source_path).resolve()), "--name", upload_filename
            )
            cli_duration_sec = round(time.monotonic() - started, 3)
            cli_exit_code = response.exit_code
            body = response.body
            if response.exit_code:
                outcome = "timed_out" if response.exit_code == 7 else "failed"
                detail = f"CLI exit {response.exit_code}: {response.detail}"
            elif body.get("added") is not True or body.get("type") != "video":
                outcome, detail = "failed", response.detail or "CLI exit 0 returned no video upload acknowledgement"
            else:
                # Successful payload bytes, not a wire counter. Partial failed transfers
                # and HTTP overhead are not observable through the CLI.
                transmitted = item.bytes
        except Exception as exc:  # noqa: BLE001 -- preserve a result for a client failure
            cli_duration_sec = round(time.monotonic() - started, 3)
            outcome, detail = "failed", detail or f"{type(exc).__name__}: {exc}"

    sensor_id = _sensor_id_from(body)
    # Persist ownership before the potentially long ES wait. An interrupted
    # caller can then recover this exact asset without selecting by name alone.
    if sensor_id and record_identity is not None:
        try:
            record_identity(
                sensor_id=sensor_id,
                upload_filename=upload_filename,
                camera_name=camera_name,
                request_sent_at=request_sent_at,
                cli_exit_code=cli_exit_code,
            )
            identity_recorded = True
        except (OSError, ValueError) as exc:
            outcome = outcome or "unconfirmed"
            detail = f"Cannot persist cleanup identity: {exc}"

    if not outcome:
        if not sensor_id:
            outcome, detail, polls = "unconfirmed", "CLI upload returned no sensor_id; cannot correlate ES", 0
        else:
            reported_chunks = 0
            try:
                reported_chunks = int(body.get("chunks_processed") or 0)
            except (TypeError, ValueError):
                reported_chunks = 0
            try:
                confirmation = readiness_monitor.confirm(
                    UploadContext(
                        upload_filename=upload_filename,
                        camera_name=camera_name,
                        sensor_id=sensor_id,
                        response_body=body,
                        duration_sec=item.duration_sec,
                        fps=item.fps,
                        reported_chunks=reported_chunks,
                        concurrency=concurrency,
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- failed ES observation is not upload failure
                confirmation = ReadinessResult(False, "unconfirmed", f"ES readiness error: {exc}")
            outcome = confirmation.outcome
            if not confirmation.confirmed:
                detail = confirmation.detail
            polls = confirmation.polls
            readiness_metrics = confirmation.metrics or {}
            server_completed_at = confirmation.completed_at
    else:
        polls = 0

    # ``ingest_confirmed_at`` mirrors the harness ``completed_at``: when the
    # adapter can see the moment the work actually finished -- es_readiness
    # reports max(ingested_at) across the indexed documents -- that instant is
    # recorded, otherwise the client clock at confirmation. It is what the
    # throughput window closes on, exactly as in
    # ``ingest/metrics/throughput.py::_upload_window_sec``.
    if outcome == "confirmed":
        ingest_confirmed_at = server_completed_at or utc_now()
    else:
        ingest_confirmed_at = ""

    # ``latency_sec`` is the harness ``upload_duration_sec``: client monotonic
    # time from CLI process launch to readiness being established. The
    # harness computes its ingest_latency_p50/p95 and max_upload_duration_sec
    # from exactly this value, so the reported percentiles must use it too --
    # substituting the server-observed delta would report a different, smaller
    # quantity under harness column names.
    latency_sec = round(time.monotonic() - started, 3)
    # The poll-interval-free view of the same upload, for diagnosis only. The
    # difference between the two is the poll latency plus the response leg;
    # it is never what the summary percentiles are taken over.
    es_indexed_latency_sec = _iso_delta(request_sent_at, ingest_confirmed_at)

    return UploadRecord(
        worker_index=worker_index,
        upload_sequence=upload_sequence,
        video_id=item.video_id,
        video_class=item.video_class,
        concurrency=concurrency,
        upload_filename=upload_filename,
        bytes=item.bytes,
        duration_sec=item.duration_sec,
        request_sent_at=request_sent_at,
        ingest_confirmed_at=ingest_confirmed_at,
        latency_sec=latency_sec,
        es_indexed_latency_sec=round(es_indexed_latency_sec, 3),
        http_status="",  # CLI does not expose a structured HTTP status
        cli_exit_code=cli_exit_code,
        cli_duration_sec=cli_duration_sec,
        cli_response=body,
        outcome=outcome,
        sensor_id=sensor_id,
        cleanup_intent_recorded=intent_recorded,
        cleanup_identity_recorded=identity_recorded,
        error_detail=detail,
        transmitted_bytes=transmitted,
        readiness_polls=polls,
        readiness_metrics=readiness_metrics,
    )


def delete_asset(cli: VssCli, sensor_id: str) -> tuple[bool, str]:
    """Delete only a returned or inventory-verified VIOS handle; downstream cleanup is webhook-owned.

    Exit 0 confirms VIOS deletion. It does not prove the asynchronous ES removal
    webhooks completed. Never delete ES records directly to conceal that gap.
    """
    if not sensor_id:
        return False, "no sensor id"
    try:
        result = cli.call("vios", "delete", "--type", "video", "--sensor", sensor_id)
        return result.exit_code == 0, result.detail
    except (OSError, ValueError) as exc:
        return False, str(exc)

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Paired real-video CPU characterization and controlled offline E2E."""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from contextlib import aclosing, nullcontext
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from pydantic import ValidationError

from vss import video_processing as processing
from vss.ingestion import vlm
from vss.observability import collect_vss_metrics, metric_scope
from vss.summarization import (
    InMemoryChunkStore,
    summarize_from_store,
    summarize_video,
)

from .artifacts import (
    aggregate,
    bundle_bytes,
    publish_bundle,
    verify_inventory,
    verify_trace,
    write_trace,
)
from .common import environment, import_mode, software_hash
from .media import MediaError, MediaManifest, file_hash, generated, inspect
from .processing import preflight
from .resources import ResourceSampler
from .run import FixtureGenerator
from .supervisor import TrialCleanupError, isolated_trial
from .video_results import (
    DecodePeriod,
    VideoConfiguration,
    VideoResult,
    VideoTrial,
    WarmupDiagnostic,
    WorkflowEvidence,
    balanced_orders,
    identity,
    selected_chunks,
)


async def decode_period(
    path: Path,
    manifest: MediaManifest,
    oracle: np.ndarray,
    *,
    enabled: bool,
    artifacts: Path,
    decoder: str = "opencv",
) -> DecodePeriod:
    count = pts_checks = pixels_checked = 0
    max_error = 0.0
    correct = True
    failure = None
    oracle_positions = {
        index: position for position, index in enumerate(manifest.oracle_indices)
    }
    collector = collect_vss_metrics(max_events=100000) if enabled else nullcontext(None)
    start_cpu = os.times()
    started = time.perf_counter()
    with collector as metrics:
        stream = processing.decode_frames(
            path,
            processing.DecodePlan(),
            source_id=manifest.sha256,
            options=processing.DecodeOptions(decoder=decoder, batch_size=4),
        )
        try:
            async with aclosing(stream):
                async for frame in stream:
                    if count >= len(manifest.pts_ns):
                        correct = False
                        break
                    good = (
                        frame.index == count
                        and frame.decoder == decoder
                        and frame.device == ("gpu" if decoder == "pynv" else "cpu")
                        and frame.timestamp_quality != "estimated"
                        and abs(frame.timestamp_ns - manifest.pts_ns[count])
                        <= 2_000_000
                        and frame.tensor.shape == (manifest.height, manifest.width, 3)
                        and frame.tensor.dtype == np.uint8
                        and frame.tensor.flags.c_contiguous
                        and frame.tensor.flags.owndata
                        and not frame.tensor.flags.writeable
                    )
                    pts_checks += 1
                    correct &= good
                    if count in oracle_positions:
                        error = float(
                            np.abs(
                                frame.tensor.astype(np.int16)
                                - oracle[oracle_positions[count]].astype(np.int16)
                            ).mean()
                        )
                        max_error = max(max_error, error)
                        correct &= error <= 3
                        pixels_checked += 1
                    count += 1
        except Exception:
            failure = "decode"
            correct = False
    wall = time.perf_counter() - started
    end_cpu = os.times()
    correct &= count == len(manifest.pts_ns) and pixels_checked == len(
        manifest.oracle_indices
    )
    if not correct and failure is None:
        failure = "correctness"
    records = metrics.observations if metrics is not None else ()
    if metrics is not None and not metrics.complete:
        failure = "collection"
    trace = None
    if enabled:
        try:
            trace = write_trace(artifacts, records)
        except Exception:
            failure = "artifact"
    return DecodePeriod(
        mode="enabled" if enabled else "disabled",
        status="error" if failure else "ok",
        failure=failure,
        wall_s=wall,
        cpu_s=max(0, end_cpu.user + end_cpu.system - start_cpu.user - start_cpu.system),
        frames=count,
        pts_checks=pts_checks,
        pixel_checks=pixels_checked,
        maximum_rgb_mae=max_error,
        correctness=correct,
        collection_complete=metrics.complete if metrics else None,
        metrics=aggregate(records),
        trace=trace,
    )


async def offline_workflow(
    path: Path, media: MediaManifest, config: VideoConfiguration, artifacts: Path
) -> WorkflowEvidence:
    expected = selected_chunks(media)
    seen: dict[int, tuple[int, ...]] = {}
    requests = max_bytes = 0
    jpeg_checks = 0
    maximum_jpeg_mae = 0.0
    current_frames: tuple[processing.Frame, ...] = ()
    event_payload: dict[str, Any] = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests, max_bytes, jpeg_checks, maximum_jpeg_mae
        requests += 1
        max_bytes = max(max_bytes, len(request.content))
        if requests > 61 or max_bytes > 32 * 1024**2:
            raise ValueError("offline request admission limit")
        import cv2

        body = json.loads(request.content)
        images = [
            part
            for message in body["messages"]
            if message["role"] == "user"
            for part in message["content"]
            if part.get("type") == "image_url"
        ]
        if len(images) != len(current_frames) or not images:
            raise ValueError("HTTP sampled image count mismatch")
        for image, frame in zip(images, current_frames, strict=True):
            url = image["image_url"]["url"]
            prefix = "data:image/jpeg;base64,"
            if not url.startswith(prefix):
                raise ValueError("HTTP image format mismatch")
            raw = base64.b64decode(url[len(prefix) :], validate=True)
            if jpeg_dimensions(raw) != (frame.tensor.shape[1], frame.tensor.shape[0]):
                raise ValueError("HTTP JPEG dimensions mismatch")
            bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
            if bgr is None or bgr.shape != frame.tensor.shape:
                raise ValueError("HTTP JPEG decode mismatch")
            error = float(
                np.abs(
                    cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB).astype(np.int16)
                    - frame.tensor.astype(np.int16)
                ).mean()
            )
            maximum_jpeg_mae = max(maximum_jpeg_mae, error)
            if error >= 10:
                raise ValueError("HTTP JPEG RGB mismatch")
            jpeg_checks += 1
        await asyncio.sleep(config.mock_delay_ms / 1000)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "events": [
                                        {
                                            **event_payload,
                                        }
                                    ]
                                }
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    class CheckedBackend(vlm.OpenAICompatibleVLMBackend):
        def __init__(self):
            super().__init__(
                client=client,
                model="offline-fixture",
                base_url="http://offline.invalid/v1",
            )

        async def infer(self, chunk: vlm.VLMFrameChunk, **kwargs: Any):
            nonlocal current_frames
            indices = tuple(frame.index for frame in chunk.frames)
            if indices != expected.get(chunk.chunk_idx) or any(
                abs(frame.timestamp_ns - media.pts_ns[index]) > 2_000_000
                for frame, index in zip(
                    chunk.frames, expected[chunk.chunk_idx], strict=True
                )
            ):
                raise ValueError("independent sampling oracle mismatch")
            seen[chunk.chunk_idx] = indices
            event_payload.update(
                start_time=chunk.start_s,
                end_time=min(chunk.end_s, chunk.start_s + 1),
                description=f"controlled offline event {chunk.chunk_idx}",
            )
            current_frames = chunk.frames
            try:
                return await super().infer(chunk, **kwargs)
            finally:
                current_frames = ()

    # Worker-local injection: production process_video constructs the backend and
    # owns its lifecycle; the injected client remains explicitly caller-owned.
    old = vlm._VLM_BACKEND_REGISTRY.get("vlm")
    vlm._VLM_BACKEND_REGISTRY["vlm"] = CheckedBackend
    chunks = events = 0
    stored_events = 0
    store_wall = 0.0
    status = "ok"
    started = time.perf_counter()
    with (
        collect_vss_metrics(max_events=100000) as metrics,
        metric_scope(source_id=media.sha256, pipeline="offline_e2e"),
    ):
        try:
            from vss.ingestion import CPUVLMBackend, RequestSource

            async with CPUVLMBackend() as processor:
                responses = await processor.ingest(
                    RequestSource(url=str(path), source_id=media.sha256),
                    prompt=vlm.DEFAULT_VLM_PROMPT,
                    chunk_duration=30,
                    model="offline-fixture",
                )
            chunks = len(responses)
            if chunks != len(expected):
                raise ValueError("offline returned chunk count mismatch")
            for position, (response, index) in enumerate(
                zip(responses, expected, strict=True)
            ):
                chunk = response.chunk
                start = index * 30
                end = min(start + 30, media.duration_s)
                if (
                    chunk is None
                    or chunk.sourceId != media.sha256
                    or chunk.chunkIdx != index
                    or abs(chunk.start_pts - start * 1e9) > 2_000_000
                    or abs(chunk.end_pts - end * 1e9) > 2_000_000
                    or chunk.is_first != (index == 0)
                    or chunk.is_last != (position == chunks - 1)
                    or response.error is not None
                    or response.rtvi_frame_count != len(expected[index])
                ):
                    raise ValueError("offline returned chunk identity mismatch")
                payload = json.loads(response.vlm_response or "{}")
                returned_events = payload.get("events")
                if isinstance(returned_events, list) and len(returned_events) == 1:
                    event = returned_events[0]
                    if not isinstance(event, dict) or any(
                        type(event.get(field)) not in {int, float}
                        or not math.isfinite(event[field])
                        for field in ("start_time", "end_time")
                    ):
                        raise ValueError("offline returned event timestamp invalid")
                if (
                    not isinstance(returned_events, list)
                    or len(returned_events) != 1
                    or returned_events[0].get("description")
                    != f"controlled offline event {index}"
                    or abs(returned_events[0].get("start_time", -1) - start) > 0.002
                    or abs(returned_events[0].get("end_time", -1) - min(end, start + 1))
                    > 0.002
                ):
                    raise ValueError("offline returned event identity mismatch")
            summary = await summarize_video(responses, generator=FixtureGenerator())
            events = json.loads(summary.choices[0].message.content or "{}")[
                "total_events"
            ]
            store_started = time.perf_counter()
            store = InMemoryChunkStore()
            await store.store_chunks("offline-job", responses)
            stored = await summarize_from_store(
                "offline-job", store=store, generator=FixtureGenerator()
            )
            stored_events = json.loads(stored.choices[0].message.content or "{}")[
                "total_events"
            ]
            store_wall = time.perf_counter() - store_started
            if (
                seen != expected
                or chunks != len(expected)
                or events != chunks
                or stored_events != events
                or not responses[-1].chunk
                or not responses[-1].chunk.is_last
            ):
                raise ValueError("offline output count mismatch")
        except Exception:
            status = "error"
        finally:
            await client.aclose()
            if old is None:
                vlm._VLM_BACKEND_REGISTRY.pop("vlm", None)
            else:
                vlm._VLM_BACKEND_REGISTRY["vlm"] = old
    wall = time.perf_counter() - started
    records = metrics.observations
    return WorkflowEvidence(
        status=status,
        wall_s=wall,
        chunks=chunks,
        events=events,
        stored_events=stored_events,
        store_summary_wall_s=store_wall,
        selected_frames=sum(len(group) for group in seen.values()),
        jpeg_checks=jpeg_checks,
        maximum_jpeg_mae=maximum_jpeg_mae,
        http_requests=requests,
        request_bytes_max=max_bytes,
        client_closed=client.is_closed,
        collection_complete=metrics.complete,
        metrics=aggregate(records),
        trace=write_trace(artifacts, records),
    )


def jpeg_dimensions(raw: bytes) -> tuple[int, int]:
    """Inspect bounded JPEG geometry before the native image allocation."""
    if not raw.startswith(b"\xff\xd8"):
        raise ValueError("invalid JPEG header")
    position = 2
    while position < len(raw):
        if raw[position] != 255:
            raise ValueError("invalid JPEG marker")
        while position < len(raw) and raw[position] == 255:
            position += 1
        if position >= len(raw):
            break
        marker = raw[position]
        position += 1
        if marker in {0xD8, 0xD9, 0xDA}:
            break
        if marker == 1 or 0xD0 <= marker <= 0xD7:
            continue
        length = int.from_bytes(raw[position : position + 2], "big")
        if length < 2 or position + length > len(raw):
            raise ValueError("invalid JPEG segment")
        if marker in {0xC0, 0xC1, 0xC2}:
            if length < 8 or raw[position + 2] != 8:
                raise ValueError("unsupported JPEG precision")
            height = int.from_bytes(raw[position + 3 : position + 5], "big")
            width = int.from_bytes(raw[position + 5 : position + 7], "big")
            return width, height
        position += length
    raise ValueError("JPEG geometry missing")


def verify_trial(trial: VideoTrial, root: Path) -> set[str]:
    collections = set()
    seen_spans = set()

    def lineage(records):
        if trial.status != "ok":
            return
        ids = {e.run_id for e in records}
        if len(ids) != 1 or ids.intersection(collections):
            raise ValueError("missing or reused collection identity")
        collections.update(ids)
        if any(
            (e.schema_version, e.catalog_version)
            != (trial.observation_schema, trial.catalog_version)
            for e in records
        ):
            raise ValueError("profile observation versions mismatch")
        for event in records:
            if event.span_id in seen_spans:
                raise ValueError("duplicate observation/span identity")
            seen_spans.add(event.span_id)

    for period in trial.periods:
        if period.trace is None:
            continue
        records = verify_trace(root, period.trace, period.metrics)
        lineage(records)
        if trial.status == "ok" and (
            len({e.run_id for e in records}) != 1
            or len({e.request_id for e in records}) != 1
            or len({e.source_id for e in records}) != 1
            or len({(e.schema_version, e.catalog_version) for e in records}) != 1
            or any(
                e.device != "cpu"
                for e in records
                if e.name.startswith("processing.native.")
            )
        ):
            raise ValueError("decode trace lineage/device mismatch")
        if trial.status == "ok" and any(
            e.value is None or e.value > 4
            for e in records
            if e.name
            in {
                "processing.native.produced_frames",
                "processing.native.delivered_frames",
            }
        ):
            raise ValueError("native counter exceeds admitted batch size")
    if trial.workflow is not None:
        records = verify_trace(root, trial.workflow.trace, trial.workflow.metrics)
        lineage(records)
        if trial.status == "ok" and (
            len({e.run_id for e in records}) != 1
            or any(e.status != "ok" for e in records)
        ):
            raise ValueError("offline workflow trace unsuccessful")
        if trial.status == "ok":
            assert trial.media is not None
            expected = selected_chunks(trial.media)
            prefix = (
                "processing.vlm."
                if trial.catalog_version == "1.1"
                else "ingestion.vlm."
            )
            roles = {
                prefix + name
                for name in ("prepare", "http", "input_tokens", "output_tokens")
            }
            events = [e for e in records if e.name in roles]
            if {e.chunk_idx for e in events} != set(expected) or len(
                {e.source_id for e in records}
            ) != 1:
                raise ValueError("workflow chunk/source lineage missing")
            for index in expected:
                chunk = [e for e in events if e.chunk_idx == index]
                if (
                    any(sum(e.name == name for e in chunk) != 1 for name in roles)
                    or len({e.parent_id for e in chunk}) != 1
                    or len({e.model for e in chunk}) != 1
                    or any(
                        e.model == "unknown" or e.backend == "unknown" for e in chunk
                    )
                ):
                    raise ValueError(
                        "workflow chunk stage/model/backend lineage mismatch"
                    )
    bundle_bytes(root)
    return collections


async def worker(
    config: VideoConfiguration, path: Path | None, artifacts: Path, order: str
) -> VideoTrial:
    root = Path(__file__).resolve().parents[1]
    data: dict[str, Any] = dict(
        process_id=os.getpid(),
        order=order,
        schedule_seed=config.seed,
        warmups=config.warmups,
        environment=environment(),
        native_builds={},
        software_sha256=software_hash(root),
        lockfile_sha256=file_hash(root / "uv.lock"),
        import_origin=import_mode(root),
        periods=(),
    )
    try:
        tools, builds = preflight()
        data["native_builds"] = builds
    except Exception:
        return VideoTrial(status="unsupported", failure="prerequisite", **data)
    with tempfile.TemporaryDirectory(prefix="m8-video-") as temporary:
        try:
            source = (
                path
                if path is not None
                else generated(Path(temporary), config.recipe, tools["ffmpeg"])
            )
            media, oracle = inspect(source, tools)
            data["media"] = media
        except MediaError as error:
            return VideoTrial(status="error", failure=error.reason, **data)
        except Exception:
            return VideoTrial(status="error", failure="input_or_oracle", **data)
        try:
            # Warm both modes before timing; warmup traces are bounded temporary
            # evidence and are removed rather than mixed into retained trials.
            with tempfile.TemporaryDirectory(prefix="m8-warmup-") as warm:
                for _ in range(config.warmups):
                    for enabled in (False, True):
                        period = await decode_period(
                            source, media, oracle, enabled=enabled, artifacts=Path(warm)
                        )
                        if period.status != "ok":
                            diagnostic = WarmupDiagnostic.model_validate(
                                period.model_dump(
                                    include=set(WarmupDiagnostic.model_fields)
                                )
                            )
                            return VideoTrial(
                                status="error",
                                failure="warmup",
                                warmup_diagnostic=diagnostic,
                                **data,
                            )
        except Exception:
            return VideoTrial(status="error", failure="warmup", **data)
        periods = []
        failure = None
        with ResourceSampler(config.sample_interval_s) as sampler:
            for enabled in (
                (False, True) if order == "disabled_first" else (True, False)
            ):
                period = await decode_period(
                    source, media, oracle, enabled=enabled, artifacts=artifacts
                )
                periods.append(period)
                # Preserve both measured arms even when one fails; a failed pair
                # is excluded as a whole from statistics, never survivor-filtered.
                if period.status != "ok":
                    failure = "decode_pair"
        data.update(periods=tuple(periods), resources=sampler.trace)
        try:
            if config.offline_e2e:
                data["workflow"] = await offline_workflow(
                    source, media, config, artifacts
                )
                if data["workflow"].status != "ok":
                    failure = "offline_workflow"
            if file_hash(source) != media.sha256:
                failure = "input_changed"
            trial = VideoTrial(
                status="error" if failure else "ok", failure=failure, **data
            )
            verify_trial(trial, artifacts)
            return trial
        except Exception:
            return VideoTrial(status="error", failure="evidence", **data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sources = parser.add_mutually_exclusive_group(required=True)
    sources.add_argument("--video", type=Path)
    sources.add_argument("--recipe", choices=["tiny", "720p", "1080p", "timeline30m"])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--warmups", type=int, default=2)
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--no-e2e", action="store_true")
    parser.add_argument("--mock-delay-ms", type=int, default=0)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--artifacts", type=Path, help=argparse.SUPPRESS)
    parser.add_argument(
        "--order", choices=["disabled_first", "enabled_first"], help=argparse.SUPPRESS
    )
    args = parser.parse_args()
    try:
        config = VideoConfiguration(
            recipe=args.recipe or "local",
            repetitions=args.repetitions,
            warmups=args.warmups,
            seed=args.seed,
            timeout_s=args.timeout,
            offline_e2e=not args.no_e2e,
            mock_delay_ms=args.mock_delay_ms,
        )
    except ValidationError:
        parser.error("invalid admission or repetition limits")
    if args.worker:
        if args.artifacts is None or args.order is None:
            parser.error("worker protocol needs artifact root and order")
        print(
            asyncio.run(
                worker(config, args.video, args.artifacts, args.order)
            ).model_dump_json()
        )
        return 0
    if args.output is None:
        parser.error("--output is required")
    output = args.output.resolve()
    final_artifacts = output.with_suffix(".artifacts")
    if output.exists() or final_artifacts.exists():
        parser.error("outputs are immutable; use a new output name")
    output.parent.mkdir(parents=True, exist_ok=True)
    trials = []
    collection_ids: set[str] = set()
    failure = "platform" if os.name != "posix" else None
    with tempfile.TemporaryDirectory(
        prefix=".m8-staging-", dir=output.parent
    ) as staging:
        staging_root = Path(staging) / "bundle"
        staging_root.mkdir()
        if failure is None:
            for order in balanced_orders(config.repetitions, config.seed):
                command = [
                    sys.executable,
                    "-m",
                    "benchmarks.video",
                    "--worker",
                    "--artifacts",
                    str(staging_root),
                    "--order",
                    order,
                    "--warmups",
                    str(config.warmups),
                    "--seed",
                    str(config.seed),
                    "--mock-delay-ms",
                    str(config.mock_delay_ms),
                ]
                command += (
                    ["--video", str(args.video.resolve())]
                    if args.video
                    else ["--recipe", config.recipe]
                )
                if not config.offline_e2e:
                    command.append("--no-e2e")
                try:
                    raw = isolated_trial(
                        command,
                        cwd=Path(__file__).resolve().parents[1],
                        timeout=config.timeout_s,
                    )
                    trial = VideoTrial.model_validate_json(raw)
                    ids = verify_trial(trial, staging_root)
                    if ids & collection_ids:
                        raise ValueError("reused worker collection identity")
                    collection_ids.update(ids)
                    trials.append(trial)
                    if trial.order != order:
                        failure = "order_mismatch"
                    elif trial.status != "ok":
                        failure = "worker_failure"
                    elif (
                        identity(trial) != identity(trials[0])
                        or trial.software_sha256 != trials[0].software_sha256
                    ):
                        failure = "identity_mismatch"
                    if failure:
                        break
                except subprocess.TimeoutExpired:
                    failure = "timeout"
                    break
                except TrialCleanupError:
                    failure = "cleanup_failure"
                    break
                except KeyboardInterrupt:
                    failure = "cancelled"
                    break
                except (OSError, ValueError, subprocess.CalledProcessError):
                    failure = "invalid_child_evidence"
                    break
        result = VideoResult(
            status="unsupported"
            if failure == "platform" or (trials and trials[-1].status == "unsupported")
            else "incomplete"
            if failure
            else "ok",
            configuration=config,
            trials=tuple(trials),
            failure=failure,
        )
        refs = [
            ref
            for trial in trials
            for ref in (
                [p.trace for p in trial.periods if p.trace]
                + ([trial.workflow.trace] if trial.workflow else [])
            )
        ]
        verify_inventory(staging_root, refs, complete=result.status == "ok")
        bundle_bytes(staging_root)
        publish_bundle(output, staging_root, result)
    print(
        json.dumps(
            {
                "status": result.status,
                "trials": len(trials),
                "failure": failure,
                "acceptance_eligible": False,
            }
        )
    )
    return 0 if result.status == "ok" else 3


if __name__ == "__main__":
    raise SystemExit(main())

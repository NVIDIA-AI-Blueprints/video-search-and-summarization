#!/usr/bin/env python3
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################
"""Stage and run a fresh, evidence-producing RTVI GPU canary."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import selectors
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import container_guard
import perf_plan

REQUIRED = {
    "run_id",
    "host",
    "expected_hostname",
    "repo",
    "repo_commit",
    "benchmark_python",
    "config",
    "scenario",
    "service_image",
    "service_image_id",
    "mediamtx_image",
    "mediamtx_image_id",
    "ffmpeg_image",
    "ffmpeg_image_id",
    "compose_env",
    "model_cache",
    "video",
    "video_sha256",
    "output_root",
    "public_host",
    "gpu_index",
    "gpu_uuid",
    "ports",
    "timeouts",
    "plan",
}
IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SHA = re.compile(r"^[0-9a-f]{40,64}$")
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
SSH_HOST = re.compile(r"^(?:[A-Za-z0-9_][A-Za-z0-9_.-]*@)?[A-Za-z0-9][A-Za-z0-9.-]*$")
RUN_LABEL = "com.nvidia.rtvi.harness.run_id"
FATAL = re.compile(r"EngineDeadError|CUDA out of memory|FMHA kernels are not found")
SEMANTIC_COLORS = ("red", "blue", "green", "yellow", "orange", "pink", "white", "black")
HTTP_TIMEOUT = 30
SEMANTIC_DELETE_TIMEOUT = 90
SEMANTIC_DRAIN_TIMEOUT = 60
WATCHER_BASE_GRACE = 180


def _nonempty(manifest: dict[str, Any], fields: set[str]) -> None:
    missing = sorted(key for key in fields if manifest.get(key) in (None, ""))
    if missing:
        raise ValueError("manifest missing required fields: " + ", ".join(missing))


def resolve_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    if manifest.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    _nonempty(manifest, REQUIRED)
    if not RUN_ID.fullmatch(str(manifest["run_id"])):
        raise ValueError("run_id must be 1-63 safe characters")
    if not SSH_HOST.fullmatch(str(manifest["host"])):
        raise ValueError(
            "host must be a safe SSH user and hostname without options or a port"
        )
    if not SHA.fullmatch(str(manifest["repo_commit"])):
        raise ValueError("repo_commit must be a full immutable commit")
    for field in ("service_image_id", "mediamtx_image_id", "ffmpeg_image_id"):
        if not IMAGE_ID.fullmatch(str(manifest[field])):
            raise ValueError(f"{field} must be a sha256 image ID")
    if not re.fullmatch(r"[0-9a-f]{64}", str(manifest["video_sha256"])):
        raise ValueError("video_sha256 must be a sha256 digest")
    ports = manifest["ports"]
    if not isinstance(ports, dict) or set(ports) != {"backend", "rtsp", "dcgm", "node"}:
        raise ValueError("ports must contain backend, rtsp, dcgm, and node")
    if not all(isinstance(port, int) and 0 < port < 65536 for port in ports.values()):
        raise ValueError("ports must be valid integers")
    timeouts = manifest["timeouts"]
    if not isinstance(timeouts, dict) or set(timeouts) != {"ready", "benchmark"}:
        raise ValueError("timeouts must contain ready and benchmark")
    if not all(isinstance(value, int) and value > 0 for value in timeouts.values()):
        raise ValueError("timeouts must be positive integers")

    resolved_plan = perf_plan.resolve_plan(manifest["plan"])
    workload = resolved_plan["workload"]
    scenarios = resolved_plan["resolved_scenarios"]
    stream_count = workload["source_identity_count"]
    if (
        workload["load_unit"] != "independent_live_stream"
        or stream_count not in {1, 2, 4, 8, 16, 32}
        or len(scenarios) != 1
        or scenarios[0].get("concurrency_levels") != [stream_count]
    ):
        raise ValueError(
            "canary executor supports exactly one, two, four, eight, sixteen, or thirty-two streams"
        )
    if scenarios[0]["name"] != manifest["scenario"]:
        raise ValueError("scenario must match the frozen plan")
    environment = resolved_plan["environment"]
    if environment["code_commit"] != manifest["repo_commit"]:
        raise ValueError("plan code_commit must match repo_commit")
    if environment["container_digest"] != manifest["service_image_id"]:
        raise ValueError("plan container_digest must match service_image_id")
    root = str(Path(manifest["output_root"]) / manifest["run_id"])
    expected_paths = {
        "output": str(Path(root) / "output"),
        "scratch": str(Path(root) / "scratch"),
        "mutable_cache": str(Path(root) / "cache"),
    }
    if resolved_plan["paths"] != expected_paths:
        raise ValueError(
            "plan paths must be distinct run-owned output, scratch, and cache paths"
        )
    result = dict(manifest)
    result["plan"] = resolved_plan
    result["project"] = container_guard.project_for_run(manifest["run_id"])
    result["root"] = root
    result["stream_count"] = stream_count
    semantic_isolation = manifest.get("semantic_isolation", False)
    if not isinstance(semantic_isolation, bool):
        raise TypeError("semantic_isolation must be a boolean")
    if semantic_isolation and stream_count not in {2, 4, 8, 16, 32}:
        raise ValueError(
            "semantic isolation requires exactly two, four, eight, sixteen, or thirty-two streams"
        )
    result["semantic_isolation"] = semantic_isolation
    semantic_media = manifest.get("semantic_media", {})
    if not isinstance(semantic_media, dict):
        raise TypeError("semantic_media must be an object")
    if semantic_media and not semantic_isolation:
        raise ValueError("semantic_media requires semantic_isolation")
    if stream_count == 32 and not semantic_media:
        raise ValueError("thirty-two streams require semantic_media")
    if semantic_media:
        if len(semantic_media) != stream_count:
            raise ValueError("semantic_media must contain one entry per stream")
        for label, media in semantic_media.items():
            if (
                not isinstance(label, str)
                or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", label)
                or not isinstance(media, dict)
                or set(media) != {"path", "sha256"}
                or not Path(str(media["path"])).is_absolute()
                or not re.fullmatch(r"[0-9a-f]{64}", str(media["sha256"]))
            ):
                raise ValueError(
                    "semantic_media entries require a safe label, absolute path, and sha256"
                )
    qualification_only = manifest.get("qualification_only", False)
    if not isinstance(qualification_only, bool):
        raise TypeError("qualification_only must be a boolean")
    if qualification_only and not semantic_media:
        raise ValueError("qualification_only requires semantic_media")
    result["semantic_media"] = semantic_media
    result["semantic_sources"] = (
        sorted(semantic_media)
        if semantic_media
        else list(semantic_sources(stream_count))
        if semantic_isolation
        else []
    )
    result["semantic_task"] = "object" if semantic_media else "pattern"
    result["qualification_only"] = qualification_only
    return result


def status_wait_timeout(manifest: dict[str, Any]) -> int:
    """Bound the watcher across readiness, semantic work, benchmark, and cleanup."""
    total = (
        manifest["timeouts"]["ready"]
        + manifest["timeouts"]["benchmark"]
        + WATCHER_BASE_GRACE
    )
    if manifest["semantic_isolation"]:
        total += (
            HTTP_TIMEOUT * (manifest["stream_count"] + 2)
            + 10
            + min(120, manifest["timeouts"]["benchmark"])
            + 2 * SEMANTIC_DELETE_TIMEOUT
            + SEMANTIC_DRAIN_TIMEOUT
        )
    return total


def validate_source_coverage(
    records: list[dict[str, Any]], stream_count: int, repetitions: int
) -> dict[str, Any]:
    if len(records) != repetitions:
        raise ValueError(
            f"expected {repetitions} iteration records, found {len(records)}"
        )
    measurements = []
    for record in records:
        urls = record.get("rtsp_urls") or []
        stats = record.get("per_stream_stats") or {}
        history = record.get("latency_history") or {}
        if (
            record.get("success") is not True
            or record.get("stream_count") != stream_count
            or record.get("actual_streams_started") != stream_count
            or record.get("streams_with_errors") != 0
            or record.get("skipped_rtsp_source_count") != 0
            or record.get("unique_rtsp_url_per_stream") is not True
            or record.get("rtsp_url_source_count") != stream_count
            or record.get("rtsp_url_pool_exhausted") is not False
            or record.get("rtsp_url_reuse_count") != 0
            or len(urls) != stream_count
            or len(set(urls)) != stream_count
            or len(stats) != stream_count
            or set(stats) != set(history)
        ):
            raise ValueError(
                f"iteration {record.get('iteration')} failed independent-source gates"
            )
        counts = [value.get("total_measurements", 0) for value in stats.values()]
        if any(not isinstance(count, int) or count < 1 for count in counts) or any(
            not history[source] for source in stats
        ):
            raise ValueError(
                f"iteration {record.get('iteration')} lacks fresh measurements for every source"
            )
        measurements.append(sum(counts))
    return {
        "stream_count": stream_count,
        "iterations": len(records),
        "measurements_per_iteration": measurements,
        "status": "PASS",
    }


def semantic_colors(stream_count: int) -> tuple[str, ...]:
    if stream_count not in {2, 4, 8}:
        raise ValueError("semantic colors require exactly two, four, or eight streams")
    return SEMANTIC_COLORS[:stream_count]


def semantic_sources(stream_count: int) -> tuple[str, ...]:
    if stream_count == 16:
        return tuple(
            f"{color}-{pattern}"
            for color in SEMANTIC_COLORS
            for pattern in ("solid", "border")
        )
    return semantic_colors(stream_count)


def semantic_video_filter(source: str) -> str:
    parts = source.split("-")
    if len(parts) == 1 and parts[0] in SEMANTIC_COLORS:
        return ""
    if (
        len(parts) != 2
        or parts[0] not in SEMANTIC_COLORS
        or parts[1] not in {"solid", "border"}
    ):
        raise ValueError(f"invalid semantic source: {source}")
    color, pattern = parts
    if pattern == "solid":
        return ""
    marker = "white" if color in {"red", "blue", "green", "black"} else "black"
    return f"drawbox=x=0:y=0:w=iw:h=ih:color={marker}:t=30"


def semantic_publisher_input(
    source: str, media: dict[str, dict[str, str]]
) -> tuple[list[str], list[str]]:
    codec = [
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-g",
        "10",
    ]
    if media:
        return ["-v", f"{media[source]['path']}:/fixture.jpg:ro"], [
            "-loop",
            "1",
            "-framerate",
            "10",
            "-i",
            "/fixture.jpg",
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
            *codec,
        ]
    color = source.split("-", 1)[0]
    args = ["-f", "lavfi", "-i", f"color=c={color}:s=640x360:r=10"]
    video_filter = semantic_video_filter(source)
    if video_filter:
        args.extend(["-vf", video_filter])
    return [], [*args, *codec]


def score_semantic_isolation(
    outputs: dict[str, list[str]],
    samples: int,
    expected: tuple[str, ...] = SEMANTIC_COLORS[:2],
) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("semantic isolation samples must be positive")
    failures = []
    signatures = {
        label: set(re.findall(r"[A-Z0-9]+", label.upper())) for label in expected
    }
    for label in expected:
        captions = outputs.get(label) or []
        if len(captions) < samples:
            failures.append(
                f"{label}: expected {samples} captions, found {len(captions)}"
            )
            continue
        wanted = signatures[label]
        for index, caption in enumerate(captions[:samples], start=1):
            tokens = set(re.findall(r"[A-Z0-9]+", caption.upper()))
            foreign = [
                other
                for other, signature in signatures.items()
                if other != label and signature <= tokens
            ]
            if not wanted <= tokens or foreign:
                failures.append(f"{label}[{index}]={caption!r}")
    if failures:
        raise ValueError("semantic isolation failed: " + "; ".join(failures))
    return {
        "status": "PASS",
        "samples_per_source": samples,
        "expected": {label: label.upper().replace("-", " ") for label in expected},
        "outputs": outputs,
    }


def _json_request(
    method: str, url: str, payload: dict[str, Any] | None = None, timeout: int = 30
) -> Any:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read()
    return json.loads(body) if body else None


def caption_from_event(event: dict[str, Any]) -> str | None:
    chunk_responses = event.get("chunk_responses") or []
    if chunk_responses:
        caption = (chunk_responses[0].get("content") or "").strip()
        return caption or None
    choices = event.get("choices") or []
    if not choices:
        return None
    choice = choices[0]
    caption = (
        (choice.get("message") or {}).get("content")
        or (choice.get("delta") or {}).get("content")
        or ""
    ).strip()
    return caption or None


def _caption_samples(
    base_url: str,
    model: str,
    stream_id: str,
    samples: int,
    expected: tuple[str, ...],
    semantic_task: str,
    timeout: int,
    start_gate: threading.Barrier,
    stop_event: threading.Event,
) -> list[str]:
    if semantic_task == "object":
        instruction = "Identify the single main object"
    else:
        patterned = any("-" in label for label in expected)
        instruction = (
            "Identify the background color and whether it is SOLID or has a contrasting BORDER"
            if patterned
            else "Identify the dominant solid color"
        )
    payload = {
        "id": [stream_id],
        "model": model,
        "stream": True,
        "stream_options": {"include_usage": True},
        "chunk_duration": 3,
        "prompt": (
            instruction
            + ". Reply with exactly one of: "
            + ", ".join(label.upper().replace("-", " ") for label in expected)
            + "."
        ),
        "max_tokens": 8,
        "temperature": 0,
        "response_format": {"type": "text"},
    }
    request = urllib.request.Request(
        f"{base_url}/generate_captions",
        data=json.dumps(payload).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    captions = []
    start_gate.wait(timeout=10)
    deadline = time.monotonic() + timeout
    with urllib.request.urlopen(request, timeout=min(timeout, 30)) as response:
        for raw_line in response:
            if stop_event.is_set():
                break
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"semantic caption deadline exceeded for stream {stream_id}"
                )
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if not value or value == "[DONE]":
                continue
            event = json.loads(value)
            caption = caption_from_event(event)
            if not caption:
                continue
            captions.append(caption)
            if len(captions) >= samples:
                break
    return captions


def build_launch(
    manifest: dict[str, Any], manifest_path: str, bundle: str
) -> dict[str, Any]:
    remote_dir = f"/tmp/{manifest['project']}-executor"
    socket = f"{manifest['project']}-executor"
    session = f"{manifest['project'][:45]}-job"
    bootstrap = f"{manifest['project'][:39]}-bootstrap"
    tmux = f"tmux -L {shlex.quote(socket)}"
    runner_log = f"{remote_dir}/runner.log"
    sources = [
        manifest_path,
        str(Path(bundle) / "canary_executor.py"),
        str(Path(bundle) / "perf_plan.py"),
        str(Path(bundle) / "container_guard.py"),
    ]
    remote_manifest = f"{remote_dir}/{Path(manifest_path).name}"
    runner = f"{remote_dir}/canary_executor.py"
    run = " ".join(
        shlex.quote(part)
        for part in ["python3", runner, "remote", remote_manifest, "--execute"]
    )
    wrapped = f"{run} >{shlex.quote(runner_log)} 2>&1"
    start_remote = (
        f"{tmux} has-session -t {shlex.quote(session)} 2>/dev/null && "
        "{ echo 'active executor session already exists' >&2; exit 3; }; "
        f"{tmux} new-session -d -s {shlex.quote(bootstrap)} 'sleep 86400'; "
        f"{tmux} set-option -g default-shell /bin/bash; "
        f"{tmux} new-session -d -s {shlex.quote(session)} {shlex.quote(wrapped)}; "
        f"{tmux} kill-session -t {shlex.quote(bootstrap)}"
    )
    wait_remote = (
        f"status={shlex.quote(manifest['root'] + '/status.json')}; "
        "while :; do "
        'if test -f "$status" && grep -Eq \'"result": "(PASS|FAIL)"\' "$status" '
        '&& grep -Eq \'"state": "(completed|failed)"\' "$status"; '
        "then python3 -c 'import json,sys; print(json.dumps(json.load(open(sys.argv[1]))))' "
        '"$status"; exit 0; fi; '
        f"if ! {tmux} has-session -t {shlex.quote(session)} 2>/dev/null; "
        f"then cat {shlex.quote(runner_log)} >&2; exit 2; fi; "
        "sleep 5; done"
    )
    return {
        "remote_dir": remote_dir,
        "stage_argv": [
            ["ssh", manifest["host"], f"install -d -m 700 {shlex.quote(remote_dir)}"],
            ["scp", *sources, f"{manifest['host']}:{remote_dir}/"],
        ],
        "start_argv": [
            "ssh",
            manifest["host"],
            "bash",
            "--noprofile",
            "--norc",
            "-lc",
            shlex.quote(start_remote),
        ],
        "wait_argv": [
            "ssh",
            manifest["host"],
            "bash",
            "--noprofile",
            "--norc",
            "-lc",
            shlex.quote(wait_remote),
        ],
    }


def status_from_output(output: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    status = None
    for start, character in enumerate(output):
        if character != "{":
            continue
        try:
            candidate, _ = decoder.raw_decode(output[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict):
            status = candidate
    if status is None:
        raise ValueError("remote launcher returned no JSON status")
    return status


def wait_for_status(argv: list[str], timeout: int) -> dict[str, Any]:
    process = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    if process.stdout is None:
        raise RuntimeError("remote status process has no output stream")
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    remaining = float(timeout)
    output = ""
    try:
        while remaining > 0:
            wait = min(5, remaining)
            before_wait = time.monotonic()
            for key, _ in selector.select(timeout=wait):
                line = key.fileobj.readline()
                if line:
                    output += line
                    try:
                        status = status_from_output(output)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if status.get("result") in {"PASS", "FAIL"} and status.get(
                        "state"
                    ) in {
                        "completed",
                        "failed",
                    }:
                        return status
            remaining -= min(wait, max(0, time.monotonic() - before_wait))
            if process.poll() is not None:
                output += process.stdout.read()
                raise subprocess.CalledProcessError(
                    process.returncode, argv, output=output
                )
        raise RuntimeError("remote status wait timed out")
    finally:
        selector.close()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def _run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, check=True, text=True, **kwargs)


class RemoteRun:
    def __init__(self, manifest: dict[str, Any]) -> None:
        self.m = manifest
        self.root = Path(manifest["root"])
        self.logs = self.root / "logs"
        self.evidence = self.root / "evidence"
        self.output = self.root / "output"
        self.project = manifest["project"]
        self.deploy = Path(manifest["repo"]) / "docker/rtvi_vlm/deploy"
        self.media_name = f"{self.project}-mediamtx"
        self.publisher_names = [
            f"{self.project}-publisher-{index}"
            for index in range(1, manifest["stream_count"] + 1)
        ]
        self.dmon: subprocess.Popen[str] | None = None
        self.result = "RUNNING"
        self._cleaned = False

    def event(self, phase: str, state: str, message: str) -> None:
        record = {
            "schema_version": 1,
            "run_id": self.m["run_id"],
            "at": datetime.now(timezone.utc).isoformat(),
            "phase": phase,
            "state": state,
            "message": message,
        }
        with (self.root / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
        tmp = self.root / ".status.tmp"
        tmp.write_text(json.dumps({**record, "result": self.result}, indent=2) + "\n")
        os.replace(tmp, self.root / "status.json")

    def command(
        self,
        *argv: str,
        capture: Path | None = None,
        cwd: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        with (self.logs / "commands.log").open("a", encoding="utf-8") as stream:
            stream.write(shlex.join(argv) + "\n")
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            check=check,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if capture:
            capture.write_text(result.stdout)
        return result

    def compose(
        self, *argv: str, capture: Path | None = None, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        return self.command(
            "docker",
            "compose",
            "-p",
            self.project,
            "-f",
            "compose.perf.yaml",
            "-f",
            str(self.root / "compose.override.yaml"),
            "--env-file",
            str(self.root / "compose.env"),
            *argv,
            capture=capture,
            cwd=self.deploy,
            check=check,
        )

    def prepare(self) -> None:
        self.root.mkdir(parents=True, exist_ok=False)
        for path in (
            self.logs,
            self.evidence,
            self.output,
            self.root / "scratch",
            self.root / "cache",
        ):
            path.mkdir()
        frozen = {
            key: value for key, value in self.m.items() if not key.startswith("_")
        }
        (self.root / "manifest.json").write_text(
            json.dumps(frozen, indent=2, sort_keys=True) + "\n"
        )
        self.event("preflight", "running", "validating immutable inputs")

    def preflight(self) -> None:
        if os.uname().nodename.split(".")[0] != self.m["expected_hostname"]:
            raise RuntimeError("remote hostname does not match manifest")
        repo = Path(self.m["repo"])
        if self.command("git", "-C", str(repo), "status", "--short").stdout.strip():
            raise RuntimeError("remote repository is dirty")
        head = self.command("git", "-C", str(repo), "rev-parse", "HEAD").stdout.strip()
        if head != self.m["repo_commit"]:
            raise RuntimeError("remote repository commit does not match manifest")
        for image, expected in (
            (self.m["service_image"], self.m["service_image_id"]),
            (self.m["mediamtx_image"], self.m["mediamtx_image_id"]),
            (self.m["ffmpeg_image"], self.m["ffmpeg_image_id"]),
        ):
            actual = self.command(
                "docker", "image", "inspect", image, "--format", "{{.Id}}"
            ).stdout.strip()
            if actual != expected:
                raise RuntimeError(f"image identity mismatch: {image}")
        digest_builder = hashlib.sha256()
        with Path(self.m["video"]).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest_builder.update(block)
        digest = digest_builder.hexdigest()
        if digest != self.m["video_sha256"]:
            raise RuntimeError("video checksum mismatch")
        for label, media in self.m["semantic_media"].items():
            digest = hashlib.sha256(Path(media["path"]).read_bytes()).hexdigest()
            if digest != media["sha256"]:
                raise RuntimeError(f"semantic media checksum mismatch: {label}")
        gpu = self.command(
            "nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader,nounits"
        ).stdout
        identities = {
            (parts[0].strip(), parts[1].strip())
            for line in gpu.splitlines()
            if len(parts := line.split(",", 1)) == 2
        }
        if (str(self.m["gpu_index"]), self.m["gpu_uuid"]) not in identities:
            raise RuntimeError("GPU identity mismatch")
        active = self.command(
            "nvidia-smi",
            "--query-compute-apps=pid",
            "--format=csv,noheader",
            check=False,
        ).stdout.strip()
        if active:
            raise RuntimeError("GPU already has a compute workload")
        listeners = self.command("ss", "-ltnH").stdout
        for port in self.m["ports"].values():
            if re.search(rf":{port}\s", listeners):
                raise RuntimeError(f"required port occupied: {port}")
        self.command(self.m["benchmark_python"], "-c", "import pandas, requests, yaml")
        scenario_log = self.command(
            self.m["benchmark_python"],
            str(repo / "perf/benchmark/rtvi_perf_benchmark.py"),
            "--config",
            str(repo / self.m["config"]),
            "--list-scenarios",
            capture=self.evidence / "scenarios.log",
        ).stdout
        if self.m["scenario"] not in scenario_log:
            raise RuntimeError("scenario is absent from benchmark config")
        shutil.copy2(self.m["compose_env"], self.root / "compose.env")
        self._write_configs()
        self.command(
            "nvidia-smi", "-q", capture=self.evidence / "preflight-nvidia-smi.txt"
        )
        self.event("preflight", "passed", "immutable inputs and idle resource verified")

    def _write_configs(self) -> None:
        script = r"""
import json, sys, yaml
from pathlib import Path
m = json.loads(Path(sys.argv[1]).read_text())
root, repo = Path(sys.argv[2]), Path(m["repo"])
data = yaml.safe_load((repo / m["config"]).read_text())
scenario = data["test_scenarios"][m["scenario"]]
paths = ([f"semantic-{source}" for source in m["semantic_sources"]] if m.get("semantic_isolation")
         else [f"bcd-{i}" for i in range(1, m["stream_count"] + 1)])
urls = [f"rtsp://{m['public_host']}:{m['ports']['rtsp']}/{path}" for path in paths]
scenario["videos"][0]["rtsp_url"] = urls[0]
scenario["videos"][0]["rtsp_urls"] = urls
data["test_scenarios"] = {m["scenario"]: scenario}
data["global"]["rtvi_backend"] = f"http://localhost:{m['ports']['backend']}/v1"
data["global"]["output_dir"] = str(root / "output")
prom = data["global"]["gpu_monitoring"]["prometheus"]
prom["dcgm_exporter_url"] = f"http://localhost:{m['ports']['dcgm']}/metrics"
prom["node_exporter_url"] = f"http://localhost:{m['ports']['node']}/metrics"
(root / "config.yaml").write_text(yaml.safe_dump(data, sort_keys=False))
override = {"services": {"rtvi-server": {"volumes": [
  {"type": "bind", "source": m["model_cache"],
   "target": "/opt/nvidia/rtvi/.rtvi/ngc_model_cache", "read_only": True},
  {"type": "bind", "source": str(Path(m["video"]).parent),
   "target": "/opt/nvidia/rtvi/streams/perf", "read_only": True},
  {"type": "bind", "source": str(root / "cache"), "target": "/tmp/huggingface"},
]}}}
(root / "compose.override.yaml").write_text(yaml.safe_dump(override, sort_keys=False))
"""
        self.command(
            self.m["benchmark_python"],
            "-c",
            script,
            str(self.root / "manifest.json"),
            str(self.root),
        )

    def launch(self) -> None:
        label = f"{RUN_LABEL}={self.m['run_id']}"
        self.command(
            "docker",
            "run",
            "-d",
            "--name",
            self.media_name,
            "--label",
            label,
            "--network",
            "host",
            self.m["mediamtx_image"],
            capture=self.evidence / "mediamtx.container",
        )
        time.sleep(2)
        for index, name in enumerate(self.publisher_names, start=1):
            if self.m["semantic_isolation"]:
                source = self.m["semantic_sources"][index - 1]
                path = f"semantic-{source}"
                volume_args, source_args = semantic_publisher_input(
                    source, self.m["semantic_media"]
                )
            else:
                path = f"bcd-{index}"
                source_args = ["-stream_loop", "-1", "-i", "/input.mp4", "-c", "copy"]
                volume_args = ["-v", f"{self.m['video']}:/input.mp4:ro"]
            self.command(
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                label,
                "--network",
                "host",
                *volume_args,
                self.m["ffmpeg_image"],
                "-re",
                *source_args,
                "-f",
                "rtsp",
                f"rtsp://127.0.0.1:{self.m['ports']['rtsp']}/{path}",
                capture=self.evidence / f"publisher-{index}.container",
            )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            media_logs = self.command(
                "docker", "logs", self.media_name, check=False
            ).stdout
            if all(
                (
                    f"is publishing to path 'semantic-{self.m['semantic_sources'][index - 1]}'"
                    in media_logs
                    if self.m["semantic_isolation"]
                    else f"is publishing to path 'bcd-{index}'" in media_logs
                )
                for index in range(1, self.m["stream_count"] + 1)
            ):
                break
            time.sleep(1)
        else:
            raise RuntimeError("RTSP publisher did not become ready")
        dmon_log = (self.logs / "nvidia-smi-dmon.log").open("w")
        self.dmon = subprocess.Popen(
            [
                "nvidia-smi",
                "dmon",
                "-i",
                str(self.m["gpu_index"]),
                "-d",
                "1",
                "-s",
                "pucvmet",
                "-o",
                "DT",
            ],
            text=True,
            stdout=dmon_log,
            stderr=subprocess.STDOUT,
        )
        self.compose("config", capture=self.evidence / "compose.resolved.yaml")
        self.compose("up", "-d")
        cid = self.compose("ps", "-q", "rtvi-server").stdout.strip()
        if not cid:
            raise RuntimeError("compose did not create rtvi-server")
        inspect = self.command(
            "docker", "inspect", cid, capture=self.evidence / "runtime.inspect.json"
        )
        runtime = json.loads(inspect.stdout)[0]
        if runtime.get("Image") != self.m["service_image_id"]:
            raise RuntimeError("fresh runtime image does not match manifest")
        if ((runtime.get("Config") or {}).get("Labels") or {}).get(
            "com.docker.compose.project"
        ) != self.project:
            raise RuntimeError("fresh runtime ownership label does not match run")
        self.event("runtime", "created", f"fresh container {cid}")
        deadline = time.monotonic() + self.m["timeouts"]["ready"]
        ready_url = f"http://localhost:{self.m['ports']['backend']}/v1/health/ready"
        while time.monotonic() < deadline:
            state = self.command(
                "docker",
                "inspect",
                cid,
                "--format",
                "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
            ).stdout.strip()
            logs = self.command("docker", "logs", cid, check=False).stdout
            if FATAL.search(logs):
                raise RuntimeError("fatal service marker before readiness")
            probe = self.command("curl", "-fsS", ready_url, check=False)
            if state == "running|healthy" and probe.returncode == 0:
                (self.evidence / "ready.json").write_text(probe.stdout)
                break
            if state.startswith(("exited", "dead")):
                raise RuntimeError(f"service stopped before readiness: {state}")
            time.sleep(10)
        else:
            raise RuntimeError("service readiness timeout")

    def semantic_probe(self) -> None:
        if not self.m["semantic_isolation"]:
            return
        self.event(
            "fidelity", "running", "cross-stream semantic isolation probe started"
        )
        base_url = f"http://localhost:{self.m['ports']['backend']}/v1"
        models = _json_request("GET", f"{base_url}/models")
        model = models["data"][0]["id"]
        stream_ids: dict[str, str] = {}
        outputs: dict[str, list[str]] = {}
        samples = 3
        sources = tuple(self.m["semantic_sources"])
        try:
            for source in sources:
                body = _json_request(
                    "POST",
                    f"{base_url}/streams/add",
                    {
                        "streams": [
                            {
                                "liveStreamUrl": (
                                    f"rtsp://{self.m['public_host']}:"
                                    f"{self.m['ports']['rtsp']}/semantic-{source}"
                                ),
                                "description": f"semantic-{source}",
                            }
                        ]
                    },
                )
                if body.get("errors") or len(body.get("results") or []) != 1:
                    raise RuntimeError(
                        f"failed to add semantic {source} stream: {body}"
                    )
                stream_ids[source] = body["results"][0]["id"]
            if len(set(stream_ids.values())) != len(sources):
                raise RuntimeError(
                    "semantic sources did not receive distinct stream IDs"
                )
            start_gate = threading.Barrier(len(sources))
            stop_event = threading.Event()
            executor = ThreadPoolExecutor(max_workers=len(sources))
            try:
                futures = {
                    source: executor.submit(
                        _caption_samples,
                        base_url,
                        model,
                        stream_id,
                        samples,
                        sources,
                        self.m["semantic_task"],
                        min(120, self.m["timeouts"]["benchmark"]),
                        start_gate,
                        stop_event,
                    )
                    for source, stream_id in stream_ids.items()
                }
                outputs = {
                    source: future.result() for source, future in futures.items()
                }
            finally:
                stop_event.set()
                executor.shutdown(wait=False, cancel_futures=True)
            result = score_semantic_isolation(outputs, samples, sources)
            result.update({"model": model, "stream_ids": stream_ids})
            (self.evidence / "semantic-isolation.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n"
            )
            self.event("fidelity", "passed", "all outputs remained source-correct")
        except Exception as error:
            (self.evidence / "semantic-isolation.json").write_text(
                json.dumps(
                    {
                        "status": "FAIL",
                        "error": str(error),
                        "stream_ids": stream_ids,
                        "outputs": outputs,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            raise
        finally:
            if stream_ids:
                try:
                    _json_request(
                        "DELETE",
                        f"{base_url}/streams/delete-batch",
                        {
                            "stream_ids": list(stream_ids.values()),
                            "blocking": True,
                            "drain_timeout_seconds": 60,
                        },
                        timeout=SEMANTIC_DELETE_TIMEOUT,
                    )
                except urllib.error.HTTPError as error:
                    if error.code != 422:
                        raise
                    _json_request(
                        "DELETE",
                        f"{base_url}/streams/delete-batch",
                        {"stream_ids": list(stream_ids.values())},
                        timeout=SEMANTIC_DELETE_TIMEOUT,
                    )
                deadline = time.monotonic() + SEMANTIC_DRAIN_TIMEOUT
                while time.monotonic() < deadline:
                    active = _json_request("GET", f"{base_url}/streams/get-stream-info")
                    if not set(stream_ids.values()) & {
                        str(item.get("id")) for item in active or []
                    }:
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError(
                        "semantic streams did not drain before performance benchmark"
                    )

    def benchmark(self) -> None:
        self.event(
            "canary", "running", f"{self.m['stream_count']}-stream benchmark started"
        )
        argv = [
            "timeout",
            str(self.m["timeouts"]["benchmark"]),
            self.m["benchmark_python"],
            str(Path(self.m["repo"]) / "perf/benchmark/rtvi_perf_benchmark.py"),
            "--config",
            str(self.root / "config.yaml"),
            "--output-json",
            str(self.output / "result.json"),
            "--scenario",
            self.m["scenario"],
            "--concurrency-levels",
            str(self.m["stream_count"]),
        ]
        result = self.command(*argv, check=False)
        (self.logs / "benchmark.log").write_text(result.stdout)
        if result.returncode:
            raise RuntimeError(f"benchmark exited {result.returncode}")
        data = json.loads((self.output / "result.json").read_text())
        summary = data["summary"]
        if (
            summary.get("overall_status") != "PASS"
            or summary.get("failed") != 0
            or not data.get("test_cases")
        ):
            raise RuntimeError(f"benchmark result failed: {summary}")
        (self.evidence / "result-summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True)
        )
        records = [
            json.loads(path.read_text())
            for path in sorted(
                self.output.rglob("concurrent_live_streams_results.json")
            )
        ]
        coverage = validate_source_coverage(
            records,
            self.m["stream_count"],
            self.m["plan"]["measurement"]["repetitions"],
        )
        (self.evidence / "source-coverage.json").write_text(
            json.dumps(coverage, indent=2, sort_keys=True) + "\n"
        )
        service_log = self.command(
            "docker", "logs", self.compose("ps", "-q", "rtvi-server").stdout.strip()
        ).stdout
        (self.logs / "service-complete.log").write_text(service_log)
        if FATAL.search(service_log):
            raise RuntimeError("fatal service marker during canary")
        self.result = "PASS"
        self.event(
            "canary", "passed", "benchmark, source coverage, and service logs passed"
        )

    def cleanup(self) -> None:
        if self._cleaned or not self.root.exists():
            return
        self._cleaned = True
        self.event("cleanup", "running", "stopping owned writers and containers")
        if self.dmon and self.dmon.poll() is None:
            self.dmon.send_signal(signal.SIGTERM)
            try:
                self.dmon.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.dmon.kill()
        self.compose(
            "logs", "--no-color", capture=self.logs / "compose.log", check=False
        )
        self.compose(
            "down",
            "-v",
            "--remove-orphans",
            capture=self.logs / "cleanup.log",
            check=False,
        )
        expected_aux = [*self.publisher_names, self.media_name]
        try:
            owned_aux = container_guard.find_owned_containers(
                self.m["run_id"], self.project, expected_aux
            )
        except (
            ValueError,
            json.JSONDecodeError,
            OSError,
            subprocess.CalledProcessError,
        ) as error:
            owned_aux = []
            self.result = "FAIL"
            (self.logs / "aux-cleanup.log").write_text(
                f"refused unsafe auxiliary cleanup: {error}\n"
            )
        owned_by_name = {
            str(record.get("Name", "")).removeprefix("/"): str(record["Id"])
            for record in owned_aux
        }
        for index, name in enumerate(expected_aux, start=1):
            if container_id := owned_by_name.get(name):
                log_name = (
                    "mediamtx.log"
                    if name == self.media_name
                    else f"publisher-{index}.log"
                )
                self.command(
                    "docker",
                    "logs",
                    container_id,
                    check=False,
                    capture=self.logs / log_name,
                )
        owned_ids = sorted(set(owned_by_name.values()))
        if owned_ids:
            self.command(
                "docker",
                "rm",
                "-f",
                *owned_ids,
                check=False,
                capture=self.logs / "aux-cleanup.log",
            )
        post_run = self.command(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label={RUN_LABEL}={self.m['run_id']}",
            "--format",
            "{{.ID}}|{{.Names}}|{{.Status}}",
            check=False,
        ).stdout
        post_compose = self.command(
            "docker",
            "ps",
            "-a",
            "--filter",
            f"label=com.docker.compose.project={self.project}",
            "--format",
            "{{.ID}}|{{.Names}}|{{.Status}}",
            check=False,
        ).stdout
        post = post_run + post_compose
        (self.evidence / "post-cleanup-containers.txt").write_text(post)
        gpu = self.command(
            "nvidia-smi",
            "--query-compute-apps=gpu_uuid,pid,process_name,used_memory",
            "--format=csv,noheader",
            check=False,
        ).stdout
        (self.evidence / "post-cleanup-gpu.csv").write_text(gpu)
        stable = [
            path
            for path in self.root.rglob("*")
            if path.is_file()
            and path.name not in {"status.json", "events.jsonl", "checksums.sha256"}
            and path != self.logs / "runner.log"
        ]
        with (self.root / "checksums.sha256").open("w") as stream:
            for path in sorted(stable):
                stream.write(
                    f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(self.root)}\n"
                )
        state = "completed" if self.result == "PASS" and not post.strip() else "failed"
        if state == "failed":
            self.result = "FAIL"
        self.event(
            "cleanup", state, "owned resources removed and stable artifacts hashed"
        )

    def execute(self) -> None:
        self.prepare()
        try:
            self.preflight()
            self.launch()
            self.semantic_probe()
            if self.m["qualification_only"]:
                self.result = "PASS"
                self.event("qualification", "passed", "all object fixtures qualified")
            else:
                self.benchmark()
        except Exception as error:
            self.result = "FAIL"
            self.event("run", "failed", str(error) or type(error).__name__)
            raise
        finally:
            self.cleanup()


def _load(path: Path) -> dict[str, Any]:
    data = resolve_manifest(json.loads(path.read_text()))
    data["_manifest_path"] = str(path.resolve())
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("launch", "remote"):
        command = sub.add_parser(name)
        command.add_argument("manifest", type=Path)
        command.add_argument(
            "--execute", action="store_true", help="perform side effects"
        )
    args = parser.parse_args()
    try:
        manifest = _load(args.manifest)
        if args.command == "launch":
            launch = build_launch(
                manifest, str(args.manifest.resolve()), str(Path(__file__).parent)
            )
            if not args.execute:
                print(json.dumps(launch, indent=2, sort_keys=True))
                return 0
            for argv in launch["stage_argv"]:
                _run(argv)
            _run(launch["start_argv"], stdout=subprocess.PIPE)
            wait_timeout = status_wait_timeout(manifest)
            status = wait_for_status(launch["wait_argv"], wait_timeout)
            print(json.dumps(status, indent=2, sort_keys=True))
            return 0 if status.get("result") == "PASS" else 1
        if not args.execute:
            print(
                json.dumps(
                    {
                        "mode": "dry-run",
                        "run_id": manifest["run_id"],
                        "root": manifest["root"],
                    },
                    indent=2,
                )
            )
            return 0
        RemoteRun(manifest).execute()
        return 0
    except (
        KeyError,
        ValueError,
        OSError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
        RuntimeError,
    ) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

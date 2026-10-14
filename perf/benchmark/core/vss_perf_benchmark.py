#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Compose-base GPU benchmark. Deployed operations use the configured VSS CLI."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# The core entry point may be loaded directly by contract tests as well as invoked.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from jsonschema import Draft202012Validator
from resource_monitor import CollectionError, ResourceMonitor

PERF_ROOT = Path(__file__).resolve().parents[2]
# Reuse the repository's shared first-party model; no second schema version is invented.
COMMON_PATH = (
    PERF_ROOT.parent / "services/vios/test/bdd_tests/tests/perf/vss_perf_common.py"
)
SPEC = importlib.util.spec_from_file_location("compose_base_shared_perf", COMMON_PATH)
SHARED = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SHARED
SPEC.loader.exec_module(SHARED)
SCHEMA_PATH = PERF_ROOT / "tests" / "result-schema.json"
FPS_PAIR = re.compile(r"(?<![\w.])([0-9]+\.[0-9]+)\s*\(([0-9]+\.[0-9]+)\)")


class BenchmarkError(RuntimeError):
    """A command or deployment contract failed; never retry with another API."""


def run_command(argv: list[str], *, json_output: bool = True) -> tuple[Any, float]:
    start = time.monotonic()
    try:
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise BenchmarkError(f"{argv[0]} could not start: {exc}") from exc
    duration = time.monotonic() - start
    if completed.returncode:
        # Keep failure evidence bounded and do not record source credentials or URLs.
        raise BenchmarkError(
            f"{' '.join(argv[:3])} exited {completed.returncode}: "
            f"{completed.stderr.strip()[-2000:]}"
        )
    is_docker_logs = any(
        Path(item).name == "docker" and argv[index + 1 : index + 2] == ["logs"]
        for index, item in enumerate(argv)
    )
    output = completed.stdout + (completed.stderr if is_docker_logs else "")
    if not json_output:
        return output, duration
    try:
        return json.loads(output), duration
    except ValueError as exc:
        raise BenchmarkError(f"{' '.join(argv[:3])} returned invalid JSON") from exc


def parse_fps(logs: str) -> list[list[float]]:
    """Read instantaneous FPS, excluding cumulative averages and header rows.

    The app prints either one **PERF line or multiurisrcbin pairs on following
    lines. Source-name details between pairs do not terminate a sample.
    """
    samples: list[list[float]] = []
    current: list[float] | None = None
    for line in logs.splitlines():
        if "**PERF:" in line or re.search(r"PERF\(\d+\):", line):
            if current:
                samples.append(current)
            current = []
            # Header output contains "FPS n (Avg)", never numeric pairs.
            if "FPS " in line:
                current = None
                continue
        elif "Active sources :" in line:
            if current:
                samples.append(current)
            current = None
        if current is not None:
            current.extend(float(match[0]) for match in FPS_PAIR.findall(line))
    if current:
        samples.append(current)
    return samples


def percentile(values: list[float], percent: float) -> float:
    """Linearly interpolated percentile including small sample counts."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent / 100
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def checked_listing(payload: Any, key: str) -> int:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise BenchmarkError(f"CLI listing is missing {key}")
    count = payload.get("count")
    if (
        isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count != len(payload[key])
    ):
        raise BenchmarkError(f"CLI {key} count does not match its rows")
    return count


def check_config(
    config: dict[str, Any],
    matrix: dict[str, Any],
    config_id: str,
    scenario_name: str,
    *,
    require_enabled: bool = True,
) -> tuple[dict[str, Any], dict[str, Any]]:
    # Preserve identifiers verbatim; reject unsafe filenames instead of renaming.
    if not config_id or "/" in config_id or "\\" in config_id or "\x00" in config_id:
        raise BenchmarkError(
            "config-id must be nonempty and contain no path separators"
        )
    matching = [
        entry
        for entry in matrix.get("perf_configs", [])
        if entry.get("id") == config_id
    ]
    if len(matching) != 1:
        raise BenchmarkError(
            f"config-id must match exactly one perf_configs entry: {config_id}"
        )
    entry = matching[0]
    if require_enabled and entry.get("enabled", True) is not True:
        raise BenchmarkError(f"configuration is disabled: {config_id}")
    scenario = config.get("test_scenarios", {}).get(scenario_name)
    if not isinstance(scenario, dict):
        raise BenchmarkError(f"unknown scenario: {scenario_name}")
    for field in ("duration_seconds", "interval_seconds"):
        value = scenario.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise BenchmarkError(f"{field} must be finite and positive")
    if scenario.get("expected_streams", 1) < 1:
        raise BenchmarkError("expected_streams must be positive")
    return entry, scenario


def discover_platform(gpu_ids: str = "0") -> dict[str, Any]:
    gpu: dict[str, Any] = {"model": "Unknown", "count": 0, "topology": "single-gpu"}
    # Hardware discovery is metadata, not a second infrastructure collector.
    try:
        rows, _ = run_command(
            [
                "nvidia-smi",
                "--id=" + gpu_ids,
                "--query-gpu=name,memory.total,driver_version",
                "--format=csv,noheader,nounits",
            ],
            json_output=False,
        )
        parsed = [row.split(",") for row in rows.splitlines() if row.strip()]
        if parsed:
            gpu.update(
                model=parsed[0][0].strip(),
                count=len(parsed),
                memory_gb=round(float(parsed[0][1]) / 1024, 3),
                driver_version=parsed[0][2].strip(),
            )
    except (BenchmarkError, ValueError, IndexError):
        pass
    return {
        "gpu": gpu,
        "cpu": {"model": platform.processor(), "cores": os.cpu_count() or 0},
        "os": platform.platform(),
    }


def case_failure(case_id: str, message: str) -> dict[str, Any]:
    return {"test_case_id": case_id, "success": False, "metrics": {}, "error": message}


def deployment_version_fact(check_output: str) -> dict[str, Any]:
    """Keep the version fact reported by the CLI; config schema version is unrelated.

    Current CLI check probes version on every invocation instead of caching it
    in config.json. A lean profile or an older CLI may report no version.
    """
    fact: dict[str, Any] = {
        "version": None,
        "status": "unavailable",
        "source": "vss configure check",
    }
    for line in check_output.splitlines():
        match = re.match(r"^\s*version\s+(.*?)\s*$", line)
        if not match:
            continue
        value = match[1]
        if value.startswith("not reported"):
            fact.update(
                status="not_reported",
                detail=value.removeprefix("not reported").strip()[:500],
            )
        elif value:
            fact.update(version=value.split()[0], status="reported")
        return fact
    fact["detail"] = "CLI capability report contains no deployment version fact"
    return fact


def measure(
    scenario: dict[str, Any],
    container: str,
    vss: str = "vss",
    monitor_settings: dict[str, Any] | None = None,
    gpu_ids: str = "0",
    docker_command: list[str] | None = None,
    deployment_facts: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    operations = [
        ("vios_stream_listing", ["vios", "list", "--type", "stream"], "sensors"),
        ("analytics_sensor_listing", ["analytics", "sensors"], "sensors"),
        (
            "analytics_incident_query",
            ["analytics", "incidents", "--limit", "100"],
            "incidents",
        ),
    ]
    # configure check exits successfully for a partial deployment; actual operations
    # below enforce exactly the required capabilities and preserve typed failures.
    run_command(
        ["nvidia-smi", "--query-gpu=uuid", "--format=csv,noheader"], json_output=False
    )
    check_output, _ = run_command([vss, "configure", "check"], json_output=False)
    if deployment_facts is not None:
        deployment_facts.update(deployment_version_fact(check_output))
    for _ in range(scenario.get("warmup_iterations", 1)):
        for _, command, key in operations:
            output, _ = run_command([vss, *command])
            checked_listing(output, key)
    monitor = (
        ResourceMonitor(gpu_ids, monitor_settings)
        if monitor_settings is not None
        else None
    )
    if monitor:
        monitor.start()
    started_utc = datetime.now(timezone.utc).isoformat()  # noqa: UP017 - generic Jenkins runners include Python 3.10.
    started = time.monotonic()
    observations: dict[str, list[tuple[float, int]]] = {
        name: [] for name, _, _ in operations
    }
    failures: dict[str, str] = {}
    try:
        # This loop generates the requested load; it never retries a failed command.
        while True:
            for name, command, key in operations:
                if name in failures:
                    continue
                try:
                    output, duration = run_command([vss, *command])
                    count = checked_listing(output, key)
                    observations[name].append((duration, count))
                except BenchmarkError as exc:
                    failures[name] = str(exc)
            elapsed = time.monotonic() - started
            if failures or elapsed >= scenario["duration_seconds"]:
                break
            time.sleep(
                min(
                    scenario["interval_seconds"], scenario["duration_seconds"] - elapsed
                )
            )
    finally:
        if monitor:
            monitor.stop()
    measured_duration = time.monotonic() - started
    thresholds = scenario.get("thresholds", {})
    max_p95 = thresholds.get("max_cli_p95_seconds", 10.0)
    expected = scenario.get("expected_streams", 1)
    cases = []
    for name, _, _ in operations:
        if name in failures:
            cases.append(case_failure(name, failures[name]))
            continue
        rows = observations[name]
        latencies = [row[0] for row in rows]
        counts = [row[1] for row in rows]
        count_min = expected if name == "vios_stream_listing" else 0
        if name == "analytics_sensor_listing":
            count_min = scenario.get("expected_analytics_sensors", 1)
        p95 = percentile(latencies, 95)
        success = min(counts) >= count_min and p95 <= max_p95
        cases.append(
            {
                "test_case_id": name,
                "success": success,
                "metrics": {
                    "latency": {
                        "client_observed_mean_seconds": statistics.mean(latencies),
                        "client_observed_p50_seconds": percentile(latencies, 50),
                        "client_observed_p95_seconds": p95,
                    },
                    "requests": {
                        "completed": len(rows),
                        "success_rate_percent": 100.0,
                        "sequential_requests_per_second": len(rows) / measured_duration,
                    },
                    "inventory": {"minimum_returned_count": min(counts)},
                },
                "thresholds": {
                    "max_cli_p95_seconds": max_p95,
                    "min_returned_count": count_min,
                },
                **(
                    {"error": "workload inventory or client latency threshold not met"}
                    if not success
                    else {}
                ),
            }
        )
    try:
        logs, _ = run_command(
            [
                *(docker_command or ["docker"]),
                "logs",
                "--since",
                started_utc,
                container,
            ],
            json_output=False,
        )
        samples = parse_fps(logs)
        if not samples:
            raise BenchmarkError("no RT-CV FPS samples in the measurement window")
        totals = [sum(sample) for sample in samples]
        active = [sum(fps > 0 for fps in sample) for sample in samples]
        minimum_fps = min(min(sample) for sample in samples)
        threshold = thresholds.get("min_rt_cv_fps", 1.0)
        success = min(active) >= expected and minimum_fps >= threshold
        cases.append(
            {
                "test_case_id": "rt_cv_stream_throughput",
                "success": success,
                "metrics": {
                    "throughput": {
                        "aggregate_fps_mean": statistics.mean(totals),
                        "minimum_stream_fps": minimum_fps,
                        "active_streams_minimum": min(active),
                        "sample_count": len(samples),
                    }
                },
                "thresholds": {
                    "minimum_stream_fps": threshold,
                    "active_streams_minimum": expected,
                },
                **(
                    {"error": "RT-CV throughput threshold not met"}
                    if not success
                    else {}
                ),
            }
        )
    except BenchmarkError as exc:
        cases.append(case_failure("rt_cv_stream_throughput", str(exc)))
    if monitor:
        try:
            cases.append(
                {
                    "test_case_id": "infrastructure_resources",
                    "success": True,
                    "metrics": monitor.result(),
                }
            )
        except CollectionError as exc:
            cases.append(case_failure("infrastructure_resources", str(exc)))
    return cases


def load_runtime_metadata(config_id: str, settings: dict[str, Any]) -> dict[str, Any]:
    """Load measured, sanitized fixture and image provenance from post_deploy."""
    configured = os.environ.get("VSS_PERF_RUNTIME_METADATA") or settings.get(
        "runtime_metadata_file"
    )
    path = (
        Path(configured)
        if configured
        else PERF_ROOT / ".runtime" / config_id / "metadata.json"
    )
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise BenchmarkError(
            "actual fixture/image metadata is unavailable; run post_deploy capture"
        ) from exc
    if not isinstance(data, dict):
        raise BenchmarkError("runtime metadata must be a JSON object")
    fixture, images = data.get("fixture"), data.get("deployed_images")
    if not isinstance(fixture, dict) or not isinstance(images, list) or len(images) < 3:
        raise BenchmarkError(
            "runtime metadata must identify the fixture and deployed profile images"
        )
    if not re.fullmatch(r"[a-fA-F0-9]{64}", str(fixture.get("sha256", ""))):
        raise BenchmarkError(
            "successful benchmark requires a fixture SHA256; external-stream capture is not implemented"
        )
    for key in ("width", "height", "fps", "duration_seconds"):
        value = fixture.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (float, int))
            or not math.isfinite(value)
            or value <= 0
        ):
            raise BenchmarkError(f"fixture metadata requires measured positive {key}")
    sanitized_images = []
    for image in images:
        if not isinstance(image, dict) or not re.fullmatch(
            r"sha256:[a-fA-F0-9]{64}", str(image.get("image_id", ""))
        ):
            raise BenchmarkError(
                "deployed image metadata requires actual Docker image IDs"
            )
        if not image.get("container_id") or not image.get("container_name"):
            raise BenchmarkError("deployed image metadata requires container identity")
        sanitized_images.append(
            {
                key: image[key]
                for key in (
                    "container_id",
                    "container_name",
                    "service",
                    "image_ref",
                    "image_id",
                    "repo_digests",
                )
                if key in image
            }
        )
    sanitized = {
        "fixture": {
            key: fixture[key]
            for key in (
                "kind",
                "source_name",
                "sha256",
                "width",
                "height",
                "fps",
                "duration_seconds",
                "codec",
            )
            if key in fixture
        },
        "deployed_images": sanitized_images,
    }
    for key, identity_key in (
        ("compatibility_helpers", "name"),
        ("model_assets", "file_name"),
    ):
        if key not in data:
            continue
        rows = data[key]
        if not isinstance(rows, list):
            raise BenchmarkError(f"runtime {key} must be a list")
        validated = []
        identities = set()
        for row in rows:
            if not isinstance(row, dict):
                raise BenchmarkError(f"runtime {key} entries must be objects")
            identity = row.get(identity_key)
            if not isinstance(identity, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*", identity
            ):
                raise BenchmarkError(
                    f"runtime {key} identity must be a plain name without paths or commands"
                )
            if identity in identities:
                raise BenchmarkError(f"runtime {key} contains duplicate identities")
            identities.add(identity)
            checksum = row.get("sha256")
            if not isinstance(checksum, str) or not re.fullmatch(
                r"[a-fA-F0-9]{64}", checksum
            ):
                raise BenchmarkError(f"runtime {key} requires actual SHA256 checksums")
            clean = {identity_key: identity, "sha256": checksum}
            if key == "model_assets":
                if row.get("kind") not in ("onnx", "tensorrt_engine"):
                    raise BenchmarkError(
                        "runtime model_assets kind must identify a supported model format"
                    )
                clean["kind"] = row["kind"]
            validated.append(clean)
        sanitized[key] = validated
    promotion_path = path.parent / "promoted-artifact.json"
    if promotion_path.is_file():
        promotion = json.loads(promotion_path.read_text())
        sanitized["promoted_artifact"] = {
            key: promotion[key]
            for key in (
                "artifact_ref",
                "ref",
                "source_version",
                "version",
                "payload_sha256",
                "compose_tree_sha256",
                "resource_version",
                "verified",
                "container_pins",
                "status",
            )
            if key in promotion
        }
    return sanitized


def source_provenance() -> dict[str, Any]:
    """Record source revision plus package digest, including uncommitted work."""
    revision = os.environ.get("VSS_PERF_SOURCE_REVISION", "")
    dirty = None
    try:
        git_revision, _ = run_command(
            ["git", "-C", str(PERF_ROOT.parent), "rev-parse", "HEAD"], json_output=False
        )
        if not revision:
            revision = git_revision.strip()
        changes, _ = run_command(
            [
                "git",
                "-C",
                str(PERF_ROOT.parent),
                "status",
                "--porcelain",
                "--untracked-files=normal",
                "--",
                "perf",
                "libs/vss",
                "services/vios/test/bdd_tests/tests/perf",
            ],
            json_output=False,
        )
        dirty = bool(changes.strip())
    except BenchmarkError:
        pass
    digest = hashlib.sha256()
    paths = []
    for directory, subdirs, files in os.walk(PERF_ROOT):
        subdirs[:] = [
            name
            for name in subdirs
            if not name.startswith(".") and name not in ("__pycache__", "results")
        ]
        paths.extend(
            Path(directory) / name
            for name in files
            if Path(name).suffix
            in (".py", ".sh", ".yaml", ".yml", ".json", ".txt", ".md")
        )
    for path in sorted(paths):
        relative = path.relative_to(PERF_ROOT)
        digest.update(str(relative).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "revision": revision or None,
        "working_tree_dirty": dirty,
        "perf_package_sha256": digest.hexdigest(),
        "revision_source": "environment"
        if os.environ.get("VSS_PERF_SOURCE_REVISION")
        else "git"
        if revision
        else "unavailable",
        "artifact_ref": os.environ.get("ARTIFACT_REF")
        or os.environ.get("IMAGE_TAG", ""),
        "artifact_reference_source": "ARTIFACT_REF"
        if os.environ.get("ARTIFACT_REF")
        else "IMAGE_TAG"
        if os.environ.get("IMAGE_TAG")
        else "unavailable",
        "artifact_provenance": "runner reference supplied; deployment equivalence not verified"
        if (os.environ.get("ARTIFACT_REF") or os.environ.get("IMAGE_TAG"))
        else "local deployment; promoted artifact not supplied",
    }


def build_result(
    args: argparse.Namespace,
    entry: dict[str, Any],
    scenario: dict[str, Any],
    cases: list[dict[str, Any]],
    started: datetime,
    elapsed: float,
) -> dict[str, Any]:
    passed = sum(case["success"] for case in cases)
    total = len(cases)
    return {
        "schema_version": "0.1",
        "metadata": {
            "service": "compose-base",
            "run_info": {
                "run_id": f"compose-base_{args.config_id}_{started:%Y%m%d_%H%M%S}",
                "timestamp": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "duration_seconds": elapsed,
                "triggered_by": args.triggered_by,
                "release": args.release,
                "pipeline_url": getattr(args, "pipeline_url", None)
                or os.environ.get("BUILD_URL", ""),
            },
            "platform": discover_platform(args.rt_cv_gpus),
            "config": {
                "config_id": args.config_id,
                "deployment": entry,
                "scenario": scenario,
                "rt_cv_gpus": args.rt_cv_gpus,
                "compose_file": getattr(args, "compose_file", None)
                or entry.get("composePath", ""),
                "requested_gpu_model": getattr(args, "gpu_model", None) or "",
                "source": source_provenance(),
                "deployment_version": getattr(
                    args,
                    "deployment_version",
                    {
                        "version": None,
                        "status": "not_observed",
                        "source": "vss configure check",
                    },
                ),
                "runtime": getattr(
                    args,
                    "runtime_metadata",
                    {"status": "unavailable for failed or sample run"},
                ),
                "measurement_method": "VSS CLI wall time (includes Python startup); RT-CV instantaneous FPS logs",
            },
            "benchmark_name": args.scenario,
            "benchmark_mode": scenario.get("benchmark_mode", "compose_base"),
        },
        "summary": {
            "total_tests": total,
            "passed": passed,
            "failed": total - passed,
            "pass_rate": round(100 * passed / total, 1) if total else 0.0,
            "overall_status": "PASS"
            if total and passed == total
            else "PARTIAL"
            if passed
            else "FAIL",
        },
        "test_cases": cases,
    }


def validate_result(result: dict[str, Any]) -> None:
    SHARED.VSSBenchmarkResult.model_validate(result)
    Draft202012Validator(json.loads(SCHEMA_PATH.read_text())).validate(result)
    cases, summary = result["test_cases"], result["summary"]
    passed = sum(case["success"] for case in cases)
    if (
        summary["total_tests"] != len(cases)
        or summary["passed"] != passed
        or summary["failed"] != len(cases) - passed
    ):
        raise BenchmarkError("summary does not match test case outcomes")
    expected_status = (
        "PASS" if passed == len(cases) else "PARTIAL" if passed else "FAIL"
    )
    if summary["overall_status"] != expected_status:
        raise BenchmarkError("overall_status does not match test case outcomes")
    rate = round(100 * passed / len(cases), 1)
    if summary["pass_rate"] != rate:
        raise BenchmarkError("pass_rate does not match test case outcomes")


def write_result(result: dict[str, Any], directory: Path) -> Path:
    validate_result(result)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (result["metadata"]["run_info"]["run_id"] + ".json")
    # Do not silently replace another run started in the same UTC second.
    with path.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    return path


def upload_result(path: Path, settings: dict[str, Any] | None = None) -> None:
    from minio import Minio

    settings = settings or {}
    endpoint = os.environ.get("MINIO_ENDPOINT") or settings.get("endpoint")
    key, secret = os.environ.get("MINIO_ACCESS_KEY"), os.environ.get("MINIO_SECRET_KEY")
    if not endpoint or not key or not secret:
        raise BenchmarkError(
            "--upload requires MINIO_ENDPOINT, MINIO_ACCESS_KEY and MINIO_SECRET_KEY"
        )
    client = Minio(
        endpoint,
        access_key=key,
        secret_key=secret,
        secure=os.environ.get(
            "MINIO_SECURE", str(settings.get("secure", False))
        ).lower()
        == "true",
    )
    bucket = os.environ.get("MINIO_BUCKET") or settings.get("bucket", "perf-results")
    client.fput_object(
        bucket, f"compose-base/{path.name}", str(path), content_type="application/json"
    )


def record_failure(
    path: Path, config_id: str, stage: str, message: str
) -> dict[str, Any]:
    """Finalize one existing run atomically, idempotently per lifecycle stage."""
    result = json.loads(path.read_text())
    validate_result(result)
    if result["metadata"]["config"]["config_id"] != config_id:
        raise BenchmarkError("existing result config_id mismatch")
    existing = [case for case in result["test_cases"] if case["test_case_id"] == stage]
    if existing:
        existing[0].update(success=False, error=message)
    else:
        result["test_cases"].append(case_failure(stage, message))
    total = len(result["test_cases"])
    passed = sum(case["success"] for case in result["test_cases"])
    result["summary"].update(
        total_tests=total,
        passed=passed,
        failed=total - passed,
        pass_rate=round(100 * passed / total, 1),
        overall_status="PARTIAL" if passed else "FAIL",
    )
    validate_result(result)
    temp_path = path.with_suffix(".json.tmp")
    with temp_path.open("x") as output:
        json.dump(result, output, indent=2, allow_nan=False)
        output.write("\n")
    temp_path.replace(path)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=PERF_ROOT / "configs/config.yaml"
    )
    parser.add_argument(
        "--perf-config",
        "--perf-configs",
        dest="perf_config",
        type=Path,
        default=PERF_ROOT / "configs/perf-configs.yaml",
    )
    parser.add_argument("--scenario", default="quick_test")
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--output-json", type=Path, default=Path("benchmark_results"))
    parser.add_argument("--rt-cv-gpus", default="0")
    parser.add_argument("--rt-cv-container")
    parser.add_argument("--triggered-by", default="manual")
    parser.add_argument("--release", default="")
    parser.add_argument(
        "--compose-file", help="Deployment recipe selected by the shared runner"
    )
    parser.add_argument(
        "--gpu-model",
        help="Runner hardware label; actual GPU identity is discovered separately",
    )
    parser.add_argument(
        "--pipeline-url", help="Build URL supplied by the shared runner"
    )
    parser.add_argument("--upload", action="store_true")
    parser.add_argument(
        "--upload-results-only",
        action="store_true",
        help="Publish validated final result JSON files without executing workloads",
    )
    parser.add_argument(
        "--stage-failure",
        choices=["pre_flight", "deploy", "post_deploy", "benchmark", "teardown"],
    )
    parser.add_argument("--failure-message", default="stage failed")
    parser.add_argument(
        "--existing-result",
        type=Path,
        help="Append a stage failure to an existing run JSON",
    )
    args = parser.parse_args(argv)
    if not args.config_id or any(
        char in args.config_id for char in ("/", "\\", "\x00")
    ):
        parser.error("config-id must be nonempty and contain no path separators")
    started, clock_start = datetime.now(timezone.utc), time.monotonic()  # noqa: UP017 - generic Jenkins runners include Python 3.10.
    entry, scenario, config = {}, {}, {}
    if args.upload_results_only or args.existing_result:
        if args.existing_result and not args.stage_failure:
            parser.error("--existing-result requires --stage-failure")
        try:
            if args.upload or args.upload_results_only:
                config = yaml.safe_load(args.config.read_text())
            if args.existing_result:
                paths = [args.existing_result]
            else:
                # This output directory belongs to one shared-runner result key.
                paths = sorted(args.output_json.glob("compose-base_*.json"))
                if not paths:
                    raise BenchmarkError("no final benchmark results to publish")
            publications = []
            for path in paths:
                result = json.loads(path.read_text())
                validate_result(result)
                if result["metadata"]["config"]["config_id"] != args.config_id:
                    raise BenchmarkError("publication result config_id mismatch")
                if result["metadata"]["config"].get("sample_only"):
                    raise BenchmarkError(
                        "sample fixtures cannot be published as benchmark measurements"
                    )
                if args.stage_failure:
                    result = record_failure(
                        path, args.config_id, args.stage_failure, args.failure_message
                    )
                if args.upload or args.upload_results_only:
                    upload_result(path, config.get("global", {}).get("minio", {}))
                publications.append(
                    {
                        "result_file": str(path),
                        "status": result["summary"]["overall_status"],
                    }
                )
            print(
                json.dumps(
                    {"published_results": publications}
                    if args.upload_results_only
                    else publications[0]
                )
            )
            return 0 if args.upload_results_only else 1
        except Exception as exc:  # noqa: BLE001 - surface any schema, disk or object-store finalization failure.
            print(f"final result publication failed: {exc}", file=sys.stderr)
            if args.existing_result:
                print(
                    json.dumps(
                        {"result_file": str(args.existing_result), "status": "FAIL"}
                    )
                )
            return 1
    try:
        config = yaml.safe_load(args.config.read_text())
        matrix = yaml.safe_load(args.perf_config.read_text())
        entry, scenario = check_config(
            config,
            matrix,
            args.config_id,
            args.scenario,
            require_enabled=not args.stage_failure,
        )
        if args.stage_failure:
            raise BenchmarkError(f"{args.stage_failure}: {args.failure_message}")
        container = args.rt_cv_container or config.get("global", {}).get(
            "rt_cv_container"
        )
        if not container:
            raise BenchmarkError("set global.rt_cv_container or --rt-cv-container")
        cli_path = config.get("global", {}).get("cli_path")
        repo_cli = PERF_ROOT / ".venv/bin/vss"
        vss = cli_path or (str(repo_cli) if repo_cli.is_file() else "vss")
        docker_command = config.get("global", {}).get("docker_command") or (
            ["sudo", "-n", "docker"]
            if os.environ.get("DOCKER_USE_SUDO", "").lower() == "true"
            else ["docker"]
        )
        if (
            not isinstance(docker_command, list)
            or not docker_command
            or any(not isinstance(item, str) for item in docker_command)
        ):
            raise BenchmarkError(
                "global.docker_command must be a nonempty list of executable/arguments"
            )
        args.runtime_metadata = load_runtime_metadata(
            args.config_id, config.get("global", {})
        )
        args.deployment_version = {
            "version": None,
            "status": "not_observed",
            "source": "vss configure check",
        }
        cases = measure(
            scenario,
            container,
            vss,
            config.get("global", {}),
            args.rt_cv_gpus,
            docker_command,
            args.deployment_version,
        )
    except (
        BenchmarkError,
        CollectionError,
        OSError,
        ValueError,
        TypeError,
        yaml.YAMLError,
    ) as exc:
        cases = [case_failure(args.stage_failure or "preflight", str(exc))]
    result = build_result(
        args, entry, scenario, cases, started, time.monotonic() - clock_start
    )
    path = None
    try:
        path = write_result(result, args.output_json)
        if args.upload:
            upload_result(
                path,
                config.get("global", {}).get("minio", {})
                if isinstance(config, dict)
                else {},
            )
    except Exception as exc:  # noqa: BLE001 - surface any schema, disk or object-store publication failure.
        print(f"result publication failed: {exc}", file=sys.stderr)
        if path:
            try:
                result = record_failure(
                    path,
                    args.config_id,
                    "result_publication",
                    "Result upload failed; inspect pipeline diagnostics",
                )
            except (OSError, ValueError, BenchmarkError) as update_error:
                print(
                    f"failed publication result update: {update_error}", file=sys.stderr
                )
        print(
            json.dumps(
                {
                    "result_file": str(path) if path else "",
                    "status": "FAIL"
                    if not path
                    else result["summary"]["overall_status"],
                }
            )
        )
        return 1
    print(
        json.dumps(
            {"result_file": str(path), "status": result["summary"]["overall_status"]}
        )
    )
    return 0 if result["summary"]["overall_status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())

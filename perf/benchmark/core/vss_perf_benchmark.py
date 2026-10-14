#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Measure compose-base, clean up, and publish one standard VSS result."""

import argparse
import importlib.util
import json
import math
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from resource_monitor import ResourceMonitor

PERF_ROOT = Path(__file__).resolve().parents[2]
COMMON = PERF_ROOT.parent / "services/vios/test/bdd_tests/tests/perf/vss_perf_common.py"
SPEC = importlib.util.spec_from_file_location("shared_perf", COMMON)
SHARED = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = SHARED
SPEC.loader.exec_module(SHARED)
FPS_PAIR = re.compile(r"([0-9]+\.[0-9]+)\s*\(([0-9]+\.[0-9]+)\)")


def run(command, *, json_output=True, env=None):
    started = time.monotonic()
    response = subprocess.run(
        command, capture_output=True, text=True, env=env, check=False
    )
    if response.returncode:
        raise RuntimeError(
            f"{Path(command[0]).name} exited {response.returncode}: {response.stderr[-1000:]}"
        )
    output = response.stdout
    if "logs" in command:
        output += response.stderr
    return (json.loads(output) if json_output else output), time.monotonic() - started


def parse_fps(logs):
    samples, current = [], None
    for line in logs.splitlines():
        if "**PERF:" in line or re.search(r"PERF\(\d+\):", line):
            if current:
                samples.append(current)
            current = None if "FPS " in line else []
        elif "Active sources :" in line:
            if current:
                samples.append(current)
            current = None
        if current is not None:
            current.extend(float(fps) for fps, _ in FPS_PAIR.findall(line))
    if current:
        samples.append(current)
    return samples


def deployment_version_fact(output):
    fact = {"version": None, "status": "unavailable", "source": "vss configure check"}
    for line in output.splitlines():
        match = re.match(r"^\s*version\s+(.*?)\s*$", line)
        if match:
            value = match[1]
            if value.startswith("not reported"):
                fact.update(
                    status="not_reported",
                    detail=value.removeprefix("not reported").strip(),
                )
            else:
                fact.update(version=value.split()[0], status="reported")
            break
    return fact


def listing_count(payload, key):
    rows, count = payload.get(key), payload.get("count")
    if (
        not isinstance(rows, list)
        or type(count) is not int
        or count < 0
        or count != len(rows)
    ):
        raise ValueError(f"CLI {key} listing has inconsistent rows/count")
    return count


def failure(name, error):
    return {"test_case_id": name, "success": False, "metrics": {}, "error": str(error)}


def measure(scenario, gpu_ids, container):
    cli = PERF_ROOT / ".venv/bin/vss"
    cli = str(cli) if cli.exists() else "vss"
    output, _ = run([cli, "configure", "check"], json_output=False)
    version = deployment_version_fact(output)
    operations = [
        ("vios_stream_listing", ["vios", "list", "--type", "stream"], "sensors"),
        ("analytics_sensor_listing", ["analytics", "sensors"], "sensors"),
        (
            "analytics_incident_query",
            ["analytics", "incidents", "--limit", "100"],
            "incidents",
        ),
    ]
    for _ in range(scenario.get("warmup_iterations", 1)):
        for _, command, key in operations:
            payload, _ = run([cli, *command])
            listing_count(payload, key)
    monitor = ResourceMonitor(gpu_ids)
    monitor.start()
    utc = datetime.now(timezone.utc).isoformat()  # noqa: UP017 - shared runners support Python 3.10.
    started = time.monotonic()
    observations, errors = {name: [] for name, _, _ in operations}, {}
    try:
        while True:
            for name, command, key in operations:
                if name in errors:
                    continue
                try:
                    payload, elapsed = run([cli, *command])
                    observations[name].append((elapsed, listing_count(payload, key)))
                except (OSError, RuntimeError, ValueError) as error:
                    errors[name] = error
            monitor.sample()
            elapsed = time.monotonic() - started
            if errors or elapsed >= scenario["duration_seconds"]:
                break
            time.sleep(
                min(
                    scenario["interval_seconds"], scenario["duration_seconds"] - elapsed
                )
            )
    finally:
        resources = monitor.stop()
    cases, thresholds = [], scenario.get("thresholds", {})
    for name, _, _ in operations:
        if name in errors:
            cases.append(failure(name, errors[name]))
            continue
        rows = observations[name]
        latencies = [duration for duration, _ in rows]
        p95 = (
            latencies[0]
            if len(latencies) == 1
            else statistics.quantiles(latencies, n=100, method="inclusive")[94]
        )
        minimum = (
            scenario.get("expected_streams", 1) if name == "vios_stream_listing" else 0
        )
        if name == "analytics_sensor_listing":
            minimum = scenario.get("expected_analytics_sensors", 0)
        success = min(count for _, count in rows) >= minimum and p95 <= thresholds.get(
            "max_cli_p95_seconds", 10
        )
        cases.append(
            {
                "test_case_id": name,
                "success": success,
                "metrics": {
                    "latency": {
                        "client_observed_mean_seconds": statistics.mean(latencies),
                        "client_observed_p50_seconds": statistics.median(latencies),
                        "client_observed_p95_seconds": p95,
                    },
                    "requests": {
                        "completed": len(rows),
                        "success_rate_percent": 100,
                        "sequential_requests_per_second": len(rows) / elapsed,
                    },
                    "inventory": {
                        "minimum_returned_count": min(count for _, count in rows)
                    },
                },
            }
        )
    docker = (
        ["sudo", "-n", "docker"]
        if os.environ.get("DOCKER_USE_SUDO") == "true"
        else ["docker"]
    )
    logs, _ = run([*docker, "logs", "--since", utc, container], json_output=False)
    samples = parse_fps(logs)
    if samples:
        minimum = min(min(sample) for sample in samples)
        active = min(sum(fps > 0 for fps in sample) for sample in samples)
        cases.append(
            {
                "test_case_id": "rt_cv_stream_throughput",
                "success": minimum >= thresholds.get("min_rt_cv_fps", 1)
                and active >= scenario.get("expected_streams", 1),
                "metrics": {
                    "throughput": {
                        "aggregate_fps_mean": statistics.mean(map(sum, samples)),
                        "minimum_stream_fps": minimum,
                        "active_streams_minimum": active,
                        "sample_count": len(samples),
                    }
                },
            }
        )
    else:
        cases.append(
            failure(
                "rt_cv_stream_throughput", "no RT-CV FPS samples in measurement window"
            )
        )
    cases.append(
        {
            "test_case_id": "infrastructure_resources",
            "success": True,
            "metrics": resources,
        }
    )
    selected = {
        "model": next(iter(monitor.identities.values()))["model"],
        "count": len(monitor.handles),
        "topology": "single-gpu",
        "full_name": next(iter(monitor.identities.values()))["model"],
    }
    return cases, version, selected


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=PERF_ROOT / "configs/config.yaml"
    )
    parser.add_argument("--config-id", required=True)
    parser.add_argument("--scenario", default="quick_test")
    parser.add_argument("--output-json", type=Path, default=Path("benchmark_results"))
    for name in ("compose-file", "gpu-model", "release", "pipeline-url", "rt-cv-gpus"):
        parser.add_argument("--" + name)
    parser.add_argument("--triggered-by", default="manual")
    parser.add_argument(
        "--stage-failure",
        choices=["pre_flight", "deploy", "post_deploy", "benchmark", "teardown"],
    )
    parser.add_argument("--failure-message", default="stage failed")
    parser.add_argument("--upload", action="store_true")
    args = parser.parse_args(argv)
    if (
        not args.config_id
        or any(c in args.config_id for c in "/\\")
        or any(ord(c) < 32 for c in args.config_id)
    ):
        parser.error(
            "config-id must be nonempty with no path separators or control characters"
        )
    started = datetime.now(timezone.utc)  # noqa: UP017 - shared runners support Python 3.10.
    clock = time.monotonic()
    config, entry, scenario, runtime, selected = {}, {}, {}, {}, {}
    version = {"version": None, "status": "not_observed"}
    cleanup_failed = False
    try:
        config = yaml.safe_load(args.config.read_text())
        matrix = yaml.safe_load((PERF_ROOT / "configs/perf-configs.yaml").read_text())
        entries = [row for row in matrix["perf_configs"] if row["id"] == args.config_id]
        if len(entries) != 1:
            raise ValueError("config-id must match exactly one hardware configuration")
        entry = entries[0]
        if entry.get("enabled", True) is not True and not args.stage_failure:
            raise ValueError("hardware configuration is disabled")
        args.rt_cv_gpus = args.rt_cv_gpus or str(entry.get("rtCvGpus", "0"))
        scenario = config["test_scenarios"][args.scenario]
        for name in ("duration_seconds", "interval_seconds"):
            if (
                type(scenario[name]) not in (int, float)
                or not math.isfinite(scenario[name])
                or scenario[name] <= 0
            ):
                raise ValueError(f"{name} must be finite and positive")
        if args.stage_failure:
            raise RuntimeError(args.failure_message)
        runtime_dir = Path(
            os.environ.get("VSS_DATA_DIR", PERF_ROOT / ".runtime" / args.config_id)
        )
        runtime = json.loads((runtime_dir / "metadata.json").read_text())
        if not runtime.get("fixture") or not runtime.get("deployed_images"):
            raise ValueError("post_deploy fixture/image metadata missing")
        cases, version, selected = measure(
            scenario, args.rt_cv_gpus, config["global"]["rt_cv_container"]
        )
    except Exception as error:  # noqa: BLE001 - record any workload or configuration failure before cleanup.
        cases = [failure(args.stage_failure or "benchmark", error)]
    finally:
        try:
            run(
                ["bash", str(PERF_ROOT / "scripts/teardown.sh")],
                json_output=False,
                env={
                    **os.environ,
                    "VSS_PERF_CORE_CLEANUP": "true",
                    "CONFIG_ID": args.config_id,
                },
            )
        except (OSError, RuntimeError) as error:
            cleanup_failed = True
            cases.append(failure("teardown", error))
    written_path = None
    try:
        platform = SHARED.discover_platform()
        platform["gpu"].update(selected)
        revision = os.environ.get("VSS_PERF_SOURCE_REVISION")
        if not revision:
            response = subprocess.run(
                ["git", "-C", str(PERF_ROOT.parent), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=False,
            )
            revision = response.stdout.strip() or None
        passed = sum(case["success"] for case in cases)
        result = SHARED.build_result(
            "compose-base",
            f"compose-base_{args.config_id}_{started:%Y%m%d_%H%M%S}",
            cases,
            timestamp=started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            platform=platform,
            config={
                "config_id": args.config_id,
                "deployment": entry,
                "scenario": scenario,
                "rt_cv_gpus": args.rt_cv_gpus,
                "runtime": runtime,
                "deployment_version": version,
                "source": {
                    "revision": revision,
                    "artifact_ref": os.environ.get("ARTIFACT_REF")
                    or os.environ.get("IMAGE_TAG", ""),
                },
                "measurement_method": "VSS CLI wall time (includes Python startup); RT-CV instantaneous FPS logs",
            },
            benchmark_name=args.scenario,
            benchmark_mode="compose_base",
            triggered_by=args.triggered_by,
            release=args.release or "",
            pipeline_url=args.pipeline_url or os.environ.get("BUILD_URL", ""),
            duration_seconds=time.monotonic() - clock,
            passed=passed,
            failed=len(cases) - passed,
        )
        if cleanup_failed:
            result["summary"]["overall_status"] = "FAIL"
        serialized = json.dumps(result, indent=2, allow_nan=False) + "\n"
        args.output_json.mkdir(parents=True, exist_ok=True)
        path = args.output_json / (result["metadata"]["run_info"]["run_id"] + ".json")
        with path.open("x") as output:
            output.write(serialized)
        written_path = path
        if args.upload:
            if not os.environ.get("MINIO_ACCESS_KEY") or not os.environ.get(
                "MINIO_SECRET_KEY"
            ):
                raise ValueError("MinIO credentials are required for upload")
            settings = config.get("global", {}).get("minio", {})
            if not SHARED.upload_result_file(
                str(path),
                "compose-base",
                endpoint=os.environ.get("MINIO_ENDPOINT") or settings.get("endpoint"),
                bucket=os.environ.get("MINIO_BUCKET") or settings.get("bucket"),
            ):
                raise RuntimeError("result upload failed")
        status = result["summary"]["overall_status"]
        print(json.dumps({"result_file": str(path), "status": status}))
        return 0 if status == "PASS" else 1
    except Exception as error:  # noqa: BLE001 - report result validation, file, or upload failures without hiding them.
        print(f"result publication failed: {error}", file=sys.stderr)
        if written_path:
            result["test_cases"].append(failure("result_upload", error))
            summary = result["summary"]
            summary.update(
                total_tests=summary["total_tests"] + 1,
                failed=summary["failed"] + 1,
                overall_status="FAIL",
            )
            summary["pass_rate"] = round(
                100 * summary["passed"] / summary["total_tests"], 1
            )
            SHARED.VSSBenchmarkResult.model_validate(result)
            temporary = written_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
            temporary.replace(written_path)
            print(json.dumps({"result_file": str(written_path), "status": "FAIL"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

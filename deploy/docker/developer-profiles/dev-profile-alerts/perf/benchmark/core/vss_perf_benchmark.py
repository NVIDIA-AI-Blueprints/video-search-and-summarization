#!/usr/bin/env python3
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

"""Alert verification perf benchmark (entry point called by the perf pipeline).

Runs one scenario from configs/config.yaml against a deployed
dev-profile-alerts stack (MODE=2d_cv) and writes one standard VSS result JSON
through vss_perf_common:

    alert-verification_{config_id}_{YYYYMMDD}_{HHMMSS}.json

For each stream level of the scenario (ascending), the benchmark registers
NVStreamer streams as VST sensors until that many are active, waits for the
pipeline to settle, measures for ``measure_minutes``, and reads Alert Bridge
latency, throughput and failure metrics from Prometheus over that window.

Like the other VSS perf adapters (RTVI-VLM, VIOS), a test case's ``success``
means the measurement ran and produced data. Regressions are judged by the
dashboard against the baselines in configs/vss_perf_alert_kpis.yaml, not here.

Example (what the pipeline runs after post_deploy.sh):

    python vss_perf_benchmark.py \\
        --scenario quick_test \\
        --config-id spark-1-rtdetr-cr3-bf16 \\
        --cv-gpus 0 --vlm-gpus 0 \\
        --output-json benchmark_results --upload \\
        --triggered-by ci_pipeline --release 3.3.0-rc1

Exit codes: 0 every test case produced data, 1 some did not, 2 the run could
not start or the upload failed. A result file is written whenever possible.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

import alert_metrics  # noqa: E402
from streams import SensorRegistry, discover_streams  # noqa: E402
from system_monitor import SystemMonitor  # noqa: E402
from vss_perf_common import (  # noqa: E402
    VSSBenchmarkResult,
    build_and_save,
    discover_platform,
    upload_result_file,
)

PERF_DIR = Path(__file__).resolve().parents[2]
# Must match PERF_SERVICE in the trigger entries and `service` in vss_perf_alert_kpis.yaml.
SERVICE = "alert-verification"
logger = logging.getLogger("alert-perf")


def load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def find_entry(perf_configs: Path, config_id: str) -> Dict[str, Any]:
    for entry in load_yaml(perf_configs).get("perf_configs") or []:
        if entry.get("id") == config_id:
            return entry
    raise SystemExit(f"config id '{config_id}' not found in {perf_configs}")


def video_label(url: str) -> str:
    stem = Path(url.split("?")[0]).stem or "video"
    return re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").lower()


def gpu_topology(cv_gpus: str, vlm_gpus: str) -> str:
    cv = {g.strip() for g in str(cv_gpus).split(",") if g.strip()}
    vlm = {g.strip() for g in str(vlm_gpus).split(",") if g.strip()}
    return "shared" if cv == vlm else f"cv{len(cv)}-vlm{len(vlm)}"


def wait_ready(url: str, timeout_s: int = 300) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if requests.get(url, timeout=5).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(5)
    raise RuntimeError(f"not ready: {url}")


def sleep_with_progress(seconds: int, what: str) -> None:
    end = time.time() + seconds
    while True:
        left = int(end - time.time())
        if left <= 0:
            return
        logger.info("%s: %ds left", what, left)
        time.sleep(min(60, left))


def run_level(
    *,
    registry: SensorRegistry,
    level: int,
    wait_s: int,
    measure_s: int,
    scrape_s: int,
    prom_url: str,
    args: argparse.Namespace,
    global_cfg: Dict[str, Any],
) -> Dict[str, Any]:
    active = registry.ensure(level)
    if active < level:
        raise RuntimeError(f"only {active} of {level} streams active")
    sleep_with_progress(wait_s, f"{level} stream(s) settling")

    gpu_cfg = global_cfg.get("gpu_monitoring") or {}
    cpu_cfg = global_cfg.get("cpu_monitoring") or {}
    monitor = SystemMonitor(
        args.cv_gpus,
        args.vlm_gpus,
        interval_s=gpu_cfg.get("interval_seconds", 1),
        gpu=gpu_cfg.get("enabled", True),
        cpu=cpu_cfg.get("enabled", True),
    )
    window_start = datetime.now(timezone.utc)
    with monitor:
        sleep_with_progress(measure_s, f"measuring {level} stream(s)")
    # Let Prometheus take one more scrape so the window's tail is included.
    time.sleep(scrape_s + 1)
    window_end = datetime.now(timezone.utc)

    metrics = alert_metrics.collect_window(prom_url, measure_s, level)
    metrics.update(monitor.summary())
    metrics["window"] = {
        "start": window_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seconds": measure_s,
        "num_streams": level,
    }
    return metrics


def has_data(metrics: Dict[str, Any]) -> bool:
    """A level produced a measurement when at least one alert was verified."""
    return bool((metrics.get("latency") or {}).get("e2e_samples"))


def run_scenario(args: argparse.Namespace) -> int:
    config = load_yaml(args.config)
    defaults = load_yaml(args.defaults) if args.defaults.is_file() else {}
    entry = find_entry(args.perf_configs, args.config_id)
    global_cfg = config.get("global") or {}
    scenarios = config.get("test_scenarios") or {}
    if args.scenario not in scenarios:
        raise SystemExit(f"scenario '{args.scenario}' not in {args.config} (have: {', '.join(scenarios)})")
    scenario = scenarios[args.scenario]
    mode = scenario.get("benchmark_mode", "live_streams")
    if mode not in ("live_streams", "max_streams"):
        raise SystemExit(f"unsupported benchmark_mode '{mode}'")

    args.cv_gpus = args.cv_gpus or str(entry.get("cvGpus", "0"))
    args.vlm_gpus = args.vlm_gpus or str(entry.get("vlmGpus", "0"))
    prom_url = global_cfg.get("prometheus_url", "http://localhost:9091").rstrip("/")
    backend = global_cfg.get("vss_backend", "http://localhost:9080").rstrip("/")
    scrape_s = int((defaults.get("prometheus") or {}).get("scrape_interval_seconds", 5))
    warmup_s = int(args.warmup_seconds if args.warmup_seconds is not None else scenario.get("warmup_seconds", 180))
    settle_s = int(scenario.get("settle_seconds", 120))
    measure_minutes = args.measure_minutes if args.measure_minutes is not None else scenario.get("measure_minutes", 10)
    measure_s = int(float(measure_minutes) * 60)

    started = time.time()
    test_cases: List[Dict[str, Any]] = []
    setup_error: Optional[str] = None

    try:
        wait_ready(f"{backend}/ready")
        if not alert_metrics.prometheus_healthy(prom_url):
            raise RuntimeError(f"Prometheus not reachable at {prom_url}")

        videos = scenario.get("videos") or []
        max_level = max(int(c) for v in videos for c in v.get("stream_counts") or [1])
        streams = discover_streams(global_cfg.get("nvstreamer_url", "http://localhost:31000"), max_level)
        registry = SensorRegistry(global_cfg.get("vst_url", "http://localhost:30888"), streams)

        first = True
        for video in videos:
            label = video_label(video.get("url", "video"))
            target_p90 = float(video.get("target_e2e_p90_seconds", 10.0))
            max_fail = float(video.get("max_verification_failure_rate", 0.02))
            last_within_target: Optional[int] = None
            aborted = False
            for level in sorted(int(c) for c in video.get("stream_counts") or [1]):
                tc_id = f"{mode}_{label}_{level}streams"
                logger.info("=== %s ===", tc_id)
                try:
                    metrics = run_level(
                        registry=registry,
                        level=level,
                        wait_s=warmup_s if first else settle_s,
                        measure_s=measure_s,
                        scrape_s=scrape_s,
                        prom_url=prom_url,
                        args=args,
                        global_cfg=global_cfg,
                    )
                except Exception as exc:  # keep the result file for the dashboard
                    logger.exception("%s failed", tc_id)
                    test_cases.append({"test_case_id": tc_id, "success": False, "metrics": {}, "error": str(exc)})
                    aborted = True
                    break
                first = False
                ok = has_data(metrics)
                tc: Dict[str, Any] = {"test_case_id": tc_id, "success": ok, "metrics": metrics}
                if not ok:
                    tc["error"] = "no verified alerts in the measurement window"
                logger.info(
                    "%s: %s (e2e p90=%s s, verified/min/stream=%s)",
                    tc_id,
                    "OK" if ok else "NO DATA",
                    metrics["latency"].get("e2e_p90_seconds"),
                    metrics["throughput"].get("verified_per_min_per_stream"),
                )
                if mode == "max_streams":
                    # The scenario target only decides where the sweep stops and
                    # which level counts as sustainable; it is not a pass/fail.
                    p90 = metrics["latency"].get("e2e_p90_seconds")
                    fail_rate = metrics["reliability"].get("verification_failure_rate")
                    within = (
                        ok
                        and p90 is not None
                        and p90 <= target_p90
                        and (fail_rate is None or fail_rate <= max_fail)
                    )
                    tc["within_target"] = within
                    test_cases.append(tc)
                    if not within:
                        break
                    last_within_target = level
                else:
                    test_cases.append(tc)
            if mode == "max_streams" and not aborted:
                test_cases.append(
                    {
                        "test_case_id": f"max_streams_{label}",
                        "success": True,
                        "metrics": {
                            "throughput": {"max_sustainable_streams": last_within_target or 0},
                            "target": {"e2e_p90_seconds": target_p90, "verification_failure_rate": max_fail},
                        },
                    }
                )
            if aborted:
                break
    except Exception as exc:
        logger.exception("run failed before measuring")
        setup_error = str(exc)
        test_cases.append({"test_case_id": f"{mode}_setup", "success": False, "metrics": {}, "error": str(exc)})

    passed = sum(1 for tc in test_cases if tc["success"])
    failed = len(test_cases) - passed
    out_dir = Path(args.output_json or global_cfg.get("output_dir", "vss-perf-report"))
    path = build_and_save(
        out_dir,
        SERVICE,
        test_cases,
        config_id=entry["id"],
        platform=discover_platform(
            config_id=entry["id"], topology=gpu_topology(args.cv_gpus, args.vlm_gpus)
        ),
        config={
            "config_id": entry["id"],
            "hardware_profile": entry.get("hardwareProfile"),
            "cv_model": entry.get("cvModel"),
            "cv_gpus": args.cv_gpus,
            "vlm_model": entry.get("vlmModel"),
            "vlm_gpus": args.vlm_gpus,
            "vlm_precision": entry.get("vlmPrecision"),
            "llm_mode": entry.get("llmMode", "local_shared"),
            "alerts_mode": "verification",
            "artifact_ref": args.artifact_ref,
        },
        benchmark_name=args.scenario,
        benchmark_mode=mode,
        triggered_by=args.triggered_by,
        pipeline_url=args.pipeline_url,
        duration_seconds=round(time.time() - started, 1),
        release=args.release,
        passed=passed,
        failed=failed,
    )
    logger.info("wrote %s", path)
    print(json.dumps(json.loads(path.read_text(encoding="utf-8"))["summary"]))

    if args.upload and not upload_result_file(str(path), SERVICE):
        logger.error("upload to MinIO failed")
        return 2
    if setup_error:
        return 2
    return 0 if failed == 0 else 1


def validate_file(path: Path) -> int:
    """Validate an existing result file against the shared VSS result schema."""
    VSSBenchmarkResult.load_from_file(path)
    print(f"OK: {path} matches the VSS result schema")
    return 0


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", help="test_scenarios key in config.yaml")
    p.add_argument("--config-id", help="perf_configs id in perf-configs.yaml")
    p.add_argument("--cv-gpus", default="", help="GPU indices running RTVI-CV (default: cvGpus of the entry)")
    p.add_argument("--vlm-gpus", default="", help="GPU indices running RT-VLM (default: vlmGpus of the entry)")
    p.add_argument("--output-json", default="", help="directory for the result JSON")
    p.add_argument("--upload", action="store_true", help="upload the result to the perf-results MinIO bucket")
    p.add_argument("--triggered-by", default=os.environ.get("TRIGGERED_BY", "manual"))
    p.add_argument("--release", default=os.environ.get("RELEASE", ""))
    p.add_argument("--pipeline-url", default=os.environ.get("BUILD_URL", ""))
    p.add_argument("--artifact-ref", default=os.environ.get("ARTIFACT_REF", ""))
    p.add_argument("--measure-minutes", type=float, default=None, help="override measure_minutes")
    p.add_argument("--warmup-seconds", type=int, default=None, help="override warmup_seconds")
    p.add_argument("--config", type=Path, default=PERF_DIR / "configs" / "config.yaml")
    p.add_argument("--defaults", type=Path, default=PERF_DIR / "configs" / "defaults.yaml")
    p.add_argument("--perf-configs", type=Path, default=PERF_DIR / "configs" / "perf-configs.yaml")
    p.add_argument("--validate", type=Path, default=None, help="only validate this result file and exit")
    p.add_argument("--log-level", default="INFO")
    args = p.parse_args(argv)
    if args.validate is None and not (args.scenario and args.config_id):
        p.error("--scenario and --config-id are required (unless --validate)")
    return args


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.validate:
        return validate_file(args.validate)
    return run_scenario(args)


if __name__ == "__main__":
    sys.exit(main())

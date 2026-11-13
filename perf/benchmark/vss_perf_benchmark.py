#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Write the warehouse industry-example BA + VA-API result.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from http_latency import build_api_result
from kpis import evaluate_kpis, load_kpis
from latency import correct_topics, parse_latency_report
from safe_fs import safe_write_text


def build_result(
    report_text: str,
    *,
    artifact_ref: str,
    scenario: str,
    config_id: str,
    duration_seconds: int,
    warmup_seconds: int,
    api_base_url: str,
    endpoints: list[str],
    sensor_id: str,
    place: str,
    request_count: int,
    warmup_requests: int,
    compose_profiles: str = "",
    hardware_profile: str = "",
    apply_kpis: bool = True,
) -> dict[str, object]:
    topics = parse_latency_report(report_text)
    correction = correct_topics(topics)
    ba_payload = {
        "service": "behavior-analytics",
        "correction": "topic_summary_minus_raw",
        "success": correction["success"],
        "error": correction["error"],
        "warning": correction.get("warning"),
        "raw": correction["raw"],
        "topics": correction["topics"],
    }
    api_payload = build_api_result(
        base_url=api_base_url,
        names=endpoints,
        sensor_id=sensor_id,
        place=place,
        request_count=request_count,
        warmup_requests=warmup_requests,
        window_seconds=duration_seconds,
    )
    payload: dict[str, object] = {
        "schema_version": "0.1",
        "benchmark_type": "devx_profile",
        "service": "warehouse-bp",
        "profile_name": "warehouse-operations",
        "mode": "2d",
        "bp_profile": "bp_wh_kafka",
        "setup": "industry_example_warehouse_2d_kafka",
        "scenario": scenario,
        "config_id": config_id,
        "artifact_ref": artifact_ref,
        "duration_seconds": duration_seconds,
        "warmup_seconds": warmup_seconds,
        "hardware_profile": hardware_profile,
        "compose_metadata": {
            "profile_name": "warehouse-operations",
            "compose_profiles": compose_profiles,
            "microservices": [
                "vss-behavior-analytics",
                "vss-video-analytics-api",
                "vss-rtvi-cv",
                "vst",
                "nvstreamer",
                "elasticsearch",
                "kafka",
            ],
        },
        "behavior_analytics": ba_payload,
        "video_analytics_api": api_payload,
        "success": bool(correction["success"]),
        "error": correction["error"],
    }
    if apply_kpis:
        kpi_result = evaluate_kpis(payload, load_kpis())
        payload["kpis"] = kpi_result
        if not kpi_result["passed"]:
            payload["success"] = False
            previous = payload.get("error")
            payload["error"] = (
                f"{previous}; {kpi_result['error']}" if previous else kpi_result["error"]
            )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--artifact-ref", default="")
    parser.add_argument("--scenario", default="quick_test")
    parser.add_argument("--config-id", default="warehouse-2d-kafka")
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--warmup-seconds", type=int, default=15)
    parser.add_argument("--api-base-url", required=True)
    parser.add_argument("--endpoints", default="livez frames behavior events incidents")
    parser.add_argument("--sensor-id", default="Camera")
    parser.add_argument("--place", default="building=Warehouse/room=Room-1")
    parser.add_argument("--request-count", type=int, default=20)
    parser.add_argument("--warmup-requests", type=int, default=2)
    parser.add_argument("--compose-profiles", default="")
    parser.add_argument("--hardware-profile", default="")
    args = parser.parse_args()

    result = build_result(
        args.report.read_text(),
        artifact_ref=args.artifact_ref,
        scenario=args.scenario,
        config_id=args.config_id,
        duration_seconds=args.duration_seconds,
        warmup_seconds=args.warmup_seconds,
        api_base_url=args.api_base_url,
        endpoints=args.endpoints.split(),
        sensor_id=args.sensor_id,
        place=args.place,
        request_count=args.request_count,
        warmup_requests=args.warmup_requests,
        compose_profiles=args.compose_profiles,
        hardware_profile=args.hardware_profile,
    )
    safe_write_text(args.output, json.dumps(result, indent=2) + "\n")
    print(args.output)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

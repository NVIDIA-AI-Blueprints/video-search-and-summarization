# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Map Behavior Analytics playback measurements to canonical VSS test cases."""

from __future__ import annotations

from typing import Any


def to_test_cases(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one canonical test case without changing BA measurement values."""
    success = bool(payload.get("success"))
    test_case: dict[str, Any] = {
        "test_case_id": str(payload.get("setup") or "warehouse_2d_kafka_playback"),
        "description": "Behavior Analytics Kafka playback latency",
        "status": "PASS" if success else "FAIL",
        "config": {
            "duration_seconds": payload.get("duration_seconds"),
            "warmup_seconds": payload.get("warmup_seconds"),
            "setup": payload.get("setup", ""),
            "correction": payload.get("correction", ""),
            "app_overlay": payload.get("app_overlay", {}),
        },
        "metrics": {
            "raw": payload.get("raw", {}),
            "topics": payload.get("topics", {}),
            "kpis": payload.get("kpis", {}),
        },
    }
    if payload.get("error"):
        test_case["error"] = {
            "stage": payload.get("failure_stage", "benchmark"),
            "message": str(payload["error"]),
        }
    return [test_case]


def failure_payload(
    *,
    scenario: str,
    config_id: str,
    stage: str,
    message: str,
) -> dict[str, Any]:
    """Create the BA-native failure input consumed by ``to_test_cases``."""
    return {
        "schema_version": "0.1",
        "service": "behavior-analytics",
        "scenario": scenario,
        "config_id": config_id,
        "setup": "warehouse_2d_kafka_playback",
        "correction": "topic_summary_minus_raw",
        "success": False,
        "failure_stage": stage,
        "error": message,
        "raw": {},
        "topics": {},
        "kpis": {"passed": False, "violations": [message]},
    }

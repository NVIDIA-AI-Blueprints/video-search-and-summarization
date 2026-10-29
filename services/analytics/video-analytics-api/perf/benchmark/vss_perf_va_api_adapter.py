# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Map Video Analytics API HTTP measurements to canonical VSS test cases."""

from __future__ import annotations

from typing import Any, Mapping

_LATENCY_FIELDS = ("min_s", "max_s", "avg_s", "p50_s", "p95_s", "p99_s")


def _endpoint_violations(name: str, violations: list[str]) -> list[str]:
    return [
        violation
        for violation in violations
        if violation == f"{name} missing" or violation.startswith(f"{name}.")
    ]


def to_test_cases(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Preserve endpoint measurements while exposing dashboard-friendly metrics."""
    endpoints = payload.get("endpoints")
    kpis = payload.get("kpis")
    all_violations = [
        str(item)
        for item in ((kpis or {}).get("violations") or [])
        if str(item).strip()
    ]
    cases: list[dict[str, Any]] = []
    assigned: set[str] = set()

    if isinstance(endpoints, Mapping):
        for name, raw in endpoints.items():
            measured = raw if isinstance(raw, Mapping) else {}
            violations = _endpoint_violations(str(name), all_violations)
            assigned.update(violations)
            present = bool(measured.get("present"))
            status = "PASS" if present and not violations else "FAIL"
            latency = {
                key: measured.get(key)
                for key in _LATENCY_FIELDS
                if measured.get(key) is not None
            }
            test_case: dict[str, Any] = {
                "test_case_id": str(name),
                "description": (
                    f"{str(measured.get('method') or 'GET').upper()} "
                    f"{measured.get('path') or name} latency"
                ),
                "status": status,
                "config": {
                    "method": str(measured.get("method") or "GET").upper(),
                    "path": str(measured.get("path") or ""),
                    "query": measured.get("query") or {},
                },
                "metrics": {
                    "latency": latency,
                    "requests": {
                        "count": measured.get("count", 0),
                        "ok_count": measured.get("ok_count", 0),
                        "ok_ratio": measured.get("ok_ratio", 0.0),
                    },
                    "kpi": {
                        "passed": status == "PASS",
                        "violations": violations,
                    },
                },
            }
            if violations:
                test_case["error"] = {
                    "stage": "benchmark",
                    "message": "; ".join(violations),
                }
            cases.append(test_case)

    unassigned = [item for item in all_violations if item not in assigned]
    if not cases or unassigned or payload.get("error") and not all_violations:
        message = str(
            payload.get("error")
            or "; ".join(unassigned)
            or "benchmark produced no endpoint measurements"
        )
        cases.append(
            {
                "test_case_id": "benchmark_execution",
                "description": "Video Analytics API benchmark execution",
                "status": "FAIL",
                "config": {},
                "metrics": {
                    "latency": {},
                    "requests": {"count": 0, "ok_count": 0, "ok_ratio": 0.0},
                    "kpi": {"passed": False, "violations": unassigned or [message]},
                },
                "error": {
                    "stage": str(payload.get("failure_stage") or "benchmark"),
                    "message": message,
                },
            }
        )
    return cases


def failure_payload(
    *,
    scenario: str,
    config_id: str,
    stage: str,
    message: str,
) -> dict[str, Any]:
    """Create a native-shaped failure consumed by the canonical adapter."""
    return {
        "schema_version": "0.1",
        "service": "video-analytics-api",
        "scenario": scenario,
        "config_id": config_id,
        "success": False,
        "failure_stage": stage,
        "error": message,
        "endpoints": {},
        "kpis": {"passed": False, "violations": [message], "error": message},
    }

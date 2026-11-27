# SPDX-License-Identifier: Apache-2.0
"""Preserve native evaluator aggregates; do not recalculate latency percentiles."""


def to_test_cases(payload):
    summary = payload.get("summary") or {}
    if not summary:
        raise ValueError("Evaluator response has no aggregate summary")
    issues = []
    if payload.get("status") not in ("success", "completed"):
        issues.append("evaluator did not report successful completion")
    if summary.get("summary_status") not in ("success", "completed"):
        issues.append("native measurement summary is incomplete or degraded")
    if not summary.get("e2e_count", 0):
        issues.append("no E2E latency samples")
    for name in (
        "verification_failed_events",
        "unknown_events",
        "dropped_events",
        "sensors_no_data",
        "sensors_error",
        "synthetic_failed_submissions",
        "synthetic_missed_submissions",
        "e2e_missing_count",
    ):
        if summary.get(name, 0):
            issues.append(f"{name}={summary[name]}")
    minimum = summary.get("e2e_min_coverage_ratio", 0.99)
    for name in (
        "submission_coverage_ratio",
        "terminal_coverage_ratio",
        "e2e_completion_ratio",
    ):
        if name in summary and summary[name] is not None and summary[name] < minimum:
            issues.append(f"{name} below {minimum}")
    latency = {
        name: summary[key]
        for name, key in (
            ("e2e_seconds", "e2e_avg_sec"),
            ("e2e_p50_seconds", "e2e_p50_sec"),
            ("e2e_p90_seconds", "e2e_p90_sec"),
            ("e2e_p99_seconds", "e2e_p99_sec"),
            ("vlm_p90_seconds", "vlm_p90_sec"),
            ("vst_p90_seconds", "vst_p90_sec"),
            ("queue_wait_p90_seconds", "queue_wait_p90_sec"),
            ("kafka_lag_p90_seconds", "kafka_lag_p90_sec"),
        )
        if summary.get(key) is not None
    }
    # Histogram overflow values are bounds, not exact percentiles. Retain the
    # native values/flags below but do not chart them as exact latency numbers.
    for percentile in (50, 90, 99):
        if summary.get(f"e2e_p{percentile}_censored"):
            latency.pop(f"e2e_p{percentile}_seconds", None)
    case = {
        "test_case_id": "alerts_verification_latency",
        "status": "FAIL" if issues else "PASS",
        "metrics": {"latency": latency},
        "native_summary": summary,
        "sensor_results": payload.get("results", []),
    }
    if issues:
        case["error"] = {"stage": "evaluation", "message": "; ".join(issues)}
    return [case]

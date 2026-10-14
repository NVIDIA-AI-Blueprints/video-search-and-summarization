#!/usr/bin/env python3
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################
"""Validate and render an RTVI VLM performance benchmark plan without executing it."""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path
from typing import Any

REQUIRED_ENVIRONMENT = {
    "code_commit",
    "container_digest",
    "runtime_policy",
    "clean_source_identity",
    "cache_policy",
    "model_revision",
    "hardware",
    "precision",
}
REQUIRED_PATHS = {"output", "scratch", "mutable_cache"}
REQUIRED_WORKLOAD = {"media", "prompt", "input_tokens", "output_tokens"}
LOAD_UNITS = {
    "independent_live_stream",
    "shared_stream_subscriber",
    "file_request",
    "batch_request",
}
SOURCE_IDENTITY_POLICIES = {
    "unique_per_stream",
    "shared_across_requests",
    "not_applicable",
}
CACHE_POLICIES = {
    "empty",
    "content-addressed-read-only",
    "empty-or-content-addressed-read-only",
}
CAPACITY_OBSERVATION_SOURCES = {"client", "server", "engine", "gpu", "cleanup"}


def _require_nonempty(mapping: dict[str, Any], fields: set[str], label: str) -> None:
    missing = sorted(field for field in fields if mapping.get(field) in (None, ""))
    if missing:
        raise ValueError(f"{label} missing required fields: {', '.join(missing)}")


def resolve_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if plan.get("schema_version") != 1:
        raise ValueError("schema_version must be 1")
    if not str(plan.get("name", "")).strip():
        raise ValueError("name is required")
    command = plan.get("benchmark_command")
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(part, str) and part for part in command)
    ):
        raise ValueError("benchmark_command must be a non-empty list of strings")
    environment = plan.get("environment")
    paths = plan.get("paths")
    workload = plan.get("workload")
    measurement = plan.get("measurement")
    if not all(
        isinstance(value, dict) for value in (environment, paths, workload, measurement)
    ):
        raise ValueError(
            "environment, paths, workload, and measurement must be objects"
        )
    _require_nonempty(environment, REQUIRED_ENVIRONMENT, "environment")
    _require_nonempty(paths, REQUIRED_PATHS, "paths")
    _require_nonempty(workload, REQUIRED_WORKLOAD, "workload")
    if environment["runtime_policy"] != "fresh_per_run":
        raise ValueError("environment.runtime_policy must be fresh_per_run")
    if environment["cache_policy"] not in CACHE_POLICIES:
        raise ValueError(
            "environment.cache_policy must declare an empty or content-addressed read-only cache"
        )
    if len({str(paths[field]) for field in REQUIRED_PATHS}) != len(REQUIRED_PATHS):
        raise ValueError(
            "paths.output, paths.scratch, and paths.mutable_cache must be distinct"
        )

    load_unit = workload.get("load_unit")
    source_identity_policy = workload.get("source_identity_policy")
    session_reuse = workload.get("session_reuse")
    if load_unit not in LOAD_UNITS:
        raise ValueError(
            f"workload.load_unit must be one of: {', '.join(sorted(LOAD_UNITS))}"
        )
    if source_identity_policy not in SOURCE_IDENTITY_POLICIES:
        raise ValueError(
            "workload.source_identity_policy must be one of: "
            + ", ".join(sorted(SOURCE_IDENTITY_POLICIES))
        )
    if not isinstance(session_reuse, bool):
        raise TypeError("workload.session_reuse must be a boolean")
    if load_unit == "independent_live_stream" and (
        source_identity_policy != "unique_per_stream" or session_reuse
    ):
        raise ValueError(
            "independent_live_stream requires source_identity_policy=unique_per_stream "
            "and session_reuse=false"
        )
    source_identity_count = workload.get("source_identity_count")
    if load_unit == "independent_live_stream" and (
        not isinstance(source_identity_count, int) or source_identity_count < 1
    ):
        raise ValueError(
            "independent_live_stream requires a positive workload.source_identity_count"
        )
    if load_unit == "shared_stream_subscriber" and (
        source_identity_policy != "shared_across_requests" or not session_reuse
    ):
        raise ValueError(
            "shared_stream_subscriber requires source_identity_policy=shared_across_requests "
            "and session_reuse=true"
        )
    if (
        load_unit in {"file_request", "batch_request"}
        and source_identity_policy != "not_applicable"
    ):
        raise ValueError(f"{load_unit} requires source_identity_policy=not_applicable")

    if (
        not isinstance(measurement.get("warmup_runs"), int)
        or measurement["warmup_runs"] < 0
    ):
        raise ValueError("measurement.warmup_runs must be a non-negative integer")
    if (
        not isinstance(measurement.get("repetitions"), int)
        or measurement["repetitions"] < 1
    ):
        raise ValueError("measurement.repetitions must be a positive integer")
    if not isinstance(measurement.get("metrics"), list) or not measurement["metrics"]:
        raise ValueError("measurement.metrics must be a non-empty list")

    if measurement.get("claim") == "capacity_ceiling":
        envelope = plan.get("capacity_envelope")
        if not isinstance(envelope, dict):
            raise ValueError(
                "capacity_envelope is required for measurement.claim=capacity_ceiling"
            )
        _require_nonempty(
            envelope,
            {
                "claim",
                "boundary_policy",
                "stability_window_seconds",
                "success_criteria",
                "admission_policy",
                "observation_sources",
                "fatal_markers",
            },
            "capacity_envelope",
        )
        if envelope["claim"] != "capacity_ceiling":
            raise ValueError("capacity_envelope.claim must be capacity_ceiling")
        if envelope["boundary_policy"] != "highest_stable_and_first_unstable":
            raise ValueError(
                "capacity_envelope.boundary_policy must observe highest_stable_and_first_unstable"
            )
        if (
            not isinstance(envelope["stability_window_seconds"], int)
            or envelope["stability_window_seconds"] < 1
        ):
            raise ValueError(
                "capacity_envelope.stability_window_seconds must be a positive integer"
            )
        criteria = envelope["success_criteria"]
        if not isinstance(criteria, dict):
            raise ValueError("capacity_envelope.success_criteria must be an object")
        _require_nonempty(
            criteria,
            {"min_success_rate", "max_p95_latency_ms", "zero_cross_scenario_residue"},
            "capacity_envelope.success_criteria",
        )
        if criteria["zero_cross_scenario_residue"] is not True:
            raise ValueError(
                "capacity_envelope.success_criteria.zero_cross_scenario_residue must be true"
            )
        if not isinstance(criteria["min_success_rate"], (int, float)) or not (
            0 < criteria["min_success_rate"] <= 1
        ):
            raise ValueError(
                "capacity_envelope.success_criteria.min_success_rate must be greater than 0 and at most 1"
            )
        if not isinstance(criteria["max_p95_latency_ms"], (int, float)) or (
            criteria["max_p95_latency_ms"] <= 0
        ):
            raise ValueError(
                "capacity_envelope.success_criteria.max_p95_latency_ms must be positive"
            )
        admission = envelope["admission_policy"]
        if not isinstance(admission, dict):
            raise ValueError("capacity_envelope.admission_policy must be an object")
        _require_nonempty(
            admission,
            {"mode", "controller", "limits_source"},
            "capacity_envelope.admission_policy",
        )
        observed = envelope["observation_sources"]
        if (
            not isinstance(observed, list)
            or set(observed) != CAPACITY_OBSERVATION_SOURCES
        ):
            raise ValueError(
                "capacity_envelope.observation_sources must contain client, server, engine, gpu, and cleanup"
            )
        if (
            not isinstance(envelope["fatal_markers"], list)
            or not envelope["fatal_markers"]
        ):
            raise ValueError("capacity_envelope.fatal_markers must be a non-empty list")

    scenarios = plan.get("scenarios")
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenarios must be a non-empty list")
    resolved: list[dict[str, Any]] = []
    names = set()
    for index, scenario in enumerate(scenarios, start=1):
        if not isinstance(scenario, dict):
            raise TypeError(f"scenario {index} must be an object")
        name = str(scenario.get("name", "")).strip()
        if not name or name in names:
            raise ValueError(f"scenario {index} has a missing or duplicate name")
        names.add(name)
        concurrency_levels = scenario.get("concurrency_levels")
        if concurrency_levels is not None:
            if (
                not isinstance(concurrency_levels, list)
                or not concurrency_levels
                or not all(
                    isinstance(level, int) and level > 0 for level in concurrency_levels
                )
            ):
                raise ValueError(
                    f"scenario {name} concurrency_levels must be a non-empty list of positive integers"
                )
            required_sources = max(concurrency_levels)
            if (
                load_unit == "independent_live_stream"
                and source_identity_count < required_sources
            ):
                raise ValueError(
                    f"workload.source_identity_count must be at least {required_sources} for scenario {name}"
                )
            args = (
                list(command)
                + ["--scenario", name, "--concurrency-levels"]
                + [str(level) for level in concurrency_levels]
            )
            resolved.append(
                {"name": name, "concurrency_levels": concurrency_levels, "argv": args}
            )
            continue
        initial = scenario.get("initial_stream_count")
        reference = scenario.get("reference_maximum")
        offset = scenario.get("offset")
        if initial is None:
            if not isinstance(reference, int) or not isinstance(offset, int):
                raise ValueError(
                    f"scenario {name} requires initial_stream_count or integer reference_maximum and offset"
                )
            initial = reference - offset
        if not isinstance(initial, int) or initial < 1:
            raise ValueError(
                f"scenario {name} resolves to an invalid initial_stream_count"
            )
        if load_unit == "independent_live_stream" and source_identity_count < initial:
            raise ValueError(
                f"workload.source_identity_count must be at least {initial} for scenario {name}"
            )
        increment = scenario.get("add_stream_count")
        if not isinstance(increment, int) or increment < 1:
            raise ValueError(f"scenario {name} requires a positive add_stream_count")
        args = list(command) + [
            "--scenario",
            name,
            "--initial-stream-count",
            str(initial),
            "--add-stream-count",
            str(increment),
        ]
        if scenario.get("binary_search_refinement", False):
            args.append("--binary-search-refinement")
        resolved.append({"name": name, "initial_stream_count": initial, "argv": args})
    result = dict(plan)
    result["resolved_scenarios"] = resolved
    return result


def load_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("plan root must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("validate", "render"))
    parser.add_argument("plan", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    try:
        resolved = resolve_plan(load_plan(args.plan))
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.command == "validate":
        output: Any = {
            "valid": True,
            "name": resolved["name"],
            "scenario_count": len(resolved["resolved_scenarios"]),
            "load_unit": resolved["workload"]["load_unit"],
            "source_identity_policy": resolved["workload"]["source_identity_policy"],
        }
    else:
        output = resolved["resolved_scenarios"]
    if args.as_json:
        print(json.dumps(output, indent=2, sort_keys=True))
    elif args.command == "validate":
        print(
            f"valid: {output['name']} ({output['scenario_count']} scenario(s), "
            f"{output['load_unit']}, {output['source_identity_policy']})"
        )
    else:
        for item in output:
            print(f"{item['name']}: {shlex.join(item['argv'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Warehouse-dump GET matrix derived from the Video Analytics API OpenAPI spec."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

SERVICE_DIR = Path(__file__).resolve().parents[2]
SPEC_PATH = SERVICE_DIR / "src" / "app" / "specification" / "openapi.json"

# Stable names for the original five KPI rows.
SPECIAL_NAMES = {
    "/livez": "livez",
    "/frames": "frames",
    "/behavior": "behavior",
    "/events/tripwire": "events",
    "/incidents": "incidents",
}

# Query keys this dump-backed job can fill from warehouse_2d constants.
CONTEXT_KEYS = (
    "sensorId",
    "sensorIds",
    "place",
    "fromTimestamp",
    "toTimestamp",
    "timestamp",
    "emptyIfNotFound",
    "x",
    "y",
)

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def load_spec(path: Path | None = None) -> dict[str, Any]:
    spec_path = path or SPEC_PATH
    data = json.loads(spec_path.read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{spec_path} did not parse as a mapping")
    return data


def endpoint_name(path: str) -> str:
    if path in SPECIAL_NAMES:
        return SPECIAL_NAMES[path]
    return path.strip("/").replace("/", "_").replace("-", "_")


def _exclusive_location_keys(query_keys: list[str], required: set[str]) -> list[str]:
    """The service JSON schema is oneOf sensorId/sensorIds XOR place.

    Sending both returns 400 on /behavior, /events, /incidents, alerts, and
    several metrics/tracker routes. Prefer the warehouse Camera sensorId when
    both are optional (same as Jenkins #69). Keep place only when it is required.
    """
    has_sensor = "sensorId" in query_keys or "sensorIds" in query_keys
    has_place = "place" in query_keys
    if not (has_sensor and has_place):
        return query_keys
    sensor_required = "sensorId" in required or "sensorIds" in required
    place_required = "place" in required
    if place_required and not sensor_required:
        return [key for key in query_keys if key not in ("sensorId", "sensorIds")]
    return [key for key in query_keys if key != "place"]


def _query_params(operation: Mapping[str, Any]) -> tuple[set[str], set[str]]:
    names: set[str] = set()
    required: set[str] = set()
    for param in operation.get("parameters") or []:
        if not isinstance(param, Mapping) or param.get("in") != "query":
            continue
        name = str(param.get("name") or "")
        if not name:
            continue
        names.add(name)
        if param.get("required"):
            required.add(name)
    return names, required


def spec_get_endpoints(spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return GET operations whose required query params this job can supply.

    Skips POST/PUT/PATCH/DELETE, path-parameter routes, and GETs that need
    IDs this warehouse snapshot does not provide (segmentInfo, view,
    endFrameId, behaviorTimeInterval, referenceId).
    """
    document = spec if spec is not None else load_spec()
    rows: list[dict[str, Any]] = []
    for path, operations in (document.get("paths") or {}).items():
        if "{" in path:
            continue
        if not isinstance(operations, Mapping):
            continue
        operation = operations.get("get")
        if not isinstance(operation, Mapping):
            continue
        listed, required = _query_params(operation)
        if not required.issubset(CONTEXT_KEYS):
            continue
        query_keys = []
        for key in CONTEXT_KEYS:
            if key not in listed:
                continue
            # Do not mix a point-in-time timestamp with a from/to range.
            if (
                key == "timestamp"
                and key not in required
                and "fromTimestamp" in listed
            ):
                continue
            query_keys.append(key)
        query_keys = _exclusive_location_keys(query_keys, required)
        rows.append(
            {
                "name": endpoint_name(path),
                "path": path,
                "query_keys": query_keys,
                "tags": list(operation.get("tags") or []),
            }
        )
    return rows


def spec_get_names(spec: Mapping[str, Any] | None = None) -> list[str]:
    return [row["name"] for row in spec_get_endpoints(spec)]


def query_params_for(row: Mapping[str, Any], context: Mapping[str, str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for key in row.get("query_keys") or []:
        value = context.get(key)
        if value is not None and value != "":
            params[str(key)] = str(value)
    return params

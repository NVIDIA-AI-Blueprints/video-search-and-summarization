# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Warehouse-dump HTTP matrix derived from the Video Analytics API OpenAPI spec."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

SERVICE_DIR = Path(__file__).resolve().parents[2]
SPEC_PATH = SERVICE_DIR / "src" / "app" / "specification" / "openapi.json"
FIXTURES_DIR = SERVICE_DIR / "test" / "integration-test" / "fixtures"

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
    "view",
    "fromTimestamp",
    "toTimestamp",
    "timestamp",
    "emptyIfNotFound",
    "x",
    "y",
)

# Fixture metadata is place-keyed (building=Warehouse/room=Room-1, plan-view).
PLACE_PREFERRED_PATHS = frozenset({"/config/calibration/image"})

# Ganapathy extra warehouse GETs (Slack 23 Oct) already in the dump-backed matrix.
GANAPATHY_EXTRA_PATHS = frozenset(
    {
        "/metrics/average-speed",
        "/metrics/flowrate",
        "/frames/alerts",
        "/clustering/behavior",
        "/events/roi",
    }
)

# OpenAPI required params are fillable, but warehouse_2d still cannot HTTP 200.
SKIP_PATHS = frozenset(
    {
        # Needs city/corridor; warehouse place is building/room.
        "/metrics/average-speed-with-travel-time",
        # Runtime oneOf requires behaviorIds XOR globalId (OpenAPI marks both optional).
        "/tracker/behavior-locations",
        # PTS only for calibration source=nvstreamer.
        "/frames/pts",
        # Config docs are not in the warehouse dump (404).
        "/config/road-network",
        "/config/usd-assets",
    }
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


def _exclusive_location_keys(
    path: str, query_keys: list[str], required: set[str]
) -> list[str]:
    """The service JSON schema is oneOf sensorId/sensorIds XOR place.

    Sending both returns 400 on /behavior, /events, /incidents, alerts, and
    several metrics/tracker routes. Prefer the warehouse Camera sensorId when
    both are optional (same as Jenkins #69). Keep place when the spec requires
    it, or when the fixture is place-keyed (calibration image).
    """
    has_sensor = "sensorId" in query_keys or "sensorIds" in query_keys
    has_place = "place" in query_keys
    if not (has_sensor and has_place):
        return query_keys
    sensor_required = "sensorId" in required or "sensorIds" in required
    place_required = "place" in required
    if path in PLACE_PREFERRED_PATHS or (place_required and not sensor_required):
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


def _schema_default(param: Mapping[str, Any]) -> Any:
    schema = param.get("schema")
    if isinstance(schema, Mapping) and "default" in schema:
        return schema["default"]
    return None


def _limit_defaults(operation: Mapping[str, Any]) -> dict[str, Any]:
    """OpenAPI defaults that change retrieval time (Ganapathy, 24 Oct)."""
    limits: dict[str, Any] = {}
    for param in operation.get("parameters") or []:
        if not isinstance(param, Mapping) or param.get("in") != "query":
            continue
        name = str(param.get("name") or "")
        if name not in ("maxResultSize", "timeWindowSizeInSec", "maxClusterSampleSize"):
            continue
        default = _schema_default(param)
        if default is not None:
            limits[name] = default
    return limits


def spec_get_endpoints(spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return GET operations whose required query params this job can supply.

    Skips POST/PUT/PATCH/DELETE, path-parameter routes, GETs that need IDs
    this warehouse snapshot does not provide (segmentInfo, endFrameId,
    behaviorTimeInterval, referenceId), and SKIP_PATHS (travel-time corridor,
    behavior-locations IDs, nvstreamer PTS, missing road-network/usd-assets).
    GET /config/calibration/image is included (view=plan-view, place from the
    warehouse fixture). POST setup for that image lives in p0_post_endpoints.
    """
    document = spec if spec is not None else load_spec()
    rows: list[dict[str, Any]] = []
    for path, operations in (document.get("paths") or {}).items():
        if "{" in path or path in SKIP_PATHS:
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
        query_keys = _exclusive_location_keys(path, query_keys, required)
        limits = _limit_defaults(operation)
        rows.append(
            {
                "name": endpoint_name(path),
                "path": path,
                "query_keys": query_keys,
                "limits": limits,
                "tags": list(operation.get("tags") or []),
            }
        )
    return rows


def spec_get_names(spec: Mapping[str, Any] | None = None) -> list[str]:
    return [row["name"] for row in spec_get_endpoints(spec)]


def _require_fixture(name: str) -> Path:
    path = FIXTURES_DIR / name
    if not path.is_file():
        raise FileNotFoundError(f"missing P0 fixture {path}")
    return path


def p0_post_endpoints() -> list[dict[str, Any]]:
    """Kartikay P0 writes this dump + Kafka-off stack can HTTP 201.

    POST /config/update/{docType} and GET /config/update/status/{docType}/{referenceId}
    need Kafka plus a Behavior Analytics ack. kafka.js stays null when
    brokers: [], so those two stay out of this job.
    """
    return [
        {
            "name": "config_upload_file_calibration",
            "path": "/config/upload-file/calibration",
            "method": "POST",
            "ok_statuses": (201,),
            "query_keys": [],
            "multipart": [
                ("configFiles", _require_fixture("calibration.json")),
            ],
            "tags": ["config"],
        },
        {
            "name": "config_calibration_images",
            "path": "/config/calibration/images",
            "method": "POST",
            "ok_statuses": (201,),
            "query_keys": [],
            "multipart": [
                ("images", _require_fixture("Top.png")),
                ("imageMetadata", _require_fixture("calibration_image_metadata.json")),
            ],
            "tags": ["config"],
        },
    ]


def dump_backed_endpoints(spec: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    """P0 fixture POSTs first so GET /config/calibration/image can HTTP 200."""
    return p0_post_endpoints() + spec_get_endpoints(spec)


def dump_backed_names(spec: Mapping[str, Any] | None = None) -> list[str]:
    return [row["name"] for row in dump_backed_endpoints(spec)]


def query_params_for(row: Mapping[str, Any], context: Mapping[str, str]) -> dict[str, str]:
    params: dict[str, str] = {}
    for key in row.get("query_keys") or []:
        value = context.get(key)
        if value is not None and value != "":
            params[str(key)] = str(value)
    for key, value in (row.get("limits") or {}).items():
        params[str(key)] = str(value)
    return params


def query_window_s(params: Mapping[str, str]) -> float | None:
    start = params.get("fromTimestamp")
    end = params.get("toTimestamp")
    if not start or not end:
        window = params.get("timeWindowSizeInSec")
        if window is None:
            return None
        try:
            return float(window)
        except ValueError:
            return None
    from datetime import datetime

    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (end_dt - start_dt).total_seconds()


def query_notes(row: Mapping[str, Any], params: Mapping[str, str]) -> dict[str, Any]:
    """Record the request shape so reviewers can compare retrieval time."""
    max_result = params.get("maxResultSize")
    payload: dict[str, Any] = {
        "params": dict(params),
        "query_window_s": query_window_s(params),
        "maxResultSize": int(max_result) if max_result and str(max_result).isdigit() else None,
    }
    if "timeWindowSizeInSec" in params:
        payload["timeWindowSizeInSec"] = int(float(params["timeWindowSizeInSec"]))
    if "maxClusterSampleSize" in params:
        payload["maxClusterSampleSize"] = int(float(params["maxClusterSampleSize"]))
    return payload

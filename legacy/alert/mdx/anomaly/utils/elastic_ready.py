# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Utilities for making MDX events Elasticsearch-ready."""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
from typing import Any, Iterable, Mapping, MutableMapping, Optional, Sequence


__all__ = [
    "coerce_epoch_fields",
    "strip_embeddings",
    "normalize_location_fields",
    "remove_logstash_artifacts",
    "generate_alert_fingerprint",
    "normalize_alert_event",
    "generate_incident_fingerprint",
    "normalize_incident_event",
]


_HMAC_KEY = b"HMAC"


def coerce_epoch_fields(
    event: MutableMapping[str, Any],
    *,
    fields: Sequence[str] = ("timestamp",),
    timezone: _dt.tzinfo = _dt.timezone.utc,
) -> None:
    """Convert nested ``seconds``/``nanos`` structures into ISO-8601 strings."""

    for field in fields:
        value = event.get(field)
        if not isinstance(value, Mapping):
            continue

        milliseconds = _extract_epoch_millis(value)
        if milliseconds is None:
            continue

        timestamp = _dt.datetime.fromtimestamp(milliseconds / 1000, tz=timezone)
        event[field] = timestamp.isoformat().replace("+00:00", "Z")


def _extract_epoch_millis(timestamp_dict: Mapping[str, Any]) -> Optional[int]:
    seconds = timestamp_dict.get("seconds")
    nanos = timestamp_dict.get("nanos")

    if seconds is None and nanos is None:
        return None

    try:
        sec = int(seconds or 0)
        nano = int(nanos or 0)
    except (TypeError, ValueError):
        return None

    return (sec * 1000) + int(nano / 1_000_000)


def _normalize_timestamp_fields_to_millis(
    event: MutableMapping[str, Any],
    *,
    fields: Sequence[str],
) -> None:
    """Normalize selected timestamp fields to millisecond ISO-8601 strings."""

    for field in fields:
        normalized = _normalize_ts_to_millis(event.get(field))
        if normalized is not None:
            event[field] = normalized


def strip_embeddings(event: MutableMapping[str, Any]) -> None:
    """Remove embedding payloads for parity with the Logstash alert pipeline."""

    event.pop("embeddings", None)

    obj = event.get("object")
    if isinstance(obj, MutableMapping):
        bbox = obj.get("bbox3d")
        if isinstance(bbox, MutableMapping):
            bbox.pop("embeddings", None)


def _flatten_coordinate_collection(collection: MutableMapping[str, Any]) -> bool:
    coordinates = collection.get("coordinates")
    if not isinstance(coordinates, Iterable):
        return False

    flattened = []
    for entry in coordinates:
        if isinstance(entry, Mapping) and "point" in entry:
            flattened.append(entry["point"])
        else:
            flattened.append(entry)

    collection["coordinates"] = flattened

    geometry_type = collection.get("type")
    if isinstance(geometry_type, str) and geometry_type.lower() == "linestring":
        collection["type"] = "LineString"

    return True


def normalize_location_fields(event: MutableMapping[str, Any]) -> None:
    """Flatten location-style fields to align with Logstash alert output."""

    locations = event.get("locations")
    if isinstance(locations, MutableMapping):
        _flatten_coordinate_collection(locations)

    smooth_locations = event.get("smoothLocations")
    if isinstance(smooth_locations, MutableMapping):
        _flatten_coordinate_collection(smooth_locations)


def remove_logstash_artifacts(event: MutableMapping[str, Any]) -> None:
    """Drop helper fields Logstash deletes before indexing alerts."""

    for field in ("kafka", "message", "@timestamp", "@version"):
        event.pop(field, None)


from datetime import datetime, timezone

def _normalize_ts_to_millis(ts: Any) -> Optional[str]:
    """
    Convert ANY timestamp (string or {seconds,nanos} dict)
    into a 3-digit millisecond ISO8601 datetime string used by Logstash.
    """

    # Case 1 — ES-style dict: {"seconds": ..., "nanos": ...}
    if isinstance(ts, Mapping):
        epoch_ms = _extract_epoch_millis(ts)
        if epoch_ms is None:
            return None
        dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
        millis = dt.microsecond // 1000
        return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{millis:03d}Z")

    # Case 2 — Timestamp string
    if isinstance(ts, str):
        try:
            # Normalize "Z" to +00:00 for parsing
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            millis = dt.microsecond // 1000
            return dt.strftime(f"%Y-%m-%dT%H:%M:%S.{millis:03d}Z")
        except Exception:
            return ts  # fallback

    return None


def generate_alert_fingerprint(event):
    """Replicate Logstash fingerprinting for alert-like documents."""

    field_map = {
        "[analyticsModule][id]": ("analyticsModule", "id"),
        "[object][id]": ("object", "id"),
        "[place][name]": ("place", "name"),
        "[sensor][id]": ("sensor", "id"),
        "timestamp": ("timestamp",),
    }

    segments = []
    for field_name in sorted(field_map.keys()):
        value = _nested_get(event, field_map[field_name])
        if value is None:
            continue

        # Normalize timestamp only
        if field_name == "timestamp":
            value = _normalize_ts_to_millis(value)

        segments.append(f"|{field_name}|{value}")

    if not segments:
        return None

    payload = "".join(segments) + "|"

    digest = hmac.new(_HMAC_KEY, payload.encode("utf-8"), hashlib.sha1)
    return digest.hexdigest()

def _nested_get(container: Mapping[str, Any], path: Sequence[str]) -> Optional[Any]:
    current: Any = container
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
        if current is None:
            return None
    return current


def normalize_alert_event(event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Apply Logstash-equivalent cleanup steps in-place and return the event."""

    coerce_epoch_fields(event, fields=("timestamp", "end"))
    _normalize_timestamp_fields_to_millis(event, fields=("timestamp", "end"))
    strip_embeddings(event)
    normalize_location_fields(event)
    remove_logstash_artifacts(event)
    return event


def generate_incident_fingerprint(event: Mapping[str, Any]) -> Optional[str]:
    """Replicate Logstash fingerprinting for incident documents (mdx-incidents/mdx-vlm-incidents).

    Logstash fingerprint logic uses info.primaryObjectId if present:
      - With primaryObjectId: |[info][primaryObjectId]|obj_123|category|collision|sensorId|HWY_20...|timestamp|2025-...Z|
      - Without: |category|collision|sensorId|HWY_20...|timestamp|2025-...Z|
    """
    kv_pairs: list[tuple[str, str]] = []

    # Check for info.primaryObjectId (matches Logstash fingerprint logic)
    info = event.get("info")
    if isinstance(info, Mapping):
        primary_obj_id = info.get("primaryObjectId")
        if primary_obj_id is not None and primary_obj_id != "":
            kv_pairs.append(("[info][primaryObjectId]", str(primary_obj_id)))

    # Common fields
    for key in ("category", "sensorId", "timestamp"):
        value = event.get(key)  # type: ignore[index]
        if value is not None and value != "":
            if key == "timestamp":
                value = _normalize_ts_to_millis(value)
            kv_pairs.append((key, str(value)))

    if not kv_pairs:
        return None

    # Sort by key to match LS ordering
    kv_pairs.sort(key=lambda kv: kv[0])

    # Build payload with leading '|' for each pair and a final trailing '|'
    parts: list[str] = []
    for k, v in kv_pairs:
        parts.append(f"|{k}|{v}")
    parts.append("|")
    payload = "".join(parts)

    digest = hmac.new(_HMAC_KEY, payload.encode("utf-8"), hashlib.sha1)
    return digest.hexdigest()


def normalize_incident_event(event: MutableMapping[str, Any]) -> MutableMapping[str, Any]:
    """Apply Logstash-equivalent cleanup for incidents (no location flattening)."""
    coerce_epoch_fields(event, fields=("timestamp", "end"))
    _normalize_timestamp_fields_to_millis(event, fields=("timestamp", "end"))
    strip_embeddings(event)
    remove_logstash_artifacts(event)
    return event


# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""DB row <-> API JSON mapping.

Verified against the live C++ /sensor/list output (2026-06-09). Two non-obvious transforms:
  - the SENSOR_DETAILS.POSITION JSON column uses snake_case keys (field_of_view, geo_location)
    while the API emits camelCase (fieldOfView, geoLocation);
  - `state` is derived from http_status (200 -> "online", else "offline"); IS_REMOTE is a string
    ("true"/"false") in the DB but a bool in the API.
"""
from __future__ import annotations

import json
from typing import Any

from ..db.models import SensorDetails, SensorStreams

CAMERA_NO_ERROR_CODE = 200
CAMERA_UNAUTHORIZED_CODE = 401   # discovered ONVIF device without valid credentials

# camera HTTP status -> (errorCode, errorMessage) for /status (utils.cpp:1163 mapping subset).
_HTTP_TO_ERROR = {
    200: ("NoError", "No Error"),
    401: ("CameraUnauthorizedError", "Camera is not authorized"),
    404: ("CameraNotFoundError", "Camera not found OR camera id is not valid"),
    408: ("DeviceRequestTimeoutError", "Request Timout"),
}


def http_status_to_error(code: int | None) -> tuple[str, str]:
    return _HTTP_TO_ERROR.get(code or 0, ("CommunicationError", "Camera communication error"))


# StreamType enum (device_manager.cpp:24) and StreamStorageType (sensor_info.h:382).
_STREAM_TYPE = {0: "Http", 1: "Hls", 2: "Rtsp", 3: "FileDownload", 4: "Udp",
                5: "Webrtc", 6: "Native", 7: "NotSupported"}
_STORAGE = {0: "Local", 1: "Cloud", 2: "Unknown"}


def _position_db_to_api(position_json: str | None) -> dict[str, Any]:
    """Parse the POSITION JSON column and rename snake_case keys to the API's camelCase."""
    empty = {
        "coordinates": {"x": "", "y": ""},
        "depth": "", "direction": "", "fieldOfView": "",
        "geoLocation": {"latitude": "", "longitude": ""},
        "origin": {"latitude": "", "longitude": ""},
    }
    if not position_json:
        return empty
    try:
        p = json.loads(position_json)
    except (json.JSONDecodeError, TypeError):
        return empty
    coords = p.get("coordinates", {}) or {}
    geo = p.get("geo_location", p.get("geoLocation", {})) or {}
    origin = p.get("origin", {}) or {}
    return {
        "coordinates": {"x": coords.get("x", ""), "y": coords.get("y", "")},
        "depth": p.get("depth", ""),
        "direction": p.get("direction", ""),
        "fieldOfView": p.get("field_of_view", p.get("fieldOfView", "")),
        "geoLocation": {"latitude": geo.get("latitude", ""), "longitude": geo.get("longitude", "")},
        "origin": {"latitude": origin.get("latitude", ""), "longitude": origin.get("longitude", "")},
    }


def position_api_to_db(position: dict[str, Any] | None) -> str:
    """Serialize an API position object (camelCase) to the POSITION JSON column (snake_case keys,
    alphabetically sorted, compact) so it round-trips through _position_db_to_api. Mirrors the C++
    setSensorInfo position persistence."""
    p = position or {}
    coords = p.get("coordinates", {}) or {}
    geo = p.get("geoLocation", p.get("geo_location", {})) or {}
    origin = p.get("origin", {}) or {}
    db = {
        "coordinates": {"x": coords.get("x", ""), "y": coords.get("y", "")},
        "depth": p.get("depth", ""),
        "direction": p.get("direction", ""),
        "field_of_view": p.get("fieldOfView", p.get("field_of_view", "")),
        "geo_location": {"latitude": geo.get("latitude", ""), "longitude": geo.get("longitude", "")},
        "origin": {"latitude": origin.get("latitude", ""), "longitude": origin.get("longitude", "")},
    }
    return json.dumps(db, sort_keys=True, separators=(",", ":"))


def _to_bool(v: str | None) -> bool:
    return str(v).strip().lower() == "true"


def sensor_row_to_info(row: SensorDetails, *, timeline_present: bool, include_list_fields: bool) -> dict[str, Any]:
    """Build the SensorInfo API object from a SENSOR_DETAILS row.

    include_list_fields adds the fields the C++ only populates on GET /sensor/list
    (state, type, isTimelinePresent); GET /sensor/{id}/info omits them.
    """
    info: dict[str, Any] = {
        "sensorId": row.sensor_id or "",
        "name": row.name or "",
        "sensorIp": row.ipaddress or "",
        "hardware": row.hardware or "",
        "manufacturer": row.manufacturer or "",
        "serialNumber": row.serial_number or "",
        "firmwareVersion": row.firmware_version or "",
        "hardwareId": row.hardware_id or "",
        "location": row.location or "",
        "tags": row.tags or "",
        "position": _position_db_to_api(row.position),
        "isRemoteSensor": _to_bool(row.is_remote),
        "remoteDeviceId": row.remote_device_id or "",
        "remoteDeviceName": row.remote_device_name or "",
        "remoteDeviceLocation": row.remote_device_location or "",
    }
    if include_list_fields:
        online = (row.http_status == CAMERA_NO_ERROR_CODE)
        info["state"] = "online" if online else "offline"
        info["type"] = row.type or ""
        info["isTimelinePresent"] = timeline_present
    return info


def stream_row_to_info(row: SensorStreams) -> dict[str, Any]:
    """Build the StreamInfo API object from a SENSOR_STREAMS row.

    Verified against live /sensor/{id}/streams: `url` is the served proxy URL (stream_proxy_url),
    `vodUrl` is stream_replay_url (NOT stream_live_url, which is the upstream camera URL).
    """
    return {
        "streamId": row.stream_id or "",
        "isMain": _to_bool(row.stream_ismainstream),
        "type": _STREAM_TYPE.get(row.stream_type, "NotSupported"),
        "storageLocation": _STORAGE.get(row.stream_storage_location, "Unknown"),
        "vodUrl": row.stream_replay_url or "",
        "url": row.stream_proxy_url or "",
        "name": row.stream_name or "",
        "metadata": {
            "bitrate": row.bitrate or "",
            "codec": row.stream_encoding or "",
            "framerate": row.stream_framerate or "",
            "govlength": row.stream_encoding_interval or "",
            "resolution": row.stream_resolution or "",
        },
    }

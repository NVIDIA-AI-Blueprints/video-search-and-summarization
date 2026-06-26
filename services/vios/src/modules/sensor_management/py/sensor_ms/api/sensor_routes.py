# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Sensor REST routes — full surface from doc/api/vst_sensor_management_ms/swagger.yaml.

Mounted under /api (full paths /api/v1/sensor/...). Mutating endpoints depend on `require_bearer`;
GETs are open. Handlers delegate to SensorManagement; until P2 lands they surface VmsError, which the
global handler renders as the snake_case error envelope with the mapped HTTP status.

Responses inherit the app default_response_class (text/plain JSON) — see main.py.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from . import schemas as s
from .auth import require_bearer

router = APIRouter(prefix="/v1/sensor", tags=["v1"])


def _mgmt(request: Request):
    return request.app.state.mgmt


# --- collection ---
@router.get("/list", response_model=list[s.SensorInfo])
async def get_sensor_list(request: Request) -> Any:
    return await _mgmt(request).list_sensors()


@router.post("/scan", dependencies=[Depends(require_bearer)])
async def post_sensor_scan(request: Request) -> Any:
    await _mgmt(request).scan(force=True)
    return {}


@router.get("/streams")
async def get_streams_all(request: Request) -> Any:
    # StreamInfoWrapper[]: each element is a single-entry map {sensorId: [StreamInfo]}.
    mgmt = _mgmt(request)
    out = []
    for s in await mgmt.list_sensors():
        sid = s["sensorId"]
        out.append({sid: await mgmt.list_streams(sid)})
    return out


@router.post("/add", response_model=s.SensorAddResponse, dependencies=[Depends(require_bearer)])
async def post_sensor_add(request: Request, body: s.SensorAddInfo) -> Any:
    sensor_id = await _mgmt(request).add_sensor(body.model_dump(exclude_none=True))
    return {"sensorId": sensor_id}


@router.get("/status", response_model=dict[str, s.SensorStatus])
async def get_all_status(request: Request) -> Any:
    return await _mgmt(request).all_status()


@router.get("/configuration")
async def get_configuration(request: Request) -> Any:
    return _mgmt(request).get_configuration()


@router.post("/configuration", dependencies=[Depends(require_bearer)])
async def post_configuration(request: Request, body: s.SetConfiguration) -> Any:
    # Apply deviceDiscoveryInterfaces / ntpServers; restart discovery if the interface set changed
    # (parity with C++ handleSensorConfiguration POST).
    await _mgmt(request).apply_configuration(body.model_dump(exclude_none=True))
    return {}


@router.get("/version", response_model=s.VersionResponse)
async def get_version(request: Request) -> Any:
    from .. import SERVICE_VERSION
    return {"type": _mgmt(request).device_type, "version": SERVICE_VERSION}


@router.get("/help", response_model=list[str])
async def get_help() -> Any:
    # Exact sorted list emitted by the C++ /sensor/help.
    return [
        "/api/v1/sensor/*", "/api/v1/sensor/add", "/api/v1/sensor/configuration",
        "/api/v1/sensor/help", "/api/v1/sensor/list", "/api/v1/sensor/qos",
        "/api/v1/sensor/scan", "/api/v1/sensor/status", "/api/v1/sensor/streams",
        "/api/v1/sensor/timelines", "/api/v1/sensor/version",
        "/v1/live", "/v1/ready", "/v1/startup",
    ]


@router.get("/debug/system/stats")
async def get_system_stats() -> Any:
    from ..core.system_stats import get_system_stats as _stats
    return _stats()


# --- debug plug/unplug (test hooks; gated by enableDebugApis, parity with C++ handleSensorDebugAPI) ---
@router.get("/debug/status")
async def get_debug_status(request: Request, ip: str = "") -> Any:
    # {"status": "unplug"} if the ip is currently blocked (simulated unplug), else {"status": "plug"}.
    return {"status": _mgmt(request).sensor_block_status(ip)}


@router.post("/debug/unplug", dependencies=[Depends(require_bearer)])
async def post_debug_unplug(request: Request, body: s.DebugIp) -> Any:
    return _mgmt(request).block_sensor(body.ip, "unplug")


@router.post("/debug/plug", dependencies=[Depends(require_bearer)])
async def post_debug_plug(request: Request, body: s.DebugIp) -> Any:
    return _mgmt(request).block_sensor(body.ip, "plug")


@router.get("/qos", deprecated=True)
async def get_qos() -> Any:
    # Deprecated: sensor-ms has no RTSP server; always null stats (swagger). Use proxy/debug/qos.
    return {"stats": None, "numActiveRtspConnections": "0", "rtspServerTxBitrate": "0"}


@router.get("/timelines", response_model=dict[str, list[s.TimelineEntry]])
async def get_all_timelines(request: Request) -> Any:
    return await _mgmt(request).get_all_timelines()


# --- per-sensor ---
@router.get("/{sensor_id}/info", response_model=s.SensorInfo, response_model_exclude_none=True)
async def get_sensor_info(request: Request, sensor_id: str) -> Any:
    # exclude_none drops state/type/isTimelinePresent (list-only fields) so /info matches the C++.
    return await _mgmt(request).get_sensor(sensor_id)


@router.post("/{sensor_id}/info", dependencies=[Depends(require_bearer)])
async def post_sensor_info(request: Request, sensor_id: str, body: s.PostSensorInfo) -> Any:
    # Update name (uniqueness-checked + truncated), position, location, tags, hardware metadata.
    return await _mgmt(request).set_sensor_info(sensor_id, body.model_dump(exclude_none=True))


@router.get("/{sensor_id}/status", response_model=s.SensorStatus)
async def get_sensor_status(request: Request, sensor_id: str) -> Any:
    return await _mgmt(request).sensor_status(sensor_id)


@router.delete("/{sensor_id}", dependencies=[Depends(require_bearer)])
async def delete_sensor(request: Request, sensor_id: str) -> Any:
    await _mgmt(request).delete_sensor(sensor_id)
    return True


@router.post("/{sensor_id}/replace", dependencies=[Depends(require_bearer)])
async def post_replace(request: Request, sensor_id: str, body: s.ReplaceRequest) -> Any:
    # Replace an offline sensor with another existing sensor, preserving the old sensor id so its
    # recordings/timelines reattach (parity with C++ replaceSensorId).
    await _mgmt(request).replace_sensor(sensor_id, body.model_dump(exclude_none=True))
    return True


@router.get("/{sensor_id}/streams", response_model=list[s.StreamInfo])
async def get_sensor_streams(request: Request, sensor_id: str) -> Any:
    return await _mgmt(request).sensor_streams(sensor_id)


@router.get("/{sensor_id}/settings")
async def get_settings(request: Request, sensor_id: str) -> Any:
    return await _mgmt(request).sensor_settings(sensor_id)


@router.post("/{sensor_id}/settings", dependencies=[Depends(require_bearer)])
async def post_settings(request: Request, sensor_id: str, body: dict) -> Any:
    # Apply ONVIF Image/Encode settings via the control adaptor (VMSNotSupportedError for non-ONVIF).
    return await _mgmt(request).set_sensor_settings(sensor_id, body)


@router.post("/{sensor_id}/credentials", dependencies=[Depends(require_bearer)])
async def post_credentials(request: Request, sensor_id: str, body: s.Credentials) -> Any:
    # Validates against the camera; wrong credentials -> InvalidParameterError (not 200).
    return await _mgmt(request).set_credentials(sensor_id, body.username, body.password)


@router.get("/{sensor_id}/network", response_model=s.NetworkInfo)
async def get_network(request: Request, sensor_id: str) -> Any:
    return await _mgmt(request).sensor_network(sensor_id)


@router.post("/{sensor_id}/network", response_model=s.NetworkSetResponse,
             dependencies=[Depends(require_bearer)])
async def post_network(request: Request, sensor_id: str, body: s.NetworkInfo) -> Any:
    return await _mgmt(request).set_sensor_network(sensor_id, body.model_dump(exclude_none=True))


@router.post("/{sensor_id}/reboot", dependencies=[Depends(require_bearer)])
async def post_reboot(request: Request, sensor_id: str) -> Any:
    await _mgmt(request).reboot_sensor(sensor_id)
    return {}


@router.get("/{sensor_id}/timelines", response_model=list[s.TimelineEntry])
async def get_sensor_timelines(
    request: Request, sensor_id: str, startTime: str | None = None, endTime: str | None = None
) -> Any:
    return await _mgmt(request).get_recording_timelines(sensor_id, startTime or "", endTime or "")

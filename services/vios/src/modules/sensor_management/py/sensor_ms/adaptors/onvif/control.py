# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""ONVIF control adaptor on onvif-zeep-async (MIT) — replaces the proprietary nvsoap path.

Maps ONVIF Media/Device/Imaging/PTZ services to the SensorControlAdaptor interface. The camera-facing
methods require a real ONVIF device; the response->model mapping is factored into pure functions
(profile_to_stream, device_info_to_fields) that ARE unit-tested with mock objects.

OUTSTANDING VALIDATION GATE (DESIGN.md §7, P3): the live camera matrix (discover, GetProfiles,
GetStreamUri, PTZ, digest auth) must be run against real hardware before cutover. Not reproducible in
this environment (no ONVIF camera).
"""
from __future__ import annotations

import logging
from typing import Any

from ..base import SensorControlAdaptor

log = logging.getLogger(__name__)

STREAM_TYPE_RTSP = "Rtsp"


# --- pure mapping helpers (unit-tested) ---
def profile_to_stream(profile: Any, stream_uri: str, is_main: bool) -> dict[str, Any]:
    """Map an ONVIF media Profile (+ resolved RTSP URI) to our StreamInfo API dict."""
    vec = getattr(profile, "VideoEncoderConfiguration", None)
    codec = (getattr(vec, "Encoding", "") or "") if vec else ""
    res = getattr(vec, "Resolution", None) if vec else None
    resolution = f"{res.Width}x{res.Height}" if res is not None else ""
    rc = getattr(vec, "RateControl", None) if vec else None
    framerate = str(getattr(rc, "FrameRateLimit", "") or "") if rc is not None else ""
    govlength = str(getattr(vec, "GovLength", "") or "") if vec else ""
    bitrate = str(getattr(rc, "BitrateLimit", "") or "") if rc is not None else ""
    token = getattr(profile, "token", "") or getattr(profile, "_token", "")
    return {
        "streamId": token,
        "isMain": is_main,
        "type": STREAM_TYPE_RTSP,
        "storageLocation": "Local",
        "url": stream_uri,            # ONVIF GetStreamUri result (live RTSP)
        "vodUrl": "",
        "name": getattr(profile, "Name", "") or "",
        "metadata": {
            "bitrate": bitrate, "codec": codec, "framerate": framerate,
            "govlength": govlength, "resolution": resolution,
        },
    }


def device_info_to_fields(dev_info: Any) -> dict[str, str]:
    """Map ONVIF GetDeviceInformation -> sensor hardware fields."""
    return {
        "hardware": getattr(dev_info, "HardwareId", "") or getattr(dev_info, "Model", "") or "",
        "manufacturer": getattr(dev_info, "Manufacturer", "") or "",
        "serialNumber": getattr(dev_info, "SerialNumber", "") or "",
        "firmwareVersion": getattr(dev_info, "FirmwareVersion", "") or "",
    }


class OnvifControl(SensorControlAdaptor):
    """ONVIF control via onvif-zeep-async. Lazily builds an ONVIFCamera per (host, creds)."""

    def __init__(self) -> None:
        super().__init__()
        self._cameras: dict[str, Any] = {}

    async def _camera(self, host: str, port: int, user: str, pw: str):
        key = f"{host}:{port}:{user}"
        cam = self._cameras.get(key)
        if cam is None:
            from onvif import ONVIFCamera  # lazy: only when an ONVIF device is actually used

            cam = ONVIFCamera(host, port, user, pw)
            await cam.update_xaddrs()
            self._cameras[key] = cam
        return cam

    async def connect(self) -> int:
        # Connection is per-sensor (cameras are independent); nothing global to do.
        return 0

    async def validate_credentials(self, sensor: dict[str, Any], username: str, password: str) -> bool:
        try:
            cam = await self._camera(sensor["ip"], int(sensor.get("port", 80)), username, password)
            dev = await cam.create_devicemgmt_service()
            await dev.GetDeviceInformation()
            return True
        except Exception as e:
            log.info("ONVIF credential validation failed for %s: %s", sensor.get("ip"), e)
            return False

    async def get_sensor_stream_info(self, sensor: dict[str, Any]) -> int:
        """Populate sensor['streams'] from ONVIF profiles + stream URIs. Returns 0 on success."""
        try:
            cam = await self._camera(sensor["ip"], int(sensor.get("port", 80)),
                                     sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            sensor.update(device_info_to_fields(await dev.GetDeviceInformation()))
            media = await cam.create_media_service()
            profiles = await media.GetProfiles()
            streams = []
            for i, prof in enumerate(profiles):
                token = getattr(prof, "token", "")
                uri_resp = await media.GetStreamUri(
                    {"StreamSetup": {"Stream": "RTP-Unicast",
                                     "Transport": {"Protocol": "RTSP"}}, "ProfileToken": token}
                )
                uri = getattr(uri_resp, "Uri", "") or ""
                streams.append(profile_to_stream(prof, uri, is_main=(i == 0)))
            sensor["streams"] = streams
            return 0
        except Exception as e:
            log.error("ONVIF get_sensor_stream_info failed for %s: %s", sensor.get("ip"), e)
            return -1

    # The remaining camera-facing operations follow the same pattern (create service -> call ->
    # map). They require a real device for validation (P3 gate) and are scaffolded here.
    async def reboot_sensor(self, sensor: dict[str, Any]) -> int:
        try:
            cam = await self._camera(sensor["ip"], int(sensor.get("port", 80)),
                                     sensor.get("user", ""), sensor.get("password", ""))
            dev = await cam.create_devicemgmt_service()
            await dev.SystemReboot()
            return 0
        except Exception as e:
            log.error("ONVIF reboot failed for %s: %s", sensor.get("ip"), e)
            return -1

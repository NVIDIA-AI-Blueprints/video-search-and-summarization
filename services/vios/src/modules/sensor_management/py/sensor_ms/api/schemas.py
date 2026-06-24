# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Pydantic models mirroring doc/api/vst_sensor_management_ms/swagger.yaml.

Field names match the swagger exactly (these are the wire contract). String fields that the
swagger types as `string` stay `str` even when they carry numeric values (e.g. metadata.bitrate),
because the C++ service serializes them as strings.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    # Preserve unknown fields on input rather than rejecting (forward-compat with C++ additions).
    model_config = ConfigDict(extra="allow")


# --- enums (swagger) ---
class SensorState(str, Enum):
    online = "online"
    offline = "offline"
    removed = "removed"


class SensorType(str, Enum):
    sensor_rtsp = "sensor_rtsp"
    sensor_onvif = "sensor_onvif"
    sensor_streamer = "sensor_streamer"
    sensor_mms = "sensor_mms"


class StreamType(str, Enum):
    Http = "Http"
    Hls = "Hls"
    Rtsp = "Rtsp"
    FileDownload = "FileDownload"
    Udp = "Udp"
    Webrtc = "Webrtc"
    Native = "Native"
    NotSupported = "NotSupported"


class StorageLocation(str, Enum):
    Local = "Local"
    Cloud = "Cloud"
    Unknown = "Unknown"


# --- position ---
class Coordinates(_Model):
    x: str | None = None
    y: str | None = None


class GeoLocation(_Model):
    latitude: str | None = None
    longitude: str | None = None


class Origin(_Model):
    latitude: str | None = None
    longitude: str | None = None


class Position(_Model):
    depth: str | None = None
    direction: str | None = None
    fieldOfView: str | None = None
    coordinates: Coordinates | None = None
    geoLocation: GeoLocation | None = None
    origin: Origin | None = None


# --- sensor info ---
class SensorInfo(_Model):
    firmwareVersion: str | None = None
    hardware: str | None = None
    hardwareId: str | None = None
    sensorId: str | None = None
    sensorIp: str | None = None
    location: str | None = None
    manufacturer: str | None = None
    name: str | None = None
    position: Position | None = None
    serialNumber: str | None = None
    # state/type kept as free str (not the swagger enum): the system uses more sensor types than the
    # swagger lists (sensor_file, sensor_csi, sensor_udp, ...). Enum-constraining the RESPONSE caused
    # ResponseValidationError -> 500 on /list when other services wrote e.g. sensor_file rows. The
    # C++ returns the raw string; match that.
    state: str | None = None                    # only on GET /sensor/list
    tags: str | None = None
    type: str | None = None                     # only on GET /sensor/list
    isTimelinePresent: bool | None = None        # only on GET /sensor/list
    isRemoteSensor: bool | None = None
    remoteDeviceId: str | None = None
    remoteDeviceLocation: str | None = None
    remoteDeviceName: str | None = None


class PostSensorInfo(_Model):
    firmwareVersion: str | None = None
    hardware: str | None = None
    hardwareId: str | None = None
    sensorId: str | None = None
    sensorIp: str | None = None
    location: str | None = None
    manufacturer: str | None = None
    name: str | None = None
    position: Position | None = None
    serialNumber: str | None = None
    tags: str | None = None


# --- streams ---
class StreamMetadata(_Model):
    bitrate: str | None = None
    codec: str | None = None
    framerate: str | None = None
    govlength: str | None = None
    resolution: str | None = None


class StreamInfo(_Model):
    streamId: str | None = None
    isMain: bool | None = None
    # str (not enum) for the same reason as SensorInfo.type: avoid ResponseValidationError 500s on
    # values outside the swagger enum. mapping.py still produces the documented strings.
    type: str | None = None
    storageLocation: str | None = None
    vodUrl: str | None = None
    url: str | None = None
    name: str | None = None
    metadata: StreamMetadata | None = None


# --- add sensor (oneOf: IP+creds | RTSP-url+creds) ---
class SensorAddInfo(_Model):
    sensorIp: str | None = None
    sensorUrl: str | None = None
    # Optional: anonymous RTSP cameras are added without credentials (verified vs C++).
    username: str | None = None
    password: str | None = None
    name: str | None = None
    location: str | None = None
    hardware: str | None = None
    manufacturer: str | None = None
    serialNumber: str | None = None
    firmwareVersion: str | None = None
    hardwareId: str | None = None
    tags: str | None = None
    verifyRtsp: bool = False


class SensorAddResponse(_Model):
    sensorId: str


# --- status ---
class SensorStatus(_Model):
    errorCode: str
    errorMessage: str
    state: SensorState | None = None
    name: str | None = None                     # absent when CameraNotFoundError


# --- credentials / replace / network ---
class Credentials(_Model):
    username: str = Field(max_length=128)
    password: str = Field(max_length=128)


class ReplaceRequest(_Model):
    sensorId: str | None = None
    deviceid: str | None = None                 # legacy alias


class NetworkInfo(_Model):
    dhcpV4: str | None = None
    dhcpV6: str | None = None
    ipAddressV4: str | None = None
    ipAddressV6: str | None = None
    isIpv4Enabled: bool | None = None
    isIpv6Enabled: bool | None = None
    subnetMaskV4: str | None = None
    subnetMaskV6: str | None = None


class NetworkSetResponse(_Model):
    rebootNeeded: bool


# --- settings (get/set) ---
class RangeSetting(_Model):
    Min: str
    Max: str
    Value: str


class EnumSetting(_Model):
    AllowedValues: list[str]
    Value: str


# --- configuration ---
class SetConfiguration(_Model):
    deviceDiscoveryInterfaces: list[str] | None = None
    ntpServers: list[str] | None = None


# --- debug (test hooks) ---
class DebugIp(_Model):
    ip: str = ""


class VersionResponse(_Model):
    type: str
    version: str


# --- timelines ---
class TimelineEntry(_Model):
    startTime: str | None = None
    endTime: str | None = None


# --- error envelope (snake_case) ---
class Error(_Model):
    error_code: str
    error_message: str

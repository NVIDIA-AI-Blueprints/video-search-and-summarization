# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Adaptor ABCs — Python equivalent of the C++ dlopen vtable plugins.

Mirrors include/sensor_control_adaptor.h (ISensorControlInterface) and
include/sensor_discovery_adaptor.h (ISensorDiscoveryInterface / ISensorDiscoveryEvent).
Default return values match the C++ defaults (DESIGN.md §6.6): connect/setters return 0,
unimplemented capability returns -1, validateCredentials returns False, deleteSensor True.

Concrete adaptors (onvif, rtsp_streams, milestone, remote_device, native, streamer) subclass these
and are loaded by loader.py via importlib (no .so).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Any


@dataclass
class AdaptorInfo:
    m_id: str = ""
    m_name: str = ""
    m_type: str = ""          # "vst" | "mms" | "streamer" | "event"
    m_user: str = ""
    m_password: str = ""
    m_port: str = ""
    m_ipaddress: str = ""
    m_url: str = ""


class SensorDiscoveryEvent(abc.ABC):
    """Listener interface (ISensorDiscoveryEvent). SensorMonitoring implements this."""

    @abc.abstractmethod
    async def on_sensor_found(self, sensor: dict[str, Any]) -> int: ...

    @abc.abstractmethod
    async def on_sensor_changed(self, sensor: dict[str, Any]) -> int: ...

    @abc.abstractmethod
    async def on_sensor_removed(self, sensor_id: str) -> int: ...

    async def notify_event(self, status: dict[str, Any], url: str) -> None:
        return None

    async def refresh_sensor_list(self) -> None:
        return None


class SensorControlAdaptor(abc.ABC):
    """ISensorControlInterface. Async (SOAP/HTTP I/O must not block the event loop)."""

    def __init__(self) -> None:
        self.info = AdaptorInfo()
        self._cache: list[dict[str, Any]] = []

    @abc.abstractmethod
    async def connect(self) -> int: ...

    @abc.abstractmethod
    async def get_sensor_stream_info(self, sensor: dict[str, Any]) -> int: ...

    async def synchronize_sensor_time(self, sensor: dict[str, Any]) -> int:
        return -1

    async def get_sensor_status(self, camera_id: str) -> tuple[int, dict[str, Any]]:
        return -1, {}

    async def reboot_sensor(self, sensor: dict[str, Any]) -> int:
        return -1

    async def is_server_online(self, url: str) -> bool:
        return False

    async def get_sensor_image_settings(self, sensor: dict[str, Any], stream_id: str) -> int:
        return -1

    async def set_sensor_image_settings(self, sensor: dict[str, Any], settings: dict[str, Any]) -> int:
        return -1

    async def get_network_info(self, sensor: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return -1, {}

    async def set_network_info(self, sensor: dict[str, Any], net: dict[str, Any]) -> tuple[int, bool]:
        return -1, False

    async def get_sensor_encode_settings(self, sensor: dict[str, Any], stream_id: str) -> int:
        return -1

    async def set_sensor_encode_settings(self, sensor: dict[str, Any], settings: dict[str, Any]) -> int:
        return -1

    async def get_settings(self, sensor: dict[str, Any], type_: str = "") -> tuple[int, dict[str, Any]]:
        """High-level GET /settings: return (0, {streamId: {Image, Encode}}) or (-1, {}). type_ in
        {"", "Image", "Encode"} selects which block(s) to include."""
        return -1, {}

    async def set_settings(self, sensor: dict[str, Any], settings: dict[str, Any]) -> int:
        """High-level POST /settings: apply Image/Encode settings. Returns 0 on success, -1 on error."""
        return -1

    async def set_ptz(self, sensor: dict[str, Any], action: str, x: str, y: str) -> int:
        return 0

    async def get_ptz(self, sensor: dict[str, Any]) -> dict[str, Any]:
        return {}

    async def validate_credentials(self, sensor: dict[str, Any], username: str, password: str) -> bool:
        return False

    async def add_sensor(self, sensor_info: dict[str, Any]) -> str:
        return "NoError"

    async def delete_sensor(self, sensor: dict[str, Any]) -> bool:
        return True

    async def get_recording_timelines(self, sensor: dict[str, Any]) -> tuple[int, list[dict[str, Any]]]:
        return -1, []

    def set_cache(self, sensors: list[dict[str, Any]]) -> None:
        self._cache = sensors

    def get_cache(self) -> list[dict[str, Any]]:
        return self._cache


class SensorDiscoveryAdaptor(abc.ABC):
    """ISensorDiscoveryInterface + listener registry (publish_* fan-out)."""

    def __init__(self) -> None:
        self._listeners: list[SensorDiscoveryEvent] = []
        self._cache: list[dict[str, Any]] = []

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    async def search_sensor(self, sensor: dict[str, Any]) -> int:
        return -1

    def register_listener(self, listener: SensorDiscoveryEvent) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    def deregister_listener(self, listener: SensorDiscoveryEvent) -> None:
        if listener in self._listeners:
            self._listeners.remove(listener)

    async def publish_on_sensor_found(self, sensor: dict[str, Any]) -> None:
        for lst in self._listeners:
            await lst.on_sensor_found(sensor)

    async def publish_on_sensor_changed(self, sensor: dict[str, Any]) -> None:
        for lst in self._listeners:
            await lst.on_sensor_changed(sensor)

    async def publish_on_sensor_removed(self, sensor_id: str) -> None:
        for lst in self._listeners:
            await lst.on_sensor_removed(sensor_id)

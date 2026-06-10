# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""SensorManagement orchestrator (Python port of src/modules/sensor_management).

P1/P2: read path (list/get/streams) + write path (add/delete) against the shared DB, emitting the
verified notification events. ONVIF discovery/control + camera_proxy stream resolution are P3.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import logging
import os

from ..adaptors.loader import load_adaptor, load_control_adaptor
from ..adaptors.rtsp_preflight import rtsp_describe
from ..api.errors import VmsError, VmsErrorCode
from ..config import Config
from ..db.models import SensorDetails, SensorStreams
from ..events.publisher import ChangeEvent, EventPublisher, build_payload
from . import mapping
from .device_manager import DeviceManager

log = logging.getLogger(__name__)

SENSOR_TYPE_RTSP = "sensor_rtsp"
SENSOR_TYPE_ONVIF = "sensor_onvif"
ONVIF_ADAPTOR = "onvif"
STREAM_TYPE_RTSP = 2
DEFAULT_ENCODING = "h264"
ONVIF_DEFAULT_PORT = 80
MAX_SENSOR_NAME_LENGTH = 175  # sensor_info.h:50; C++ truncates the name on add
EMPTY_POSITION = (
    '{"coordinates":{"x":"","y":""},"depth":"","direction":"","field_of_view":"",'
    '"geo_location":{"latitude":"","longitude":""},"origin":{"latitude":"","longitude":""}}'
)


def _embed_creds(url: str, user: str, pw: str) -> str:
    """Embed user:pass@ into an RTSP url (matches the credentialed live_url the C++ stores/emits)."""
    if not url or not user:
        return url
    p = urlparse(url)
    if p.username:  # already has credentials
        return url
    netloc = f"{user}:{pw}@{p.hostname or ''}"
    if p.port:
        netloc += f":{p.port}"
    return p._replace(netloc=netloc).geturl()


def _now_iso() -> str:
    # ISO8601 UTC, second precision, Z suffix — matches getCurrentUtcTime() / event created_at.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class SensorManagement:
    def __init__(self, cfg: Config):
        self._cfg = cfg
        self._dm = DeviceManager(cfg)
        self._events = EventPublisher(cfg)
        # Select the active adaptor from configs/adaptor_config.json ($ADAPTOR by name, else first
        # enabled entry), matching the C++ AdaptorLoader. Fall back to the ADAPTOR env name only if
        # the config file is absent/unreadable. control is None for url-only adaptors (e.g. vst_rtsp).
        self._adaptor_name, self._control = self._select_adaptor(cfg)
        log.info("sensor adaptor: %s (control=%s)", self._adaptor_name,
                 type(self._control).__name__ if self._control else "none")

    @staticmethod
    def _select_adaptor(cfg: Config):
        path = cfg.adaptor_config_path
        if path and os.path.isfile(path):
            try:
                loaded = load_adaptor(path)
                return loaded.info.m_name, loaded.control
            except Exception as e:
                log.error("failed to load %s: %s; falling back to ADAPTOR=%s", path, e, cfg.adaptor)
        else:
            log.warning("adaptor_config not found at %s; using ADAPTOR=%s", path, cfg.adaptor)
        return cfg.adaptor, load_control_adaptor(cfg.adaptor)

    @property
    def repo(self):
        return self._dm.repo

    async def start(self) -> None:
        # Create the sensor schema if missing so a fresh standalone DB works (best-effort: if the DB
        # is unreachable, log and continue; per-request handlers then surface VMSInternalError).
        try:
            from ..db.engine import init_schema
            init_schema(self._dm.engine)
        except Exception as e:
            log.warning("schema init skipped: %s", e)
        return None

    async def stop(self) -> None:
        self._dm.dispose()

    # --- reads ---
    async def list_sensors(self) -> list[dict[str, Any]]:
        rows = self.repo.list_sensors()
        return [
            mapping.sensor_row_to_info(
                r, timeline_present=self.repo.timeline_present(r.sensor_id), include_list_fields=True
            )
            for r in rows
        ]

    async def get_sensor(self, sensor_id: str) -> dict[str, Any]:
        row = self.repo.get_sensor(sensor_id)
        if row is None:
            raise VmsError(VmsErrorCode.CameraNotFoundError)
        return mapping.sensor_row_to_info(row, timeline_present=False, include_list_fields=False)

    async def list_streams(self, sensor_id: str) -> list[dict[str, Any]]:
        return [mapping.stream_row_to_info(s) for s in self.repo.list_streams(sensor_id)]

    async def all_status(self) -> dict[str, dict[str, Any]]:
        out: dict[str, dict[str, Any]] = {}
        for r in self.repo.list_sensors():
            online = r.http_status == mapping.CAMERA_NO_ERROR_CODE
            out[r.sensor_id] = {
                "name": r.name or "",
                "errorCode": "NoError" if online else "CameraNotFoundError",
                "errorMessage": "No Error" if online else "Camera not found OR camera id is not valid",
                "state": "online" if online else "offline",
            }
        return out

    # --- writes ---
    async def add_sensor(self, body: dict[str, Any]) -> str:
        url = body.get("sensorUrl", "")
        ip = body.get("sensorIp", "")
        if not url and not ip:
            raise VmsError(VmsErrorCode.InvalidParameterError, "sensorUrl or sensorIp required")

        # Truncate the name to the max length (C++ truncateString(name, MAX_SENSOR_NAME_LENGTH)).
        name = (body.get("name") or "")[:MAX_SENSOR_NAME_LENGTH]
        body = {**body, "name": name}
        # Duplicate detection (parity with C++ addSensor). URL/IP match (any name) -> "Sensor exists
        # already"; otherwise a name-only collision -> "User given name is invalid or already exists".
        existing = self.repo.list_sensors()
        for e in existing:
            if (url and (e.url or "") == url) or (ip and (e.ipaddress or "") == ip):
                raise VmsError(VmsErrorCode.InvalidParameterError, "Sensor exists already")
        for e in existing:
            if name and (e.name or "") == name:
                raise VmsError(VmsErrorCode.InvalidParameterError,
                               "User given name is invalid or already exists")
        # Opt-in RTSP DESCRIBE pre-flight (verifyRtsp). Reject unreachable/auth-failed URLs before
        # persisting (testRtspUrl parity). Off by default to preserve legacy behaviour.
        probe_codec = ""
        if url and body.get("verifyRtsp"):
            probe = await rtsp_describe(url, body.get("username", ""), body.get("password", ""))
            if not probe.ok:
                raise VmsError(VmsErrorCode.InvalidParameterError,
                               f"RTSP DESCRIBE failed for the supplied sensorUrl ({probe.reason})")
            probe_codec = probe.codec

        # IP-based add under the ONVIF adaptor: connect to the device, validate, and pull device
        # info + stream profiles before persisting. Without a reachable ONVIF camera this fails
        # cleanly with CommunicationError (no partial row is written).
        sensor_type = SENSOR_TYPE_RTSP
        onvif_dev: dict[str, Any] = {}
        onvif_streams: list[dict[str, Any]] = []
        if ip and not url and self._adaptor_name == ONVIF_ADAPTOR:
            if self._control is None:
                raise VmsError(VmsErrorCode.VMSInternalError, "ONVIF adaptor not loaded")
            probe_sensor: dict[str, Any] = {
                "ip": ip, "port": int(body.get("port", ONVIF_DEFAULT_PORT)),
                "user": body.get("username", ""), "password": body.get("password", ""), "streams": [],
            }
            if await self._control.get_sensor_stream_info(probe_sensor) != 0:
                raise VmsError(VmsErrorCode.CommunicationError,
                               "ONVIF connection or profile retrieval failed for the supplied sensorIp")
            onvif_dev = probe_sensor
            onvif_streams = probe_sensor.get("streams") or []
            sensor_type = SENSOR_TYPE_ONVIF

        sensor_id = str(uuid.uuid4())
        now = _now_iso()
        row = SensorDetails(
            device_id=self._cfg.device_name or "",
            sensor_id=sensor_id,
            sensor_hw_id=sensor_id,
            username=body.get("username", ""),
            name=body.get("name", "") or sensor_id,
            ipaddress=ip,
            hardware=body.get("hardware", "") or onvif_dev.get("hardware", ""),
            manufacturer=body.get("manufacturer", "") or onvif_dev.get("manufacturer", ""),
            serial_number=body.get("serialNumber", "") or onvif_dev.get("serialNumber", ""),
            firmware_version=body.get("firmwareVersion", "") or onvif_dev.get("firmwareVersion", ""),
            hardware_id=body.get("hardwareId", ""),
            location=body.get("location", ""),
            tags=body.get("tags", ""),
            url=url,
            type=sensor_type,
            position=EMPTY_POSITION,
            is_remote="false",
            http_status=mapping.CAMERA_NO_ERROR_CODE,
            sensor_status=1,
            created_date_time=now,
            modified_date_time=now,
        )
        self.repo.insert_sensor(row, body.get("password", ""), now)
        await self._events.publish(build_payload(
            change=ChangeEvent.camera_add, camera_id=sensor_id,
            camera_name=row.name, camera_url="", tags=row.tags or "",
            created_at=now, metadata=None,
        ))

        # For an RTSP-url sensor, create the main stream (stream_id == sensor_id) and emit
        # camera_proxy. The proxy_url is left empty; the RTSP-server MS fills it after proxying.
        # (IP/ONVIF sensors resolve streams via discovery — P4.)
        if url:
            codec = body.get("encoding") or probe_codec or DEFAULT_ENCODING
            framerate = str(body.get("framerate", "") or "")
            w, h = body.get("width", ""), body.get("height", "")
            resolution = f"{w}x{h}" if w and h else ""
            live_url = _embed_creds(url, body.get("username", ""), body.get("password", ""))
            self.repo.insert_stream(SensorStreams(
                sensor_id=sensor_id, stream_id=sensor_id, stream_live_url=live_url,
                stream_proxy_url="", stream_replay_url="", stream_encoding=codec,
                stream_framerate=framerate, stream_resolution=resolution,
                stream_ismainstream="true", stream_type=STREAM_TYPE_RTSP,
                stream_storage_location=0, stream_name=row.name,
                created_date_time=now, modified_date_time=now,
            ), now)
            await self._events.publish(build_payload(
                change=ChangeEvent.camera_proxy, camera_id=sensor_id,
                camera_name=row.name, camera_url=live_url, tags=row.tags or "", created_at=now,
                metadata={"codec": codec, "framerate": framerate, "resolution": resolution},
            ))
        elif onvif_streams:
            # Persist ONVIF-discovered profiles as streams and emit camera_proxy per main stream.
            for i, s in enumerate(onvif_streams):
                md = s.get("metadata", {})
                self.repo.insert_stream(SensorStreams(
                    sensor_id=sensor_id, stream_id=f"{sensor_id}_{i}", stream_live_url=s.get("url", ""),
                    stream_proxy_url="", stream_replay_url="", stream_encoding=md.get("codec", ""),
                    stream_framerate=md.get("framerate", ""), stream_resolution=md.get("resolution", ""),
                    stream_ismainstream="true" if s.get("isMain") else "false",
                    stream_type=STREAM_TYPE_RTSP, stream_storage_location=0, stream_name=row.name,
                    created_date_time=now, modified_date_time=now,
                ), now)
                if s.get("isMain"):
                    await self._events.publish(build_payload(
                        change=ChangeEvent.camera_proxy, camera_id=sensor_id, camera_name=row.name,
                        camera_url=s.get("url", ""), tags=row.tags or "", created_at=now, metadata=md,
                    ))
        return sensor_id

    async def delete_sensor(self, sensor_id: str) -> None:
        row = self.repo.get_sensor(sensor_id)
        if row is None:
            raise VmsError(VmsErrorCode.CameraNotFoundError)
        name, tags = row.name or "", row.tags or ""
        if not self.repo.delete_sensor(sensor_id):
            raise VmsError(VmsErrorCode.CameraNotFoundError)
        await self._events.publish(build_payload(
            change=ChangeEvent.camera_remove, camera_id=sensor_id,
            camera_name=name, camera_url="", tags=tags, created_at=_now_iso(), metadata=None,
        ))

    def get_configuration(self) -> dict[str, Any]:
        """Assemble the GetConfiguration response (swagger) from the loaded config."""
        c = self._cfg
        return {
            "deviceDiscoveryFrequencySeconds": c.sensor_discovery_freq_secs,
            "deviceDiscoveryInterfaces": c.sensor_discovery_interfaces,
            "deviceDiscoveryTimeoutSeconds": c.sensor_discovery_timeout,
            "httpPort": str(c.http_port),
            "useMessageBroker": c.use_message_broker,
            "kafkaServerAddress": c.kafka_server_address,
            "redisServerEnvironmentVariable": c.redis_server_env_var,
            "messageBrokerTopic": c.message_broker_topic,
            "messageBrokerMetadataTopic": c.message_broker_metadata_topic,
            "message_broker_payload_key": c.message_broker_payload_key,
            "mqttBrokerAddress": c.mqtt_broker_address,
            "maxSensorsSupported": c.max_sensors_supported,
            "ntpServers": c.ntp_servers,
            "use_sensor_ntp_time": c.use_sensor_ntp_time,
            "onvifRequestTimeoutSeconds": c.onvif_request_timeout_secs,
            "enableNotification": c.enable_notification,
            "enablePrometheus": False,
            "prometheusPort": "0",
            "enableDebugApis": True,
            "supportedVideoCodecs": "h264,h265",
            "supportedAudioCodecs": "",
            "useHttpDigestAuthentication": False,
            "useHttps": False,
            "vstDataPath": c.vst_data_path,
            "webserviceAccessControlList": "",
            "enableUserCleanup": False,
            "multiUserExtraOptions": "",
            "useMultiUser": c.use_multi_user,
            "vstIp": c.centralize_remote_db_hostaddr,
            "remoteVstAddress": c.remote_vst_address,
            "adaptor": self._adaptor_name,
            "deviceName": c.device_name,
            "deviceLocation": c.device_location,
            "defaultProfile": c.default_profile,
            "defaultResolution": c.default_resolution,
            "defaultGovLength": c.default_gov_length,
            "defaultFramerate": c.default_framerate,
            "defaultBitrateKbps": c.default_bitrate,
        }

    async def scan(self, force: bool = True) -> int:
        """Trigger sensor discovery. For the ONVIF adaptor, run a WS-Discovery probe and report how
        many NVT devices responded. Other adaptors (e.g. vst_rtsp) have no discovery and no-op.
        Returns the number of devices found."""
        if self._adaptor_name != ONVIF_ADAPTOR:
            return 0
        from ..adaptors.onvif.discovery import discover

        try:
            matches = await discover(message_id=str(uuid.uuid4()),
                                     timeout=float(self._cfg.sensor_discovery_timeout))
        except Exception as e:
            log.warning("WS-Discovery probe failed: %s", e)
            return 0
        log.info("WS-Discovery found %d ONVIF device(s)", len(matches))
        # TODO(P4): for each match, fetch device info via OnvifControl and persist as a sensor.
        return len(matches)

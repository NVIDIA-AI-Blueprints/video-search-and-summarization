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

import asyncio
import logging
import os

from ..adaptors.loader import load_adaptor, load_control_adaptor
from ..adaptors.nvstreamer import fetch_streams
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
# Adaptor names whose discovery polls nvstreamer for RTSP streams (vst_rtsp parity).
RTSP_ADAPTORS = frozenset({"vst_rtsp", "rtsp", "streamer", "nvstream"})
STREAM_TYPE_RTSP = 2
DEFAULT_ENCODING = "h264"
ONVIF_DEFAULT_PORT = 80
MAX_SENSOR_NAME_LENGTH = 175  # sensor_info.h:50; C++ truncates the name on add
# WS-Discovery is multicast UDP: a device may miss a probe round, so the per-scan device count
# fluctuates. Only treat a known device as gone after this many consecutive scans without a reply,
# to avoid false "removed" logs from transient packet loss.
ONVIF_DISCOVERY_MISS_THRESHOLD = 3
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
        self._discovery_task: asyncio.Task | None = None
        # Consecutive missed-discovery counts per ONVIF sensor id, for debounced removal logging.
        self._onvif_misses: dict[str, int] = {}
        log.info("sensor adaptor: %s (control=%s, nvstreamer=%s)", self._adaptor_name,
                 type(self._control).__name__ if self._control else "none", cfg.nvstreamer_endpoints)

    @staticmethod
    def _select_adaptor(cfg: Config):
        """Pick the active adaptor name. Precedence: $ADAPTOR env (explicit switch, authoritative) ->
        first enabled entry in adaptor_config.json -> cfg.adaptor default. Control object loaded from
        the registry by name (None for url-only adaptors like vst_rtsp)."""
        name = os.environ.get("ADAPTOR", "").strip()
        if not name:
            path = cfg.adaptor_config_path
            if path and os.path.isfile(path):
                try:
                    name = load_adaptor(path).info.m_name
                except Exception as e:
                    log.error("failed to load %s: %s; using ADAPTOR/default", path, e)
            else:
                log.warning("adaptor_config not found at %s; using default", path)
        name = name or cfg.adaptor
        return name, load_control_adaptor(name)

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
        # Initial discovery, then a periodic background loop (parity with the C++ discovery threads)
        # so deleted sources reappear within sensor_discovery_freq_secs.
        try:
            await self.scan(force=False)
        except Exception as e:
            log.warning("initial discovery failed: %s", e)
        # Re-announce camera_add + camera_proxy for every already-online sensor so a freshly
        # (re)started RTSP-server / stream-processor rebuilds its proxies. Mirrors C++ start() +
        # getSensorInfo(rescan=true) -> getAndAddProxyUrl. Without this, restarting the
        # stream-processor never re-delivers the credentialed proxy URL and playback fails.
        try:
            await self._reannounce_online_sensors()
        except Exception as e:
            log.warning("startup re-announce failed: %s", e)
        self._discovery_task = asyncio.create_task(self._periodic_discovery())

    async def _reannounce_online_sensors(self) -> None:
        """Republish camera_add + camera_proxy (creds embedded) for each sensor that has a resolved
        main stream, so downstream RTSP/WebRTC proxies are rebuilt after a restart. Uncredentialed
        ONVIF sensors (no main stream yet) are skipped, matching the C++ online-only re-announce."""
        for row in self.repo.list_sensors():
            streams = self.repo.list_streams(row.sensor_id)
            main = next((st for st in streams
                         if (st.stream_ismainstream or "").lower() == "true"), None)
            if main is None:
                continue
            now = _now_iso()
            await self._events.publish(build_payload(
                change=ChangeEvent.camera_add, camera_id=row.sensor_id, camera_name=row.name,
                camera_url="", tags=row.tags or "", created_at=now, metadata=None))
            # Embed creds (idempotent: no-op if already present or non-credentialed) so the proxy
            # DESCRIBE authenticates -- same as getAndAddProxyUrl.
            url = _embed_creds(main.stream_live_url or "", row.username or "",
                               self.repo.get_password(row.sensor_id) or "")
            await self._events.publish(build_payload(
                change=ChangeEvent.camera_proxy, camera_id=row.sensor_id, camera_name=row.name,
                camera_url=url, tags=row.tags or "", created_at=now,
                metadata={"codec": main.stream_encoding or "", "framerate": main.stream_framerate or "",
                          "resolution": main.stream_resolution or ""}))
            log.info("re-announced camera_proxy for online sensor %s", row.sensor_id)

    async def _periodic_discovery(self) -> None:
        interval = max(5, int(self._cfg.sensor_discovery_freq_secs))
        while True:
            await asyncio.sleep(interval)
            try:
                await self.scan(force=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("periodic discovery error: %s", e)

    async def stop(self) -> None:
        if self._discovery_task is not None:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except (asyncio.CancelledError, Exception):
                pass
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

    @staticmethod
    def _onvif_unauthorized(row) -> bool:
        """An ONVIF sensor with no stored credentials cannot be queried -> CameraUnauthorizedError,
        matching the C++ which contacts the camera and gets 401 for per-sensor live endpoints."""
        return row is not None and row.type == SENSOR_TYPE_ONVIF and not (row.username or "")

    def _require_sensor(self, sensor_id: str):
        row = self.repo.get_sensor(sensor_id)
        if row is None:
            raise VmsError(VmsErrorCode.CameraNotFoundError)
        if self._onvif_unauthorized(row):
            raise VmsError(VmsErrorCode.CameraUnauthorizedError)
        return row

    async def sensor_streams(self, sensor_id: str) -> list[dict[str, Any]]:
        self._require_sensor(sensor_id)            # CameraUnauthorizedError for uncredentialed onvif
        return await self.list_streams(sensor_id)

    async def sensor_status(self, sensor_id: str) -> dict[str, Any]:
        row = self._require_sensor(sensor_id)
        ec, em = mapping.http_status_to_error(row.http_status)
        return {"name": row.name or "", "errorCode": ec, "errorMessage": em,
                "state": "online" if (row.sensor_status or 0) != 0 else "offline"}

    async def sensor_network(self, sensor_id: str) -> dict[str, Any]:
        self._require_sensor(sensor_id)
        return {}   # non-onvif/authorized: no network info available from sensor-py

    async def sensor_settings(self, sensor_id: str):
        self._require_sensor(sensor_id)
        return None   # non-onvif: null (per swagger, settings is null for non-ONVIF sources)

    async def get_recording_timelines(self, sensor_id: str, start: str = "", end: str = "") -> list[dict[str, Any]]:
        """Merged recording ranges for a sensor from video_record_details (vst_common parity).
        Optional ISO start/end query window. Records are keyed by stream_id (== sensor_id for our
        rtsp/onvif registrations)."""
        from .timelines import build_timeline, iso_to_epoch_ms

        start_ms = iso_to_epoch_ms(start)
        end_ms = iso_to_epoch_ms(end) or None
        rows = self.repo.read_video_records(sensor_id, start_ms, end_ms)
        return build_timeline(rows)

    async def get_all_timelines(self) -> dict[str, list[dict[str, Any]]]:
        """Map of streamId -> timeline ranges for every recorded stream in video_record_details
        (not just current sensors), matching the C++ GetAllRecordTimelines."""
        out: dict[str, list[dict[str, Any]]] = {}
        for sid in self.repo.list_recorded_stream_ids():
            tl = await self.get_recording_timelines(sid)
            if tl:
                out[sid] = tl
        return out

    async def all_status(self) -> dict[str, dict[str, Any]]:
        # Bulk /status reads cache: errorCode from http_status, state from sensor_status (so an
        # uncredentialed onvif shows errorCode=CameraUnauthorizedError with state=online, per C++).
        out: dict[str, dict[str, Any]] = {}
        for r in self.repo.list_sensors():
            ec, em = mapping.http_status_to_error(r.http_status)
            out[r.sensor_id] = {
                "name": r.name or "", "errorCode": ec, "errorMessage": em,
                "state": "online" if (r.sensor_status or 0) != 0 else "offline",
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
            # Stream-id scheme matches the C++ onvif_client: main == sensor_id, sub == sensor_id-token.
            ouser, opass = body.get("username", ""), body.get("password", "")
            for s in onvif_streams:
                md = s.get("metadata", {})
                is_main = bool(s.get("isMain"))
                token = s.get("streamId", "")
                if is_main:
                    stream_id, stream_name = sensor_id, (row.name or "CAMERA")
                else:
                    stream_id = f"{sensor_id}-{token}"
                    pname = s.get("name", "")
                    stream_name = f"{row.name}-{pname}" if row.name else f"CAMERA-{pname}"
                # Credentialed live_url so restoreRtspStreamsFromDB can DESCRIBE (see set_credentials).
                live_url = _embed_creds(s.get("url", ""), ouser, opass)
                self.repo.insert_stream(SensorStreams(
                    sensor_id=sensor_id, stream_id=stream_id, stream_live_url=live_url,
                    stream_proxy_url="", stream_replay_url="", stream_encoding=md.get("codec", ""),
                    stream_framerate=md.get("framerate", ""), stream_resolution=md.get("resolution", ""),
                    stream_ismainstream="true" if is_main else "false",
                    stream_type=STREAM_TYPE_RTSP, stream_storage_location=0, stream_name=stream_name,
                    created_date_time=now, modified_date_time=now,
                ), now)
                if is_main:
                    await self._events.publish(build_payload(
                        change=ChangeEvent.camera_proxy, camera_id=sensor_id, camera_name=row.name,
                        camera_url=live_url, tags=row.tags or "", created_at=now, metadata=md,
                    ))
        return sensor_id

    async def set_credentials(self, sensor_id: str, username: str, password: str) -> bool:
        """Validate credentials against the camera and, on success, persist them and resolve the
        RTSP streams (ONVIF GetProfiles/GetStreamUri). Mirrors C++ setSensorCredentials:
        wrong credentials -> InvalidParameterError; unchanged -> no-op true."""
        row = self.repo.get_sensor(sensor_id)
        if row is None:
            raise VmsError(VmsErrorCode.CameraNotFoundError, f"Invalid Sensor ID {sensor_id}")
        if (row.username or "") == username and self.repo.get_password(sensor_id) == password:
            return True  # unchanged

        if row.type == SENSOR_TYPE_ONVIF:
            if self._control is None:
                raise VmsError(VmsErrorCode.VMSInternalError, "ONVIF adaptor not loaded")
            port = urlparse(row.url).port or ONVIF_DEFAULT_PORT
            sensor: dict[str, Any] = {"ip": row.ipaddress, "port": port, "user": username,
                                      "password": password, "streams": []}
            if not await self._control.validate_credentials(sensor, username, password):
                raise VmsError(VmsErrorCode.InvalidParameterError,
                               "setSensorCredentials: invalid username or password")
            # Authorized: resolve device info + media profiles (RTSP URIs).
            await self._control.get_sensor_stream_info(sensor)
            now = _now_iso()
            self.repo.authorize_sensor(sensor_id, username, password, mapping.CAMERA_NO_ERROR_CODE,
                                       sensor, now)
            for st in sensor.get("streams") or []:
                md = st.get("metadata", {})
                is_main = bool(st.get("isMain"))
                token = st.get("streamId", "")
                # C++ onvif_client stream-id scheme: main stream id == sensor_id (so the webui's
                # streamStart(streamId=sensor_id) and the RTSP-server's getSensorIdFromStreamId both
                # resolve); sub-streams are sensor_id + "-" + profileToken.
                if is_main:
                    stream_id = sensor_id
                    stream_name = row.name or "CAMERA"
                else:
                    stream_id = f"{sensor_id}-{token}"
                    pname = st.get("name", "")
                    stream_name = f"{row.name}-{pname}" if row.name else f"CAMERA-{pname}"
                # Store the credentialed RTSP url: the RTSP-server's restoreRtspStreamsFromDB reads
                # stream_live_url verbatim (no cred insertion), so the camera DESCRIBE only
                # authenticates if credentials are embedded here -- same as the RTSP-add path.
                live_url = _embed_creds(st.get("url", ""), username, password)
                self.repo.insert_stream(SensorStreams(
                    sensor_id=sensor_id, stream_id=stream_id,
                    stream_live_url=live_url, stream_proxy_url="", stream_replay_url="",
                    stream_encoding=md.get("codec", ""), stream_framerate=md.get("framerate", ""),
                    stream_resolution=md.get("resolution", ""),
                    stream_ismainstream="true" if is_main else "false",
                    stream_type=STREAM_TYPE_RTSP, stream_storage_location=0,
                    stream_name=stream_name, created_date_time=now,
                    modified_date_time=now), now)
                if is_main:
                    await self._events.publish(build_payload(
                        change=ChangeEvent.camera_proxy, camera_id=sensor_id, camera_name=row.name,
                        camera_url=live_url, tags=row.tags or "", created_at=now, metadata=md))
            return True

        # Non-ONVIF (e.g. rtsp): store credentials without a live camera handshake.
        self.repo.authorize_sensor(sensor_id, username, password, mapping.CAMERA_NO_ERROR_CODE, {}, _now_iso())
        return True

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
        # Key set matches the C++ GetConfiguration exactly. Empty interface/ntp arrays render as
        # null (jsoncpp). No "adaptor" key (C++ does not emit one).
        return {
            "deviceDiscoveryFrequencySeconds": c.sensor_discovery_freq_secs,
            "deviceDiscoveryInterfaces": c.sensor_discovery_interfaces or None,
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
            "ntpServers": c.ntp_servers or None,
            "use_sensor_ntp_time": c.use_sensor_ntp_time,
            "onvifRequestTimeoutSeconds": c.onvif_request_timeout_secs,
            "enableNotification": c.enable_notification,
            "enablePrometheus": False,
            "prometheusPort": "8080",
            "enableDebugApis": True,
            "supportedVideoCodecs": "h264, h265",
            "supportedAudioCodecs": "pcmu, pcma, mpeg4-generic",
            "useHttpDigestAuthentication": False,
            "useHttps": False,
            "vstDataPath": c.vst_data_path,
            "webserviceAccessControlList": "",
            "enableUserCleanup": False,
            "multiUserExtraOptions": "Secure, SameSite=none",
            "useMultiUser": c.use_multi_user,
            "vstIp": c.centralize_remote_db_hostaddr,
            "remoteVstAddress": c.remote_vst_address,
            "nvNgcKey": "",
            "nvOrgId": "",
            "deviceName": c.device_name,
            "deviceLocation": c.device_location,
            "defaultProfile": c.default_profile,
            "defaultQuality": 3.0,
            "defaultEncodingInterval": 1,
            "defaultResolution": c.default_resolution,
            "defaultGovLength": c.default_gov_length,
            "defaultFramerate": c.default_framerate,
            "defaultBitrateKbps": c.default_bitrate,
        }

    async def scan(self, force: bool = True) -> int:
        """Adaptor-aware discovery + registration. Returns the number of sensors newly registered.
        - onvif: WS-Discovery probe, register each NVT device as a sensor_onvif.
        - vst_rtsp/streamer: poll nvstreamer endpoints, register each stream as a sensor_rtsp.
        Registration is idempotent (stable sensor_id), so re-scan after a delete re-adds the source
        and any persisted recordings/timelines (keyed by sensor_id) reattach."""
        if self._adaptor_name == ONVIF_ADAPTOR:
            return await self._scan_onvif()
        if self._adaptor_name in RTSP_ADAPTORS:
            return await self._scan_nvstreamer()
        return 0

    async def _scan_nvstreamer(self) -> int:
        added = 0
        for ep in self._cfg.nvstreamer_endpoints:
            try:
                streams = await asyncio.to_thread(fetch_streams, ep, float(self._cfg.sensor_discovery_timeout))
            except Exception as e:
                log.warning("nvstreamer poll failed for %s: %s", ep, e)
                continue
            for s in streams:
                if self.repo.get_sensor(s["sensorId"]) is not None:
                    continue  # idempotent: already registered
                await self._register_rtsp_sensor(s["sensorId"], s["name"], s["url"], s["metadata"])
                added += 1
        if added:
            log.info("nvstreamer discovery registered %d new stream(s)", added)
        return added

    async def _register_rtsp_sensor(self, sid: str, name: str, url: str, md: dict[str, Any]) -> None:
        now = _now_iso()
        row = SensorDetails(
            device_id=self._cfg.device_name or "", sensor_id=sid, sensor_hw_id=sid,
            name=(name or sid)[:MAX_SENSOR_NAME_LENGTH], ipaddress="", url=url, type=SENSOR_TYPE_RTSP,
            position=EMPTY_POSITION, is_remote="false", http_status=mapping.CAMERA_NO_ERROR_CODE,
            sensor_status=1, created_date_time=now, modified_date_time=now,
        )
        self.repo.insert_sensor(row, "", now)
        self.repo.insert_stream(SensorStreams(
            sensor_id=sid, stream_id=sid, stream_live_url=url, stream_proxy_url="", stream_replay_url="",
            stream_encoding=md.get("codec", ""), stream_framerate=md.get("framerate", ""),
            stream_resolution=md.get("resolution", ""), stream_ismainstream="true",
            stream_type=STREAM_TYPE_RTSP, stream_storage_location=0, stream_name=row.name,
            created_date_time=now, modified_date_time=now,
        ), now)
        await self._events.publish(build_payload(
            change=ChangeEvent.camera_add, camera_id=sid, camera_name=row.name,
            camera_url="", tags="", created_at=now, metadata=None))
        await self._events.publish(build_payload(
            change=ChangeEvent.camera_proxy, camera_id=sid, camera_name=row.name, camera_url=url,
            tags="", created_at=now,
            metadata={"codec": md.get("codec", ""), "framerate": md.get("framerate", ""),
                      "resolution": md.get("resolution", "")}))

    async def _scan_onvif(self) -> int:
        from ..adaptors.onvif.discovery import discover

        try:
            matches = await discover(message_id=str(uuid.uuid4()),
                                     timeout=float(self._cfg.sensor_discovery_timeout))
        except Exception as e:
            log.warning("WS-Discovery probe failed: %s", e)
            return 0
        # Per-scan totals fluctuate with UDP packet loss; keep at debug so the periodic discovery
        # loop does not spam the log. Only state changes (new/removed devices) are logged at info.
        log.debug("WS-Discovery probe returned %d ProbeMatch response(s)", len(matches))
        seen: set[str] = set()
        added = 0
        for m in matches:
            ip = urlparse(m.device_service_url).hostname if m.device_service_url else ""
            sid = (m.address.rsplit(":", 1)[-1] if m.address else "") or ip
            if not sid or not ip:
                continue
            seen.add(sid)
            self._onvif_misses.pop(sid, None)  # responded this scan -> reset miss counter
            if self.repo.get_sensor(sid) is not None:
                continue  # already known -> no log (avoid spam)
            # Populate basic device details from the ProbeMatch scopes (name/hardware/location/type),
            # matching the C++ which fills the sensor from discovery before any credentialed
            # GetProfiles. Full RTSP profiles are resolved later once credentials are supplied.
            sf = m.scope_fields()
            name = (sf["name"] or ip)[:MAX_SENSOR_NAME_LENGTH]   # kept URL-encoded (C++ parity)
            now = _now_iso()
            # C++ parity: name from /name/ scope (raw), hardware from /hardware/, location from
            # /location/; manufacturer and hardwareId stay empty. Discovered-but-uncredentialed ->
            # http_status 401 (CameraUnauthorizedError) so /list state is "offline"; sensor_status
            # online. NO stream row is created (bulk /streams returns [] until profiles resolve).
            self.repo.insert_sensor(SensorDetails(
                device_id=self._cfg.device_name or "", sensor_id=sid, sensor_hw_id=m.address or sid,
                name=name, ipaddress=ip, hardware=sf["hardware"], manufacturer="",
                hardware_id="", location=sf["location"], url=m.device_service_url,
                type=SENSOR_TYPE_ONVIF, position=EMPTY_POSITION, is_remote="false",
                remote_device_name=self._cfg.device_name or "",
                http_status=mapping.CAMERA_UNAUTHORIZED_CODE, sensor_status=1,
                created_date_time=now, modified_date_time=now), "", now)
            await self._events.publish(build_payload(
                change=ChangeEvent.camera_add, camera_id=sid, camera_name=name, camera_url="",
                tags="", created_at=now, metadata=None))
            # First-time detection only -- this fires once per device (then it is in the DB).
            log.info("ONVIF device detected: %s (%s) at %s", name, sid, ip)
            added += 1
        self._log_onvif_removals(seen)
        return added

    def _log_onvif_removals(self, seen: set[str]) -> None:
        """Log (once) ONVIF sensors that stopped replying to discovery for several consecutive
        scans. Debounced via ONVIF_DISCOVERY_MISS_THRESHOLD so transient UDP loss is not reported as
        a removal. Discovered sensors are left in the DB (an authorized sensor must persist); only
        the state-change is logged."""
        for r in self.repo.list_sensors():
            if r.type != SENSOR_TYPE_ONVIF or r.sensor_id in seen:
                continue
            misses = self._onvif_misses.get(r.sensor_id, 0) + 1
            self._onvif_misses[r.sensor_id] = misses
            if misses == ONVIF_DISCOVERY_MISS_THRESHOLD:  # exactly once at the threshold
                log.info("ONVIF device removed from network: %s (%s)", r.name, r.sensor_id)

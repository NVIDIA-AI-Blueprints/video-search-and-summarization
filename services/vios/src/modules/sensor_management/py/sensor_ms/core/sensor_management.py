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
import json
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
# Milestone XProtect (mms) SOAP adaptor: discovery via the milestone control adaptor's discover().
MMS_ADAPTORS = frozenset({"milestone_soap", "milestone"})
STREAM_TYPE_RTSP = 2
DEFAULT_ENCODING = "h264"
ONVIF_DEFAULT_PORT = 80
MAX_SENSOR_NAME_LENGTH = 175  # sensor_info.h:50; C++ truncates the name on add
# WS-Discovery is multicast UDP: a device may miss a probe round, so the per-scan device count
# fluctuates. Only treat a known device as gone after this many consecutive scans without a reply,
# to avoid false "removed" logs from transient packet loss.
ONVIF_DISCOVERY_MISS_THRESHOLD = 3
# http_status set on an ONVIF sensor the monitoring loop has marked offline (maps to
# DeviceRequestTimeoutError in /status; sensor_status=0 drives the "offline" state).
ONVIF_OFFLINE_HTTP_CODE = 408
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


def _is_valid_ipv4(addr: str) -> bool:
    """True for a dotted-quad IPv4 address (parity with C++ validateIpAddress for set network)."""
    import ipaddress

    try:
        ipaddress.IPv4Address(addr)
        return True
    except (ValueError, ipaddress.AddressValueError):
        return False


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
        # Periodic ONVIF time-sync task (pushes UTC time to online ONVIF cameras); ONVIF-only.
        self._time_sync_task: asyncio.Task | None = None
        # Sensor ids already switched to NTP discipline (configure_sensor_ntp is persistent on the
        # camera, so it only needs doing once per online session, not every time-sync pass).
        self._ntp_configured: set[str] = set()
        # Consecutive missed-discovery counts per ONVIF sensor id, for debounced removal logging.
        self._onvif_misses: dict[str, int] = {}
        # Debug "unplug": IPs the test hooks (/sensor/debug/unplug) have blocked. Discovery skips them
        # so a camera can be simulated offline without physically unplugging it (C++ blockSensor).
        self._blocked_ips: set[str] = set()
        # Whether the "sensors count limit reached" message has already been logged; reset when a scan
        # no longer hits the cap, so it is logged once per state change instead of every scan.
        self._cap_logged = False
        # Set True when a scan could not reach a discovery endpoint; drives the bounded startup retry.
        self._last_scan_had_failures = False
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

    @property
    def device_type(self) -> str:
        """Service type reported by /sensor/version, matching the C++ DeviceManager::getDeviceType:
        "mms" for the Milestone adaptors, "vst" otherwise."""
        return "mms" if self._adaptor_name in MMS_ADAPTORS else "vst"

    async def start(self) -> None:
        # Create the sensor schema if missing so a fresh standalone DB works (best-effort: if the DB
        # is unreachable, log and continue; per-request handlers then surface VMSInternalError).
        try:
            from ..db.engine import init_schema
            init_schema(self._dm.engine)
        except Exception as e:
            log.warning("schema init skipped: %s", e)
        # Initial discovery at startup.
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
        # Periodic re-discovery runs ONLY for ONVIF (continuous WS-Discovery, lightweight multicast --
        # C++ parity). RTSP/nvstreamer sources are discovered once at startup and on demand via
        # POST /api/v1/sensor/scan (matching the C++ rtsp_streams adaptor) -- no continuous HTTP
        # polling, so unreachable endpoints are not retried every few seconds.
        if self._periodic_discovery_enabled():
            self._discovery_task = asyncio.create_task(self._periodic_discovery())
        elif self._last_scan_had_failures:
            # One-shot adaptor whose startup scan hit endpoint failures -> bounded background retry.
            self._discovery_task = asyncio.create_task(self._startup_discovery_retry())
        # Periodic ONVIF time-sync (push UTC to online cameras), ONVIF adaptor only -- C++ parity
        # (SensorMonitoring schedules synchronizeSensorTime every onvif_sensor_time_sync_interval_secs).
        if self._time_sync_enabled():
            self._time_sync_task = asyncio.create_task(self._periodic_time_sync())

    def _periodic_discovery_enabled(self) -> bool:
        return self._adaptor_name == ONVIF_ADAPTOR

    def _time_sync_enabled(self) -> bool:
        return (self._adaptor_name == ONVIF_ADAPTOR and self._control is not None
                and int(self._cfg.onvif_sensor_time_sync_interval_secs) > 0)

    async def _time_sync_once(self) -> int:
        """One time-sync pass over ONLINE, non-blocked, credentialed ONVIF sensors.

        Two modes (ONVIF parity with the C++ sensor service):
        - NTP (use_sensor_ntp_time + ntp_servers): switch each camera to NTP discipline once
          (configure_sensor_ntp). The camera then keeps its own clock at millisecond accuracy.
        - Manual (default): set ALL cameras to the same upcoming whole-second boundary SIMULTANEOUSLY
          (synchronize_sensors_time_batch), mirroring C++ OnvifDiscovery::synchronizeDateAndTime. This
          locks the cameras to EACH OTHER (so overlaid frame timestamps line up); it is done every
          pass with no drift-check, exactly like the C++ curl_multi batch.

        Returns the number of cameras set/configured this pass. Per-sensor failures are logged."""
        if self._control is None:
            return 0
        # Only credentialed + online cameras: discovered-but-uncredentialed ONVIF devices
        # (http_status 401) have no valid login, so SetSystemDateAndTime would just return
        # "Sender not Authorized" -- on a large network that floods the log. http_status == NoError
        # means credentials were validated and streams resolved.
        rows = [r for r in self.repo.list_sensors()
                if r.type == SENSOR_TYPE_ONVIF and (r.sensor_status or 0) != 0
                and r.http_status == mapping.CAMERA_NO_ERROR_CODE
                and (r.ipaddress or "") not in self._blocked_ips]
        if not rows:
            return 0
        if self._cfg.use_sensor_ntp_time and self._cfg.ntp_servers:
            return await self._time_sync_ntp(rows)
        # Manual: simultaneous boundary-aligned batch set across all cameras (inter-camera sync).
        return await self._control.synchronize_sensors_time_batch(
            [self._onvif_sensor_dict(r) for r in rows],
            self._cfg.onvif_sensor_time_sync_compensation_ms)

    async def _time_sync_ntp(self, rows) -> int:
        """NTP mode: configure_sensor_ntp once per camera (persistent on the device, so already-
        configured cameras are skipped). Returns the number newly configured this pass."""
        synced = 0
        for row in rows:
            if row.sensor_id in self._ntp_configured:
                continue
            try:
                if await self._control.configure_sensor_ntp(
                        self._onvif_sensor_dict(row), self._cfg.ntp_servers) == 0:
                    self._ntp_configured.add(row.sensor_id)
                    synced += 1
            except Exception as e:
                log.warning("NTP config failed for sensor %s: %s", row.sensor_id, e)
        if synced:
            log.info("ONVIF time-sync: configured NTP on %d/%d online sensor(s)", synced, len(rows))
        return synced

    async def _periodic_time_sync(self) -> None:
        """Every onvif_sensor_time_sync_interval_secs, run one _time_sync_once pass (ONVIF parity with
        SensorMonitoring's scheduled synchronizeSensorTime)."""
        interval = max(5, int(self._cfg.onvif_sensor_time_sync_interval_secs))
        while True:
            await asyncio.sleep(interval)
            try:
                await self._time_sync_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("periodic time-sync iteration failed: %s", e)

    async def _startup_discovery_retry(self) -> None:
        """For one-shot (rtsp/nvstreamer) discovery: if the startup scan couldn't reach an endpoint,
        retry a bounded number of times with a delay, then stop. Resilient to a transient boot-time
        failure (e.g. nvstreamer not ready yet) without falling back to continuous polling."""
        retries = max(0, int(self._cfg.discovery_retry_count))
        interval = max(1, int(self._cfg.discovery_retry_interval_secs))
        for attempt in range(1, retries + 1):
            await asyncio.sleep(interval)
            log.info("retrying discovery after failure (attempt %d/%d)", attempt, retries)
            try:
                await self.scan(force=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning("discovery retry %d failed: %s", attempt, e)
            if not self._last_scan_had_failures:
                log.info("discovery retry succeeded; no further retries")
                return
        log.info("discovery retries exhausted (%d); giving up until next POST /sensor/scan", retries)

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
            log.info("re-announced camera_proxy for online sensor %s (%s)",
                     row.name or "unnamed", row.sensor_id)

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
        for task in (self._discovery_task, self._time_sync_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self._events.close()
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

    def _onvif_sensor_dict(self, row) -> dict[str, Any]:
        """Build the {ip, port, user, password} dict the ONVIF control adaptor consumes from a row."""
        return {
            "ip": row.ipaddress,
            "port": urlparse(row.url or "").port or ONVIF_DEFAULT_PORT,
            "user": row.username or "",
            "password": self.repo.get_password(row.sensor_id) or "",
            "streams": [],
        }

    async def sensor_network(self, sensor_id: str) -> dict[str, Any]:
        """GET /{id}/network. ONVIF: query the camera via the control adaptor (GetNetworkInterfaces).
        Non-ONVIF / no control / adaptor failure -> VMSInternalError (C++ getSensorNetworkInfo)."""
        row = self._require_sensor(sensor_id)
        if row.type != SENSOR_TYPE_ONVIF or self._control is None:
            raise VmsError(VmsErrorCode.VMSInternalError)
        ret, info = await self._control.get_network_info(self._onvif_sensor_dict(row))
        if ret != 0:
            raise VmsError(VmsErrorCode.VMSInternalError)
        return info

    async def sensor_settings(self, sensor_id: str, type_: str = ""):
        """GET /{id}/settings. ONVIF: per-stream {Image, Encode} from the control adaptor. Non-ONVIF:
        empty object (C++ getSensorSettings leaves the response empty when the adaptor can't supply)."""
        row = self._require_sensor(sensor_id)
        if row.type != SENSOR_TYPE_ONVIF or self._control is None:
            return {}
        ret, settings = await self._control.get_settings(self._onvif_sensor_dict(row), type_)
        if ret != 0:
            return {}
        return settings

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
        # Duplicate detection (parity with C++ addSensor). Both error_messages carry the CONFLICTING
        # sensor's id+name so callers (e.g. AMC) can distinguish the cases and reuse the pre-registered
        # sensor instead of re-adding. Exact format matches the C++ fix (sensor_management_utils.cpp):
        #   URL/IP match (any name) -> "Sensor exists already, sensorId: <id>, sensorName: <name>"
        #   name-only collision     -> "User given name is invalid or already exists, sensorId: <id>, sensorName: <name>"
        existing = self.repo.list_sensors()
        # Capacity check (parity with C++ isSpaceForNewSensor): reject once the configured limit is hit.
        if not self._has_capacity(len(existing)):
            raise VmsError(VmsErrorCode.VMSNotSupportedError, "Sensors count limit reached")
        for e in existing:
            if (url and (e.url or "") == url) or (ip and (e.ipaddress or "") == ip):
                raise VmsError(
                    VmsErrorCode.InvalidParameterError,
                    f"Sensor exists already, sensorId: {e.sensor_id}, sensorName: {e.name or ''}")
        for e in existing:
            if name and (e.name or "") == name:
                raise VmsError(
                    VmsErrorCode.InvalidParameterError,
                    "User given name is invalid or already exists, "
                    f"sensorId: {e.sensor_id}, sensorName: {e.name or ''}")
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
        # Log the add (source host only -- never the credentialed URL/password).
        source = ip or (urlparse(url).hostname or "")
        log.info("added %s sensor %s '%s' (%s)", sensor_type, sensor_id, row.name, source)
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
            # Resolve device info + media profiles (RTSP URIs). A failure here (e.g. "Sender not
            # Authorized" on GetProfiles/GetStreamUri) means the credentials are NOT actually valid
            # for media access -- some cameras serve GetDeviceInformation anonymously, so validate
            # alone can pass with wrong credentials. Reject instead of persisting bad credentials.
            if await self._control.get_sensor_stream_info(sensor) != 0:
                raise VmsError(VmsErrorCode.InvalidParameterError,
                               "setSensorCredentials: invalid username or password")
            now = _now_iso()
            self.repo.authorize_sensor(sensor_id, username, password, mapping.CAMERA_NO_ERROR_CODE,
                                       sensor, now)
            # One-time drift-checked sync on credentialing (C++ parity): get the camera clock and
            # correct it if it differs from host UTC. The periodic batch then keeps cameras locked to
            # each other. Best-effort -- never fail credentialing because a clock write failed.
            try:
                await self._control.synchronize_sensor_time(sensor)
            except Exception as e:
                log.warning("initial time-sync failed for sensor %s: %s", sensor_id, e)
            # Push configured default encoder settings to the camera's main stream (C++ parity:
            # setStreamDefaultSettings via getAndAddProxyUrl). Best-effort -- never fail credentialing
            # because the camera rejected an encoder write.
            try:
                await self._control.apply_default_encode_settings(sensor, {
                    "bitrate": self._cfg.default_bitrate,
                    "framerate": self._cfg.default_framerate,
                    "gov_length": self._cfg.default_gov_length,
                    "resolution": self._cfg.default_resolution,
                })
            except Exception as e:
                log.warning("default encode settings failed for sensor %s: %s", sensor_id, e)
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
            log.info("credentials validated for sensor %s; resolved %d stream(s)",
                     sensor_id, len(sensor.get("streams") or []))
            return True

        # Non-ONVIF (e.g. rtsp): store credentials without a live camera handshake.
        self.repo.authorize_sensor(sensor_id, username, password, mapping.CAMERA_NO_ERROR_CODE, {}, _now_iso())
        log.info("credentials stored for sensor %s", sensor_id)
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
        log.info("deleted sensor %s ('%s')", sensor_id, name)

    async def set_sensor_info(self, sensor_id: str, body: dict[str, Any]) -> bool:
        """Update editable sensor fields (name/position/location/tags/hardware metadata).
        Parity with C++ setSensorInfo: a changed name must be non-empty and unique, else
        InvalidParameterError (with the conflicting sensor surfaced); name is truncated to the max."""
        if not body:
            raise VmsError(VmsErrorCode.InvalidParameterError)
        log.info("applying sensor info for %s: %s", sensor_id, body)
        row = self.repo.get_sensor(sensor_id)
        if row is None:
            raise VmsError(VmsErrorCode.VMSInternalError, f"Failed to get sensor info: {sensor_id}")

        fields: dict[str, Any] = {}
        if "name" in body:
            new_name = (body.get("name") or "")[:MAX_SENSOR_NAME_LENGTH]
            if new_name != (row.name or ""):
                if not new_name:
                    raise VmsError(VmsErrorCode.InvalidParameterError,
                                   "User given name is invalid or already exists")
                conflict = next((e for e in self.repo.list_sensors()
                                 if (e.name or "") == new_name and e.sensor_id != sensor_id), None)
                if conflict is not None:
                    raise VmsError(
                        VmsErrorCode.InvalidParameterError,
                        "User given name is invalid or already exists, "
                        f"sensorId: {conflict.sensor_id}, sensorName: {conflict.name}")
            fields["name"] = new_name
        for api_key, col in (("hardware", "hardware"), ("manufacturer", "manufacturer"),
                             ("serialNumber", "serial_number"), ("firmwareVersion", "firmware_version"),
                             ("hardwareId", "hardware_id"), ("location", "location"), ("tags", "tags")):
            if api_key in body:
                fields[col] = body[api_key] or ""
        if "position" in body:
            fields["position"] = mapping.position_api_to_db(body.get("position"))

        if not self.repo.update_sensor_info(sensor_id, fields, _now_iso()):
            raise VmsError(VmsErrorCode.VMSInternalError)
        log.info("set sensor info for %s", sensor_id)
        return True

    async def replace_sensor(self, old_sensor_id: str, body: dict[str, Any]) -> None:
        """Replace an offline sensor with another existing sensor, keeping the OLD id so persisted
        recordings/timelines (keyed by sensor_id) reattach. Parity with C++ replaceSensorId/replaceSensor:
        both sensors must exist, the old one must be offline, then the new sensor's details + streams
        are re-keyed onto the old id and the old/new rows collapse into one."""
        new_sensor_id = body.get("sensorId") or body.get("deviceid") or ""
        if not new_sensor_id:
            raise VmsError(VmsErrorCode.InvalidParameterError, "Sensor ID is empty")
        old_row = self.repo.get_sensor(old_sensor_id)
        if old_row is None:
            raise VmsError(VmsErrorCode.InvalidParameterError, "Old Sensor does not exists, cannot replace")
        new_row = self.repo.get_sensor(new_sensor_id)
        if new_row is None:
            raise VmsError(VmsErrorCode.InvalidParameterError, "New Sensor does not exists, cannot replace")
        # "Online"/"Streaming" sensor_status (>=1) is still active; only an offline (0) sensor can be
        # replaced (C++ blocks SensorStatusOnline/Streaming).
        if (old_row.sensor_status or 0) != 0:
            raise VmsError(VmsErrorCode.InvalidParameterError, "Old Sensor still active, cannot replace")

        new_password = self.repo.get_password(new_sensor_id)
        new_streams = self.repo.list_streams(new_sensor_id)
        now = _now_iso()
        # Collapse both rows, then re-insert the new sensor's details under the OLD id.
        self.repo.delete_sensor(old_sensor_id)
        self.repo.delete_sensor(new_sensor_id)
        replaced = SensorDetails(
            device_id=new_row.device_id or (self._cfg.device_name or ""),
            sensor_id=old_sensor_id, sensor_hw_id=new_row.sensor_hw_id or old_sensor_id,
            username=new_row.username or "", name=new_row.name or old_sensor_id,
            ipaddress=new_row.ipaddress or "", hardware=new_row.hardware or "",
            manufacturer=new_row.manufacturer or "", serial_number=new_row.serial_number or "",
            firmware_version=new_row.firmware_version or "", hardware_id=new_row.hardware_id or "",
            location=new_row.location or "", tags=new_row.tags or "", url=new_row.url or "",
            type=new_row.type or SENSOR_TYPE_RTSP, position=new_row.position or EMPTY_POSITION,
            is_remote=new_row.is_remote or "false", http_status=new_row.http_status,
            sensor_status=new_row.sensor_status or 0, created_date_time=now, modified_date_time=now,
        )
        self.repo.insert_sensor(replaced, new_password, now)
        main_url = ""
        main_md: dict[str, Any] = {}
        for st in new_streams:
            is_main = (st.stream_ismainstream or "").lower() == "true"
            # main stream id == old sensor id; sub-streams keep their token suffix re-keyed to old id.
            stream_id = old_sensor_id if is_main else f"{old_sensor_id}-{st.stream_id.split('-', 1)[-1]}"
            self.repo.insert_stream(SensorStreams(
                sensor_id=old_sensor_id, stream_id=stream_id, stream_live_url=st.stream_live_url or "",
                stream_proxy_url="", stream_replay_url="", stream_encoding=st.stream_encoding or "",
                stream_framerate=st.stream_framerate or "", stream_resolution=st.stream_resolution or "",
                stream_ismainstream="true" if is_main else "false", stream_type=st.stream_type or STREAM_TYPE_RTSP,
                stream_storage_location=st.stream_storage_location or 0,
                stream_name=st.stream_name or replaced.name, created_date_time=now, modified_date_time=now), now)
            if is_main:
                main_url = st.stream_live_url or ""
                main_md = {"codec": st.stream_encoding or "", "framerate": st.stream_framerate or "",
                           "resolution": st.stream_resolution or ""}
        # Re-announce so the RTSP-server/stream-processor rebuilds the proxy for the replaced id.
        await self._events.publish(build_payload(
            change=ChangeEvent.camera_add, camera_id=old_sensor_id, camera_name=replaced.name,
            camera_url="", tags=replaced.tags or "", created_at=now, metadata=None))
        if main_url:
            await self._events.publish(build_payload(
                change=ChangeEvent.camera_proxy, camera_id=old_sensor_id, camera_name=replaced.name,
                camera_url=main_url, tags=replaced.tags or "", created_at=now, metadata=main_md))
        log.info("replaced sensor %s with %s", old_sensor_id, new_sensor_id)

    async def apply_configuration(self, body: dict[str, Any]) -> None:
        """POST /configuration. Apply deviceDiscoveryInterfaces / ntpServers to the in-memory config
        and restart discovery if the (non-empty) interface set changed (C++ handleSensorConfiguration)."""
        interfaces = body.get("deviceDiscoveryInterfaces")
        if isinstance(interfaces, list):
            new_ifaces = [i for i in interfaces if i]
            if new_ifaces != self._cfg.sensor_discovery_interfaces:
                self._cfg.sensor_discovery_interfaces = new_ifaces
                if new_ifaces:
                    await self._restart_discovery()
        ntp = body.get("ntpServers")
        if isinstance(ntp, list):
            new_ntp = [n for n in ntp if n]
            if new_ntp != self._cfg.ntp_servers:
                self._cfg.ntp_servers = new_ntp
        log.info("configuration applied (discovery_interfaces=%s, ntp_servers=%s)",
                 self._cfg.sensor_discovery_interfaces, self._cfg.ntp_servers)

    async def _restart_discovery(self) -> None:
        """Cancel and re-launch the periodic discovery loop (C++ rebootSensorDiscovery)."""
        if self._discovery_task is not None:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await self.scan(force=False)
        except Exception as e:
            log.warning("discovery restart scan failed: %s", e)
        # Re-arm the periodic loop only for ONVIF (RTSP/nvstreamer is one-shot + on /scan).
        if self._periodic_discovery_enabled():
            self._discovery_task = asyncio.create_task(self._periodic_discovery())

    async def reboot_sensor(self, sensor_id: str) -> None:
        """POST /{id}/reboot via the ONVIF control adaptor (C++ rebootSensor)."""
        row = self._require_sensor(sensor_id)
        if row.type != SENSOR_TYPE_ONVIF or self._control is None:
            raise VmsError(VmsErrorCode.VMSInternalError)
        if await self._control.reboot_sensor(self._onvif_sensor_dict(row)) != 0:
            raise VmsError(VmsErrorCode.VMSInternalError)
        log.info("reboot requested for sensor %s", sensor_id)

    async def set_sensor_network(self, sensor_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /{id}/network. Validate the IPv4 address, push to the camera via the control adaptor,
        and report whether a reboot is needed (C++ setSensorNetworkInfo)."""
        if not body:
            raise VmsError(VmsErrorCode.InvalidParameterError, "setSensorNetworkInfo: invalid parameters")
        log.info("applying network settings for sensor %s: %s", sensor_id, body)
        row = self._require_sensor(sensor_id)
        ipv4 = body.get("ipAddressV4", "")
        if ipv4 and not _is_valid_ipv4(ipv4):
            raise VmsError(VmsErrorCode.InvalidParameterError, "Invalid ipv4 address")
        if row.type != SENSOR_TYPE_ONVIF or self._control is None:
            raise VmsError(VmsErrorCode.VMSInternalError)
        ret, reboot_needed = await self._control.set_network_info(self._onvif_sensor_dict(row), body)
        if ret != 0:
            raise VmsError(VmsErrorCode.VMSInternalError)
        log.info("network settings applied for sensor %s (rebootNeeded=%s)", sensor_id, reboot_needed)
        return {"rebootNeeded": reboot_needed}

    async def set_sensor_settings(self, sensor_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST /{id}/settings. ONVIF only (VMSNotSupportedError otherwise), applied via the control
        adaptor (C++ setSensorSettings)."""
        row = self.repo.get_sensor(sensor_id)
        if row is None:
            raise VmsError(VmsErrorCode.CameraNotFoundError)
        if row.type != SENSOR_TYPE_ONVIF:
            raise VmsError(VmsErrorCode.VMSNotSupportedError,
                           "SetSensorSettings is not supported for non-onvif sensor")
        if self._control is None:
            raise VmsError(VmsErrorCode.VMSInternalError, "ONVIF adaptor not loaded")
        # Log the requested values (codec/bitrate/etc. -- no credentials here).
        log.info("applying settings for sensor %s: %s", sensor_id, body or {})
        if await self._control.set_settings(self._onvif_sensor_dict(row), body or {}) != 0:
            raise VmsError(VmsErrorCode.VMSInternalError)
        log.info("settings applied for sensor %s", sensor_id)
        return {}

    # --- debug test hooks (blockSensor parity) ---
    def block_sensor(self, ip: str, action: str) -> dict[str, str]:
        """Simulate a camera being plugged/unplugged from the network for testing. 'unplug' blocks the
        IP (discovery skips it -> camera goes offline); 'plug' unblocks it."""
        if ip:
            if action == "unplug":
                self._blocked_ips.add(ip)
            elif action == "plug":
                self._blocked_ips.discard(ip)
        return {"status": action}

    def sensor_block_status(self, ip: str) -> str:
        """'unplug' if the IP is currently blocked, else 'plug' (C++ debug GET status)."""
        return "unplug" if ip in self._blocked_ips else "plug"

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
        self._last_scan_had_failures = False
        if self._adaptor_name == ONVIF_ADAPTOR:
            return await self._scan_onvif()
        if self._adaptor_name in MMS_ADAPTORS:
            return await self._scan_milestone()
        if self._adaptor_name in RTSP_ADAPTORS:
            # Prefer configs/rtsp_streams.json (C++ vst_rtsp parity: direct `streams` + `Nvstreamer`
            # endpoints). Fall back to the NVSTREAMER_ENDPOINTS env only when the file is absent.
            data = self._read_rtsp_streams_config()
            if data is not None:
                return await self._scan_from_rtsp_streams_config(data)
            return await self._scan_nvstreamer()
        return 0

    def _rtsp_streams_config_path(self) -> str:
        """Locate configs/rtsp_streams.json -- it lives in the same configs dir as adaptor_config.json
        (mounted at /home/vst/vst_release/configs), falling back to VST_CONFIG_PATH."""
        base = os.path.dirname(self._cfg.adaptor_config_path or "") or os.environ.get("VST_CONFIG_PATH", "")
        return os.path.join(base, "rtsp_streams.json") if base else ""

    def _read_rtsp_streams_config(self) -> dict[str, Any] | None:
        path = self._rtsp_streams_config_path()
        if not path or not os.path.isfile(path):
            return None
        try:
            with open(path) as fh:
                return json.load(fh)
        except (OSError, ValueError) as e:
            log.warning("failed to read rtsp_streams.json (%s): %s", path, e)
            return None

    @staticmethod
    def _rtsp_stream_sensor_id(stream_in: str) -> str:
        """Stable sensor id derived from the RTSP url, so re-scans are idempotent and a disabled
        stream maps back to the same id for removal."""
        return str(uuid.uuid5(uuid.NAMESPACE_URL, stream_in))

    async def _scan_from_rtsp_streams_config(self, data: dict[str, Any]) -> int:
        """Register sources from rtsp_streams.json: enabled direct `streams` (RTSP urls) and enabled
        `Nvstreamer` endpoints (polled at their api, capped at max_stream_count). Mirrors the C++
        vst_rtsp adaptor (rtsp_streams.cpp)."""
        added = 0
        count = len(self.repo.list_sensors())
        hit_cap = False

        # 1) Direct RTSP streams.
        for s in data.get("streams") or []:
            stream_in = (s.get("stream_in") or "").strip()
            if not stream_in or stream_in.lower().startswith("udp"):
                continue  # UDP sources need port pooling; out of scope for the control plane (P3)
            sid = self._rtsp_stream_sensor_id(stream_in)
            if not s.get("enabled", False):
                # Disabled -> remove if previously registered (C++ disableStream parity).
                if self.repo.get_sensor(sid) is not None:
                    await self.delete_sensor(sid)
                continue
            if self.repo.get_sensor(sid) is not None:
                continue  # idempotent
            if (urlparse(stream_in).hostname or "") in self._blocked_ips:
                continue
            if not self._has_capacity(count):
                hit_cap = True
                continue
            name = (s.get("name") or "")[:MAX_SENSOR_NAME_LENGTH]
            video = s.get("video") or {}
            md = {"codec": video.get("codec", ""), "framerate": str(video.get("framerate", "") or "")} if video else {}
            await self._register_rtsp_sensor(sid, name or sid, stream_in, md)
            count += 1
            added += 1

        # 2) Nvstreamer endpoints.
        for nv in data.get("Nvstreamer") or []:
            if not nv.get("enabled"):
                continue
            endpoint = (nv.get("endpoint") or "").strip()
            if not endpoint:
                continue
            api = nv.get("api") or "/api/v1/sensor/streams"
            max_count = int(nv.get("max_stream_count", self._cfg.max_sensors_supported))
            try:
                streams = await asyncio.to_thread(
                    fetch_streams, endpoint, float(self._cfg.sensor_discovery_timeout), api, max_count)
            except Exception as e:
                log.warning("nvstreamer poll failed for %s: %s", endpoint, e)
                self._last_scan_had_failures = True
                continue
            for st in streams:
                if self.repo.get_sensor(st["sensorId"]) is not None:
                    continue
                if (urlparse(st["url"]).hostname or "") in self._blocked_ips:
                    continue
                if not self._has_capacity(count):
                    hit_cap = True
                    continue
                await self._register_rtsp_sensor(st["sensorId"], st["name"], st["url"], st["metadata"])
                count += 1
                added += 1

        self._note_cap(hit_cap, "streams")
        if added:
            log.info("rtsp_streams.json discovery registered %d new stream(s)", added)
        return added

    def _has_capacity(self, current_count: int) -> bool:
        """Whether another sensor may be registered: current count < max_sensors_supported
        (parity with C++ DeviceManager::isSpaceForNewSensor)."""
        return current_count < int(self._cfg.max_sensors_supported)

    def _note_cap(self, hit_cap: bool, what: str) -> None:
        """Log 'sensors count limit reached' ONCE per state change rather than on every scan."""
        if hit_cap and not self._cap_logged:
            log.info("Sensors count limit reached (%d); not registering more discovered %s",
                     self._cfg.max_sensors_supported, what)
            self._cap_logged = True
        elif not hit_cap:
            self._cap_logged = False

    async def _scan_nvstreamer(self) -> int:
        added = 0
        count = len(self.repo.list_sensors())
        hit_cap = False
        for ep in self._cfg.nvstreamer_endpoints:
            try:
                streams = await asyncio.to_thread(fetch_streams, ep, float(self._cfg.sensor_discovery_timeout))
            except Exception as e:
                log.warning("nvstreamer poll failed for %s: %s", ep, e)
                self._last_scan_had_failures = True
                continue
            for s in streams:
                if self.repo.get_sensor(s["sensorId"]) is not None:
                    continue  # idempotent: already registered
                if (urlparse(s["url"]).hostname or "") in self._blocked_ips:
                    continue  # debug "unplug": skip blocked source
                if not self._has_capacity(count):
                    hit_cap = True
                    continue
                await self._register_rtsp_sensor(s["sensorId"], s["name"], s["url"], s["metadata"])
                count += 1
                added += 1
        self._note_cap(hit_cap, "streams")
        if added:
            log.info("nvstreamer discovery registered %d new stream(s)", added)
        return added

    async def _scan_milestone(self) -> int:
        """Discover Milestone XProtect cameras via the SOAP control adaptor (Login + systeminfo.xml)
        and register each as an RTSP sensor with its live/vod URLs. Mirrors the C++ milestone_vms
        bulk discovery; idempotent (stable GUID sensor_id). One-shot like the rtsp adaptors (the
        bounded startup-retry covers transient login/HTTP failures)."""
        control = self._control
        if control is None or not hasattr(control, "discover"):
            return 0
        try:
            cams = await control.discover()
        except Exception as e:
            log.warning("Milestone discovery failed: %s", e)
            self._last_scan_had_failures = True
            return 0
        if not cams:
            # Could be an auth/endpoint failure -> let the bounded retry try again.
            self._last_scan_had_failures = True
        added = 0
        count = len(self.repo.list_sensors())
        hit_cap = False
        for cam in cams:
            sid = cam.get("sensor_id") or ""
            if not sid or self.repo.get_sensor(sid) is not None:
                continue
            if (urlparse(cam.get("live_url", "")).hostname or "") in self._blocked_ips:
                continue
            if not self._has_capacity(count):
                hit_cap = True
                continue
            await self._register_rtsp_sensor(
                sid, cam.get("name", ""), cam.get("live_url", ""),
                {"codec": cam.get("codec", "")}, replay_url=cam.get("replay_url", ""))
            count += 1
            added += 1
        self._note_cap(hit_cap, "Milestone cameras")
        if added:
            log.info("Milestone discovery registered %d new camera(s)", added)
        return added

    async def _register_rtsp_sensor(self, sid: str, name: str, url: str, md: dict[str, Any],
                                    replay_url: str = "") -> None:
        now = _now_iso()
        row = SensorDetails(
            device_id=self._cfg.device_name or "", sensor_id=sid, sensor_hw_id=sid,
            name=(name or sid)[:MAX_SENSOR_NAME_LENGTH], ipaddress="", url=url, type=SENSOR_TYPE_RTSP,
            position=EMPTY_POSITION, is_remote="false", http_status=mapping.CAMERA_NO_ERROR_CODE,
            sensor_status=1, created_date_time=now, modified_date_time=now,
        )
        self.repo.insert_sensor(row, "", now)
        self.repo.insert_stream(SensorStreams(
            sensor_id=sid, stream_id=sid, stream_live_url=url, stream_proxy_url="",
            stream_replay_url=replay_url,
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
        count = len(self.repo.list_sensors())
        hit_cap = False
        for m in matches:
            ip = urlparse(m.device_service_url).hostname if m.device_service_url else ""
            sid = (m.address.rsplit(":", 1)[-1] if m.address else "") or ip
            if not sid or not ip:
                continue
            if ip in self._blocked_ips:
                continue  # debug "unplug": treat as not present this scan
            seen.add(sid)
            self._onvif_misses.pop(sid, None)  # responded this scan -> reset miss counter
            known = self.repo.get_sensor(sid)
            if known is not None:
                # Re-appeared after monitoring marked it offline -> bring back online + re-announce.
                if (known.sensor_status or 0) == 0:
                    await self._mark_onvif_online(known)
                continue  # already known -> no log (avoid spam)
            # Cap the number of registered sensors (parity with C++ isSpaceForNewSensor): once the
            # limit is hit, stop persisting newly discovered devices so the DB / list / streams are
            # not flooded by every camera on the network.
            if not self._has_capacity(count):
                hit_cap = True
                continue
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
            count += 1
            added += 1
        self._note_cap(hit_cap, "ONVIF devices")
        await self._handle_onvif_offline(seen)
        return added

    async def _mark_onvif_online(self, row) -> None:
        """An ONVIF sensor re-appeared in discovery after being marked offline: restore online
        status and re-announce camera_add (+ camera_proxy when a credentialed main stream exists),
        so downstream rebuilds its proxy. Mirrors C++ SensorMonitoring onSensorChanged (online)."""
        streams = self.repo.list_streams(row.sensor_id)
        main = next((st for st in streams if (st.stream_ismainstream or "").lower() == "true"), None)
        http = mapping.CAMERA_NO_ERROR_CODE if main is not None else mapping.CAMERA_UNAUTHORIZED_CODE
        now = _now_iso()
        self.repo.set_sensor_status(row.sensor_id, 1, http_status=http, now_iso=now)
        log.info("ONVIF device back online: %s (%s)", row.name, row.sensor_id)
        await self._events.publish(build_payload(
            change=ChangeEvent.camera_add, camera_id=row.sensor_id, camera_name=row.name or "",
            camera_url="", tags=row.tags or "", created_at=now, metadata=None))
        if main is not None:
            url = _embed_creds(main.stream_live_url or "", row.username or "",
                               self.repo.get_password(row.sensor_id) or "")
            await self._events.publish(build_payload(
                change=ChangeEvent.camera_proxy, camera_id=row.sensor_id, camera_name=row.name or "",
                camera_url=url, tags=row.tags or "", created_at=now,
                metadata={"codec": main.stream_encoding or "", "framerate": main.stream_framerate or "",
                          "resolution": main.stream_resolution or ""}))

    async def _handle_onvif_offline(self, seen: set[str]) -> None:
        """Mark ONVIF sensors that stopped replying to discovery for several consecutive scans as
        offline and publish camera_remove so downstream tears down the proxy. Debounced via
        ONVIF_DISCOVERY_MISS_THRESHOLD so transient UDP loss is not treated as a removal. The DB row
        is kept (an authorized sensor persists); only the status flips and one event is emitted.
        Mirrors C++ SensorMonitoring onSensorRemoved/notifyEvent."""
        for r in self.repo.list_sensors():
            if r.type != SENSOR_TYPE_ONVIF or r.sensor_id in seen:
                continue
            misses = self._onvif_misses.get(r.sensor_id, 0) + 1
            self._onvif_misses[r.sensor_id] = misses
            if misses != ONVIF_DISCOVERY_MISS_THRESHOLD:
                continue  # debounced: act exactly once when the threshold is first crossed
            # Only a currently-online CREDENTIALED camera transitions offline + emits camera_remove
            # (it has a downstream proxy to tear down, and _mark_onvif_online re-announces it on
            # return). Discovered-but-uncredentialed devices (http_status 401) are left as-is: no
            # proxy exists, and WS-Discovery multicast loss makes them flap, so emitting
            # camera_remove/add (or "back online" churn) for them would be pure noise -- their removal
            # is logged at DEBUG only, while real (credentialed) removals stay at INFO.
            if (r.sensor_status or 0) != 0 and r.http_status == mapping.CAMERA_NO_ERROR_CODE:
                log.info("ONVIF device removed from network: %s (%s)", r.name, r.sensor_id)
                self._ntp_configured.discard(r.sensor_id)  # re-apply NTP when it returns
                now = _now_iso()
                self.repo.set_sensor_status(r.sensor_id, 0,
                                            http_status=ONVIF_OFFLINE_HTTP_CODE, now_iso=now)
                await self._events.publish(build_payload(
                    change=ChangeEvent.camera_remove, camera_id=r.sensor_id, camera_name=r.name or "",
                    camera_url="", tags=r.tags or "", created_at=now, metadata=None))
            else:
                log.debug("ONVIF device removed from network (uncredentialed): %s (%s)",
                          r.name, r.sensor_id)

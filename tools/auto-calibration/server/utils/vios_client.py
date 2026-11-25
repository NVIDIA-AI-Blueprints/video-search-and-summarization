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

"""
VIOS (Video Storage Technology / Video IO and Storage) Microservice Client

Provides an async HTTP client for interacting with the NVIDIA VST/VIOS REST API.

Verified API paths (NVIDIA VST 2.1.x, nginx-proxied at /vst/):
  Sensor Management:
    GET  /vst/api/v1/sensor/list                       – List all registered sensors
    POST /vst/api/v1/sensor/add                        – Register an IP/ONVIF camera
    GET  /vst/api/v1/sensor/{sensorId}/info            – Get sensor details
    GET  /vst/api/v1/sensor/status                     – Live status for all sensors

  Record Management:
    GET  /vst/api/v1/record/status                     – Per-sensor recording status
                                                         (e.g. "alwaysOn", "off")

  Storage:
    GET  /vst/api/v1/storage/timelines                 – Available clip ranges per sensor
    GET  /vst/api/v1/storage/file/{sensorId}
             ?startTime=<ISO8601>&endTime=<ISO8601>
             &disableAudio=true                        – Stream-download an MP4 clip

Notes on VIOS recording model:
  * VIOS 2.x operates in "alwaysRecording" mode by default.  Recording starts
    automatically when a sensor is registered; there is no per-session
    start/stop API.
  * RTSP streams sourced from nvstreamer are registered via the
    ``rtsp_streams.json`` config file (not the REST /sensor/add endpoint which
    is designed for ONVIF/IP cameras and requires credentials).
  * Sensors whose streams originate from nvstreamer already have a
    ``sensor_id`` that can be passed directly in RtspStreamConfig.
  * For the download endpoint, ``startTime`` / ``endTime`` must fall within an
    available timeline window (query /storage/timelines first).

Reference: https://docs.nvidia.com/vss/latest/vios-microservices.html
"""

import logging
import re
from typing import Optional, List, Dict, Any

import httpx

logger = logging.getLogger(__name__)


class ViosClientError(Exception):
    """Raised when a VIOS API call returns an error response."""
    def __init__(self, message: str, status_code: Optional[int] = None):
        super().__init__(message)
        self.status_code = status_code


class ViosClient:
    """
    Async HTTP client for the NVIDIA VST / VIOS Microservice.

    Args:
        base_url:   VST base URL including the ``/vst`` nginx prefix,
                    e.g. ``http://vios-host:30888``.
                    The client prepends ``/vst/api/v1`` to every path.
        token:      Optional Bearer token (only needed when VST authentication
                    is enabled; ``useHttpDigestAuthentication: true``).
        ssl_verify: Verify TLS certificates.  Default False for self-signed
                    certs inside a Docker/K8s network.
        timeout:    HTTP request timeout in seconds.
    """

    _API_PREFIX = "/vst/api/v1"
    _SENSOR_EXISTS_RE = re.compile(r"\bsensorId\s*:\s*([^,\s]+)", re.IGNORECASE)
    _DUPLICATE_STREAM_ERROR = "Sensor exists already"
    _NAME_CLASH_ERROR = "User given name is invalid or already exists"

    def __init__(
        self,
        base_url: str,
        token: Optional[str] = None,
        ssl_verify: bool = False,
        timeout: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.ssl_verify = ssl_verify
        self.timeout = timeout
        self._headers: Dict[str, str] = {"Content-Type": "application/json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}{self._API_PREFIX}/{path.lstrip('/')}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            verify=self.ssl_verify,
            headers=self._headers,
            timeout=self.timeout,
        )

    # ------------------------------------------------------------------
    # Sensor Management
    # ------------------------------------------------------------------

    async def list_sensors(self) -> List[Dict[str, Any]]:
        """
        GET /vst/api/v1/sensor/list

        Returns all sensors registered in VST.

        Response schema (each item):
            {
              "sensorId": "cam_00",
              "name":     "cam_00",
              "state":    "online" | "offline",
              "isTimelinePresent": true,
              "type":     "sensor_rtsp",
              ...
            }
        """
        async with self._client() as client:
            resp = await client.get(self._url("sensor/list"))
            self._raise_for_status(resp, "list_sensors")
            data = resp.json()
            return data if isinstance(data, list) else data.get("sensors", [])

    async def get_sensor(self, sensor_id: str) -> Dict[str, Any]:
        """
        GET /vst/api/v1/sensor/{sensorId}/info

        Retrieve details for a specific sensor.
        """
        async with self._client() as client:
            resp = await client.get(self._url(f"sensor/{sensor_id}/info"))
            self._raise_for_status(resp, f"get_sensor({sensor_id})")
            return resp.json()

    async def get_sensor_name(self, sensor_id: str) -> Optional[str]:
        """Return the VIOS registered sensor name, if the sensor exists and exposes one."""
        data = await self.get_sensor(sensor_id)
        return self._sensor_name_from_payload(data)

    async def verify_sensor_registered(self, sensor_id: str) -> bool:
        """
        Return True if the sensor exists in VST.

        VIOS returns HTTP 200 with a JSON body of ``null`` for unknown sensor
        IDs (rather than HTTP 404), so we must inspect the body — a truthy
        dict means the sensor exists. HTTP 404 is handled defensively for
        any VIOS build that does return it. Other errors propagate as
        :class:`ViosClientError`.
        """
        try:
            data = await self.get_sensor(sensor_id)
            return bool(data)
        except ViosClientError as exc:
            if exc.status_code == 404:
                return False
            raise

    async def register_sensor(
        self,
        name: str,
        rtsp_url: str,
        username: str = "",
        password: str = "",
        location: str = "",
        tags: str = "",
    ) -> tuple[str, bool]:
        """
        POST /vst/api/v1/sensor/add

        Register a plain RTSP stream as a VST sensor.

        Works for both nvstreamer RTSP streams and physical RTSP/ONVIF cameras.
        VIOS assigns a UUID ``sensorId`` automatically and starts recording
        immediately (``alwaysRecording: true``).

        Request body:
            {
              "name":      "Front Camera",
              "sensorUrl": "rtsp://...",
              "username":  "",
              "password":  "",
              "location":  "",
              "tags":      ""
            }

        Returns:
            Tuple ``(sensor_id, was_created)``. ``was_created`` is False when
            VIOS reports that this RTSP stream already exists and returns the
            existing ``sensorId``.

        Raises:
            ViosClientError: If the requested name already exists but the RTSP
                stream does not. Callers should use a different
                ``camera_name`` in that case.
        """
        payload = {
            "name": name,
            "sensorUrl": rtsp_url,
            "username": username,
            "password": password,
            "location": location,
            "tags": tags,
        }
        async with self._client() as client:
            resp = await client.post(self._url("sensor/add"), json=payload)

            if self._is_duplicate_stream_response(resp):
                existing_sensor_id = self._extract_existing_sensor_id(resp)
                if not existing_sensor_id:
                    raise ViosClientError(
                        f"register_sensor({name}): duplicate RTSP stream response did not include sensorId: "
                        f"{self._error_message(resp)}",
                        status_code=resp.status_code,
                    )
                logger.info(
                    "register_sensor(%s): RTSP stream already exists in VIOS as sensor %s",
                    name, existing_sensor_id,
                )
                return existing_sensor_id, False

            if self._is_name_clash_response(resp):
                raise ViosClientError(
                    f"register_sensor({name}): Sensor name already exists in VIOS and "
                    "RTSP URL was not previously registered, use different camera name",
                    status_code=resp.status_code,
                )

            return self._registered_sensor_id(resp, name), True

    @classmethod
    def _extract_existing_sensor_id(cls, resp: httpx.Response) -> Optional[str]:
        """Return sensorId from VIOS error messages, if present."""
        if resp.status_code != 400:
            return None
        message = cls._error_message(resp)
        match = cls._SENSOR_EXISTS_RE.search(message)
        return match.group(1) if match else None

    @staticmethod
    def _sensor_name_from_payload(data: Any) -> Optional[str]:
        if not isinstance(data, dict):
            return None
        for key in ("name", "sensorName", "sensor_name"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        sensor_data = data.get("sensor")
        if isinstance(sensor_data, dict):
            for key in ("name", "sensorName", "sensor_name"):
                value = sensor_data.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    @classmethod
    def _is_duplicate_stream_response(cls, resp: httpx.Response) -> bool:
        if resp.status_code != 400:
            return False
        return cls._DUPLICATE_STREAM_ERROR.lower() in cls._error_message(resp).lower()

    @classmethod
    def _is_name_clash_response(cls, resp: httpx.Response) -> bool:
        if resp.status_code != 400:
            return False
        return cls._NAME_CLASH_ERROR.lower() in cls._error_message(resp).lower()

    @staticmethod
    def _error_message(resp: httpx.Response) -> str:
        try:
            data = resp.json()
        except ValueError:
            return resp.text
        if isinstance(data, dict):
            return str(data.get("error_message") or data)
        return resp.text

    def _registered_sensor_id(self, resp: httpx.Response, name: str) -> str:
        self._raise_for_status(resp, f"register_sensor({name})")
        data = resp.json()
        sensor_id = data.get("sensorId") if isinstance(data, dict) else None
        if not sensor_id:
            raise ViosClientError(f"register_sensor({name}): no sensorId in response: {data}")
        return sensor_id

    async def deregister_sensor(self, sensor_id: str) -> bool:
        """
        DELETE /vst/api/v1/sensor/{sensorId}

        Remove a sensor from VST.  Returns True on success.
        """
        async with self._client() as client:
            resp = await client.delete(self._url(f"sensor/{sensor_id}"))
            if resp.status_code in (200, 204):
                return True
            logger.warning("deregister_sensor(%s) → HTTP %d: %s", sensor_id, resp.status_code, resp.text)
            return False

    # ------------------------------------------------------------------
    # Record Management
    # ------------------------------------------------------------------

    async def get_record_status(self) -> Dict[str, Any]:
        """
        GET /vst/api/v1/record/status

        Returns per-sensor recording status.

        Response:
            {
              "cam_00": {"id": "cam_00", "recording_status": "alwaysOn"},
              "cam_01": {"id": "cam_01", "recording_status": "off"},
              ...
            }
        """
        async with self._client() as client:
            resp = await client.get(self._url("record/status"))
            self._raise_for_status(resp, "get_record_status")
            data = resp.json()
            return data if isinstance(data, dict) else {}

    async def is_sensor_recording(self, sensor_id: str) -> bool:
        """Return True if the given sensor is actively recording."""
        status = await self.get_record_status()
        entry = status.get(sensor_id, {})
        return entry.get("recording_status") in ("alwaysOn", "on", "user")

    async def start_recording(self, sensor_id: str) -> bool:
        """
        POST /vst/api/v1/record/{sensor_id}/start

        Start recording for a sensor that is currently not recording.
        Returns True on success, False if VIOS rejected the request.

        Request body:
            {"sensorId": "<sensor_id>"}
        """
        async with self._client() as client:
            resp = await client.post(
                self._url(f"record/{sensor_id}/start"),
                json={"sensorId": sensor_id},
            )
            if resp.status_code in (200, 201, 204):
                logger.info("start_recording(%s): success (HTTP %d)", sensor_id, resp.status_code)
                return True
            logger.warning(
                "start_recording(%s): HTTP %d – %s", sensor_id, resp.status_code, resp.text
            )
            return False

    async def stop_recording(self, sensor_id: str) -> bool:
        """
        POST /vst/api/v1/record/{sensor_id}/stop

        Stop recording for a sensor.
        Returns True on success, False if VIOS rejected the request.
        """
        async with self._client() as client:
            resp = await client.post(
                self._url(f"record/{sensor_id}/stop"),
                json={"sensorId": sensor_id},
            )
            if resp.status_code in (200, 201, 204):
                logger.info("stop_recording(%s): success (HTTP %d)", sensor_id, resp.status_code)
                return True
            logger.warning(
                "stop_recording(%s): HTTP %d – %s", sensor_id, resp.status_code, resp.text
            )
            return False

    # ------------------------------------------------------------------
    # Storage / Timelines
    # ------------------------------------------------------------------

    async def get_timelines(self, sensor_id: Optional[str] = None) -> Dict[str, List[Dict]]:
        """
        GET /vst/api/v1/storage/timelines

        Return available clip windows for all sensors (or a specific one).

        Response:
            {
              "cam_00": [
                {"startTime": "2025-01-01T00:00:00Z", "endTime": "2025-01-01T00:01:00Z"},
                ...
              ],
              ...
            }
        """
        async with self._client() as client:
            resp = await client.get(self._url("storage/timelines"))
            self._raise_for_status(resp, "get_timelines")
            data = resp.json()
            if not isinstance(data, dict):
                data = {}
            if sensor_id:
                return {sensor_id: data.get(sensor_id, [])}
            return data

    async def find_clip_window(
        self,
        sensor_id: str,
        after: str,
        before: str,
    ) -> Optional[Dict[str, str]]:
        """
        Find a timeline clip that overlaps with [after, before].

        Returns {"startTime": ..., "endTime": ...} of the best matching clip,
        or None if no overlapping clip is available.

        Args:
            sensor_id: VST sensor ID.
            after:     ISO 8601 UTC lower bound (e.g. recording_start_time).
            before:    ISO 8601 UTC upper bound (e.g. recording_end_time).
        """
        timelines = await self.get_timelines(sensor_id)
        if not isinstance(timelines, dict):
            return None
        clips = timelines.get(sensor_id, [])
        for clip in clips:
            clip_start = clip.get("startTime", "")
            clip_end = clip.get("endTime", "")
            # Overlap check: clip_start < before AND clip_end > after
            if clip_start < before and clip_end > after:
                # Clamp to the requested window
                start = max(clip_start, after)
                end = min(clip_end, before)
                return {"startTime": start, "endTime": end}
        return None

    async def download_video(
        self,
        sensor_id: str,
        start_time: str,
        end_time: str,
        output_path: str,
    ) -> bool:
        """
        GET /vst/api/v1/storage/file/{sensorId}
            ?startTime=<ISO8601>&endTime=<ISO8601>&disableAudio=true

        Stream-download a recorded clip and write it to *output_path* as MP4.

        ``startTime`` and ``endTime`` must overlap an existing timeline window
        (use :meth:`get_timelines` or :meth:`find_clip_window` to confirm).

        Returns:
            True on successful download.

        Raises:
            ViosClientError: on HTTP error or I/O failure.
        """
        params = {
            "startTime": start_time,
            "endTime": end_time,
            "disableAudio": "true",
        }
        try:
            async with self._client() as client:
                async with client.stream(
                    "GET",
                    self._url(f"storage/file/{sensor_id}"),
                    params=params,
                ) as resp:
                    self._raise_for_status(resp, f"download_video(sensor={sensor_id})")
                    with open(output_path, "wb") as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            fh.write(chunk)
            return True
        except ViosClientError:
            raise
        except OSError as exc:
            raise ViosClientError(f"Failed to write video to {output_path}: {exc}") from exc

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _raise_for_status(resp: httpx.Response, context: str) -> None:
        """Raise ViosClientError for non-2xx responses."""
        if resp.status_code >= 400:
            raise ViosClientError(
                f"VIOS {context} failed – HTTP {resp.status_code}: {resp.text}",
                status_code=resp.status_code,
            )

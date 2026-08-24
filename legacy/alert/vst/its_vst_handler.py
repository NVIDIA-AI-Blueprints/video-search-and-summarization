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

import base64
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import pandas as pd
import requests
from pandas.core.frame import DataFrame

from vst.exceptions import (
    VSTError,
    VSTClientError,
    VSTOverloadedError,
    VSTRecordingNotFoundError,
    VSTTimeoutError,
    VSTUnavailableError,
)

logger = logging.getLogger(__name__)


class ITS_VST_HANDLER:
    def __init__(self, config: dict):
        self.config = config
        self.logger = logging.getLogger(self.__class__.__name__)
        self._vst_stream_status_cache = {}  # More descriptive cache name
        self._cache_duration = self.config.get('vst_config', {}).get('stream_status_cache_duration', 60)  # Default 60 seconds
        self.add_overlay = self.config.get("vst_config", {}).get("add_overlay", False)
        self.url_retention_minutes = self.config.get("vst_config", {}).get("url_retention_minutes", 1440)
        self.base_url = self.config.get('vst_config', {}).get('base_url')
        self.vst_config = self.config.get("vst_config", {})
        overlay_cfg = self.vst_config.get("overlay", {})
        self.overlay_config = {
            "color": overlay_cfg.get("color", "green"),
            "thickness": overlay_cfg.get("thickness", 5),
            "opacity": overlay_cfg.get("opacity", 254),
            "debug": overlay_cfg.get("debug", True),
            "showObjId": overlay_cfg.get("showObjId", False),
            "objIdPosition": overlay_cfg.get("objIdPosition", 0),
        }
        if not self.base_url:
            raise ValueError("VST base URL not configured")

    def _compute_effective_time_window(
        self, start_time: str, end_time: str, alert_type_anchor: Optional[str] = None
    ) -> Tuple[str, str]:
        """
        Compute the [start, end] window to request from VST using the configured
        segment duration (`segment_duration_seconds`) and anchor
        (`segment_anchor`).

        Behavior overview:
        - Inputs:
          - start_time and end_time: must be ISO-8601 UTC strings; missing/invalid values raise
          ValueError.
          - alert_type_anchor: Optional per-alert-type anchor override ("start", "end", "middle").
          Takes precedence over global vst_config.segment_anchor.
        - When `segment_duration_seconds` (M) <= 0: return [start, end] after
          enforcing a minimum 1s duration (extending the end when needed). The
          original strings are preserved when no adjustment is required.
        - Anchor = "start" (default):
            [start, start + M]. The provided start string is returned verbatim
            to preserve formatting such as ".000Z"; the end is normalized with
            a trailing "Z".
        - Anchor = "end":
            Clamp the provided end to `now`. If the available interval is <= M,
            pass through [start, clamped_end]; otherwise return
            [clamped_end - M, clamped_end]. Always ensure >= 1s by moving the
            start backward (end stays fixed).
        - Anchor = "middle":
            Clamp end to `now`. If the interval <= M, pass through [start,
            clamped_end]. Otherwise, center an M-second window (integer-second
            precision) around the midpoint of [start, clamped_end] and shift the
            window backward when the computed end would exceed `now`. Ensure
            >= 1s by moving the start backward.
        """
        now_utc = datetime.now(timezone.utc)
        anchor, duration = self._determine_anchor_and_duration(alert_type_anchor)

        start_dt = self._parse_required_iso_utc(start_time, "start_time")
        end_dt = self._parse_required_iso_utc(end_time, "end_time")

        # Warn and correct if incoming event has invalid time range (end before start)
        if end_dt < start_dt:
            delta = (start_dt - end_dt).total_seconds()
            self.logger.warning(
                "Invalid event time range: end_time (%s) is before start_time (%s), delta=%.1fs",
                end_time, start_time, delta
            )
            end_dt = start_dt
            end_time = start_time

        if duration <= 0:
            eff_start_str, eff_end_str = self._pass_through_with_minimum(
                start_time, end_time, start_dt, end_dt
            )
            self._log_effective_window(anchor, start_time, end_time, eff_start_str, eff_end_str)
            return eff_start_str, eff_end_str

        if anchor == 'end':
            eff_start, eff_end = self._window_end_anchor(start_dt, end_dt, duration, now_utc)
            eff_start, eff_end = self._ensure_minimum_with_fixed_end(eff_start, eff_end)
            eff_start_str = self._normalize_to_z(eff_start)
            eff_end_str = self._normalize_to_z(eff_end)
            self._log_effective_window(anchor, start_time, end_time, eff_start_str, eff_end_str)
            return eff_start_str, eff_end_str

        if anchor == 'middle':
            eff_start, eff_end = self._window_middle_anchor(start_dt, end_dt, duration, now_utc)
            eff_start, eff_end = self._ensure_minimum_with_fixed_end(eff_start, eff_end)
            eff_start_str = self._normalize_to_z(eff_start)
            eff_end_str = self._normalize_to_z(eff_end)
            self._log_effective_window(anchor, start_time, end_time, eff_start_str, eff_end_str)
            return eff_start_str, eff_end_str

        # Default "start" behavior
        eff_start, eff_end = self._window_start_anchor(start_dt, end_dt, duration, now_utc)
        eff_start, eff_end = self._ensure_minimum_with_fixed_end(eff_start, eff_end)
        eff_start_str = self._normalize_to_z(eff_start)
        eff_end_str = self._normalize_to_z(eff_end)
        self._log_effective_window(anchor, start_time, end_time, eff_start_str, eff_end_str)
        return eff_start_str, eff_end_str

    def _parse_required_iso_utc(self, value: str, field_name: str) -> datetime:
        """Parse an ISO-8601 UTC string, raising a descriptive error if invalid."""
        if not value:
            raise ValueError(f"{field_name} is required to compute the VST segment window")
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except Exception as exc:
            raise ValueError(f"{field_name} must be an ISO-8601 UTC string: {value}") from exc
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _normalize_to_z(dt: datetime) -> str:
        """Return a UTC ISO string with a trailing Z."""
        iso_str = dt.isoformat()
        if iso_str.endswith("+00:00"):
            return iso_str[:-6] + "Z"
        return iso_str

    @staticmethod
    def _cap_future_to_now(dt: datetime, now_dt: datetime) -> datetime:
        """Clamp the datetime to `now_dt` when it lies in the future."""
        return dt if dt <= now_dt else now_dt

    def _determine_anchor_and_duration(self, alert_type_anchor: Optional[str] = None) -> Tuple[str, int]:
        """Read vst_config and return (anchor, duration_in_seconds).

        Anchor fallback order:
        1. Per-alert-type segment_anchor (alert_type_anchor param)
        2. Global vst_config.segment_anchor
        3. Default: "end"

        Args:
            alert_type_anchor: Optional per-alert-type anchor override.
                              Takes precedence over global config if provided.

        Returns:
            Tuple of (anchor, duration_in_seconds)
        """
        vst_cfg = self.config.get('vst_config', {}) or {}

        # Fallback order: per-alert-type -> global config -> default "end"
        anchor = (alert_type_anchor or vst_cfg.get('segment_anchor') or 'end').lower()
        if anchor not in {'start', 'end', 'middle'}:
            anchor = 'end'
        try:
            duration = int(vst_cfg.get('segment_duration_seconds') or 0)
        except Exception:
            duration = 0
        return anchor, duration

    @staticmethod
    def _ensure_minimum_with_fixed_start(start_dt: datetime, end_dt: datetime) -> Tuple[datetime, datetime]:
        """Ensure duration >= 1s by extending the end while keeping the start fixed."""
        if (end_dt - start_dt).total_seconds() >= 1.0:
            return start_dt, end_dt
        return start_dt, start_dt + timedelta(seconds=1)

    @staticmethod
    def _ensure_minimum_with_fixed_end(start_dt: datetime, end_dt: datetime) -> Tuple[datetime, datetime]:
        """Ensure duration >= 1s by moving the start backward while keeping the end fixed."""
        if (end_dt - start_dt).total_seconds() >= 1.0:
            return start_dt, end_dt
        return end_dt - timedelta(seconds=1), end_dt

    def _window_start_anchor(self, start_dt: datetime, end_dt: datetime, duration: int, now_dt: datetime) -> Tuple[datetime, datetime]:
        """Return [start, min(start+duration, end, now)] for start-anchored mode."""
        capped_end = self._cap_future_to_now(end_dt, now_dt)  # min(end, now)
        eff_end = start_dt + timedelta(seconds=duration)
        if eff_end > capped_end:
            delta = (eff_end - capped_end).total_seconds()
            self.logger.debug(
                "Start anchor: clamping effective_end from %s to %s, delta=%.1fs",
                eff_end.isoformat(), capped_end.isoformat(), delta
            )
            eff_end = capped_end
        return start_dt, eff_end

    def _window_end_anchor(self, start_dt: datetime, end_dt: datetime, duration: int, now_dt: datetime) -> Tuple[datetime, datetime]:
        """Anchor the window on the end timestamp (clamped to now)."""
        capped_end = self._cap_future_to_now(end_dt, now_dt)
        available_secs = (capped_end - start_dt).total_seconds()
        if available_secs <= duration:
            return start_dt, capped_end
        return capped_end - timedelta(seconds=duration), capped_end

    def _window_middle_anchor(self, start_dt: datetime, end_dt: datetime, duration: int, now_dt: datetime) -> Tuple[datetime, datetime]:
        """Center an M-second window around the midpoint of [start, end]."""
        capped_end = self._cap_future_to_now(end_dt, now_dt)
        interval_secs = int((capped_end - start_dt).total_seconds())
        if interval_secs <= duration:
            return start_dt, capped_end

        start_epoch = int(start_dt.timestamp())
        end_epoch = int(capped_end.timestamp())
        center_epoch = start_epoch + (end_epoch - start_epoch) // 2
        start_window_epoch = center_epoch - duration // 2
        end_window_epoch = start_window_epoch + duration

        now_epoch = int(now_dt.timestamp())
        if end_window_epoch > now_epoch:
            end_window_epoch = now_epoch
            start_window_epoch = end_window_epoch - duration

        eff_start = datetime.fromtimestamp(start_window_epoch, tz=timezone.utc)
        eff_end = datetime.fromtimestamp(end_window_epoch, tz=timezone.utc)
        return eff_start, eff_end

    def _pass_through_with_minimum(
        self,
        start_str: str,
        end_str: str,
        start_dt: datetime,
        end_dt: datetime,
    ) -> Tuple[str, str]:
        """Return inputs when possible; otherwise enforce >=1s by extending the end."""
        adj_start, adj_end = self._ensure_minimum_with_fixed_start(start_dt, end_dt)
        if adj_start == start_dt and adj_end == end_dt:
            return start_str, end_str
        return self._normalize_to_z(adj_start), self._normalize_to_z(adj_end)

    def _log_effective_window(
        self,
        anchor: str,
        original_start: str,
        original_end: str,
        effective_start: str,
        effective_end: str,
    ) -> None:
        """Emit a debug log describing the window transformation."""
        self.logger.debug(
            "VST effective window (anchor=%s): start=%s end=%s -> effective_start=%s effective_end=%s",
            anchor,
            original_start,
            original_end,
            effective_start,
            effective_end,
        )

    # ------------------------------
    # VST storage helpers (modularized)
    # ------------------------------
    def _get_storage_config(self) -> Tuple[str, str, int]:
        """Return (base_url, endpoint, timeout) for VST storage lookup.

        If STORAGE_MODULE_ENDPOINT is provided in the environment, it is expanded
        (e.g., "http://${HOST_IP}:${STORAGE_HTTP_PORT}") and used to fully
        override the storage base URL. Otherwise, falls back to config values.
        """
        vst_cfg = self.config.get('vst_config', {})
        storage_cfg = vst_cfg.get('storage', {})
        storage_base = storage_cfg.get('base_url') or self.base_url

        env_endpoint = os.environ.get('STORAGE_MODULE_ENDPOINT')
        if env_endpoint:
            try:
                # Expand $VARS in the provided endpoint string
                storage_base = os.path.expandvars(env_endpoint)
            except Exception:
                # If expansion fails, use the raw env value
                storage_base = env_endpoint

        storage_ep = storage_cfg.get('media_file_path_by_id_endpoint', '/api/v1/storage/file/path')
        timeout = vst_cfg.get('request_timeout', 10)
        return storage_base, storage_ep, timeout

    def _request_storage_lookup(self, url: str, vst_id: str, timeout: int) -> dict:
        """Call storage endpoint and return JSON payload or {} on empty body.

        Raises requests.RequestException for HTTP/network errors.
        """
        response = requests.get(url, params={'id': vst_id}, timeout=timeout)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def _extract_media_path(self, payload: dict) -> Optional[str]:
        """Extract mediaFilePath from storage payload using multiple key variants."""
        return (
            payload.get('mediaFilePath')
            or payload.get('media_file_path')
            or payload.get('mediafilepath')
        )

    def _resolve_media_path(self, vst_id: str, media_path: str) -> str:
        """Apply optional base-dir mapping and log final resolved path."""
        base_dir = (
            os.environ.get('ALERT_REVIEW_MEDIA_BASE_DIR')
            or self.config.get('ALERT_REVIEW_MEDIA_BASE_DIR')
        )
        if base_dir:
            normalized = media_path.lstrip('/')
            resolved_path = os.path.join(base_dir, normalized)
            self.logger.debug(
                f"VST media path resolved with base dir: vst_id={vst_id}, base_dir={base_dir}, resolved_path={resolved_path}"
            )
            return resolved_path

        self.logger.debug(
            f"VST media path resolved without base dir: vst_id={vst_id}, path={media_path}"
        )
        return media_path

    def get_media_file_path_by_vst_id(self, vst_id: str) -> Optional[str]:
        """Resolve the media file path for a given VST storage id.

        This calls the VST storage service endpoint with the provided `vst_id`
        and returns the media file path. If `ALERT_REVIEW_MEDIA_BASE_DIR`
        is configured, the returned path is resolved against that base dir.

        Args:
            vst_id: Identifier returned by VST to locate media in storage

        Returns:
            The resolved media file path (string) if available; otherwise None
        """
        try:
            if not vst_id or not isinstance(vst_id, str):
                self.logger.error("Invalid vst_id provided to storage lookup")
                return None

            storage_base, storage_ep, timeout = self._get_storage_config()
            url = f"{(storage_base or '').rstrip('/')}{storage_ep}"
            self.logger.debug(
                f"VST storage lookup: connecting url={url}, vst_id={vst_id}, timeout={timeout}s"
            )

            payload = self._request_storage_lookup(url, vst_id, timeout)
            self.logger.debug(
                f"VST storage lookup: response received for vst_id={vst_id}"
            )
            media_path = self._extract_media_path(payload)
            if not media_path:
                self.logger.error(
                    f"Storage lookup for vst_id={vst_id} did not include mediaFilePath"
                )
                return None

            return self._resolve_media_path(vst_id, media_path)

        except requests.RequestException as e:
            self.logger.error(
                f"VST storage lookup failed for vst_id={vst_id}: {e}", exc_info=True
            )
            return None
        except Exception as e:
            self.logger.error(
                f"Unexpected error during VST storage lookup for vst_id={vst_id}: {e}",
                exc_info=True,
            )
            return None

    def get_vst_sensor_details(self, url):
        # Check if URL is in cache and not expired
        current_time = time.time()
        if url in self._vst_stream_status_cache:
            cached_response, timestamp = self._vst_stream_status_cache[url]
            if current_time - timestamp < self._cache_duration:
                return cached_response

        # If not in cache or expired, make new request
        response = requests.get(url)
        response.raise_for_status()
        response_data = response.json()

        # Store in cache with current timestamp
        self._vst_stream_status_cache[url] = (response_data, current_time)

        return response_data

    def build_sensor_id_sensor_name_mapping(self, sensor_data):
        """
        Builds a mapping of sensor names to their IDs.

        Args:
            sensor_data (dict): Dictionary where each key is a sensor ID and value is a list
                              containing sensor information dictionaries

        Returns:
            dict: Mapping of sensor names to their corresponding sensor IDs
        """
        name_to_id_mapping = {}

        for sensor_dict in sensor_data:
            for sensor_id, sensor_info_list in sensor_dict.items():
                for sensor_info in sensor_info_list:
                    if "name" in sensor_info:
                        name_to_id_mapping[sensor_info["name"]] = sensor_info["streamId"]

        # logger.debug(f"Sensor data: {sensor_data}")  # Debug logger.debug
        # logger.debug(f"Name to ID mapping: {name_to_id_mapping}")  # Debug logger.debug

        return name_to_id_mapping

    def build_sensor_name_rtsp_url_mapping(self, sensor_data):
        """
        Builds a mapping of sensor names to RTSP URLs.

        Args:
            sensor_data (list): List of dictionaries where each dictionary contains sensor ID as the key
                                and a list of sensor data dictionaries as the value.

        Returns:
            dict: Mapping of sensor names to their corresponding RTSP URLs.
        """
        logger = logging.getLogger(__name__)

        sensor_name_rtsp_mapping = {}

        for sensor_dict in sensor_data:
            for sensor_id, sensor_info_list in sensor_dict.items():
                for sensor_info in sensor_info_list:
                    if sensor_info.get("vodUrl"):
                        logger.debug(
                            f"Processing sensor data item: {sensor_info}")
                        sensor_name_rtsp_mapping[sensor_info["name"]
                                                 ] = sensor_info["vodUrl"]
                    else:
                        logger.debug(
                            f"Skipping sensor data item with missing 'vodUrl': {sensor_info}")

        logger.debug(
            f"Built sensor name to RTSP URL mapping: {sensor_name_rtsp_mapping}")
        return sensor_name_rtsp_mapping

    def build_vst_urls(self, es_results, sensor_dict, base_url, video_buffer_time=10):
        now = datetime.utcnow()
        for entry in es_results:
            sensor_id = entry["sensor_id"]
            device_id = sensor_dict.get(sensor_id)
            if not device_id:
                logger.error(f"Device ID not found for sensor ID: {sensor_id}")
                continue

            start_time = datetime.strptime(
                entry["start"], '%Y-%m-%dT%H:%M:%S.%fZ') - timedelta(seconds=video_buffer_time)
            end_time = datetime.strptime(
                entry["end"], '%Y-%m-%dT%H:%M:%S.%fZ') + timedelta(seconds=video_buffer_time)

            if end_time > now:
                end_time = now

            url = f"{base_url}/{device_id}?startTime={start_time.isoformat()}Z&endTime={end_time.isoformat()}Z&container=mp4"
            entry['url'] = url

        return es_results

    def get_video_stream_url(
        self,
        stream_name: str,
        start_time: str,
        end_time: str,
        objects_ids: Optional[List] = None,
        remove_overlay: bool = False,
        latency: Optional[Dict[str, float]] = None,
        alert_type_anchor: Optional[str] = None,
    ) -> str:
        '''
        Get the video stream url from the VST API.
        Args:
            stream_name: The stream identifier (e.g., 'Building_K_Cam1_clip1')
            start_time: Start timestamp in ISO format (e.g., '2025-08-20T12:40:00.000Z')
            end_time: End timestamp in ISO format (e.g., '2025-08-20T12:41:00.000Z')
            objects_ids: Optional list of object IDs for overlay
            remove_overlay: Whether to remove overlay from video
            latency: Optional dict for tracking latency metrics
            alert_type_anchor: Optional per-alert-type segment anchor override ("start", "end", "middle").
                              Takes precedence over global vst_config.segment_anchor.
                              Fallback order: per-alert-type -> global config -> default "end"

        Returns:
            API response schema for reference
            {'absolutePath': '/home/vst/vst_release/webroot/temp_videos/Warehouse_Camera01_2025-09-10_11_16:31_928__2025-09-10_11_16:33_918_.mp4', 'baseUrl': 'http://localhost:30011', 'endTime': '2025-09-10T11:16:33.918Z', 'expiryISO': '2025-09-10T11:19:23.675Z', 'expiryMinutes': 2, 'expiryTimestamp': 1757503163675, 'fileName': 'Warehouse_Camera01_2025-09-10_11_16:31_928__2025-09-10_11_16:33_918_.mp4', 'startTime': '2025-09-10T11:16:31.928Z', 'streamId': 'Warehouse_Camera01', 'videoUrl': 'http://localhost:30011/temp_videos/Warehouse_Camera01_2025-09-10_11_16:31_928__2025-09-10_11_16:33_918_.mp4'}
            str: The video stream url
        '''
        try:
            latency = latency or {}
            # Determine storage base and timeout using shared helper (honors env override)
            storage_base, _unused_ep, timeout = self._get_storage_config()
            if not storage_base:
                raise ValueError('Storage base URL not configured')

            timeout = self.config.get('vst_config', {}).get('timeout', 20)
            expiry_minutes = self.url_retention_minutes

            stream_id = self._get_stream_id_from_name(stream_name) or stream_name
            endpoint_path = '/vst/api/v1/storage/file/' + stream_id + '/url'
            #endpoint_path = '/vst/api/v1/storage/file/url'
            url = f"{storage_base.rstrip('/')}{endpoint_path}"

            headers = {
                'Accept': 'application/json, text/plain, */*'
            }

            effective_start_time, effective_end_time = self._compute_effective_time_window(
                start_time, end_time, alert_type_anchor
            )

            configuration = {
                "disableAudio": False,
            }

            if self.add_overlay and not remove_overlay:
                configuration["overlay"] = {
                    "bbox": {
                        "showAll": False,
                        "objectId": objects_ids,
                        "showObjId": self.overlay_config["showObjId"],
                        "objIdPosition": self.overlay_config["objIdPosition"],
                    },
                    "color": self.overlay_config["color"],
                    "thickness": self.overlay_config["thickness"],
                    "opacity": self.overlay_config["opacity"],
                    "debug": self.overlay_config["debug"],
                }

            params = {
                'streamId': stream_id,
                'startTime': effective_start_time,
                'endTime': effective_end_time,
                'expiryMinutes': int(expiry_minutes),
                'container': 'mp4',
                'configuration': json.dumps(configuration)
            }

            self.logger.debug(
                f"Requesting VST video URL: url={url}, streamId={stream_id}, start={effective_start_time}, end={effective_end_time}, expiryMinutes={expiry_minutes}, objects_ids={objects_ids}"
            )

            response = requests.get(url, headers=headers, params=params, timeout=timeout)
            self.logger.debug(f"VST video URL response status: {response.status_code}, final_url={response.url}")
            response.raise_for_status()

            # Parse JSON and return the video URL
            data = response.json() if response.content else {}

            video_url = (
                data.get('videoUrl')
            )
            if not video_url:
                raise VSTError(
                    f"videoUrl missing in VST response (HTTP {response.status_code}): {response.text}",
                    status_code=response.status_code,
                    response_body=response.text,
                    category="missing_video_url",
                )

            return video_url, effective_start_time, effective_end_time

        except VSTError:
            raise

        except requests.exceptions.Timeout as e:
            self.logger.error(
                "VST request timed out: url=%s, timeout=%ss, stream=%s",
                url, timeout, stream_name,
            )
            raise VSTTimeoutError(
                f"VST request timed out after {timeout}s for stream={stream_name}",
                category="timeout",
            ) from e

        except requests.exceptions.ConnectionError as e:
            self.logger.error(
                "VST connection failed: url=%s, stream=%s, error=%s",
                url, stream_name, e,
            )
            raise VSTUnavailableError(
                f"Cannot connect to VST at {url}: {e}",
                category="connection_failed",
            ) from e

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else None
            body = e.response.text if e.response is not None else None
            self.logger.error(
                "VST HTTP error: status=%s, url=%s, stream=%s, body=%s",
                status, url, stream_name, body,
            )
            if status == 404:
                raise VSTRecordingNotFoundError(
                    f"No recording found in VST (HTTP {status}): {body}",
                    status_code=status, response_body=body,
                    category="recording_not_found",
                ) from e
            elif status in (429, 503):
                raise VSTOverloadedError(
                    f"VST overloaded (HTTP {status}): {body}",
                    status_code=status, response_body=body,
                    category="overloaded",
                ) from e
            elif status and 400 <= status < 500:
                raise VSTClientError(
                    f"VST client error (HTTP {status}): {body}",
                    status_code=status, response_body=body,
                    category="client_error",
                ) from e
            else:
                raise VSTUnavailableError(
                    f"VST server error (HTTP {status}): {body}",
                    status_code=status, response_body=body,
                    category="server_error",
                ) from e

        except requests.RequestException as e:
            self.logger.error(
                "VST request error: url=%s, stream=%s, error=%s",
                url, stream_name, e,
            )
            raise VSTError(
                f"VST request failed for stream={stream_name}: {e}",
                category="request_error",
            ) from e

        except Exception as e:
            self.logger.error(
                "Unexpected VST error: url=%s, stream=%s, error=%s",
                url, stream_name, e,
            )
            raise VSTError(
                f"Unexpected VST error for stream={stream_name}: {e}",
                category="unexpected",
            ) from e

    def download_clips(self, results_download):
        download_dir = self.config['vst_config']['download_dir']
        if not os.path.exists(download_dir):
            os.makedirs(download_dir)

        delay_time = self.config.get('vst_config', {}).get(
            'delay_before_processing', 0)

        for entry in results_download:
            alert_id = entry.get('request_id')
            url = entry.get('url')
            if not url:
                logger.error(f"No URL found for alert ID: {alert_id}")
                continue
            file_name = url.split('/')[-1].split('?')[0] + f"_{alert_id}.mp4"
            file_path = os.path.join(download_dir, file_name)

            def attempt_download():
                try:
                    response = requests.get(url, stream=True)
                    response.raise_for_status()
                    with open(file_path, 'wb') as file:
                        for chunk in response.iter_content(chunk_size=8192):
                            file.write(chunk)
                    logger.info(f"Downloaded: {file_path}")
                    entry['download_status'] = "Complete"
                    entry['file_path'] = file_path
                    return True
                except requests.exceptions.RequestException as e:
                    logger.error(f"Failed to download {url}: {e}")
                    entry['download_status'] = "Not Complete"
                    return False

            success = attempt_download()

            if not success:
                logger.info(
                    f"Retrying download for alert ID: {alert_id} after {delay_time} seconds.")
                time.sleep(delay_time)
                attempt_download()

        return results_download

    def _download_image(self, timestamp: str, stream_id: str, output_file: str) -> bool:
        """Download an image from the stream API for the given timestamp."""
        try:
            # Format timestamp for API
            dt_obj = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            api_timestamp = dt_obj.isoformat(timespec='milliseconds') + "Z"

            # Construct API URL
            base_url = self.config['vst_config']['image_api_base_url']
            api_url = f"{base_url}/{stream_id}/picture?startTime={api_timestamp}"
            logger.debug(f"Downloading image from: {api_url}")

            # Ensure the output directory exists
            output_dir = os.path.dirname(output_file)
            if output_dir and not os.path.exists(output_dir):
                os.makedirs(output_dir, exist_ok=True)
                logger.debug(f"Created directory: {output_dir}")

            # Get retry configuration
            max_attempts = self.config.get('vst_config', {}).get('image_download_max_attempts', 3)
            retry_delay = self.config.get('vst_config', {}).get('image_download_retry_delay', 0.5)

            for attempt in range(max_attempts):
                try:
                    response = requests.get(api_url, timeout=10)
                    if response.status_code == 200:
                        with open(output_file, 'wb') as f:
                            f.write(response.content)
                        break
                    else:
                        logger.debug(f"Error downloading image on attempt {attempt + 1}: HTTP status {response.status_code}")
                except Exception as e:
                    logger.debug(f"Exception downloading image on attempt {attempt + 1}: {e}")
                time.sleep(retry_delay)
            else:
                logger.debug(f"Failed to download image after {max_attempts} retries: {api_url}")
                return False

            # Write content to file
            with open(output_file, 'wb') as file:
                file.write(response.content)

            if not os.path.exists(output_file) or os.path.getsize(output_file) == 0:
                logger.debug(f"Downloaded file is empty or missing for {timestamp}")
                if os.path.exists(output_file):
                    os.unlink(output_file)
                return False

            logger.debug(f"Successfully downloaded image to {output_file}")
            return True

        except Exception as e:
            logger.debug(f"Exception while downloading image: {str(e)}")
            return False

    def get_vst_rtsp_urls(self):
        vst_base_url = self.config['vst_config']['base_url']
        vst_sensor_list = f"{vst_base_url}{self.config['vst_config']['sensor_list_endpoint']}"

        try:
            sensor_data = self.get_vst_sensor_details(vst_sensor_list)
            # Build sensor-to-RTSP URL mapping
            sensor_name_rtsp_url_mapping = self.build_sensor_name_rtsp_url_mapping(sensor_data)

            # Log the constructed mapping
            logger.debug("Constructed RTSP URL mapping for sensors:")
            for sensor_name, rtsp_url in sensor_name_rtsp_url_mapping.items():
                logger.debug(f"Sensor: {sensor_name}, RTSP URL: {rtsp_url}")

            return sensor_name_rtsp_url_mapping

        except Exception as e:
            # Log the error and return an empty mapping
            logger.error(f"An error occurred while constructing RTSP URL mapping: {e}", exc_info=True)
            return {}

    def _get_stream_id_from_name(self, stream_name):
        """
        Private utility method to get stream ID from stream name.

        Args:
            stream_name (str): Name of the stream

        Returns:
            str: Stream ID if found, None otherwise
        """
        try:
            vst_base_url = self.config['vst_config']['base_url']
            vst_sensor_list = f"{vst_base_url}{self.config['vst_config']['sensor_list_endpoint']}"

            # Get sensor data using cached method
            sensor_data = self.get_vst_sensor_details(vst_sensor_list)

            # Build name to ID mapping
            name_to_id_mapping = self.build_sensor_id_sensor_name_mapping(sensor_data)

            # Look up stream ID
            stream_id = name_to_id_mapping.get(stream_name)
            if not stream_id:
                logger.warning(f"No stream ID found for stream name: {stream_name}")
                return None

            return stream_id

        except Exception as e:
            logger.error(f"Error getting stream ID for {stream_name}: {e}")
            return None

    def check_time_in_recording_with_retries(self, stream_id, start_time, end_time, latency):
        max_attempts = self.config.get('vst_config', {}).get('recording_check_max_attempts', 3)
        process_start_time = time.time()
        for attempt in range(1, max_attempts + 1):
            if self.check_time_in_recording(stream_id, start_time, end_time):
                duration = round(time.time() - process_start_time, 3)
                logger.debug(f"The timestamps is in the recording timeline, after {duration} seconds")
                latency['stream_existence_validation'] = {'success': True, 'duration': duration}
                return True
            else:
                logger.debug(f"The timestamps is not in the recording timeline, retrying... Attempt {attempt}")
                time.sleep(1*attempt**attempt)
        duration = round(time.time() - process_start_time, 3)
        logger.warning(f"The timestamps is not in the recording timeline, after max attempts in {duration} seconds")
        latency['stream_existence_validation'] = {'success': False, 'duration': duration}
        return False

    def check_time_in_recording(self, stream_id, start_time_str, end_time_str):
        """
        Checks if the given start and end times fall within any recording timeline for a stream.

        Args:
            stream_id (str): The ID of the stream to check
            start_time_str (str): Start time in ISO format
            end_time_str (str): End time in ISO format

        Returns:
            bool: True if both times are within recording timelines, False otherwise
        """
        try:
            vst_base_url = self.config['vst_config']['base_url']
            timeline_url = f"{vst_base_url}/vst/api/v1/record/{stream_id}/timelines"

            headers = {
                'Accept': 'application/json, text/plain, */*',
                'streamId': stream_id
            }

            response = requests.get(timeline_url, headers=headers)
            response.raise_for_status()
            timelines = response.json()

            if not timelines:
                logger.debug(f"No recording timeline found for stream {stream_id}")
                return False

            if 'startTime' not in timelines[0] or 'endTime' not in timelines[0]:
                logger.debug(f"Invalid timeline format for stream {stream_id}")
                return False

            start_dt = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            end_dt = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))

            # If timezone-aware, convert to UTC then drop tzinfo for naive comparison
            if start_dt.tzinfo is not None:
                start_dt = start_dt.astimezone(timezone.utc).replace(tzinfo=None)
            if end_dt.tzinfo is not None:
                end_dt = end_dt.astimezone(timezone.utc).replace(tzinfo=None)

            start_found = False
            end_found = False

            for timeline in timelines:
                tl_start = datetime.strptime(timeline['startTime'], '%Y-%m-%dT%H:%M:%S.%fZ')
                tl_end = datetime.strptime(timeline['endTime'], '%Y-%m-%dT%H:%M:%S.%fZ')

                if tl_start <= start_dt <= tl_end:
                    start_found = True
                if tl_start <= end_dt <= tl_end:
                    end_found = True

                if start_found and end_found:
                    logger.debug(f"Time range {start_dt} to {end_dt} found in recording timelines")
                    return True

            logger.debug(f"Time range {start_dt} to {end_dt} not found in recording timelines (start={timelines[0]['startTime']}, end={timelines[0]['endTime']})")
            return False

        except Exception as e:
            logger.error(f"Error checking recording timeline for stream {stream_id}: {e}")
            return False

    def get_sampling_images(self, entities_df: DataFrame) -> DataFrame:
        """
        Enriches sampling entities DataFrame with stream IDs, image URLs, and images.

        Args:
            entities_df: DataFrame of SamplingEntity instances with at least 'sensor_name' column

        Returns:
            DataFrame with added/updated 'stream_id', 'image_url', and 'sampled_image' columns
        """
        try:
            # Create copy to avoid modifying original
            result_df = entities_df.copy()

            # Process each row
            for idx, row in result_df.iterrows():
                try:
                    # Get stream ID, URL and snapshot
                    result = self._get_stream_snapshot(row['sensorName'])
                    if result:
                        stream_id, image_url, image_data = result
                        result_df.at[idx, 'streamId'] = stream_id
                        result_df.at[idx, 'imageUrl'] = image_url
                        result_df.at[idx, 'sampledImage'] = image_data

                        self.logger.debug(
                            f"Successfully processed sensor {row['sensorName']} "
                            f"(stream_id: {stream_id})"
                        )
                    else:
                        self.logger.warning(
                            f"Could not get snapshot for sensor {row['sensorName']}"
                        )

                except Exception as e:
                    self.logger.error(
                        f"Error processing sensor {row['sensorName']}: {e}",
                        exc_info=True
                    )

            # Filter out rows without required data
            processed_df = result_df.dropna(subset=['streamId', 'imageUrl', 'sampledImage'])

            self.logger.info(
                f"Processed {len(processed_df)} out of {len(entities_df)} entities successfully"
            )
            return processed_df

        except Exception as e:
            self.logger.error(f"Error in get_sampling_images: {e}", exc_info=True)
            return pd.DataFrame()  # Return empty DataFrame on error

    def _get_stream_snapshot(self, sensor_name: str) -> Optional[Tuple[str, str, bytes]]:
        """
        Gets a snapshot from a stream.

        Args:
            sensor_name: Name of the sensor/stream

        Returns:
            Tuple of (stream_id, image_url, image_data) if successful, None otherwise
        """
        try:
            # Get stream ID from sensor name
            stream_id = self._get_stream_id_from_name(sensor_name)
            if not stream_id:
                self.logger.error(f"Could not find stream ID for sensor {sensor_name}")
                return None

            # Build image URL using existing method
            image_url = self._build_snapshot_url(stream_id)
            if not image_url:
                self.logger.error(f"Could not build snapshot URL for stream {stream_id}")
                return None

            # Download image using existing method
            image_data = self._download_snapshot(image_url)
            if image_data is None:
                self.logger.error(f"Could not download snapshot from {image_url}")
                return None

            return stream_id, image_url, image_data

        except Exception as e:
            self.logger.error(f"Error in get_stream_snapshot: {e}")
            return None

    def _build_snapshot_url(self, stream_id: str) -> Optional[str]:
        """
        Builds the snapshot URL for a given stream ID.

        Args:
            stream_id: Stream/sensor ID

        Returns:
            Complete snapshot URL if successful, None otherwise
        """
        try:
            if not stream_id:
                return None

            # Get endpoints from config
            live_endpoint = self.config.get('vst_config', {}).get('live_endpoint', '/live')
            picture_endpoint = self.config.get('vst_config', {}).get('picture_endpoint', '/picture')

            # Build URL
            snapshot_url = f"{self.base_url.rstrip('/')}{live_endpoint}/stream/{stream_id}{picture_endpoint}"

            self.logger.debug(f"Built snapshot URL: {snapshot_url} for stream {stream_id}")
            return snapshot_url

        except Exception as e:
            self.logger.error(f"Error building snapshot URL for stream {stream_id}: {e}")
            return None

    def _download_snapshot(self, url: str) -> Optional[bytes]:
        """
        Downloads snapshot image from the given URL.

        Args:
            url: URL to download the snapshot from

        Returns:
            Image data as bytes if successful, None otherwise
        """
        try:
            # Get timeout from config or use default
            timeout = self.config.get('vst_config', {}).get('request_timeout', 10)

            response = requests.get(url, timeout=timeout)
            response.raise_for_status()

            content_type = response.headers.get('content-type', '')
            self.logger.debug(f"Response content type: {content_type}")

            # Handle JSON response with base64 data
            if 'application/json' in content_type:
                try:
                    response_data = response.json()
                    if response_data.get('status') != 'success':
                        self.logger.error(f"Error response from server: {response_data}")
                        return None

                    base64_data = response_data.get('data', '')
                    if not base64_data:
                        self.logger.error("No image data in response")
                        return None

                    return base64.b64decode(base64_data)
                except Exception as e:
                    self.logger.error(f"Error processing JSON response: {e}")
                    return None

            # Handle direct image response
            elif 'image/' in content_type:
                return response.content

            else:
                self.logger.error(f"Unexpected content type: {content_type} for URL {url}")
                return None

        except requests.RequestException as e:
            self.logger.error(f"Error downloading snapshot from {url}: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error downloading snapshot from {url}: {e}")
            return None


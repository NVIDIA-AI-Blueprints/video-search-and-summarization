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
from __future__ import annotations

import json
import logging
import os
import urllib.parse

import aiohttp

from vss_agents.utils.retry import create_retry_strategy

logger = logging.getLogger(__name__)


def build_overlay_config(
    overlay_enabled: bool,
    object_ids: list[str] | None = None,
) -> str | None:
    """Build the overlay configuration query parameter for VST API requests.

    This is a shared helper used by both snapshot and video_clip tools to
    support bounding box overlays on VST media.

    Args:
        overlay_enabled: Whether overlay configuration is enabled.
        object_ids: Optional list of object IDs to display as overlays.
            If empty or None and overlay is enabled, all bounding boxes are shown.

    Returns:
        URL-encoded overlay configuration string, or None if overlay is disabled.
    """
    if not overlay_enabled:
        return None

    overlay_object_ids = object_ids or []
    config_dict = {
        "overlay": {
            "bbox": {"showAll": not overlay_object_ids, "objectId": overlay_object_ids},
            "color": "green",
            "thickness": 5,
            "debug": True,
            "opacity": 254,
        },
    }
    return urllib.parse.quote(json.dumps(config_dict))


class VSTError(Exception):
    """Base exception for VST errors."""

    pass


async def get_name_to_stream_id_map(vst_internal_url: str | None = None) -> dict[str, str]:
    """Fetch `/api/v1/sensor/streams` and return `{name: streamId}`."""
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/streams"
    async with aiohttp.ClientSession() as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(Exception,)):
            with retry:
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise RuntimeError(f"VST streams API returned status {response.status}")
                        text = await response.text()
                        payload = json.loads(text)
                        mapping: dict[str, str] = {}
                        for file in payload:
                            stream_id = next(iter(file))
                            if isinstance(file[stream_id], list) and len(file[stream_id]) > 0:
                                name = file[stream_id][0]["name"]
                                mapping[name] = stream_id
                            else:
                                logger.warning(f"Stream ID {stream_id} is empty, skipping")
                        return mapping
                except Exception as e:
                    logger.error(f"Error getting name to stream ID map: {e}")
                    raise e
    return {}  # unreachable, but satisfies mypy


async def get_stream_id(sensor_id: str, vst_internal_url: str | None = None) -> str:
    """Get the stream ID for a given sensor ID.
    Note: sensor_id can be the name of the sensor or the stream ID.
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")
    stream_id_map = await get_name_to_stream_id_map(vst_internal_url)
    stream_id = stream_id_map.get(sensor_id)
    if not stream_id:
        if sensor_id in stream_id_map.values():
            stream_id = sensor_id
        else:
            raise VSTError(
                f"streamId not found for '{sensor_id}'. Available: {sorted(stream_id_map.keys())}"
                if stream_id_map
                else "streamId not found"
            )
    return stream_id


async def get_sensor_id_from_stream_id(stream_id: str, vst_internal_url: str | None = None) -> str:
    """Get the sensor ID (camera name) for a given stream ID (UUID).

    This is the reverse mapping of get_stream_id - takes a stream_id (UUID) and returns
    the sensor name (e.g., "Camera_03").

    Args:
        stream_id: The stream ID (UUID) to look up
        vst_internal_url: Optional VST internal URL, defaults to VST_INTERNAL_URL env var

    Returns:
        The sensor ID (camera name) corresponding to the stream_id

    Raises:
        VSTError: If the stream_id is not found in VST
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")
    name_to_stream_id_map = await get_name_to_stream_id_map(vst_internal_url)

    # Reverse the mapping: {name: streamId} -> {streamId: name}
    stream_id_to_name_map = {stream_id_val: name for name, stream_id_val in name_to_stream_id_map.items()}

    sensor_id = stream_id_to_name_map.get(stream_id)
    if not sensor_id:
        # Check if stream_id is already a sensor name (not a UUID)
        if stream_id in name_to_stream_id_map:
            sensor_id = stream_id
        else:
            raise VSTError(
                f"sensorId not found for stream_id '{stream_id}'. Available stream_ids: {sorted(stream_id_to_name_map.keys())[:10]}..."
                if stream_id_to_name_map
                else "sensorId not found"
            )
    return sensor_id


async def validate_video_url(url: str, timeout: int = 30) -> bool:
    """
    Validate if a video URL is accessible and returns a valid response.
    First tries HEAD request, then falls back to GET with range header if HEAD fails.

    Args:
        url: The video URL to validate
        timeout: Timeout in seconds for the request (default: 30)
    """
    try:
        logger.info(f"Validating video URL: {url}")

        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
            # First try HEAD request
            try:
                async with session.head(url) as response:
                    is_valid = 200 <= response.status < 300

                    if is_valid:
                        content_type = response.headers.get("content-type", "").lower()
                        content_length = response.headers.get("content-length", "0")

                        logger.info(
                            f"URL validation successful (HEAD) - Status: {response.status}, Content-Type: {content_type}, Content-Length: {content_length}"
                        )

                        # Additional check for video content type (optional)
                        if content_type and not any(
                            video_type in content_type for video_type in ["video/", "application/octet-stream"]
                        ):
                            logger.warning(f"URL may not contain video content. Content-Type: {content_type}")
                        # Check if content length is reasonable (not empty)
                        if content_length == "0":
                            logger.warning("URL returned zero content length")
                        return True
                    else:
                        logger.warning(
                            f"HEAD request failed with status {response.status}, trying GET with range header"
                        )
            except Exception as e:
                logger.warning(f"HEAD request failed: {e}, trying GET with range header")

            # Fallback to GET request with range header (only first few bytes)
            try:
                headers = {"Range": "bytes=0-1023"}  # Only request first 1KB
                async with session.get(url, headers=headers) as response:
                    is_valid = 200 <= response.status < 300 or response.status == 206  # 206 = Partial Content

                    if is_valid:
                        content_type = response.headers.get("content-type", "").lower()
                        content_length = response.headers.get("content-length", "0")

                        logger.info(
                            f"URL validation successful (GET with range) - Status: {response.status}, Content-Type: {content_type}, Content-Length: {content_length}"
                        )

                        # Additional check for video content type (optional)
                        if content_type and not any(
                            video_type in content_type for video_type in ["video/", "application/octet-stream"]
                        ):
                            logger.warning(f"URL may not contain video content. Content-Type: {content_type}")
                        return True
                    else:
                        raise VSTError(f"URL validation failed - HTTP Status: {response.status}")
            except Exception as e:
                raise VSTError(f"GET request with range also failed: {e}") from e

    except aiohttp.ClientError as e:
        raise VSTError(f"Client error validating URL {url}: {e}") from e
    except Exception as e:
        raise VSTError(f"Unexpected error validating URL {url}: {e}") from e


async def delete_vst_sensor(vst_url: str, sensor_id: str) -> tuple[bool, str]:
    """
    Delete a sensor registration from VST.

    This removes the sensor metadata (name, URL, etc.) but not the stored video files.
    Must be paired with delete_vst_storage to fully remove a video.

    Args:
        vst_url: Base VST URL (e.g., http://localhost:30888)
        sensor_id: The sensor UUID to delete

    Returns:
        (success, message) tuple
    """
    url = f"{vst_url.rstrip('/')}/vst/api/v1/sensor/{sensor_id}"
    logger.info("Deleting VST sensor: DELETE %s", url)
    try:
        async with (
            aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session,
            session.delete(url) as response,
        ):
            if response.status in (200, 204):
                logger.info("VST sensor deleted: %s", sensor_id)
                return True, "OK"
            text = await response.text()
            return False, f"VST returned {response.status}: {text}"
    except Exception as e:
        logger.error("VST sensor delete failed: %s", e, exc_info=True)
        return False, str(e)


async def delete_vst_storage(vst_url: str, sensor_id: str) -> tuple[bool, str]:
    """
    Delete stored video files from VST.

    VST requires a time range for deletion. This function fetches the timeline
    for the sensor, computes the full start/end range, then issues the delete.

    Args:
        vst_url: Base VST URL (e.g., http://localhost:30888)
        sensor_id: The sensor UUID whose storage to delete

    Returns:
        (success, message) tuple
    """
    vst_url = vst_url.rstrip("/")
    timeline_url = f"{vst_url}/vst/api/v1/storage/timelines"
    logger.info("Getting VST timeline for storage delete: GET %s", timeline_url)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session:
            async with session.get(timeline_url) as response:
                if response.status != 200:
                    text = await response.text()
                    return False, f"Failed to get timeline: {response.status}: {text}"

                text = await response.text()
                timelines = json.loads(text)
                stream_timeline = timelines.get(sensor_id)

                if not stream_timeline or len(stream_timeline) == 0:
                    logger.info("No timeline found for %s, nothing to delete", sensor_id)
                    return True, "No storage to delete"

                start_times = [t.get("startTime") for t in stream_timeline if t.get("startTime")]
                end_times = [t.get("endTime") for t in stream_timeline if t.get("endTime")]
                if not start_times or not end_times:
                    return True, "No storage to delete"

                start_time = min(start_times)
                end_time = max(end_times)

            storage_url = f"{vst_url}/vst/api/v1/storage/file/{sensor_id}"
            params = {"startTime": start_time, "endTime": end_time}
            logger.info("Deleting VST storage: DELETE %s params=%s", storage_url, params)

            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60)) as session,
                session.delete(storage_url, params=params) as del_response,
            ):
                if del_response.status in (200, 204):
                    logger.info("VST storage deleted: %s", sensor_id)
                    return True, "OK"
                text = await del_response.text()
                return False, f"VST storage returned {del_response.status}: {text}"
    except Exception as e:
        logger.error("VST storage delete failed: %s", e, exc_info=True)
        return False, str(e)


class VSTDirectUploader:
    """Handles direct VST API uploads for media files."""

    def __init__(self, vst_api_url: str):
        """
        Initialize VST direct uploader.

        Args:
            vst_api_url: Base URL for VST API
        """
        self.vst_api_url = vst_api_url.rstrip("/")

    async def upload_media_file(
        self,
        media_file_path: str,
        timestamp: str | None = None,
        sensor_id: str | None = None,
        stream_id: str | None = None,
        event_info: str | None = None,
        stream_name: str | None = None,
        tag: str | None = None,
    ) -> bool:
        """
        Upload media file to VST API with optional parameters.

        Args:
            media_file_path: Path to the media file to upload
            timestamp: ISO format timestamp (optional)
            sensor_id: Sensor ID for the upload (optional)
            stream_id: Stream ID for the upload (optional)
            event_info: Description of the event (optional)
            stream_name: Stream name for the upload (optional)
            tag: Tag for categorization (optional)

        Returns:
            True if upload successful, False otherwise
        """
        try:
            # Check if media file exists
            if not os.path.exists(media_file_path):
                logger.error(f"Media file not found: {media_file_path}")
                return False

            upload_url = f"{self.vst_api_url}/vst/api/v1/storage/file"

            metadata = {}

            if timestamp is not None:
                metadata["timestamp"] = timestamp

            if sensor_id:
                metadata["sensorId"] = sensor_id

            if stream_id:
                metadata["streamId"] = stream_id

            if event_info:
                metadata["eventInfo"] = event_info

            if stream_name:
                metadata["streamName"] = stream_name

            if tag:
                metadata["tag"] = tag

            logger.info(f"Uploading {media_file_path}")
            logger.debug(f"Metadata: {metadata}")

            # Make the upload request with file context manager
            with open(media_file_path, "rb") as media_file:
                # Build multipart form data for aiohttp
                form_data = aiohttp.FormData()
                form_data.add_field("metadata", json.dumps(metadata))
                form_data.add_field(
                    "mediaFile",
                    media_file,
                    filename=os.path.basename(media_file_path),
                    content_type="video/mp4",
                )

                async with (
                    aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session,
                    session.post(upload_url, data=form_data) as response,
                ):
                    if response.status == 200:
                        logger.info(f"Successfully uploaded {media_file_path}")
                        # Handle both JSON and text responses
                        content_type = response.headers.get("Content-Type", "")
                        if "application/json" in content_type:
                            logger.info(f"Response: {await response.json()}")
                        else:
                            logger.info(f"Response: {await response.text()}")
                        return True
                    else:
                        logger.error(f"Upload failed with status {response.status}: {await response.text()}")
                        return False

        except Exception as e:
            logger.error(f"Error uploading media file: {e}")
            return False


async def get_streams_info(vst_internal_url: str | None = None) -> dict[str, dict[str, str]]:
    """
    Fetch `/api/v1/sensor/streams` and return full stream info including URLs.
    Returns: {stream_id: {"name": name, "url": rtsp_url}} Note: this only validates 200 status code, the url is not validated.
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/streams"

    async with aiohttp.ClientSession() as session:
        async for retry in create_retry_strategy(retries=3, exceptions=(Exception,)):
            with retry:
                try:
                    async with session.get(url) as response:
                        if response.status != 200:
                            raise VSTError(f"VST streams API returned status {response.status}")
                        text = await response.text()
                        payload = json.loads(text)
                        result: dict[str, dict[str, str]] = {}
                        for entry in payload:
                            stream_id = next(iter(entry))
                            stream_list = entry[stream_id]
                            if stream_list and len(stream_list) > 0:
                                result[stream_id] = {
                                    "name": stream_list[0].get("name", ""),
                                    "url": stream_list[0].get("url", ""),
                                }
                        return result
                except Exception as e:
                    logger.error(f"Error getting streams info: {e}")
                    raise e
    return {}  # unreachable, but satisfies mypy


async def get_stream_info_by_name(name: str, vst_internal_url: str | None = None) -> tuple[str | None, str | None]:
    """
    Find stream_id and RTSP URL by sensor/camera name.
    Returns: (stream_id, rtsp_url) or (None, None) if not found
    """
    streams_info = await get_streams_info(vst_internal_url)
    for stream_id, info in streams_info.items():
        if info.get("name") == name:
            return stream_id, info.get("url")
    return None, None


async def add_sensor(
    sensor_url: str,
    name: str,
    username: str = "",
    password: str = "",
    location: str = "",
    tags: str = "",
    vst_internal_url: str | None = None,
) -> tuple[bool, str, str | None]:
    """
    Add a new sensor to VST.
    Returns: (success, message, sensor_id)
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/add"

    payload: dict[str, str] = {
        "sensorUrl": sensor_url,
        "name": name,
    }
    if username:
        payload["username"] = username
    if password:
        payload["password"] = password
    if location:
        payload["location"] = location
    if tags:
        payload["tags"] = tags

    logger.info(f"Adding sensor to VST: POST {url}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload) as response:
                if response.status not in (200, 201):
                    # Try to parse VST error response for cleaner message
                    try:
                        error_json = await response.json(content_type=None)
                        error_msg = error_json.get("error_message", str(error_json))
                    except Exception:
                        error_msg = await response.text()
                    error = f"VST error: {error_msg}"
                    logger.error(f"VST returned {response.status}: {error_msg}")
                    return False, error, None

                # Use content_type=None to handle text/plain responses from VST
                result = await response.json(content_type=None)
                sensor_id = result.get("sensorId") or result.get("id")

                if not sensor_id:
                    error = f"VST response missing sensor ID: {result}"
                    logger.error(error)
                    return False, error, None

                logger.info(f"VST sensor created: {sensor_id}")
                return True, "OK", sensor_id

        except Exception as e:
            error = f"VST add sensor request failed: {e!s}"
            logger.error(error, exc_info=True)
            return False, error, None


async def delete_sensor(sensor_id: str | None, vst_internal_url: str | None = None) -> tuple[bool, str]:
    """
    Delete a sensor from VST.
    Returns: (success, message)
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/sensor/{sensor_id}"

    logger.info(f"Deleting VST sensor: DELETE {url}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.delete(url) as response:
                if response.status in (200, 204):
                    logger.info(f"VST sensor deleted: {sensor_id}")
                    return True, "OK"
                # Try to parse VST error response for cleaner message
                try:
                    error_json = await response.json(content_type=None)
                    error_msg = error_json.get("error_message", str(error_json))
                except Exception:
                    error_msg = await response.text()
                return False, f"VST error: {error_msg}"
        except Exception as e:
            return False, str(e)


async def get_storage_timeline(
    sensor_id: str | None, vst_internal_url: str | None = None
) -> tuple[bool, str, str | None, str | None]:
    """
    Get storage timeline (start_time, end_time) for a sensor.
    Returns: (success, message, start_time, end_time)
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")

    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/timelines"
    logger.info(f"Getting VST timeline: GET {url}")

    try:
        async with aiohttp.ClientSession() as session, session.get(url) as response:
            if response.status != 200:
                # Try to parse VST error response for cleaner message
                try:
                    error_json = await response.json(content_type=None)
                    error_msg = error_json.get("error_message", str(error_json))
                except Exception:
                    error_msg = await response.text()
                return False, f"VST error: {error_msg}", None, None

            # Use content_type=None to handle text/plain responses from VST
            timelines = await response.json(content_type=None)
            stream_timeline = timelines.get(sensor_id)

            if not stream_timeline or len(stream_timeline) == 0:
                logger.info(f"No timeline found for {sensor_id}")
                return True, "No timeline", None, None

            start_time = stream_timeline[0].get("startTime")
            end_time = stream_timeline[0].get("endTime")
            return True, "OK", start_time, end_time

    except Exception as e:
        return False, str(e), None, None


async def delete_storage(sensor_id: str | None, vst_internal_url: str | None = None) -> tuple[bool, str]:
    """
    Delete storage files for a sensor from VST.
    Returns: (success, message)
    """
    if vst_internal_url is None:
        vst_internal_url = os.getenv("VST_INTERNAL_URL", "http://localhost:30888")

    # Get timeline first
    success, msg, start_time, end_time = await get_storage_timeline(sensor_id, vst_internal_url)
    if not success:
        return False, msg

    if start_time is None or end_time is None:
        logger.info(f"No timeline found for {sensor_id}, nothing to delete")
        return True, "No storage to delete"

    # Delete storage
    url = f"{vst_internal_url.rstrip('/')}/vst/api/v1/storage/file/{sensor_id}"
    params = {"startTime": start_time, "endTime": end_time}
    logger.info(f"Deleting VST storage: DELETE {url} params={params}")

    try:
        async with aiohttp.ClientSession() as session, session.delete(url, params=params) as response:
            if response.status in (200, 204):
                logger.info(f"VST storage deleted: {sensor_id}")
                return True, "OK"
            # Try to parse VST error response for cleaner message
            try:
                error_json = await response.json(content_type=None)
                error_msg = error_json.get("error_message", str(error_json))
            except Exception:
                error_msg = await response.text()
            return False, f"VST error: {error_msg}"

    except Exception as e:
        return False, str(e)


async def get_rtsp_url(sensor_id: str, vst_internal_url: str | None = None) -> tuple[bool, str, str | None]:
    """
    Get RTSP URL for a sensor from VST streams API.
    Returns: (success, message, rtsp_url)
    """
    async for retry in create_retry_strategy(delay=0.1, retries=50, exceptions=(Exception,)):
        with retry:
            streams_info = await get_streams_info(vst_internal_url)
            if sensor_id in streams_info:
                rtsp_url = streams_info[sensor_id].get("url")
                if isinstance(rtsp_url, str) and rtsp_url.startswith("rtsp://"):
                    return True, "OK", rtsp_url
                else:
                    logger.warning(f"RTSP URL is not valid: {rtsp_url}, retrying...")
                    raise ValueError(f"RTSP URL is not valid: {rtsp_url}")
            else:
                logger.warning(f"Sensor ID {sensor_id} not found in streams info, retrying...")
                raise ValueError(f"Sensor ID {sensor_id} not found in streams info")
    return False, f"RTSP URL not found for sensor {sensor_id}", None

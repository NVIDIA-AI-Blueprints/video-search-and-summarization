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
VMS (Video Management System) video upload utility.

Uploads video files to the VMS storage API using PUT with binary body and
Content-Type per file. URL pattern: {base}/vst/api/v1/storage/file/{file_name}/{iso8601_timestamp}
"""

import os
import logging
import time
from datetime import datetime, timezone
from typing import Tuple
from urllib.parse import quote

import requests

# Module-level logger; callers may override by passing a logger
_logger = logging.getLogger(__name__)

# Default request timeout (seconds)
DEFAULT_UPLOAD_TIMEOUT = 300
# Content-Type mapping by extension
VIDEO_CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".mkv": "video/x-matroska",
}


class VMSUploadError(Exception):
    """Raised when a VMS upload operation fails."""

    pass


def _content_type_for_filename(filename: str) -> str:
    """Return Content-Type for a video filename."""
    ext = os.path.splitext(filename)[1].lower()
    return VIDEO_CONTENT_TYPES.get(ext, "application/octet-stream")


def _build_vms_url(base_url: str, file_name: str) -> str:
    """Build VMS storage PUT URL: base_url/vst/api/v1/storage/file/{file_name}/{iso8601_timestamp}."""
    base_url = base_url.rstrip("/")
    if "/vst/api/v1/storage/file" not in base_url:
        base_url = f"{base_url}/vst/api/v1/storage/file"
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    # Quote filename for URL safety (spaces, special chars)
    safe_name = quote(file_name, safe="")
    return f"{base_url}/{safe_name}/{timestamp}"


def upload_videos_to_vms(
    source_directory: str,
    base_url: str,
    count: int = None,
    timeout: int = DEFAULT_UPLOAD_TIMEOUT,
    upload_delay: int = 0,
    logger: logging.Logger = None,
) -> Tuple[int, list]:
    """
    Upload video files from source_directory to VMS storage API.

    Uses PUT with Content-Type (e.g. video/mp4) and binary body.
    URL: {base_url}/vst/api/v1/storage/file/{file_name}/{iso8601_timestamp}.

    :param source_directory: Directory containing video files.
    :param base_url: VMS base URL (e.g. http://<VMS_HOST>:<PORT>).
    :param count: Maximum number of videos to upload; None to upload all.
    :param timeout: Request timeout in seconds.
    :param upload_delay: Delay in seconds between subsequent video uploads (default 0).
    :param logger: Optional logger; uses module logger if not provided.
    :returns: Tuple of (uploaded_count, list of error messages for failed uploads).
    :raises VMSUploadError: If source_directory is invalid or unreachable.
    """
    log = logger or _logger
    if not source_directory or not os.path.isdir(source_directory):
        raise VMSUploadError(f"Invalid or missing source directory: {source_directory!r}")

    # List all entries in the directory and log them; require at least one video file
    all_entries = sorted(os.listdir(source_directory))
    all_files = [
        e for e in all_entries
        if os.path.isfile(os.path.join(source_directory, e))
    ]
    log.info(
        "Files in video source directory %s (%d total): %s",
        source_directory,
        len(all_files),
        all_files if all_files else "(none)",
    )
    video_files = [f for f in all_files if f.lower().endswith((".mp4", ".mkv"))]
    if not video_files:
        raise VMSUploadError(
            "No video files (.mp4 or .mkv) found in source directory %r. "
            "Files present: %s"
            % (source_directory, all_files if all_files else "(empty directory)")
        )

    uploaded_count = 0
    errors = []

    for filename in sorted(os.listdir(source_directory)):
        if count is not None and uploaded_count >= count:
            break
        if not filename.lower().endswith((".mp4", ".mkv")):
            continue

        path = os.path.join(source_directory, filename)
        if not os.path.isfile(path):
            continue

        url = _build_vms_url(base_url, filename)
        content_type = _content_type_for_filename(filename)

        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as e:
            msg = f"Failed to read file {path}: {e}"
            log.error(msg)
            errors.append(msg)
            continue

        if upload_delay > 0 and uploaded_count > 0:
            log.info("Waiting %d seconds before next upload", upload_delay)
            time.sleep(upload_delay)

        try:
            resp = requests.put(
                url,
                data=data,
                headers={"Content-Type": content_type},
                timeout=timeout,
            )
            if resp.status_code in (200, 201, 204):
                log.info("Successfully uploaded %s to VMS", filename)
                uploaded_count += 1
            else:
                msg = f"VMS upload failed for {filename}: HTTP {resp.status_code} - {resp.text[:500]}"
                log.warning(msg)
                errors.append(msg)
        except requests.Timeout:
            msg = f"VMS upload timeout for {filename} (timeout={timeout}s)"
            log.error(msg)
            errors.append(msg)
        except requests.RequestException as e:
            msg = f"VMS upload request error for {filename}: {e}"
            log.error(msg)
            errors.append(msg)

    return uploaded_count, errors


def upload_single_file_to_vms(
    file_path: str,
    base_url: str,
    timeout: int = DEFAULT_UPLOAD_TIMEOUT,
    logger: logging.Logger = None,
) -> Tuple[bool, str]:
    """
    Upload a single video file to VMS storage API.

    :param file_path: Full path to the video file (.mp4 or .mkv).
    :param base_url: VMS base URL (e.g. http://<VMS_HOST>:<PORT>).
    :param timeout: Request timeout in seconds.
    :param logger: Optional logger.
    :returns: Tuple of (success: bool, error_message: str or empty).
    """
    log = logger or _logger
    if not file_path or not os.path.isfile(file_path):
        return False, f"Not a file: {file_path!r}"
    filename = os.path.basename(file_path)
    if not filename.lower().endswith((".mp4", ".mkv")):
        return False, f"Unsupported extension (use .mp4 or .mkv): {filename}"

    url = _build_vms_url(base_url, filename)
    content_type = _content_type_for_filename(filename)

    try:
        with open(file_path, "rb") as f:
            data = f.read()
    except OSError as e:
        return False, f"Failed to read file: {e}"

    try:
        resp = requests.put(
            url,
            data=data,
            headers={"Content-Type": content_type},
            timeout=timeout,
        )
        if resp.status_code in (200, 201, 204):
            log.info("Successfully uploaded %s to VMS", filename)
            return True, ""
        return False, f"HTTP {resp.status_code} - {resp.text[:500]}"
    except requests.Timeout:
        return False, f"Timeout after {timeout}s"
    except requests.RequestException as e:
        return False, str(e)

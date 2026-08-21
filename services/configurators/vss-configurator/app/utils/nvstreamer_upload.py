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
NVStreamer video upload utility.

Uploads video files (mp4/mkv) to the NVStreamer storage API using POST
with multipart form and NVStreamer-specific headers.
"""

import os
import logging
import time
from typing import Tuple

import requests

# Module-level logger; callers may override by passing a logger
_logger = logging.getLogger(__name__)

# Default request timeout (seconds)
DEFAULT_UPLOAD_TIMEOUT = 300


class NVStreamerUploadError(Exception):
    """Raised when an NVStreamer upload operation fails fatally (e.g. invalid directory)."""

    pass


def upload_videos(
    source_directory: str,
    upload_url: str,
    count: int = None,
    timeout: int = DEFAULT_UPLOAD_TIMEOUT,
    upload_delay: int = 0,
    logger: logging.Logger = None,
) -> Tuple[int, list]:
    """
    Upload video files from the source directory to the NVStreamer upload URL.
    Uploads up to `count` videos (or all if count is None). Supports .mp4 and .mkv.
    Continues on per-file errors and returns counts and error list.

    :param source_directory: The directory to search for video files.
    :param upload_url: The NVStreamer upload URL (e.g. http://host:port/api/v1/storage/file).
    :param count: Maximum number of videos to upload; None to upload all.
    :param timeout: Request timeout in seconds.
    :param upload_delay: Delay in seconds between subsequent video uploads (default 0).
    :param logger: Optional logger; uses module logger if not provided.
    :returns: Tuple of (uploaded_count, list of error messages for failed uploads).
    :raises NVStreamerUploadError: If source_directory is invalid or unreachable.
    """
    log = logger or _logger

    if not source_directory or not os.path.isdir(source_directory):
        raise NVStreamerUploadError(
            f"Invalid or missing source directory: {source_directory!r}"
        )

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
        raise NVStreamerUploadError(
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

        source_file_path = os.path.join(source_directory, filename)
        if not os.path.isfile(source_file_path):
            continue

        try:
            with open(source_file_path, "rb") as file:
                file_data = file.read()
        except OSError as e:
            msg = f"Failed to read file {source_file_path}: {e}"
            log.error(msg)
            errors.append(msg)
            continue

        headers = {
            "nvstreamer-chunk-number": "1",
            "nvstreamer-total-chunks": "1",
            "nvstreamer-is-last-chunk": "true",
            "nvstreamer-identifier": "identifier",
            "nvstreamer-file-name": filename,
        }
        files = {"file": (filename, file_data)}

        if upload_delay > 0 and uploaded_count > 0:
            log.info("Waiting %d seconds before next upload", upload_delay)
            time.sleep(upload_delay)

        try:
            response = requests.post(
                upload_url,
                files=files,
                headers=headers,
                timeout=timeout,
            )
            if response.status_code == 200:
                log.info("Successfully uploaded %s to NVStreamer", filename)
                uploaded_count += 1
            else:
                msg = (
                    f"NVStreamer upload failed for {filename}: "
                    f"HTTP {response.status_code} - {response.text[:500]}"
                )
                log.warning(msg)
                errors.append(msg)
        except requests.Timeout:
            msg = f"NVStreamer upload timeout for {filename} (timeout={timeout}s)"
            log.error(msg)
            errors.append(msg)
        except requests.RequestException as e:
            msg = f"NVStreamer upload request error for {filename}: {e}"
            log.error(msg)
            errors.append(msg)

    return uploaded_count, errors


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Upload video files (mp4 or mkv) from a specified directory to an endpoint API."
    )
    parser.add_argument(
        "endpoint",
        type=str,
        help="The full endpoint URL (IP:Port/Path) of the upload API endpoint, e.g., 10.0.0.1:30081/nvstreamer-2/",
    )
    parser.add_argument(
        "source_directory",
        type=str,
        nargs="?",
        default=".",
        help="The directory containing video files. Defaults to the current directory if not specified.",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=None,
        help="Max number of video files to upload (default: all).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_UPLOAD_TIMEOUT,
        help="Request timeout in seconds.",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=0,
        help="Delay in seconds between subsequent uploads (default: 0).",
    )

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        uploaded, errs = upload_videos(
            args.source_directory, args.endpoint, count=args.count,
            timeout=args.timeout, upload_delay=args.delay
        )
        if errs:
            for e in errs:
                print(e)
        print(f"Uploaded {uploaded} videos.")
        if args.count is not None and uploaded < args.count:
            print(
                f"Only {uploaded} videos were uploaded. Unable to reach the specified count of {args.count}."
            )
    except NVStreamerUploadError as e:
        print(f"Error: {e}")
        raise SystemExit(1)

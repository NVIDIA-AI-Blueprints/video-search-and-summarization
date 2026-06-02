# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Asset Management Module."""

import asyncio
import ipaddress
import json
import os
import re
import shutil
import socket
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from typing import Callable, Optional
from urllib.parse import urljoin, urlparse

import aiofiles
import aiofiles.os
import boto3
from aiofiles import tempfile

from api_models.common import (
    AWS_S3_OBJECT_URL_PATTERN,
    AWS_S3_URL_PATTERN,
    BLOCKED_IP_RANGES,
)
from common.logger import TimeMeasure, logger
from common.service_exception import ServiceException

AGE_OUT_THRESHOLD = 0.9  # Start aging out when usage is within this threshold of the max
AGE_OUT_RUN_INTERVAL_SEC = 300

# TTL-based eviction: set ASSET_MAX_AGE_HOURS env var to enable (0 = disabled)
try:
    _ASSET_MAX_AGE_HOURS = float(os.environ.get("ASSET_MAX_AGE_HOURS", "0"))
except (ValueError, TypeError) as e:
    raise ValueError(f"ASSET_MAX_AGE_HOURS must be a non-negative number: {e}") from e
if _ASSET_MAX_AGE_HOURS < 0:
    raise ValueError(
        f"ASSET_MAX_AGE_HOURS must be a non-negative number, got {_ASSET_MAX_AGE_HOURS}"
    )

DEFAULT_MAX_DOWNLOAD_FILE_SIZE_GB = 8


def _parse_max_download_file_size_bytes() -> int:
    raw_value = os.environ.get(
        "ASSET_DOWNLOAD_MAX_FILE_SIZE_GB", str(DEFAULT_MAX_DOWNLOAD_FILE_SIZE_GB)
    )
    try:
        max_size_gb = float(raw_value)
    except (ValueError, TypeError) as e:
        raise ValueError(f"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB must be a positive number: {e}") from e
    if max_size_gb <= 0:
        raise ValueError(
            f"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB must be a positive number, got {max_size_gb}"
        )
    return int(max_size_gb * 1024 * 1024 * 1024)


# Maximum file size for URL/data URI ingestion.
MAX_DOWNLOAD_FILE_SIZE = _parse_max_download_file_size_bytes()


def validate_url_ssrf_runtime(url: str) -> None:
    """Runtime validation of URL to prevent SSRF attacks during download.

    This performs DNS resolution checks that couldn't be done at request validation time.

    Args:
        url: The URL to validate

    Raises:
        ServiceException: If the URL poses an SSRF risk
    """
    parsed = urlparse(url)

    if parsed.scheme not in ["http", "https"]:
        return  # Only validate HTTP/HTTPS URLs

    hostname = parsed.hostname
    if not hostname:
        raise ServiceException("Invalid URL: missing hostname", "InvalidParameters", 422)

    # Perform DNS resolution and check resolved IPs
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)

        for result in addr_info:
            ip_str = result[4][0]
            # Remove zone index for IPv6 if present
            ip_str = ip_str.split("%")[0]

            try:
                resolved_ip = ipaddress.ip_address(ip_str)

                # Check against blocked IP ranges
                for blocked_range in BLOCKED_IP_RANGES:
                    if resolved_ip in blocked_range:
                        raise ServiceException(
                            f"Cannot download from '{hostname}': resolves to blocked IP {resolved_ip} "
                            f"in range {blocked_range} (SSRF protection)",
                            "InvalidParameters",
                            422,
                        )

                # Additional security checks
                if resolved_ip.is_loopback:
                    raise ServiceException(
                        f"Cannot download from '{hostname}': resolves to loopback address (SSRF protection)",
                        "InvalidParameters",
                        422,
                    )
                if resolved_ip.is_link_local:
                    raise ServiceException(
                        f"Cannot download from '{hostname}': resolves to link-local address (SSRF protection)",  # noqa: E501
                        "InvalidParameters",
                        422,
                    )

            except ValueError:
                # Skip invalid IPs
                continue

    except socket.gaierror as e:
        raise ServiceException(
            f"Cannot resolve hostname '{hostname}': {e}", "InvalidParameters", 422
        )


async def validate_url_ssrf_runtime_async(url: str) -> None:
    """Async runtime validation of URL to prevent SSRF attacks during download.

    This performs DNS resolution checks that couldn't be done at request validation time.
    Uses asyncio's async getaddrinfo to avoid blocking the event loop.

    Args:
        url: The URL to validate

    Raises:
        ServiceException: If the URL poses an SSRF risk
    """
    parsed = urlparse(url)

    if parsed.scheme not in ["http", "https"]:
        return  # Only validate HTTP/HTTPS URLs

    hostname = parsed.hostname
    if not hostname:
        raise ServiceException("Invalid URL: missing hostname", "InvalidParameters", 422)

    # Perform async DNS resolution and check resolved IPs
    try:
        loop = asyncio.get_event_loop()
        addr_info = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )

        for result in addr_info:
            ip_str = result[4][0]
            # Remove zone index for IPv6 if present
            ip_str = ip_str.split("%")[0]

            try:
                resolved_ip = ipaddress.ip_address(ip_str)

                # Check against blocked IP ranges
                for blocked_range in BLOCKED_IP_RANGES:
                    if resolved_ip in blocked_range:
                        raise ServiceException(
                            f"Cannot download from '{hostname}': resolves to blocked IP {resolved_ip} "
                            f"in range {blocked_range} (SSRF protection)",
                            "InvalidParameters",
                            422,
                        )

                # Additional security checks
                if resolved_ip.is_loopback:
                    raise ServiceException(
                        f"Cannot download from '{hostname}': resolves to loopback address (SSRF protection)",
                        "InvalidParameters",
                        422,
                    )
                if resolved_ip.is_link_local:
                    raise ServiceException(
                        f"Cannot download from '{hostname}': resolves to link-local address (SSRF protection)",  # noqa: E501
                        "InvalidParameters",
                        422,
                    )

            except ValueError:
                # Skip invalid IPs
                continue

    except socket.gaierror as e:
        raise ServiceException(
            f"Cannot resolve hostname '{hostname}': {e}", "InvalidParameters", 422
        )


class Asset:
    """Media asset. Can be a file or a live stream."""

    def __init__(
        self,
        asset_id: str,
        path: str,
        purpose: str,
        media_type: str,
        asset_dir: str,
        fileName="",
        username="",
        password="",
        description="",
        video_fps=None,
        place_name="",
        place_type="",
        place_lat=None,
        place_lon=None,
        place_alt=None,
        place_coordinate_x=None,
        place_coordinate_y=None,
        creation_time=None,
        url=None,
        sensor_name="",
        camera_id=None,
    ) -> None:
        """Asset constructor.

        Args:
            asset_id: Unique ID for the asset
            path: Path for the asset. Path to the file or RTSP URL
            purpose: Purpose of the file.
            media_type: Media Type (video/image) of the file.
            asset_dir: Directory where the asset information and other files related
                       to the asset are stored.
            fileName (optional): Name of the file. Defaults to "".
            username (optional): Username to access the live stream. Defaults to "".
            password (optional): Password to access the live stream. Defaults to "".
            description (optional): Description of the asset (live-stream only). Defaults to "".
            video_fps (optional): Cached video FPS. Defaults to None.
            place_name (optional): Name of the place/location. Defaults to "".
            place_type (optional): Type of place/location. Defaults to "".
            place_lat (optional): Latitude of the camera location. Defaults to None.
            place_lon (optional): Longitude of the camera location. Defaults to None.
            place_alt (optional): Altitude of the camera location. Defaults to None.
            place_coordinate_x (optional): X coordinate within the place. Defaults to None.
            place_coordinate_y (optional): Y coordinate within the place. Defaults to None.
            creation_time (optional): Creation time of the file. Defaults to None.
            url (optional): URL of the file. Defaults to None.
            sensor_name (optional): User-defined sensor name. Defaults to "".
            camera_id (optional): External camera identifier for CV-compatible lookups.
                Defaults to None.
        """
        self._asset_id = asset_id
        self._filename = fileName
        self._purpose = purpose
        self._media_type = media_type
        self._path = path
        self._use_count = 0
        self._asset_dir = asset_dir
        self._description = description
        self._username = username
        self._password = password
        self._video_fps = video_fps
        self._place_name = place_name
        self._place_type = place_type
        self._place_lat = place_lat
        self._place_lon = place_lon
        self._place_alt = place_alt
        self._place_coordinate_x = place_coordinate_x
        self._place_coordinate_y = place_coordinate_y
        self._creation_time = creation_time
        self._url = url
        self._sensor_name = sensor_name
        self._camera_id = camera_id

    @classmethod
    def fromdir(cls, asset_dir):
        with open(os.path.join(asset_dir, "info.json")) as f:
            info = json.load(f)

            return Asset(
                asset_id=info["assetId"],
                path=info["path"],
                fileName=info["fileName"],
                purpose=info["purpose"],
                media_type=info.get("media_type", "video"),
                username=info["username"],
                password=info["password"],
                description=info["description"],
                asset_dir=asset_dir,
                video_fps=info.get("video_fps", None),
                place_name=info.get("place_name", ""),
                place_type=info.get("place_type", ""),
                place_lat=info.get("place_lat", None),
                place_lon=info.get("place_lon", None),
                place_alt=info.get("place_alt", None),
                place_coordinate_x=info.get("place_coordinate_x", None),
                place_coordinate_y=info.get("place_coordinate_y", None),
                creation_time=info.get("creation_time", None),
                url=info.get("url", None),
                sensor_name=info.get("sensor_name", ""),
                camera_id=info.get("camera_id", None),
            )

    @property
    def asset_id(self):
        """Unique ID of the asset"""
        return self._asset_id

    @property
    def filename(self):
        """Name of the file"""
        return self._filename

    @property
    def purpose(self):
        """Purpose of the file"""
        return self._purpose

    @property
    def media_type(self):
        """Media type of the file"""
        return self._media_type

    @property
    def path(self):
        """Path to the file / live stream URL"""
        return self._path

    @property
    def description(self):
        """Description of the asset (live-stream only)"""
        return self._description

    @property
    def username(self):
        """Username to access the live stream"""
        return self._username

    @property
    def password(self):
        """Password to access the live stream"""
        return self._password

    @property
    def asset_dir(self):
        """Storage directory for the asset"""
        return self._asset_dir

    def lock(self):
        """Lock the asset. Asset cannot be deleted if in use."""
        self._use_count += 1

    def unlock(self):
        """Unock the asset"""
        self._use_count -= 1

    @property
    def use_count(self):
        """Reference count for the file"""
        return self._use_count

    @property
    def is_live(self):
        """Boolean indicating if the asset is a live stream."""
        return self.path.startswith("rtsp://")

    @property
    def video_fps(self):
        """Cached video FPS."""
        return self._video_fps

    @property
    def place_name(self):
        """Name of the place/location"""
        return self._place_name

    @property
    def place_type(self):
        """Type of place/location"""
        return self._place_type

    @property
    def place_lat(self):
        """Latitude of the camera location"""
        return self._place_lat

    @property
    def place_lon(self):
        """Longitude of the camera location"""
        return self._place_lon

    @property
    def place_alt(self):
        """Altitude of the camera location"""
        return self._place_alt

    @property
    def place_coordinate_x(self):
        """X coordinate within the place"""
        return self._place_coordinate_x

    @property
    def place_coordinate_y(self):
        """Y coordinate within the place"""
        return self._place_coordinate_y

    @property
    def creation_time(self):
        """Creation time of the file"""
        return self._creation_time

    @property
    def url(self):
        """URL of the file"""
        return self._url

    @property
    def sensor_name(self):
        """User-defined sensor name"""
        return self._sensor_name

    @property
    def camera_id(self):
        """External camera identifier for CV-compatible lookups"""
        return self._camera_id

    def update_video_fps(self, fps: float):
        """Update the cached video FPS (in-memory only, no disk write).

        Args:
            fps: Video frames per second
        """
        self._video_fps = fps


class AssetManager:
    """Asset Manager. Responsible for managing the assets - files & live streams
    added to the backend server.

    """

    def __init__(
        self,
        asset_dir: str,
        max_storage_usage_gb=None,
        asset_removal_callback: Callable[[Asset], bool] = None,
    ) -> None:
        """Default constructor

        Args:
            asset_dir: Path to the directory to store assets in
        """
        self._asset_dir = asset_dir
        self._max_storage_usage_gb = max_storage_usage_gb
        self._asset_removal_callback = asset_removal_callback

        try:
            os.makedirs(self._asset_dir, exist_ok=True)
        except Exception as e:
            raise ServiceException(f"Could not create assets directory '{asset_dir}'") from e

        self._asset_map: dict[str, Asset] = {}
        self._camera_id_map: dict[str, str] = {}  # camera_id -> asset_id

        self._aged_out_assets = []

        self._max_asset_age_hours = _ASSET_MAX_AGE_HOURS

        # Cache for storage usage to avoid repeated subprocess calls
        self._storage_usage_cache = None
        self._storage_usage_cache_time = 0
        self._storage_usage_cache_ttl = 2.0  # seconds

        if self._max_storage_usage_gb or self._max_asset_age_hours:
            self._age_out_thread = Thread(target=self._age_out_thread_func, daemon=True)
            self._age_out_thread.start()

    def _get_bucket_and_object_key_from_url(self, url: str):
        """Get the bucket and object key from a URL.
        Args:
            url: URL to get the bucket and object key from.
        Returns:
            A tuple of the bucket and object key.
        """

        if re.match(AWS_S3_URL_PATTERN, url):
            matches = re.match(AWS_S3_URL_PATTERN, url)
            if matches:
                return matches.group("bucket"), matches.group("object")

        elif re.match(AWS_S3_OBJECT_URL_PATTERN, url):
            matches = re.match(AWS_S3_OBJECT_URL_PATTERN, url)
            if matches:
                return matches.group("bucket_vh") or matches.group("bucket_ps"), matches.group(
                    "object_vh"
                ) or matches.group("object_ps")
        raise ServiceException("Invalid AWS S3 URL", "InvalidParameters", 400)

    async def download_file_from_s3(
        self,
        url: str,
        file_name: str,
        purpose: str,
        media_type: str,
        creation_time: Optional[str],
        file_id: str,
    ):
        """Download a file from a URL and save it as a file.
        Args:
            url: URL of the file to download.
            file_name: Name of the file.
            purpose: Purpose of the file.
            media_type: Media type (video/image) of the file.
            file_id: File ID to be used for the file.
        Returns:
            A unique id for the asset.
        """
        if not os.environ.get("AWS_ACCESS_KEY_ID") or not os.environ.get("AWS_SECRET_ACCESS_KEY"):
            raise ServiceException(
                "AWS_ACCESS_KEY_ID or AWS_SECRET_ACCESS_KEY environment variables are not set "
                "to download file from AWS S3 or MinIO",
                "InvalidParameters",
                400,
            )

        aws_access_key_id = os.environ.get("AWS_ACCESS_KEY_ID")
        aws_secret_access_key = os.environ.get("AWS_SECRET_ACCESS_KEY")
        logger.debug("AWS credentials configured.")

        bucket_name, object_key = self._get_bucket_and_object_key_from_url(url)
        if not bucket_name or not object_key:
            raise ServiceException("Invalid AWS S3 URL", "InvalidParameters", 400)
        logger.debug(f"Parsed S3 URL - bucket: {bucket_name}, object_key: {object_key}")

        endpoint_url = os.environ.get("S3_ENDPOINT_URL") or os.environ.get("AWS_ENDPOINT_URL_S3")
        if endpoint_url:
            s3_client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
            logger.info(f"Using custom S3 endpoint: {endpoint_url}")
        else:
            s3_client = boto3.client(
                "s3",
                aws_access_key_id=aws_access_key_id,
                aws_secret_access_key=aws_secret_access_key,
            )
            logger.debug("Using default S3 endpoint")

        logger.info(
            f"Downloading file from S3 - url: {url} bucket_name: {bucket_name} object_key: {object_key}"
        )

        async with tempfile.NamedTemporaryFile(mode="wb+", delete=True) as temp_file:
            logger.info(f"Created temporary file - path: {temp_file.name}")

            temp_file_name = os.path.basename(file_name)
            logger.debug(f"Temporary file name: {temp_file_name}")

            # Download file from S3 using get_object and write to temp file in chunks
            loop = asyncio.get_event_loop()
            logger.debug(
                f"Starting S3 get_object operation - bucket: {bucket_name}, key: {object_key}"
            )

            def get_s3_object():
                logger.debug("Executing S3 get_object in thread executor")
                try:
                    result = s3_client.get_object(Bucket=bucket_name, Key=object_key)
                    logger.debug(
                        f"S3 get_object successful - content_length: {result.get('ContentLength', 'unknown')}"
                    )
                    return result
                except s3_client.exceptions.NoSuchBucket:
                    logger.error(f"Bucket does not exist - bucket_name: {bucket_name}")
                    raise ServiceException(
                        "Bucket does not exist", "InvalidParameters", 400
                    ) from None
                except s3_client.exceptions.NoSuchKey:
                    logger.error(
                        f"Object does not exist - bucket_name: {bucket_name} object_key: {object_key}"
                    )
                    raise ServiceException(
                        "Object does not exist", "InvalidParameters", 400
                    ) from None
                except Exception as e:
                    logger.error(f"S3 get_object failed: {e}")
                    raise

            s3_object = await loop.run_in_executor(None, get_s3_object)

            # Read and write the file in chunks
            body = s3_object["Body"]
            bytes_written = 0
            chunk_count = 0
            logger.debug("Starting chunked download from S3 object body")

            while True:

                def read_chunk():
                    logger.debug(f"Reading chunk {chunk_count + 1} from S3 body")
                    try:
                        chunk = body.read(1024 * 1024 * 10)  # 10MB chunks
                        return chunk
                    except Exception as e:
                        logger.error(f"Failed to read chunk {chunk_count + 1}: {e}")
                        raise

                chunk = await loop.run_in_executor(None, read_chunk)
                if not chunk:
                    logger.debug("No more chunks to read, download complete")
                    break
                await temp_file.write(chunk)
                bytes_written += len(chunk)
                chunk_count += 1
            logger.info(f"Downloaded file from S3 - url: {url} bytes: {bytes_written}")

            # Flush and seek to beginning so save_file can read it
            await temp_file.flush()
            await temp_file.seek(0)

            asset_id = await self.save_file(
                temp_file, temp_file_name, purpose, media_type, creation_time, file_id, url, ""
            )
            logger.info(f"Saved file to temporary file - asset_id: {asset_id}")

            return asset_id

    async def download_file(
        self,
        url: str,
        file_name: str,
        purpose: str,
        media_type: str,
        creation_time: Optional[str],
        file_id: str,
        url_headers: Optional[dict] = None,
    ):
        """Download a file from a URL and save it as a file.
        Args:
            url: URL of the file to download.
            file_name: Name of the file.
            purpose: Purpose of the file.
            media_type: Media type (video/image) of the file.
            creation_time: ISO 8601 creation time for frame time offsets.
            file_id: File ID to be used for the file.
            url_headers: Optional HTTP headers for the download request
                (e.g., Authorization). Filtered through an allowlist and
                only sent to the original host over HTTPS. Overrides
                ASSET_DOWNLOAD_AUTH_TOKENS for this request.
        Returns:
            A unique id for the asset.
        """

        # SSRF Protection: Validate URL before making request (async)
        await validate_url_ssrf_runtime_async(url)

        parsed_url = urlparse(url)
        path_extension = os.path.splitext(parsed_url.path)[-1].lower()
        extension = path_extension or os.path.splitext(file_name)[-1].lower()

        supported_extensions = (".mp4", ".avi", ".mov", ".jpg", ".jpeg", ".png", ".webm")
        if extension not in supported_extensions:
            # VST download URL may not have an extension, so use the file_name extension
            extension = os.path.splitext(file_name)[-1].lower()
            logger.warning(
                f"Could not determine extension from URL - url: {url}, trying with default name: {file_name}"
            )

        logger.info(
            f"Downloading file from URL - url: {url} file_name: {file_name} extension: {extension}"
        )

        async with tempfile.NamedTemporaryFile(mode="wb+", delete=True) as temp_file:
            import aiohttp

            logger.info(f"Created temporary file - path: {temp_file.name}")

            # Configure timeout: 10s connect, 300s total
            # Use environment variables for timeouts, with defaults
            # Safely parse timeout values with validation
            try:
                total_timeout = int(os.environ.get("ASSET_DOWNLOAD_TOTAL_TIMEOUT", "300"))
                if total_timeout <= 0:
                    logger.warning(
                        f"Invalid ASSET_DOWNLOAD_TOTAL_TIMEOUT value: {total_timeout}, using default 300"
                    )
                    total_timeout = 300
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Failed to parse ASSET_DOWNLOAD_TOTAL_TIMEOUT: {e}, using default 300"
                )
                total_timeout = 300

            try:
                connect_timeout = int(os.environ.get("ASSET_DOWNLOAD_CONNECT_TIMEOUT", "10"))
                if connect_timeout <= 0:
                    logger.warning(
                        f"Invalid ASSET_DOWNLOAD_CONNECT_TIMEOUT value: {connect_timeout}, using default 10"
                    )
                    connect_timeout = 10
            except (ValueError, TypeError) as e:
                logger.warning(
                    f"Failed to parse ASSET_DOWNLOAD_CONNECT_TIMEOUT: {e}, using default 10"
                )
                connect_timeout = 10

            timeout = aiohttp.ClientTimeout(total=total_timeout, connect=connect_timeout)

            # SSL verification: enabled by default. To skip verification for specific
            # trusted domains (e.g., internal artifactory with self-signed certs), set:
            #   ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS=artifactory.nvidia.com,nv-wowza-pdc.nvidia.com
            # SSL is only relaxed for listed domains; all other URLs remain verified.
            ssl_skip_domains_env = os.environ.get("ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS", "")
            ssl_skip_domains = [
                d.strip().lower() for d in ssl_skip_domains_env.split(",") if d.strip()
            ]

            def _ssl_for_url(target_url):
                """Return SSL context for a URL: False to skip, None for default verification."""
                host = (urlparse(target_url).hostname or "").lower()
                if host in ssl_skip_domains:
                    logger.info(
                        "SSL verification skipped for domain %s "
                        "(in ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS)",
                        host,
                    )
                    return False
                return None

            # Redirect handling: set ASSET_DOWNLOAD_MAX_REDIRECTS to the max number of
            # hops allowed (default 0 = disabled). Each hop is SSRF-validated and gets
            # its own SSL context based on the target domain.
            try:
                max_redirects = int(os.environ.get("ASSET_DOWNLOAD_MAX_REDIRECTS", "0"))
                max_redirects = max(0, min(max_redirects, 10))  # Clamp to 0-10
            except (ValueError, TypeError):
                max_redirects = 0

            # Auth headers: request-level url_headers override server-level env config.
            # Server-level: ASSET_DOWNLOAD_AUTH_TOKENS=domain1=Bearer token1;domain2=Basic xyz
            # Security: auth headers are only sent to the original domain. On redirect
            # to a different domain, Authorization is stripped to prevent token leakage.
            ALLOWED_URL_HEADERS = {
                "authorization",
                "x-api-key",
                "x-jfrog-art-api",
                "cookie",
                "accept",
                "accept-language",
            }
            original_host = (urlparse(url).hostname or "").lower()

            def _auth_headers_for_url(target_url):
                """Build auth headers for a URL: request-level > env-level > none.
                Auth headers are never sent over plain HTTP to prevent credential leakage.
                """
                req_headers = {"User-Agent": "NVIDIA-RTVI/1.0"}
                parsed_target = urlparse(target_url)
                target_host = (parsed_target.hostname or "").lower()
                target_scheme = (parsed_target.scheme or "").lower()

                # Never send auth over plain HTTP
                if target_scheme != "https":
                    return req_headers

                # Request-level override (highest priority) — only on same origin
                if url_headers and target_host == original_host:
                    for key, value in url_headers.items():
                        if key.lower() in ALLOWED_URL_HEADERS:
                            req_headers[key] = value
                        else:
                            logger.warning("Blocked disallowed url_header: %s", key)
                    return req_headers

                # Server-level: parse ASSET_DOWNLOAD_AUTH_TOKENS env var (domain-scoped)
                auth_tokens_env = os.environ.get("ASSET_DOWNLOAD_AUTH_TOKENS", "")
                if auth_tokens_env:
                    for entry in auth_tokens_env.split(";"):
                        entry = entry.strip()
                        if "=" not in entry:
                            continue
                        domain, token = entry.split("=", 1)
                        if domain.strip().lower() == target_host:
                            req_headers["Authorization"] = token.strip()
                            logger.info(
                                "Auth header added for domain %s from ASSET_DOWNLOAD_AUTH_TOKENS",
                                target_host,
                            )
                            break

                return req_headers

            # Track downloaded size for protection against large files
            total_size = 0

            # Manual redirect loop: validates SSRF and SSL per hop
            current_url = url
            response = None
            for hop in range(max_redirects + 1):  # +1 for the initial request
                ssl_ctx = _ssl_for_url(current_url)
                connector = aiohttp.TCPConnector(ssl=ssl_ctx)
                headers = _auth_headers_for_url(current_url)
                session = aiohttp.ClientSession(
                    timeout=timeout, connector=connector, headers=headers
                )
                try:
                    response = await session.get(current_url, allow_redirects=False)
                    logger.info(
                        "Downloading file from URL - url: %s response: %d (hop %d)",
                        current_url,
                        response.status,
                        hop,
                    )

                    if response.status in (301, 302, 303, 307, 308):
                        location = response.headers.get("Location")
                        await response.release()
                        await session.close()

                        if not location:
                            raise ServiceException(
                                "Redirect without Location header.",
                                "DownloadFailed",
                                502,
                            )

                        if hop >= max_redirects:
                            raise ServiceException(
                                f"URL redirects are not allowed (HTTP {response.status}). "
                                f"Set ASSET_DOWNLOAD_MAX_REDIRECTS to enable.",
                                "RedirectNotAllowed",
                                422,
                            )

                        # Resolve relative redirect URLs
                        current_url = urljoin(current_url, location)
                        logger.info("Redirect hop %d -> %s", hop + 1, current_url)

                        # SSRF-validate each intermediate redirect target
                        await validate_url_ssrf_runtime_async(current_url)
                        continue

                    # Not a redirect — this is the final response
                    break
                except Exception:
                    await session.close()
                    raise

            # At this point, response and session are the final hop
            try:
                if response.status != 200:
                    logger.info("Failed to download file from URL. HTTP status %d", response.status)
                    body = await response.text()
                    logger.debug(
                        "Response body for failed download (status %d): %s",
                        response.status,
                        body[:500],
                    )
                    raise ServiceException(
                        f"Failed to download file from URL. HTTP status {response.status}",
                        "DownloadFailed",
                        502,
                        auto_log=False,
                    )

                # Download file with size limit protection
                async for chunk in response.content.iter_chunked(1024 * 1024 * 10):
                    if chunk:
                        total_size += len(chunk)
                        if total_size > MAX_DOWNLOAD_FILE_SIZE:
                            raise ServiceException(
                                f"File size exceeds maximum allowed size of "
                                f"{MAX_DOWNLOAD_FILE_SIZE / (1024 * 1024):.0f} MB",
                                "FileTooLarge",
                                413,
                            )
                        await temp_file.write(chunk)

                logger.info(
                    "Downloaded file from URL - url: %s bytes: %d",
                    current_url,
                    total_size,
                )
            finally:
                await response.release()
                await session.close()

            await temp_file.flush()
            await temp_file.seek(0)
            logger.info(f"Saving file to temporary file - path: {temp_file.name}")

            # Use the actual filename from URL or the provided file_name
            url_filename = os.path.basename(parsed_url.path)
            if extension == path_extension:
                temp_file_name = url_filename
            else:
                temp_file_name = file_name

            asset_id = await self.save_file(
                temp_file, temp_file_name, purpose, media_type, creation_time, file_id, url, ""
            )
            logger.info(f"Saved file to temporary file - asset_id: {asset_id}")

            return asset_id

    async def save_file(
        self,
        file,
        file_name,
        purpose: str,
        media_type: str,
        creation_time: Optional[str],
        file_id: Optional[str] = None,
        url: Optional[str] = None,
        sensor_name: str = "",
    ):
        """Save the uploaded as a file.

        Args:
            file: File object to save file.
            file_name: Name of the file.
            purpose: Purpose of the file.
            media_type: Media type (video/image) of the file.
            creation_time: Creation time of the file.
            file_id: UUID of the file. If not provided, a new ID will be generated.
            url: URL of the file if downloaded from URL.
            sensor_name: User-defined sensor name. Defaults to empty string.
        Returns:
            A unique id for the asset.
        """

        if file_id:
            asset_id = str(file_id)
        else:
            # Generate a unique id for the asset.
            asset_id = str(uuid.uuid4())
            while asset_id in self._asset_map:
                asset_id = str(uuid.uuid4())

        asset_dir = os.path.join(self._asset_dir, asset_id)

        try:
            await aiofiles.os.makedirs(asset_dir)
        except FileExistsError as err:
            raise ServiceException(
                f"Asset directory already exists: {asset_dir}", "BadParameter", 400
            ) from err
        except Exception as err:
            raise ServiceException("Could not create directory for asset") from err

        current_storage_size = await self._get_storage_usage()
        current_file_size = 0

        # Write the uploaded file to assets directory
        async with aiofiles.open(os.path.join(asset_dir, file_name), "wb") as f:
            while chunk := await file.read(1024 * 1024 * 10):
                current_file_size += len(chunk)

                # Check if writing the current chunk will cross threshold
                if self._max_storage_usage_gb and (
                    current_storage_size + current_file_size / (1024.0**3)
                    > AGE_OUT_THRESHOLD * self._max_storage_usage_gb
                ):
                    # Try to clean assets
                    await self._age_out_assets()
                    current_storage_size = await self._get_storage_usage()
                    current_file_size = 0

                # Check if writing the current chunk will cross max size
                if self._max_storage_usage_gb and (
                    current_storage_size + current_file_size / (1024.0**3)
                    > self._max_storage_usage_gb
                ):
                    await f.close()
                    try:
                        loop = asyncio.get_event_loop()
                        await loop.run_in_executor(None, shutil.rmtree, asset_dir)
                    except Exception:
                        pass
                    raise ServiceException(
                        "Asset storage full. Could not remove existing older assets"
                        " because they are in use",
                        "ServerBusy",
                        503,
                    )
                await f.write(chunk)

        asset = Asset(
            asset_id=asset_id,
            path=os.path.join(asset_dir, file_name),
            fileName=file_name,
            purpose=purpose,
            media_type=media_type,
            asset_dir=asset_dir,
            username="",
            password="",
            description="",
            video_fps=None,
            creation_time=creation_time,
            url=url,
            sensor_name=sensor_name,
        )

        # add an entry in the asset map
        self._asset_map[asset_id] = asset

        # Invalidate storage cache since we added a file
        self._storage_usage_cache = None

        logger.info(f"[AssetManager] Saved file - asset-id: {asset_id} name: {file_name}")

        # Age-out already called during upload if needed, skip redundant call

        return asset_id

    # MIME type → file extension mapping shared by save_from_base64
    _MIME_TO_EXT = {
        "video/mp4": ".mp4",
        "video/quicktime": ".mov",
        "video/mov": ".mov",
        "video/x-msvideo": ".avi",
        "video/avi": ".avi",
        "video/webm": ".webm",
        "video/mkv": ".mkv",
        "video/x-matroska": ".mkv",
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }

    async def save_from_base64(
        self,
        data_url: str,
        media_type: str,
        creation_time: Optional[str],
        asset_id: str,
    ) -> str:
        """Decode an RFC 2397 data: URI and register the result as an asset.

        Args:
            data_url: A data: URI of the form ``data:<mime>[;base64],<payload>``.
            media_type: "video" or "image" — used as the asset's media_type and
                        as a fallback when the MIME type is not recognised.
            creation_time: ISO 8601 creation time for frame-time offsets, or None.
            asset_id: Caller-supplied UUID string for the new asset.

        Returns:
            The asset_id that was registered.

        Raises:
            ServiceException(400): Malformed data URL or base64 decoding failure.
            ServiceException(413): Decoded payload exceeds MAX_DOWNLOAD_FILE_SIZE.
        """
        import base64 as _base64

        if "," not in data_url:
            raise ServiceException(
                "Invalid data URL: missing ',' separator between header and payload.",
                "InvalidDataUrl",
                400,
            )

        header, encoded_data = data_url.split(",", 1)

        # Derive file extension from MIME type in the header
        # Header format: "data:<mime>[;base64]"
        mime_part = header[len("data:") :]  # strip leading "data:"
        if ";" in mime_part:
            mime_type = mime_part.split(";")[0]
        else:
            mime_type = mime_part

        file_ext = self._MIME_TO_EXT.get(mime_type)
        if file_ext is None:
            # Unknown MIME — fall back to a sensible default based on media_type
            file_ext = ".mp4" if media_type == "video" else ".jpg"

        file_name = f"base64_media{file_ext}"

        # Decode payload — add missing padding if needed
        try:
            if ";base64" in header.lower():
                missing_padding = len(encoded_data) % 4
                if missing_padding:
                    encoded_data += "=" * (4 - missing_padding)
                raw_data = _base64.b64decode(encoded_data, validate=True)
            else:
                # URL-encoded data (less common but valid per RFC 2397)
                from urllib.parse import unquote

                raw_data = unquote(encoded_data).encode()
        except Exception as exc:
            raise ServiceException(
                f"Failed to decode base64 payload: {exc}",
                "InvalidDataUrl",
                400,
            ) from exc

        # Enforce the same size limit as HTTP downloads
        if len(raw_data) > MAX_DOWNLOAD_FILE_SIZE:
            raise ServiceException(
                f"Decoded data size ({len(raw_data)} bytes) exceeds the maximum "
                f"allowed size of {MAX_DOWNLOAD_FILE_SIZE // (1024 * 1024)} MB.",
                "FileTooLarge",
                413,
            )

        # Create the asset directory and write the file
        asset_dir = os.path.join(self._asset_dir, asset_id)
        try:
            await aiofiles.os.makedirs(asset_dir)
        except FileExistsError as err:
            raise ServiceException(
                f"Asset directory already exists: {asset_dir}", "BadParameter", 400
            ) from err

        file_path = os.path.join(asset_dir, file_name)
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(raw_data)

        asset = Asset(
            asset_id=asset_id,
            path=file_path,
            fileName=file_name,
            purpose="vision",
            media_type=media_type,
            asset_dir=asset_dir,
            creation_time=creation_time,
        )

        self._asset_map[asset_id] = asset
        self._storage_usage_cache = None

        logger.info(
            "[AssetManager] Saved base64 asset - asset-id: %s name: %s size: %d bytes",
            asset_id,
            file_name,
            len(raw_data),
        )
        return asset_id

    def add_file(
        self,
        file_path,
        purpose,
        media_type,
        reuse_asset=False,
        creation_time=None,
        file_id: Optional[str] = None,
        sensor_name: str = "",
    ):
        """Add a file already on the file system as a path.

        Args:
            file_path: Path of the file to add.
            purpose: Purpose of the file.
            media_type: Media type (video/image) of the file.
            reuse_asset: Whether to reuse an existing asset.
            creation_time: Creation time of the file.
            file_id: UUID of the file. If not provided, a new ID will be generated.
            sensor_name: User-defined sensor name. Defaults to empty string.
        Returns:
            A unique id for the asset.
        """
        if not os.path.isfile(file_path):
            raise ServiceException(f"{file_path} is not a valid file", "InvalidParameters", 400)

        if reuse_asset:
            asset = self._get_asset_id_for_file(file_path)
            if asset:
                logger.info(f"Reusing asset id {asset.asset_id} for {file_path}")
                return asset.asset_id

        if file_id:
            asset_id = str(file_id)
        else:
            # Generate a unique id for the asset.
            asset_id = str(uuid.uuid4())
            while asset_id in self._asset_map:
                asset_id = str(uuid.uuid4())

        # No directory needed for add_file since file already exists at file_path
        asset = Asset(
            asset_id=asset_id,
            path=file_path,
            fileName=os.path.basename(file_path),
            purpose=purpose,
            media_type=media_type,
            asset_dir="",  # No asset directory for existing files
            username="",
            password="",
            description="",
            video_fps=None,
            creation_time=creation_time,
            sensor_name=sensor_name,
        )

        # add an entry in the asset map
        self._asset_map[asset_id] = asset
        logger.info(
            f"[AssetManager] Added file from path - asset-id: {asset_id} original path: {file_path}"
        )
        return asset_id

    def add_live_stream(
        self,
        url: str,
        description="",
        username="",
        password="",
        place_name="",
        place_type="",
        place_lat=None,
        place_lon=None,
        place_alt=None,
        place_coordinate_x=None,
        place_coordinate_y=None,
        stream_id: Optional[str] = None,
        sensor_name: str = "",
        camera_id: Optional[str] = None,
    ):
        """Add a live stream.

        Args:
            url: RTSP url of the stream
            description (optional): Description of the live stream. Defaults to "".
            username (optional): Username to access the stream. Defaults to "".
            password (optional): Password to access the stream. Defaults to "".
            place_name (optional): Name of the place/location. Defaults to "".
            place_type (optional): Type of place/location. Defaults to "".
            place_lat (optional): Latitude of the camera location. Defaults to None.
            place_lon (optional): Longitude of the camera location. Defaults to None.
            place_alt (optional): Altitude of the camera location. Defaults to None.
            place_coordinate_x (optional): X coordinate within the place. Defaults to None.
            place_coordinate_y (optional): Y coordinate within the place. Defaults to None.
            stream_id: UUID of the stream. If not provided, a new ID will be generated.
            sensor_name (optional): User-defined sensor name. Defaults to "".
            camera_id (optional): External camera identifier for CV-compatible lookups.
                Defaults to None.
        Returns:
            A unique id for the asset.
        """
        if camera_id:
            existing_asset_id = self._camera_id_map.get(camera_id)
            if existing_asset_id and existing_asset_id in self._asset_map:
                raise ServiceException(
                    f"Live stream with camera_id '{camera_id}' already exists",
                    "DuplicateCameraId",
                    409,
                )
            if existing_asset_id:
                # Defensive cleanup for stale camera_id mappings left by an
                # interrupted or legacy cleanup path.
                self._camera_id_map.pop(camera_id, None)

        if stream_id:
            asset_id = str(stream_id)
            if asset_id in self._asset_map:
                raise ServiceException(
                    f"Live stream with stream_id '{asset_id}' already exists",
                    "DuplicateStreamId",
                    409,
                )
        else:
            # Generate a unique id for the asset.
            asset_id = str(uuid.uuid4())
            while asset_id in self._asset_map:
                asset_id = str(uuid.uuid4())

        # No directory needed for live streams since there's no file to store
        asset = Asset(
            asset_id=asset_id,
            path=url,
            fileName=url,
            purpose="",
            media_type="",
            asset_dir="",  # No asset directory for live streams
            username=username,
            password=password,
            description=description,
            video_fps=None,
            place_name=place_name,
            place_type=place_type,
            place_lat=place_lat,
            place_lon=place_lon,
            place_alt=place_alt,
            place_coordinate_x=place_coordinate_x,
            place_coordinate_y=place_coordinate_y,
            sensor_name=sensor_name,
            camera_id=camera_id,
        )

        # add an entry in the asset map
        self._asset_map[asset_id] = asset

        # Track camera_id -> asset_id mapping for CV-compatible lookups
        if camera_id:
            self._camera_id_map[camera_id] = asset_id

        logger.info(f"[AssetManager] Added live stream - asset-id: {asset_id} URL: {url}")
        return asset_id

    def cleanup_asset(self, asset_id: str, executor: Optional[ThreadPoolExecutor] = None):
        """Remove the asset and associated storage directory

        Raises an exception if the asset is in use.

        Args:
            asset_id: ID of the asset to remove
            executor: Optional ThreadPoolExecutor. When provided, the slow
                ``shutil.rmtree`` is submitted to it fire-and-forget so the
                caller only pays the cost of in-memory bookkeeping. When
                ``None``, rmtree runs inline (preserves legacy behavior).
        """
        if asset_id in self._aged_out_assets:
            raise ServiceException(f"{asset_id} already deleted", "BadParameter", 400)

        if asset_id not in self._asset_map:
            raise ServiceException(f"No such resource {asset_id}", "BadParameter", 400)

        # Do not allow asset to be removed if it is in use.
        if self._asset_map[asset_id].use_count > 0:
            raise ServiceException(
                f"Resource {asset_id} is currently being used", "ResourceInUse", 409
            )

        # Remove asset directory if it exists (only save_file creates directories now)
        asset = self._asset_map[asset_id]
        asset_dir = asset.asset_dir if asset.asset_dir else None

        def _do_rmtree(path: str, aid: str) -> None:
            try:
                shutil.rmtree(path)
            except FileNotFoundError:
                pass
            except (OSError, PermissionError) as e:
                logger.warning(f"Error removing asset {aid}: {e}")

        if asset_dir and os.path.exists(asset_dir):
            if executor is not None:
                executor.submit(_do_rmtree, asset_dir, asset_id)
            else:
                _do_rmtree(asset_dir, asset_id)

        # Clean up camera_id mapping if present
        if asset.camera_id and asset.camera_id in self._camera_id_map:
            del self._camera_id_map[asset.camera_id]

        # Defensive: two concurrent cleanup_asset calls for the same asset_id
        # would race on the pop. AssetManager has no per-asset lock, so accept
        # that the second caller sees the slot already gone.
        self._asset_map.pop(asset_id, None)

        # Invalidate storage cache since we freed space
        self._storage_usage_cache = None

        logger.info(f"Removed asset {asset_id} and cleaned up associated resources")

    def get_asset_id_by_camera_id(self, camera_id: str) -> Optional[str]:
        """Look up an asset ID by its external camera_id.

        Args:
            camera_id: The external camera identifier to look up.

        Returns:
            The asset_id if found, or None if no asset has the given camera_id.
        """
        return self._camera_id_map.get(camera_id)

    def _get_existing_asset_ids(self):
        entries = os.listdir(self._asset_dir)
        return [
            entry
            for entry in entries
            if os.path.isdir(os.path.join(self._asset_dir, entry))
            and os.path.isfile(os.path.join(self._asset_dir, entry, "info.json"))
        ]

    def _get_asset_id_for_file(self, filepath: str) -> Asset:
        """
        Returns the Asset object that matches the given filename.

        Args:
            filename (str): The filename to search for in the asset map.

        Returns:
            Asset: The Asset object that matches the filename, or None if not found.
        """
        for asset in self._asset_map.values():
            if asset.path == filepath:
                return asset
        return None

    def list_assets(self):
        """Get a list of all assets"""
        return list(self._asset_map.values())

    def check_asset_exists(self, asset_id: str):
        """Check if an asset exists.
        Args:
            asset_id: ID of the asset to check.
        Returns:
            True if the asset exists, False otherwise.
        """
        return asset_id in self._asset_map

    def get_asset(self, asset_id: str):
        """Get asset information.

        Args:
            asset_id: Unique id of the asset.

        Returns:
            Information of the asset.
        """
        if asset_id in self._aged_out_assets:
            raise ServiceException(
                f"{asset_id} already deleted because of age out policy", "BadParameter", 400
            )

        if asset_id not in self._asset_map:
            raise ServiceException(f"No such resource {asset_id}", "BadParameter", 400)
        return self._asset_map[asset_id]

    async def _get_storage_usage(self, use_cache=True):
        """Get the current storage usage of the assets directory in GB.

        Args:
            use_cache: If True, return cached value if available and fresh
        """
        current_time = time.time()

        # Return cached value if fresh
        if (
            use_cache
            and self._storage_usage_cache is not None
            and (current_time - self._storage_usage_cache_time) < self._storage_usage_cache_ttl
        ):
            return self._storage_usage_cache

        # Calculate and cache
        proc = await asyncio.subprocess.create_subprocess_exec(
            "du", "-s", "-b", self._asset_dir, stdout=asyncio.subprocess.PIPE
        )
        await proc.wait()
        output = await proc.stdout.read()
        usage = int(output.split()[0].decode("utf-8")) / (1024.0**3)

        self._storage_usage_cache = usage
        self._storage_usage_cache_time = current_time

        return usage

    async def _is_storage_above_threshold(self):
        return (
            bool(self._max_storage_usage_gb)
            and (await self._get_storage_usage()) > self._max_storage_usage_gb * AGE_OUT_THRESHOLD
        )

    async def _age_out_assets(self):
        """Age out old assets to free up storage space."""
        if not self._max_storage_usage_gb:
            return

        logger.debug(
            "Asset storage current size: %.2f GB, Threshold: %.2f GB, Max size: %.2f GB",
            await self._get_storage_usage(),
            self._max_storage_usage_gb * AGE_OUT_THRESHOLD,
            self._max_storage_usage_gb,
        )

        if not (await self._is_storage_above_threshold()):
            return

        logger.info(
            "Asset storage size above threshold. Current size: %.2f GB,"
            " Threshold: %.2f GB, Max size: %.2f GB",
            await self._get_storage_usage(),
            self._max_storage_usage_gb * AGE_OUT_THRESHOLD,
            self._max_storage_usage_gb,
        )

        # Get assets that have storage directories (only save_file creates directories)
        # add_file and add_live_stream don't use disk space so skip them
        assets_with_dirs = [
            (asset_id, asset)
            for asset_id, asset in self._asset_map.items()
            if asset.asset_dir and os.path.exists(asset.asset_dir)
        ]

        if not assets_with_dirs:
            return

        # Sort by directory modification time
        asset_ids = [aid for aid, _ in assets_with_dirs]
        mtimes = await asyncio.gather(
            *[aiofiles.os.path.getmtime(asset.asset_dir) for _, asset in assets_with_dirs]
        )
        asset_ids = [d for _, d in sorted(zip(mtimes, asset_ids))]

        loop = asyncio.get_event_loop()
        # Age out the oldest asset directories until the storage usage is below the threshold
        while await self._is_storage_above_threshold() and asset_ids:
            oldest_asset_dir = asset_ids.pop(0)
            oldest_asset = self.get_asset(oldest_asset_dir)

            if oldest_asset.use_count:
                continue

            # Remove the oldest asset directory
            size_before_removal = await self._get_storage_usage()
            try:
                if self._asset_removal_callback and not (
                    await loop.run_in_executor(None, self._asset_removal_callback, oldest_asset)
                ):
                    continue
                await loop.run_in_executor(None, self.cleanup_asset, oldest_asset_dir)
                self._aged_out_assets.append(oldest_asset_dir)
            except Exception:
                continue
            logger.info(
                "Removed asset %s due to age out policy. Asset storage size before removal"
                " = %.2f GB. After removal = %.2f GB. Max asset storage size = %.2f GB",
                oldest_asset_dir,
                size_before_removal,
                await self._get_storage_usage(),
                self._max_storage_usage_gb,
            )

        if await self._is_storage_above_threshold():
            logger.warning(
                "Asset storage close to limit. Current size = %.2f GB. Max size = %.2f GB",
                await self._get_storage_usage(),
                self._max_storage_usage_gb,
            )

    async def _ttl_expire_assets(self):
        """Evict assets whose on-disk directory is older than ASSET_MAX_AGE_HOURS."""
        if not self._max_asset_age_hours:
            return

        max_age_secs = self._max_asset_age_hours * 3600
        now = time.time()
        loop = asyncio.get_event_loop()

        assets_with_dirs = [
            (asset_id, asset)
            for asset_id, asset in list(self._asset_map.items())
            if asset.asset_dir and os.path.exists(asset.asset_dir)
        ]

        for asset_id, asset in assets_with_dirs:
            if asset.use_count:
                continue
            try:
                mtime = await aiofiles.os.path.getmtime(asset.asset_dir)
            except OSError:
                continue

            age_secs = now - mtime
            if age_secs <= max_age_secs:
                continue

            logger.info(
                "TTL-evicting asset %s (age %.2fh > limit %.1fh)",
                asset_id,
                age_secs / 3600,
                self._max_asset_age_hours,
            )
            try:
                if self._asset_removal_callback and not (
                    await loop.run_in_executor(None, self._asset_removal_callback, asset)
                ):
                    continue
                await loop.run_in_executor(None, self.cleanup_asset, asset_id)
                self._aged_out_assets.append(asset_id)
            except Exception as e:
                logger.warning("Failed to TTL-evict asset %s: %s", asset_id, e)

    def get_stats(self) -> dict:
        """Return asset storage statistics for monitoring."""
        now = time.time()
        assets_with_dirs = [
            (asset_id, asset)
            for asset_id, asset in self._asset_map.items()
            if asset.asset_dir and os.path.exists(asset.asset_dir)
        ]

        oldest_age_hours = 0.0
        for _, asset in assets_with_dirs:
            try:
                mtime = os.path.getmtime(asset.asset_dir)
                age_hours = (now - mtime) / 3600
                if age_hours > oldest_age_hours:
                    oldest_age_hours = age_hours
            except OSError:
                pass

        return {
            "asset_count": len(self._asset_map),
            "asset_count_with_storage": len(assets_with_dirs),
            "aged_out_count": len(self._aged_out_assets),
            "oldest_asset_age_hours": round(oldest_age_hours, 3),
            "max_storage_usage_gb": self._max_storage_usage_gb,
            "max_asset_age_hours": self._max_asset_age_hours or None,
        }

    def _age_out_thread_func(self):
        logger.info(
            "Started asset monitoring. Storage max = %s GB. TTL = %s h.",
            self._max_storage_usage_gb or "unlimited",
            self._max_asset_age_hours or "disabled",
        )
        while True:
            with TimeMeasure("Age out assets"):
                asyncio.run(self._age_out_assets())
                asyncio.run(self._ttl_expire_assets())
            time.sleep(AGE_OUT_RUN_INTERVAL_SEC)

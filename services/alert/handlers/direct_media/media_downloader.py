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
Media Downloader - Downloads media from URLs with security and size enforcement.

Features:
- SSRF protection (blocks private IPs, localhost, cloud metadata endpoints)
- Streaming download with size limits
- Configurable timeout and max file size
"""

import ipaddress
import logging
import mimetypes
import os
import socket
import uuid
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import requests

logger = logging.getLogger(__name__)


@dataclass
class DownloadConfig:
    """Configuration for media downloads."""
    download_dir: str = '/tmp/alert_bridge_media'
    timeout_seconds: int = 30
    max_size_mb: int = 50
    allow_private_urls: bool = False


class MediaDownloader:
    """
    Downloads media from URLs with security controls.
    
    Security features:
    - URL scheme validation (http/https only)
    - SSRF protection (blocks private/internal IPs)
    - Streaming size enforcement
    """
    
    def __init__(self, config: DownloadConfig):
        """
        Initialize MediaDownloader.
        
        Args:
            config: Download configuration
        """
        self.config = config
        self._ensure_download_dir()
    
    def _ensure_download_dir(self) -> None:
        """Ensure download directory exists."""
        if not os.path.exists(self.config.download_dir):
            os.makedirs(self.config.download_dir, exist_ok=True)
    
    def validate_url(self, url: str) -> tuple[bool, str]:
        """
        Validate URL to prevent SSRF attacks.
        
        Checks:
        - Only http/https schemes allowed
        - No private/internal IP addresses
        - No localhost or loopback addresses
        - No cloud metadata endpoints (169.254.x.x)
        
        Args:
            url: URL to validate
            
        Returns:
            Tuple of (is_valid, error_message)
        """
        if self.config.allow_private_urls:
            logger.warning("SSRF validation bypassed (allow_private_urls=true) - DO NOT USE IN PRODUCTION")
            parsed = urlsplit(url)
            if parsed.scheme not in ('http', 'https'):
                return False, f"Invalid URL scheme '{parsed.scheme}': only http/https allowed"
            return True, ""
        
        try:
            parsed = urlsplit(url)
            
            if parsed.scheme not in ('http', 'https'):
                return False, f"Invalid URL scheme '{parsed.scheme}': only http/https allowed"
            
            hostname = parsed.hostname
            if not hostname:
                return False, "URL has no hostname"
            
            # Block obvious localhost patterns
            localhost_patterns = {'localhost', 'localhost.localdomain', '127.0.0.1', '::1', '0.0.0.0'}
            if hostname.lower() in localhost_patterns:
                return False, f"Localhost URLs not allowed: {hostname}"
            
            # Resolve hostname to IP and check for private/internal ranges
            try:
                addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
                for family, _, _, _, sockaddr in addr_info:
                    ip_str = sockaddr[0]
                    try:
                        ip = ipaddress.ip_address(ip_str)
                        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
                            return False, f"Private IP addresses not allowed: {hostname} resolves to {ip_str}"
                    except ValueError:
                        continue
                        
            except socket.gaierror as e:
                return False, f"Could not resolve hostname '{hostname}': {e}"
            
            return True, ""
            
        except Exception as e:
            return False, f"URL validation error: {e}"
    
    def download(self, url: str, worker_id: int) -> Optional[str]:
        """
        Download media from URL to local file.
        
        Args:
            url: URL to download media from
            worker_id: Worker ID for unique file naming
            
        Returns:
            Local file path if successful, None otherwise
        """
        is_valid, error_msg = self.validate_url(url)
        if not is_valid:
            logger.error("URL validation failed for %s: %s", url[:100], error_msg)
            return None
        
        try:
            self._ensure_download_dir()
            
            # Determine file extension from URL
            url_path = urlsplit(url).path
            ext = os.path.splitext(url_path)[1] or '.mp4'
            
            # Generate unique filename
            unique_id = str(uuid.uuid4())[:8]
            filename = f"worker_{worker_id}_{unique_id}{ext}"
            local_path = os.path.join(self.config.download_dir, filename)
            
            logger.debug("Downloading media: %s -> %s", url[:100], local_path)
            
            response = requests.get(
                url,
                stream=True,
                timeout=self.config.timeout_seconds,
                allow_redirects=True
            )
            response.raise_for_status()
            
            # Check content length against max size
            max_size_bytes = self.config.max_size_mb * 1024 * 1024
            content_length = response.headers.get('content-length')
            if content_length:
                size_mb = int(content_length) / (1024 * 1024)
                if size_mb > self.config.max_size_mb:
                    logger.error("Media file too large: %.2f MB (max: %d MB)", size_mb, self.config.max_size_mb)
                    response.close()
                    return None
            
            # Update extension based on content-type
            content_type = response.headers.get('content-type', '')
            if content_type:
                guessed_ext = mimetypes.guess_extension(content_type.split(';')[0].strip())
                if guessed_ext and guessed_ext != ext:
                    local_path = local_path.replace(ext, guessed_ext)
            
            # Write to file with streaming size enforcement
            bytes_downloaded = 0
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    bytes_downloaded += len(chunk)
                    if bytes_downloaded > max_size_bytes:
                        logger.error("Media file exceeded max size during download: %d MB (max: %d MB)",
                                   bytes_downloaded // (1024 * 1024), self.config.max_size_mb)
                        response.close()
                        self._cleanup_file(local_path)
                        return None
                    f.write(chunk)
            
            # Verify file was written
            if not os.path.exists(local_path) or os.path.getsize(local_path) == 0:
                logger.error("Downloaded file is empty or missing: %s", local_path)
                self._cleanup_file(local_path)
                return None
            
            file_size_mb = os.path.getsize(local_path) / (1024 * 1024)
            logger.info("Media downloaded successfully: %.2f MB -> %s", file_size_mb, local_path)
            return local_path
            
        except requests.RequestException as e:
            logger.error("Failed to download media from %s: %s", url[:100], e)
            return None
        except Exception as e:
            logger.error("Unexpected error downloading media: %s", e, exc_info=True)
            return None
    
    @staticmethod
    def _cleanup_file(path: str) -> None:
        """Remove file if it exists."""
        try:
            if path and os.path.exists(path):
                os.unlink(path)
        except Exception as e:
            logger.warning("Failed to cleanup file %s: %s", path, e)
    
    @staticmethod
    def cleanup(path: str) -> None:
        """
        Public method to cleanup downloaded file.
        
        Args:
            path: Path to file to remove
        """
        MediaDownloader._cleanup_file(path)

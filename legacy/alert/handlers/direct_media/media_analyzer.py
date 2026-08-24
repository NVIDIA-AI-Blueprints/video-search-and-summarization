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
Pure media analysis helper — resolves media input and calls VLM.

No sink, no message mutation, no publish side-effects.
Both DirectMediaHandler (pipeline) and OnDemandVerificationService (HTTP)
delegate to this module so routing/download/cleanup logic lives in one place.
"""

import logging
import mimetypes
import os
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from openai.types.chat import ChatCompletionMessage

from .media_downloader import MediaDownloader

logger = logging.getLogger(__name__)


def _is_url(value: str) -> bool:
    return urlparse(value).scheme in {"http", "https", "rtsp"}


def _enforce_url_policy(downloader: MediaDownloader, url: str) -> None:
    """Validate URL against SSRF / private-address rules.

    Applies the same checks the downloader uses (scheme, private IP,
    localhost, cloud metadata) so that direct-URL-to-VLM paths cannot
    bypass the security policy.

    Raises:
        ValueError: URL blocked by policy.
    """
    is_valid, error_msg = downloader.validate_url(url)
    if not is_valid:
        raise ValueError(f"URL blocked by security policy: {error_msg}")


def _infer_media_type(path: str) -> str:
    mime, _ = mimetypes.guess_type(path)
    if mime and mime.startswith("image/"):
        return "image"
    return "video"


def analyze_single_media(
    vlm_client,
    downloader: MediaDownloader,
    media_path: str,
    user_prompt: str,
    system_prompt: Optional[str],
    use_base64: bool = False,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> ChatCompletionMessage:
    """Analyse a single media source (local file or URL) through VLM.

    Routing:
    - Local file  → upload_media_file (image) / analyze_local_video (video)
    - URL + base64=false → analyze_image_url / analyze_video_url  (VLM fetches)
    - URL + base64=true  → download → local path → same as local file → cleanup

    Returns:
        Raw ChatCompletionMessage from VLM.

    Raises:
        FileNotFoundError: local path does not exist.
        ValueError: URL download failed.
    """
    if not _is_url(media_path):
        return _analyze_local(vlm_client, media_path, user_prompt, system_prompt, config_overrides)

    _enforce_url_policy(downloader, media_path)

    if use_base64:
        if media_path.startswith(("http://", "https://")):
            return _analyze_url_via_download(
                vlm_client, downloader, media_path, user_prompt, system_prompt, config_overrides,
            )
        scheme = urlparse(media_path).scheme
        raise ValueError(
            f"base64 mode requires an http/https URL for download, "
            f"but got {scheme}:// — the VLM cannot fetch this URL directly either "
            f"(base64 mode implies VLM has no network access to the media source)"
        )

    return _analyze_url_direct(vlm_client, media_path, user_prompt, system_prompt, config_overrides)


def analyze_multiple_images(
    vlm_client,
    downloader: MediaDownloader,
    media_urls: List[str],
    user_prompt: str,
    system_prompt: Optional[str],
    use_base64: bool = False,
    config_overrides: Optional[Dict[str, Any]] = None,
) -> ChatCompletionMessage:
    """Analyse multiple image URLs in a single VLM request.

    Returns:
        Raw ChatCompletionMessage from VLM.
    """
    if not use_base64:
        for url in media_urls:
            _enforce_url_policy(downloader, url)
        return vlm_client.analyze_multiple_image_urls(
            media_urls, user_prompt, system_prompt,
            config_overrides=config_overrides,
        )

    local_paths: List[str] = []
    failed: List[str] = []
    try:
        for idx, url in enumerate(media_urls):
            local_path = downloader.download(url, worker_id=0)
            if local_path:
                local_paths.append(local_path)
            else:
                failed.append(url)
                logger.error("Failed to download image %d/%d: %s", idx + 1, len(media_urls), url[:100])

        if failed:
            raise ValueError(
                f"Failed to download {len(failed)}/{len(media_urls)} image(s): "
                f"{', '.join(u[:100] for u in failed)}"
            )

        return vlm_client.analyze_multiple_images(
            local_paths, user_prompt, system_prompt,
            config_overrides=config_overrides,
        )
    finally:
        for p in local_paths:
            MediaDownloader.cleanup(p)


# ── internal helpers ──────────────────────────────────────────────────

def _analyze_local(
    vlm_client,
    media_path: str,
    user_prompt: str,
    system_prompt: Optional[str],
    config_overrides: Optional[Dict[str, Any]],
) -> ChatCompletionMessage:
    if not os.path.isfile(media_path):
        raise FileNotFoundError(f"Media file not found: {media_path}")

    media_type = _infer_media_type(media_path)
    if media_type == "image":
        return vlm_client.upload_media_file(
            media_path, user_prompt, system_prompt,
            media_type="image",
            config_overrides=config_overrides,
        )
    return vlm_client.analyze_local_video(
        media_path, user_prompt, system_prompt,
        config_overrides=config_overrides,
    )


def _analyze_url_direct(
    vlm_client,
    media_url: str,
    user_prompt: str,
    system_prompt: Optional[str],
    config_overrides: Optional[Dict[str, Any]],
) -> ChatCompletionMessage:
    media_type = _infer_media_type(media_url)
    if media_type == "image":
        return vlm_client.analyze_image_url(media_url, user_prompt, system_prompt)
    return vlm_client.analyze_video_url(
        media_url, user_prompt, system_prompt,
        config_overrides=config_overrides,
    )


def _analyze_url_via_download(
    vlm_client,
    downloader: MediaDownloader,
    media_url: str,
    user_prompt: str,
    system_prompt: Optional[str],
    config_overrides: Optional[Dict[str, Any]],
) -> ChatCompletionMessage:
    local_path = downloader.download(media_url, worker_id=0)
    if not local_path:
        raise ValueError(f"Failed to download media from URL: {media_url}")
    try:
        return _analyze_local(vlm_client, local_path, user_prompt, system_prompt, config_overrides)
    finally:
        MediaDownloader.cleanup(local_path)

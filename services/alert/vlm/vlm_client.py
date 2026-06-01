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

import asyncio
import threading
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING
import base64
import logging
import mimetypes
import os
from openai import OpenAI, AsyncOpenAI

from models.responses import VLMResponse, ModelType, detect_model_type

if TYPE_CHECKING:
    from handlers.direct_media.media_downloader import MediaDownloader

logger = logging.getLogger(__name__)


class _VLMClientBase:
    """Shared payload and media helpers for sync/async VLM clients."""

    def __init__(self, config: dict) -> None:
        self.config = config
        self.base_url = config.get('base_url', 'http://localhost:8080/v1')
        self.model = config.get('model', 'nvidia/cosmos-reason1-7b')
        self.max_tokens = config.get("max_tokens")
        self.temperature = config.get("temperature")
        self.stream = False
        self.api_key = "not-used"
        self.request_timeout = config.get('request_timeout', 5)
        self.use_vlm_media_defaults = config.get('use_vlm_media_defaults', False)
        logger.info("use_vlm_media_defaults=%s", self.use_vlm_media_defaults)

    @staticmethod
    def _normalize_prompt(user_prompt: Optional[str], default_prompt: str) -> str:
        return user_prompt if user_prompt else default_prompt

    def _get_model_type(self, model_override: Optional[str] = None) -> ModelType:
        """Resolve the model family used to shape the request payload.

        ``model_override`` lets callers honor per-request ``vlm_params.model``
        overrides — without it the message layout and ``extra_body`` shape
        always reflect the static client model and can mismatch the model
        actually sent on the wire.
        """
        return detect_model_type(model_override or self.model)

    def _log_token_usage(self, response, media_type: str = "unknown"):
        if os.getenv('LOG_VLM_USAGE', 'false').lower() not in ('1', 'true', 'yes'):
            return

        try:
            usage = getattr(response, 'usage', None)
            if usage is None:
                logger.debug("VLM response has no usage data")
                return

            prompt_tokens = getattr(usage, 'prompt_tokens', 0) or 0
            completion_tokens = getattr(usage, 'completion_tokens', 0) or 0
            total_tokens = getattr(usage, 'total_tokens', 0) or 0

            logger.debug(
                "VLM token usage [model=%s media_type=%s]: prompt_tokens=%d completion_tokens=%d total_tokens=%d",
                self.model,
                media_type,
                prompt_tokens,
                completion_tokens,
                total_tokens
            )
        except Exception as e:
            logger.debug("Failed to log VLM usage: %s", e)

    def _build_messages_with_media(
        self,
        media_type: str,
        media_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        model_override: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        media_key = f"{media_type}_url"
        model_type = self._get_model_type(model_override)

        if model_type == ModelType.CR1:
            content: List[Dict[str, Any]] = [
                {"type": "text", "text": user_prompt},
                {"type": media_key, media_key: {"url": media_url}},
            ]
        else:
            content: List[Dict[str, Any]] = [
                {"type": media_key, media_key: {"url": media_url}},
                {"type": "text", "text": user_prompt},
            ]

        messages.append({"role": "user", "content": content})
        return messages

    def _build_extra_body(
        self,
        video: bool = True,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        overrides = config_overrides or {}

        def _cfg(key: str, default: Any = None) -> Any:
            return overrides.get(key, self.config.get(key, default))

        model_type = self._get_model_type(overrides.get('model'))
        min_pixels = _cfg('min_pixels', 1568)
        max_pixels = _cfg('max_pixels', 345600)
        enable_sampling = _cfg('enable_sampling', False)
        sampling_fps = _cfg('sampling_fps', 4)
        effective_num_frames = overrides.get('num_frames', num_frames)
        do_resize = _cfg('do_resize', True)

        if _cfg('use_vlm_media_defaults', self.use_vlm_media_defaults):
            return {}

        # Resize block — omitted entirely when do_resize is False so the model
        # uses its server-side default size. Don't pass `do_resize: False` to
        # the processor itself; that path is rejected by the cosmos-reason2
        # NIM (verified empirically), and the same effect is achieved by
        # simply not sending `size` / `videos_kwargs` / `images_kwargs`.
        if do_resize:
            if model_type == ModelType.CR1:
                pixel_kwargs = {"min_pixels": min_pixels, "max_pixels": max_pixels}
                resize_block = {"videos_kwargs" if video else "images_kwargs": pixel_kwargs}
            else:
                resize_block = {"size": {"shortest_edge": min_pixels, "longest_edge": max_pixels}}
        else:
            resize_block = None

        # Sampling block — only meaningful for video.
        if video:
            if enable_sampling:
                io_block = {"video": {"fps": sampling_fps}}
            else:
                io_block = {"video": {"num_frames": effective_num_frames}}
        else:
            io_block = None

        extra_body: Dict[str, Any] = {}
        if resize_block:
            extra_body["mm_processor_kwargs"] = resize_block
        if io_block:
            extra_body["media_io_kwargs"] = io_block
        return extra_body

    def _build_chat_kwargs(
        self,
        extra_body: Dict[str, Any],
        config_overrides: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        overrides = config_overrides or {}
        kwargs: Dict[str, Any] = {}

        effective_max_tokens = overrides.get("max_tokens", self.max_tokens)
        if effective_max_tokens:
            kwargs["max_tokens"] = effective_max_tokens

        if extra_body:
            kwargs["extra_body"] = extra_body

        effective_temperature = overrides.get("temperature", self.temperature)
        if effective_temperature is not None:
            kwargs["temperature"] = effective_temperature

        return kwargs

    def _prepare_local_media(
        self, path: str, media_type: Optional[str] = None
    ) -> Tuple[str, str]:
        if not os.path.isfile(path):
            raise FileNotFoundError(f"Media file not found: {path}")

        mime_type, _ = mimetypes.guess_type(path)

        if media_type is None:
            if mime_type and mime_type.startswith('image/'):
                media_type = 'image'
            elif mime_type and mime_type.startswith('video/'):
                media_type = 'video'
            else:
                raise ValueError(f"Unable to infer media type for '{path}'. Provide media_type explicitly.")
        elif media_type not in ('image', 'video'):
            raise ValueError("media_type must be either 'image' or 'video'")

        if not mime_type:
            mime_type = 'video/mp4' if media_type == 'video' else 'image/png'

        with open(path, 'rb') as file_handle:
            media_b64 = base64.b64encode(file_handle.read()).decode()

        data_url = f"data:{mime_type};base64,{media_b64}"
        return media_type, data_url

    def _build_messages_with_multiple_images(
        self,
        image_urls: List[str],
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        model_type = self._get_model_type()
        content: List[Dict[str, Any]] = []

        if model_type == ModelType.CR1:
            content.append({"type": "text", "text": user_prompt})
            for url in image_urls:
                content.append({"type": "image_url", "image_url": {"url": url}})
        else:
            for url in image_urls:
                content.append({"type": "image_url", "image_url": {"url": url}})
            content.append({"type": "text", "text": user_prompt})

        messages.append({"role": "user", "content": content})
        return messages


class VLMClient(_VLMClientBase):
    """Client wrapper for sending image and video prompts to a VLM endpoint."""

    def __init__(
        self,
        config: dict,
    ) -> None:
        super().__init__(config)
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.request_timeout
        )

    def _create_chat(
        self,
        messages: List[Dict[str, Any]],
        video: bool = True,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        overrides = config_overrides or {}
        extra_body = self._build_extra_body(
            video=video,
            num_frames=num_frames,
            config_overrides=overrides,
        )
        kwargs = self._build_chat_kwargs(extra_body, config_overrides=overrides)
        effective_model = overrides.get("model", self.model)
        return self.client.chat.completions.create(
            model=effective_model,
            messages=messages,
            stream=self.stream,
            **kwargs,
        )

    def analyze_image_url(
        self,
        image_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
    ):
        if not user_prompt:
            raise ValueError("user_prompt is required")
        messages = self._build_messages_with_media("image", image_url, user_prompt, system_prompt)
        response = self._create_chat(messages, video=False)
        self._log_token_usage(response, "image")
        return response.choices[0].message

    def analyze_video_url(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not user_prompt:
            raise ValueError("user_prompt is required")
        model_override = (config_overrides or {}).get('model')
        messages = self._build_messages_with_media(
            "video", video_url, user_prompt, system_prompt,
            model_override=model_override,
        )
        response = self._create_chat(
            messages,
            num_frames=num_frames,
            config_overrides=config_overrides,
        )
        self._log_token_usage(response, "video")
        return response.choices[0].message

    def analyze_media_with_base64(
        self,
        media_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        media_type: str = "video",
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        """
        Download media from URL, base64-encode, and send to VLM.
        
        Use this when VLM endpoint cannot fetch URL directly (network isolation).
        Download config is read from self.config['media_download'].
        
        Args:
            media_url: URL to download media from
            user_prompt: User prompt for analysis (required)
            system_prompt: Optional system prompt
            media_type: "video" or "image" (default: "video")
            
        Returns:
            ChatCompletionMessage with VLM response
            
        Raises:
            ValueError: If user_prompt is empty or None, or if download fails
        """
        if not user_prompt:
            raise ValueError("user_prompt is required")
        
        if media_type not in ("video", "image"):
            raise ValueError("media_type must be 'video' or 'image'")
        
        # Lazy import to avoid circular import
        from handlers.direct_media.media_downloader import MediaDownloader, DownloadConfig
        
        # Read config (with sensible defaults)
        media_config = self.config.get('media_download', {})
        
        local_path = None
        try:
            downloader = MediaDownloader(DownloadConfig(
                download_dir=media_config.get('download_dir', '/tmp/vlm_media_upload'),
                timeout_seconds=media_config.get('timeout_seconds', 60),
                max_size_mb=media_config.get('max_size_mb', 100),
                allow_private_urls=True,
            ))
            
            local_path = downloader.download(media_url, worker_id=0)
            
            if not local_path:
                raise ValueError(f"Failed to download {media_type} from URL: {media_url[:100]}")
            
            logger.info("%s downloaded for base64 upload: %s (%.2f MB)", 
                       media_type.capitalize(), local_path, os.path.getsize(local_path) / (1024 * 1024))
            
            return self.upload_media_file(
                local_path,
                user_prompt,
                system_prompt,
                media_type=media_type,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )
            
        finally:
            if local_path:
                MediaDownloader.cleanup(local_path)
    
    # Alias for backward compatibility
    def analyze_video_with_base64(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        """Alias for analyze_media_with_base64 with media_type='video'."""
        return self.analyze_media_with_base64(
            video_url,
            user_prompt,
            system_prompt,
            media_type="video",
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    def analyze_local_video(
        self,
        path: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not user_prompt:
            raise ValueError("user_prompt is required")
        return self.upload_media_file(
            path,
            user_prompt,
            system_prompt,
            media_type="video",
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    def upload_media_file(
        self,
        path: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        media_type: Optional[str] = None,
        media_io_kwargs: Optional[Dict[str, Any]] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not user_prompt:
            raise ValueError("user_prompt is required")
        media_type, media_url = self._prepare_local_media(path, media_type)
        model_override = (config_overrides or {}).get('model')
        messages = self._build_messages_with_media(
            media_type, media_url, user_prompt, system_prompt,
            model_override=model_override,
        )
        response = self._create_chat(
            messages,
            video=media_type == "video",
            num_frames=num_frames,
            config_overrides=config_overrides,
        )
        self._log_token_usage(response, media_type)
        return response.choices[0].message

    def analyze_multiple_images(
        self,
        image_paths: List[str],
        user_prompt: str = "Analyze these images.",
        system_prompt: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not image_paths:
            raise ValueError("image_paths cannot be empty")
        user_prompt = self._normalize_prompt(user_prompt, "Analyze these images.")

        image_urls = []
        for path in image_paths:
            _, data_url = self._prepare_local_media(path, media_type="image")
            image_urls.append(data_url)

        messages = self._build_messages_with_multiple_images(image_urls, user_prompt, system_prompt)
        response = self._create_chat(
            messages, video=False, config_overrides=config_overrides,
        )
        self._log_token_usage(response, f"images({len(image_paths)})")
        return response.choices[0].message

    def analyze_multiple_image_urls(
        self,
        image_urls: List[str],
        user_prompt: str = "Analyze these images.",
        system_prompt: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not image_urls:
            raise ValueError("image_urls cannot be empty")
        user_prompt = self._normalize_prompt(user_prompt, "Analyze these images.")
        messages = self._build_messages_with_multiple_images(image_urls, user_prompt, system_prompt)
        response = self._create_chat(
            messages, video=False, config_overrides=config_overrides,
        )
        self._log_token_usage(response, f"image_urls({len(image_urls)})")
        return response.choices[0].message


class AsyncVLMClient(_VLMClientBase):
    """Async client wrapper for sending image and video prompts to a VLM endpoint."""

    def __init__(
        self,
        config: dict,
    ) -> None:
        super().__init__(config)
        self.client = AsyncOpenAI(
            base_url=self.base_url,
            api_key=self.api_key,
            timeout=self.request_timeout
        )

    async def _create_chat(
        self,
        messages: List[Dict[str, Any]],
        video: bool = True,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        overrides = config_overrides or {}
        extra_body = self._build_extra_body(
            video=video,
            num_frames=num_frames,
            config_overrides=overrides,
        )
        kwargs = self._build_chat_kwargs(extra_body, config_overrides=overrides)
        effective_model = overrides.get("model", self.model)
        return await self.client.chat.completions.create(
            model=effective_model,
            messages=messages,
            stream=self.stream,
            **kwargs,
        )

    async def analyze_image_url(
        self,
        image_url: str,
        user_prompt: str = "What is in this image?",
        system_prompt: Optional[str] = None,
    ):
        user_prompt = self._normalize_prompt(user_prompt, "What is in this image?")
        messages = self._build_messages_with_media("image", image_url, user_prompt, system_prompt)
        response = await self._create_chat(messages, video=False)
        self._log_token_usage(response, "image")
        return response.choices[0].message

    async def analyze_video_url(
        self,
        video_url: str,
        user_prompt: str = "What is in this video?",
        system_prompt: Optional[str] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        user_prompt = self._normalize_prompt(user_prompt, "What is in this video?")
        model_override = (config_overrides or {}).get('model')
        messages = self._build_messages_with_media(
            "video", video_url, user_prompt, system_prompt,
            model_override=model_override,
        )
        response = await self._create_chat(
            messages,
            num_frames=num_frames,
            config_overrides=config_overrides,
        )
        self._log_token_usage(response, "video")
        return response.choices[0].message

    async def analyze_media_with_base64(
        self,
        media_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        media_type: str = "video",
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        """
        Async variant of analyze_media_with_base64().

        Download happens in a thread worker and VLM upload runs on async client.
        """
        if not user_prompt:
            raise ValueError("user_prompt is required")
        if media_type not in ("video", "image"):
            raise ValueError("media_type must be 'video' or 'image'")

        # Lazy import to avoid circular import
        from handlers.direct_media.media_downloader import MediaDownloader, DownloadConfig

        media_config = self.config.get('media_download', {})
        local_path = None
        try:
            downloader = MediaDownloader(DownloadConfig(
                download_dir=media_config.get('download_dir', '/tmp/vlm_media_upload'),
                timeout_seconds=media_config.get('timeout_seconds', 60),
                max_size_mb=media_config.get('max_size_mb', 100),
                allow_private_urls=True,
            ))

            local_path = await asyncio.to_thread(downloader.download, media_url, 0)
            if not local_path:
                raise ValueError(f"Failed to download {media_type} from URL: {media_url[:100]}")

            file_size_bytes = await asyncio.to_thread(os.path.getsize, local_path)
            logger.info(
                "%s downloaded for async base64 upload: %s (%.2f MB)",
                media_type.capitalize(),
                local_path,
                file_size_bytes / (1024 * 1024),
            )

            return await self.upload_media_file(
                local_path,
                user_prompt,
                system_prompt,
                media_type=media_type,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )
        finally:
            if local_path:
                await asyncio.to_thread(MediaDownloader.cleanup, local_path)

    async def analyze_video_with_base64(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        """Async alias for analyze_media_with_base64 with media_type='video'."""
        return await self.analyze_media_with_base64(
            video_url,
            user_prompt,
            system_prompt,
            media_type="video",
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    async def analyze_local_video(
        self,
        path: str,
        user_prompt: str = "What is in this video?",
        system_prompt: Optional[str] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        return await self.upload_media_file(
            path,
            user_prompt,
            system_prompt,
            media_type="video",
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    async def upload_media_file(
        self,
        path: str,
        user_prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        media_type: Optional[str] = None,
        media_io_kwargs: Optional[Dict[str, Any]] = None,
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        media_type, media_url = await asyncio.to_thread(self._prepare_local_media, path, media_type)
        user_prompt = self._normalize_prompt(
            user_prompt,
            "What is in this image?" if media_type == 'image' else "What is in this video?",
        )
        model_override = (config_overrides or {}).get('model')
        messages = self._build_messages_with_media(
            media_type, media_url, user_prompt, system_prompt,
            model_override=model_override,
        )
        response = await self._create_chat(
            messages,
            video=media_type == "video",
            num_frames=num_frames,
            config_overrides=config_overrides,
        )
        self._log_token_usage(response, media_type)
        return response.choices[0].message

    async def analyze_multiple_images(
        self,
        image_paths: List[str],
        user_prompt: str = "Analyze these images.",
        system_prompt: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not image_paths:
            raise ValueError("image_paths cannot be empty")
        user_prompt = self._normalize_prompt(user_prompt, "Analyze these images.")

        image_urls = []
        for path in image_paths:
            _, data_url = await asyncio.to_thread(self._prepare_local_media, path, "image")
            image_urls.append(data_url)

        messages = self._build_messages_with_multiple_images(image_urls, user_prompt, system_prompt)
        response = await self._create_chat(
            messages, video=False, config_overrides=config_overrides,
        )
        self._log_token_usage(response, f"images({len(image_paths)})")
        return response.choices[0].message

    async def analyze_multiple_image_urls(
        self,
        image_urls: List[str],
        user_prompt: str = "Analyze these images.",
        system_prompt: Optional[str] = None,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if not image_urls:
            raise ValueError("image_urls cannot be empty")
        user_prompt = self._normalize_prompt(user_prompt, "Analyze these images.")
        messages = self._build_messages_with_multiple_images(image_urls, user_prompt, system_prompt)
        response = await self._create_chat(
            messages, video=False, config_overrides=config_overrides,
        )
        self._log_token_usage(response, f"image_urls({len(image_urls)})")
        return response.choices[0].message

    async def aclose(self) -> None:
        """Close the underlying async HTTP client if available."""
        close_fn = getattr(self.client, "close", None)
        if close_fn is None:
            return

        maybe_coro = close_fn()
        if asyncio.iscoroutine(maybe_coro):
            await maybe_coro


class AsyncVLMRuntime:
    """
    Dedicated runtime for async VLM calls.

    This keeps one event loop and one AsyncVLMClient in a single background thread
    so async OpenAI client usage stays on a consistent loop/thread lifecycle.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._started_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._client: Optional[AsyncVLMClient] = None
        self._startup_error: Optional[BaseException] = None
        self._stopping = False

    def _ensure_started(self) -> None:
        with self._lock:
            if self._stopping:
                raise RuntimeError("Async VLM runtime is stopping")
            if self._thread is not None and self._thread.is_alive():
                return

            self._started_event.clear()
            self._startup_error = None
            self._stopping = False
            self._thread = threading.Thread(
                target=self._run_event_loop,
                name="ab-vlm-async-runtime",
                daemon=True,
            )
            self._thread.start()

        if not self._started_event.wait(timeout=10):
            raise RuntimeError("Timed out while starting async VLM runtime")
        with self._lock:
            startup_error = self._startup_error
        if startup_error is not None:
            raise RuntimeError("Failed to start async VLM runtime") from startup_error

    def _run_event_loop(self) -> None:
        loop = asyncio.new_event_loop()
        with self._lock:
            self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            client = AsyncVLMClient(self._config)
            with self._lock:
                self._client = client
            self._started_event.set()
            loop.run_forever()
        except BaseException as exc:
            with self._lock:
                self._startup_error = exc
            self._started_event.set()
        finally:
            with self._lock:
                client = self._client
            if client is not None:
                try:
                    loop.run_until_complete(client.aclose())
                except Exception:
                    logger.exception("Failed closing AsyncVLMClient during runtime shutdown")
            loop.close()
            with self._lock:
                self._loop = None
                self._client = None

    def run_coroutine(self, coroutine):
        """Run a coroutine on the runtime loop and wait for completion."""
        future = self.submit_coroutine(coroutine)
        return future.result()

    def submit_coroutine(self, coroutine):
        """Submit a coroutine to the runtime loop and return Future immediately."""
        try:
            self._ensure_started()
        except Exception:
            if hasattr(coroutine, "close"):
                coroutine.close()
            raise
        with self._lock:
            loop = self._loop
            thread = self._thread
            is_stopping = self._stopping
        if is_stopping or loop is None or thread is None or not thread.is_alive():
            if hasattr(coroutine, "close"):
                coroutine.close()
            raise RuntimeError("Async VLM event loop is not initialized")
        try:
            return asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError:
            if hasattr(coroutine, "close"):
                coroutine.close()
            raise

    def submit_to_thread(self, func, *args, **kwargs):
        """
        Run blocking work in runtime-managed worker thread and return Future.

        This lets caller attach callbacks without blocking the submitting thread.
        """
        async def _thread_runner():
            return await asyncio.to_thread(func, *args, **kwargs)

        return self.submit_coroutine(_thread_runner())

    async def analyze_video_url_async(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str],
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if self._client is None:
            raise RuntimeError("Async VLM client is not initialized")
        return await self._client.analyze_video_url(
            video_url,
            user_prompt,
            system_prompt,
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    async def analyze_video_with_base64_async(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str],
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        if self._client is None:
            raise RuntimeError("Async VLM client is not initialized")
        return await self._client.analyze_video_with_base64(
            video_url,
            user_prompt,
            system_prompt,
            num_frames=num_frames,
            config_overrides=config_overrides,
        )

    def analyze_video_url(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str],
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        return self.run_coroutine(
            self.analyze_video_url_async(
                video_url,
                user_prompt,
                system_prompt,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )
        )

    def analyze_video_with_base64(
        self,
        video_url: str,
        user_prompt: str,
        system_prompt: Optional[str],
        num_frames: Optional[int] = 10,
        config_overrides: Optional[Dict[str, Any]] = None,
    ):
        return self.run_coroutine(
            self.analyze_video_with_base64_async(
                video_url,
                user_prompt,
                system_prompt,
                num_frames=num_frames,
                config_overrides=config_overrides,
            )
        )

    def sleep(self, duration_seconds: float) -> None:
        """Run non-blocking sleep on runtime loop (for async retry path)."""
        self.run_coroutine(asyncio.sleep(duration_seconds))

    def stop(self, timeout: float = 10.0) -> None:
        """Stop the runtime loop and wait for thread shutdown."""
        with self._lock:
            self._stopping = True
            loop = self._loop
            thread = self._thread

        if loop is None or thread is None:
            with self._lock:
                self._stopping = False
            return

        if loop.is_running():
            loop.call_soon_threadsafe(loop.stop)

        if thread.is_alive():
            thread.join(timeout=timeout)
            if thread.is_alive():
                logger.warning(
                    "Async VLM runtime thread did not stop within %.1fs",
                    timeout,
                )

        with self._lock:
            self._thread = None
            self._startup_error = None
            self._started_event.clear()
            self._stopping = False


config = {
    'base_url': 'http://localhost:8080/v1',
    'model': 'nvidia/cosmos-reason1-7b',
    'max_tokens': 256
}
# vlm = VLMClient(config)
# print(vlm.analyze_image_url("https://upload.wikimedia.org/wikipedia/commons/thumb/d/dd/Gfp-wisconsin-madison-the-nature-boardwalk.jpg/2560px-Gfp-wisconsin-madison-the-nature-boardwalk.jpg"))
# print('')
# url = "https://download.samplelib.com/mp4/sample-5s.mp4"
# # #url = 'http://localhost:30011/temp_videos/Warehouse_Camera01_2025-09-10_11_12:50_495__2025-09-10_11_12:52_928_.mp4'
# print(vlm.analyze_video_url(url))

# print('')
# print(vlm.analyze_local_video("/home/user/alert_agent_dev/alert_agent/01_dog.mp4"))

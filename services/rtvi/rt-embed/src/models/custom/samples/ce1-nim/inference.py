# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Cosmos-Embed1 NIM backend for RTVI Embed."""

import base64
import io
import json
import os
import urllib.error
import urllib.request
from typing import List, Optional

import numpy as np
import torch
from PIL import Image

from common.chunk_info import ChunkInfo
from common.logger import logger
from models.base_vlm_model import (
    BaseVlmModel,
    InputConfig,
    VlmGenerationConfig,
    VlmModelOutput,
)

DEFAULT_NIM_MODEL = "nvidia/cosmos-embed1"
DEFAULT_NUM_FRAMES = 8
DEFAULT_RESOLUTION = 224


def _env_int(name: str, default: int) -> int:
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using %d", name, raw_value, default)
        return default
    if value <= 0:
        logger.warning("Invalid %s=%r; using %d", name, raw_value, default)
        return default
    return value


def _env_float(name: str, default: float) -> float:
    raw_value = os.getenv(name, str(default))
    try:
        value = float(raw_value)
    except ValueError:
        logger.warning("Invalid %s=%r; using %.1f", name, raw_value, default)
        return default
    if value <= 0:
        logger.warning("Invalid %s=%r; using %.1f", name, raw_value, default)
        return default
    return value


class CE1NimModel(BaseVlmModel):
    """Call Cosmos-Embed1 NIM v1.1 for text and video-frame embeddings."""

    def _initialize_model(self, **kwargs):
        self._base_url = os.getenv("REMOTE_EMBED_ENDPOINT", "").rstrip("/")
        if not self._base_url:
            raise ValueError("REMOTE_EMBED_ENDPOINT must be set for the CE1 NIM backend")

        self._nim_model = os.getenv("REMOTE_EMBED_ENDPOINT_MODEL_NAME", DEFAULT_NIM_MODEL)
        self._rtvi_model_id = self._nim_model
        self._api_key = os.getenv("REMOTE_EMBED_ENDPOINT_API_KEY", "")
        self._timeout_sec = _env_float("REMOTE_EMBED_ENDPOINT_TIMEOUT_SEC", 300.0)
        self._max_batch_size = _env_int("REMOTE_EMBED_ENDPOINT_BATCH_SIZE", 64)
        self._input_config = self.get_input_config(self.model_path)

        logger.info(
            "Initialized CE1 NIM backend: base_url=%s, nim_model=%s, rtvi_model_id=%s",
            self._base_url,
            self._nim_model,
            self._rtvi_model_id,
        )

    @property
    def model_name(self) -> str:
        return self._rtvi_model_id

    def can_batch(self, item1, item2):
        return True

    def _post_embeddings(self, inputs: list[str], request_type: str) -> list[list[float]]:
        nim_input = inputs[0] if request_type == "query" else inputs
        payload = {
            "input": nim_input,
            "request_type": request_type,
            "encoding_format": "float",
            "model": self._nim_model,
        }
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        request = urllib.request.Request(
            f"{self._base_url}/v1/embeddings",
            data=data,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_sec) as response:
                response_body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            error_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"CE1 NIM embeddings request failed with HTTP {exc.code}: {error_body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"CE1 NIM embeddings request failed: {exc}") from exc

        try:
            response_json = json.loads(response_body)
            data_items = response_json["data"]
            ordered_items = sorted(data_items, key=lambda item: item.get("index", 0))
            embeddings = [item["embedding"] for item in ordered_items]
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Invalid CE1 NIM embeddings response: {response_body}") from exc

        if len(embeddings) != len(inputs):
            raise RuntimeError(
                f"CE1 NIM returned {len(embeddings)} embeddings for {len(inputs)} inputs"
            )
        return embeddings

    def _batched_embeddings(self, inputs: list[str], request_type: str) -> list[list[float]]:
        embeddings = []
        for start in range(0, len(inputs), self._max_batch_size):
            batch = inputs[start : start + self._max_batch_size]
            embeddings.extend(self._post_embeddings(batch, request_type))
        return embeddings

    def _get_text_embeddings(self, text_list: list[str]) -> list[list[float]]:
        if not text_list:
            return []
        request_type = "query" if len(text_list) == 1 else "bulk_text"
        return self._batched_embeddings(text_list, request_type)

    def _frame_to_jpeg_base64(self, frame) -> str:
        if isinstance(frame, (bytes, bytearray, memoryview)):
            return base64.b64encode(bytes(frame)).decode("ascii")
        if isinstance(frame, str):
            return frame.split(",", 1)[-1] if frame.startswith("data:") else frame

        if isinstance(frame, np.ndarray):
            if frame.ndim == 1 and frame.dtype == np.uint8:
                return base64.b64encode(frame.tobytes()).decode("ascii")
            frame_cpu = torch.as_tensor(frame)
        else:
            frame_cpu = frame.detach().cpu()

        if frame_cpu.ndim == 1 and frame_cpu.dtype == torch.uint8:
            return base64.b64encode(frame_cpu.numpy().tobytes()).decode("ascii")

        if frame_cpu.ndim != 3:
            raise ValueError(
                f"Expected frame tensor with 3 dims, got shape {tuple(frame_cpu.shape)}"
            )

        if frame_cpu.shape[0] in (1, 3, 4) and frame_cpu.shape[-1] not in (1, 3, 4):
            frame_cpu = frame_cpu.permute(1, 2, 0)

        frame_array = frame_cpu.numpy()
        if np.issubdtype(frame_array.dtype, np.floating):
            max_value = float(np.nanmax(frame_array)) if frame_array.size else 0.0
            if max_value <= 1.0:
                frame_array = frame_array * 255.0
            frame_array = np.clip(frame_array, 0, 255).astype(np.uint8)
        else:
            frame_array = np.clip(frame_array, 0, 255).astype(np.uint8)

        if frame_array.shape[-1] == 1:
            frame_array = np.repeat(frame_array, 3, axis=-1)
        elif frame_array.shape[-1] == 4:
            frame_array = frame_array[:, :, :3]
        elif frame_array.shape[-1] != 3:
            raise ValueError(f"Expected 1, 3, or 4 channels, got shape {frame_array.shape}")

        image = Image.fromarray(frame_array)
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=95)
        return base64.b64encode(buffer.getvalue()).decode("ascii")

    def _normalize_video_frames(self, frames) -> list:
        if isinstance(frames, np.ndarray):
            frames = torch.as_tensor(frames)

        if not isinstance(frames, torch.Tensor):
            frame_list = list(frames)
            if not frame_list:
                raise ValueError("Cannot generate video embeddings for an empty frame list")

            target_count = self._input_config.num_frames
            if len(frame_list) == target_count:
                return frame_list
            if len(frame_list) > target_count:
                indices = np.linspace(0, len(frame_list) - 1, target_count).round().astype(int)
                return [frame_list[int(idx)] for idx in indices]

            last_frame = frame_list[-1]
            frame_list.extend(last_frame for _ in range(target_count - len(frame_list)))
            return frame_list

        if frames.ndim != 4:
            raise ValueError(
                f"Expected video frame tensor with 4 dims, got shape {tuple(frames.shape)}"
            )
        if frames.shape[0] == 0:
            raise ValueError("Cannot generate video embeddings for an empty frame tensor")

        target_count = self._input_config.num_frames
        if frames.shape[0] == target_count:
            return [frames[idx] for idx in range(target_count)]
        if frames.shape[0] > target_count:
            indices = torch.linspace(0, frames.shape[0] - 1, target_count).round().long()
            return [frames[int(idx)] for idx in indices]

        selected = [frames[idx] for idx in range(frames.shape[0])]
        last_frame = selected[-1]
        selected.extend(last_frame for _ in range(target_count - len(selected)))
        return selected

    def _video_frames_input(self, frames: torch.Tensor) -> str:
        frame_payloads = [
            self._frame_to_jpeg_base64(frame) for frame in self._normalize_video_frames(frames)
        ]
        return f"data:video_frames/jpg;base64,{{{','.join(frame_payloads)}}}"

    def _get_video_embeddings(self, video_list: list[torch.Tensor]) -> list[list[float]]:
        if not video_list:
            return []
        inputs = [self._video_frames_input(frames) for frames in video_list]
        # CE1 NIM v1.1 accepts base64 video_frames reliably through query mode.
        # Runtime testing showed bulk_video routes these frame payloads through
        # the URL/video download path, so RTVI frame chunks are sent one at a time.
        embeddings = []
        for nim_input in inputs:
            embeddings.extend(self._post_embeddings([nim_input], "query"))
        return embeddings

    def generate(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: Optional[List[List[float]]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        **kwargs,
    ) -> List[VlmModelOutput]:
        video_frames = video_frames or []
        text_list = []
        text_positions = []
        video_list = []
        video_positions = []
        video_idx = 0

        for idx, chunk in enumerate(chunks):
            if chunk.chunk_type == "text":
                text_positions.append(idx)
                text_list.append(chunk.text_input)
            else:
                video_positions.append(idx)
                video_list.append(video_frames[video_idx])
                video_idx += 1

        text_embeddings = self._get_text_embeddings(text_list)
        video_embeddings = self._get_video_embeddings(video_list)

        outputs: list[VlmModelOutput | None] = [None] * len(chunks)
        for position, embedding in zip(text_positions, text_embeddings):
            chunk = chunks[position]
            outputs[position] = VlmModelOutput(
                output="",
                input_tokens=len(chunk.text_input),
                output_tokens=len(embedding),
                embeddings=embedding,
            )
        for position, embedding in zip(video_positions, video_embeddings):
            outputs[position] = VlmModelOutput(
                output="",
                input_tokens=self._input_config.num_frames
                * self._input_config.width
                * self._input_config.height
                * 3,
                output_tokens=len(embedding),
                embeddings=embedding,
            )

        return [output for output in outputs if output is not None]

    def can_enqueue_requests(self) -> bool:
        return True

    def _shutdown_model(self):
        return None

    @staticmethod
    def get_model_info(model_path: str = "", vlm_model_type: str = "") -> tuple[str, str, str]:
        return (
            os.getenv("REMOTE_EMBED_ENDPOINT_MODEL_NAME", DEFAULT_NIM_MODEL),
            "custom",
            "nvidia",
        )

    @staticmethod
    def get_input_config(model_path: str = "", vlm_model_type: str = "") -> InputConfig:
        return InputConfig(
            num_frames=DEFAULT_NUM_FRAMES,
            use_jpeg_encoding=True,
            width=DEFAULT_RESOLUTION,
            height=DEFAULT_RESOLUTION,
        )

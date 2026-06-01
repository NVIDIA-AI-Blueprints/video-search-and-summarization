# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import concurrent.futures
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import torch

from common.chunk_info import ChunkInfo


@dataclass
class VlmGenerationConfig:
    """Configuration for VLM generation parameters"""

    temperature: float = 0.4
    max_new_tokens: int = 512
    top_p: float = 0.8
    top_k: int = 20
    repetition_penalty: float = 1.1
    seed: int = 1
    system_prompt: Optional[str] = None
    enable_reasoning: bool = False
    ignore_eos: bool = False
    min_tokens: Optional[int] = None
    mm_processor_kwargs: Optional[dict] = None
    media_io_kwargs: Optional[dict] = None
    preserve_reasoning_tags: bool = False


@dataclass
class InputConfig:
    """Configuration for input processing parameters."""

    num_frames: int = 8
    use_jpeg_encoding: bool = False
    width: int = 0
    height: int = 0


@dataclass
class VlmModelOutput:
    """Output from a VLM model generation for a single chunk."""

    output: str
    embeddings: Optional[List[float]] = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_description: str = ""


class BaseVlmModel(ABC):
    """
    Base class for all VLM (Vision-Language Model) implementations.

    This class defines the common interface that all VLM models must implement,
    making it easy for users to create custom model implementations.
    """

    def __init__(self, model_path: str = "", max_batch_size: Optional[int] = None, **kwargs):
        """
        Initialize the VLM model.

        Args:
            model_path: Path to the model files
            max_batch_size: Maximum batch size for processing
            **kwargs: Additional model-specific parameters
        """
        self.model_path = model_path
        self._max_batch_size = max_batch_size or 1
        self._inflight_req_ids = []
        self._output_tpool = None

        # Initialize model-specific components
        self._initialize_model(**kwargs)

    @abstractmethod
    def _initialize_model(self, **kwargs):
        """
        Initialize the model-specific components.
        This method should be implemented by subclasses to handle model loading,
        tokenizer initialization, etc.

        Args:
            **kwargs: Model-specific initialization parameters
        """
        pass

    @abstractmethod
    def _shutdown_model(self):
        """
        Shutdown the model.
        This method should be implemented by subclasses to handle model shutdown.
        """
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Return the name of the model"""
        pass

    @property
    def model_config(self) -> Optional[Dict[str, Any]]:
        """Return model configuration if available"""
        return None

    @property
    def num_time_tokens(self) -> int:
        """Return the number of time tokens used by the model"""
        return 0

    def get_conv(self):
        """Return conversation template if available"""
        return []

    @abstractmethod
    def generate(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: List[List[float]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        **kwargs,
    ) -> list[VlmModelOutput] | concurrent.futures.Future[list[VlmModelOutput]]:
        """
        Generate a response for the given prompt using video data.

        Args:
            query: Text prompt for the VLM model
            chunks: List of chunk information objects
            video_frames: List of video frame tensors (optional, model-dependent)
            video_frames_times: List of frame timestamps for each chunk
            generation_config: Generation configuration parameters (VlmGenerationConfig instance)
            **kwargs: Additional keyword arguments for extensibility

        Returns:
            Either a list of VlmModelOutput dataclasses (one per chunk) or a Future that
            resolves to List[VlmModelOutput]
        """
        pass

    def generate_text_only(
        self,
        messages: List[Dict[str, Any]],
        generation_config: Optional[VlmGenerationConfig] = None,
    ) -> list[VlmModelOutput] | concurrent.futures.Future[list[VlmModelOutput]]:
        """Generate a text-only response (no multimodal input).

        Args:
            messages: List of message dicts with 'role' and 'content' keys
                      (OpenAI chat format: system/user/assistant).
            generation_config: Generation configuration parameters.

        Returns:
            Either a list of VlmModelOutput or a Future that resolves to one.

        Raises:
            NotImplementedError: If the model backend does not support text-only generation.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support text-only generation")

    async def generate_text_only_stream(
        self,
        messages: List[Dict[str, Any]],
        generation_config: Optional[VlmGenerationConfig] = None,
    ):
        """Async generator that yields text deltas for token-level streaming.

        Args:
            messages: List of message dicts with 'role' and 'content' keys.
            generation_config: Generation configuration parameters.

        Yields:
            str: Text delta for each generated token.

        Raises:
            NotImplementedError: If the model backend does not support text-only streaming.
        """
        raise NotImplementedError(f"{type(self).__name__} does not support text-only streaming")
        yield  # Make this a generator

    @abstractmethod
    def can_enqueue_requests(self) -> bool:
        """
        Check if the model can accept new requests.

        Returns:
            True if the model can accept new requests, False otherwise
        """
        pass

    def warmup(self):
        """
        Warm up the model with dummy data to initialize CUDA kernels and memory.
        Override this method to implement model-specific warmup logic.
        """
        pass

    @staticmethod
    @abstractmethod
    def get_model_info(model_path: str, vlm_model_type: str = "") -> tuple[str, str, str]:
        """
        Return model information.

        Returns:
            Tuple of (model_id, api_type, owned_by)
        """
        pass

    @staticmethod
    @abstractmethod
    def get_input_config(model_path: str, vlm_model_type: str = "") -> InputConfig:
        """
        Get input-specific configuration parameters.

        Args:
            model_path: Path to the model weights/files

        Returns:
            InputConfig dataclass containing input configuration parameters
        """
        return InputConfig(
            num_frames=8,
            use_jpeg_encoding=False,
            width=0,
            height=0,
        )

    def _validate_input_parameters(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]],
        video_frames_times: List[List[float]],
    ):
        """
        Validate input parameters for the generate method.

        Args:
            query: Text prompt
            chunks: List of chunk information
            video_frames: Video frames
            video_frames_times: Frame timestamps

        Raises:
            ValueError: If parameters are invalid
        """
        if not query or not query.strip():
            raise ValueError("query cannot be empty")

        if not chunks:
            raise ValueError("chunks cannot be empty")

        if not video_frames_times:
            raise ValueError("video_frames_times cannot be empty")

        # Validate that we have data for each chunk
        if len(video_frames_times) != len(chunks):
            raise ValueError("Number of video_frames_times must match number of chunks")

        if video_frames is not None and len(video_frames) != len(chunks):
            raise ValueError("Number of video_frames must match number of chunks")

    def __del__(self):
        """Cleanup resources when the model is destroyed"""
        if hasattr(self, "_output_tpool") and self._output_tpool is not None:
            self._output_tpool.shutdown(wait=False)

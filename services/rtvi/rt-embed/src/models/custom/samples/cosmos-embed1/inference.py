# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
# import base64
import json
import os
from typing import List, Optional

import numpy as np
import torch
from transformers import AutoModel, AutoProcessor

from common.chunk_info import ChunkInfo
from common.logger import logger
from models.base_vlm_model import (
    BaseVlmModel,
    InputConfig,
    VlmGenerationConfig,
    VlmModelOutput,
)

DEVICE = "cuda"


class CosmosEmbedModel(BaseVlmModel):
    """Cosmos Embed1 Video Embedding Model implementation."""

    def _get_checkpoint_path(self, model_path: str, default_model_name: str) -> str:
        """Get the checkpoint path for the model."""
        if model_path and os.path.exists(model_path):
            logger.info(f"Using model checkpoint from path: {model_path}")
            return model_path
        else:
            logger.info(f"Using default model checkpoint: {default_model_name}")
            return default_model_name

    def _start_triton_server(self):
        """Start a TRITON server for the model."""
        import tritonserver

        server_options = tritonserver.Options(
            model_repository=self._triton_repo_path, exit_timeout=30
        )
        self._triton_server = tritonserver.Server(server_options)
        self._triton_server.start(wait_until_ready=True)
        logger.info("TRITON server started successfully")
        self._text_model = self._triton_server.model("text_embeddings")
        self._video_model = self._triton_server.model("video_embeddings")

    def _initialize_model(self, **kwargs):
        """Initialize the Cosmos Embed model."""
        logger.info("Initializing Cosmos Embed model")
        self._model_name, _, _ = self.get_model_info(self.model_path)
        logger.debug(f"Model name: {self._model_name}")
        self._input_config = self.get_input_config(self.model_path)
        logger.debug(
            f"Input config - frames: {self._input_config.num_frames}, "
            f"resolution: {self._input_config.width}x{self._input_config.height}"
        )
        self._ckpt = self._get_checkpoint_path(self.model_path, "nvidia/Cosmos-Embed1-448p")
        self._triton_server = None
        self._model = None
        self._triton_repo_path = f"/tmp/triton_model_repo/{self._model_name}"

        self._disable_optimization = os.getenv("DISABLE_OPTIMIZATION", "false").lower() == "true"
        logger.debug(f"Optimization {'disabled' if self._disable_optimization else 'enabled'}")
        if not self._disable_optimization:
            logger.info("Starting TRITON server for optimized inference")
            self._start_triton_server()
        else:
            logger.info("Using PyTorch inference path (non-optimized)")
            self._model = AutoModel.from_pretrained(
                self._ckpt, local_files_only=True, trust_remote_code=True
            ).to("cuda", dtype=torch.bfloat16)
            logger.debug("Model loaded to CUDA with bfloat16 precision")

        self._processor = AutoProcessor.from_pretrained(
            self._ckpt, local_files_only=True, trust_remote_code=True
        )
        logger.debug("Processor loaded successfully")
        logger.info("Cosmos Embed model initialization completed")

    @property
    def model_name(self) -> str:
        """Return the name of the model."""
        return self._model_name

    def can_batch(self, item1, item2):
        return True

    def _get_video_embeddings_torch(self, video_list: List[torch.Tensor]) -> List[torch.Tensor]:
        """Get video embeddings for a list of video frames."""
        logger.debug(f"Getting video embeddings for {len(video_list)} videos using PyTorch")
        video_list = torch.stack(video_list)
        video_list = video_list.permute(0, 1, 4, 2, 3)
        video_inputs = self._processor(videos=video_list).to("cuda", dtype=torch.bfloat16)
        with torch.no_grad():
            video_embeddings_output = self._model.get_video_embeddings(**video_inputs)
            video_embeddings = video_embeddings_output.visual_proj
            logger.debug(f"Generated video embeddings with shape: {video_embeddings.shape}")
            return video_embeddings.tolist()

    def _get_video_embeddings_triton(self, video_list: List[torch.Tensor]) -> List[torch.Tensor]:
        """Get video embeddings for a list of video frames."""

        video_list = torch.stack(video_list)
        video_list = video_list.permute(0, 1, 4, 2, 3).contiguous()
        feed_dict = {"videos": video_list}
        video_responses = self._video_model.infer(inputs=feed_dict)

        video_embeddings = []
        for video_response in video_responses:
            tensor = video_response.outputs["video_embeddings"]
            torch_array = torch.from_dlpack(tensor)
            video_embeddings.extend(torch_array.cpu().tolist())

        return video_embeddings

    def _get_video_embeddings(self, video_list: List[torch.Tensor]) -> List[torch.Tensor]:
        """Get video embeddings for a list of video frames."""
        if not self._disable_optimization:
            return self._get_video_embeddings_triton(video_list)
        else:
            return self._get_video_embeddings_torch(video_list)

    def _get_text_embeddings_torch(self, text_list: List[str]) -> List[torch.Tensor]:
        """Get text embeddings for a list of text inputs."""
        logger.debug(f"Getting text embeddings for {len(text_list)} texts using PyTorch")
        text_inputs = self._processor(text=text_list).to("cuda", dtype=torch.bfloat16)
        with torch.no_grad():
            text_embeddings_output = self._model.get_text_embeddings(**text_inputs)
            text_embeddings = text_embeddings_output.text_proj
            logger.debug(f"Generated text embeddings with shape: {text_embeddings.shape},")
            return text_embeddings.tolist()

    def _get_text_embeddings_triton(self, text_list: List[str]) -> List[torch.Tensor]:
        """Get text embeddings for a list of text inputs."""
        text_inputs = self._processor(text=text_list, return_tensors="pt")

        feed_dict = {
            "input_ids": text_inputs.input_ids.cpu().numpy().astype(np.int64),
            "attention_mask": text_inputs.attention_mask.cpu().numpy().astype(np.int64),
        }
        text_responses = self._text_model.infer(inputs=feed_dict)

        text_embeddings = []
        for text_response in text_responses:
            tensor = text_response.outputs["text_embeddings"]
            torch_array = torch.from_dlpack(tensor)
            text_embeddings.extend(torch_array.cpu().tolist())

        return text_embeddings

    def _get_text_embeddings(self, text_list: List[str]) -> List[float]:
        """Get text embeddings for a list of text inputs."""
        if not self._disable_optimization:
            return self._get_text_embeddings_triton(text_list)
        else:
            return self._get_text_embeddings_torch(text_list)

    def generate(
        self,
        query: str,
        chunks: List[ChunkInfo],
        video_frames: Optional[List[torch.Tensor]] = None,
        video_frames_times: Optional[List[List[float]]] = None,
        generation_config: Optional[VlmGenerationConfig] = None,
        **kwargs,
    ) -> List[VlmModelOutput]:
        """
        Generate responses for video chunks using Cosmos Embed.

        Args:
            query: The text query/prompt or ChatConversation object
            chunks: List of ChunkInfo objects containing video metadata
            video_frames: List of video frames (tensors)
            video_frames_times: Optional list of frame timestamps
            generation_config: Configuration for generation parameters

        Returns:
            List of VlmResponse objects, one per chunk
        """

        logger.debug(f"Generating embeddings for {len(chunks)} chunks")
        logger.debug(
            f"Query: {query[:100] if isinstance(query, str) else 'ChatConversation object'}"
        )

        vlm_output_list = []

        video_list = []
        text_list = []
        for idx, chunk in enumerate(chunks):
            if chunk.chunk_type == "text":
                text_list.append(chunk.text_input)
            else:
                images = video_frames[idx]
                # Expand the images to the number of frames (last chunk in the video)
                if images.shape[0] != self._input_config.num_frames:
                    last_image = images[-1]
                    for _ in range(self._input_config.num_frames - images.shape[0]):
                        images = torch.cat([images, last_image.unsqueeze(0)], dim=0)
                video_list.append(images)

        logger.debug(f"Processing {len(video_list)} video chunks and {len(text_list)} text chunks")

        if len(video_list) > 0:
            video_embeddings = self._get_video_embeddings(video_list)
            logger.debug(f"Generated embeddings for {len(video_list)} video chunks")

        if len(text_list) > 0:
            text_embeddings = self._get_text_embeddings(text_list)
            logger.debug(f"Generated embeddings for {len(text_list)} text chunks")

        text_chunk_idx = 0
        video_chunk_idx = 0
        for idx, chunk in enumerate(chunks):
            if chunk.chunk_type == "text":
                text_embedding = text_embeddings[text_chunk_idx]
                text_chunk_idx += 1
                vlm_output_list.append(
                    VlmModelOutput(
                        output="",
                        input_tokens=len(chunk.text_input),
                        output_tokens=len(text_embedding),
                        embeddings=text_embedding,
                    )
                )
            else:
                video_embedding = video_embeddings[video_chunk_idx]
                video_chunk_idx += 1
                vlm_output_list.append(
                    VlmModelOutput(
                        output="",
                        input_tokens=(
                            self._input_config.num_frames
                            * self._input_config.width
                            * self._input_config.height
                            * 3
                        ),
                        output_tokens=len(video_embedding),
                        embeddings=video_embedding,
                    )
                )

        logger.info(f"Successfully generated {len(vlm_output_list)} embeddings")
        return vlm_output_list

    def can_enqueue_requests(self) -> bool:
        """Check if the model can handle multiple concurrent requests."""
        return True  # Local model can handle concurrent requests

    def _shutdown_model(self):
        """
        Shutdown the model. This is a no-op for this model.
        """
        if self._triton_server is not None:
            logger.info("Shutting down TRITON server")
            self._triton_server.stop()
            logger.info("TRITON server shutdown completed")
            del self._triton_server
            self._triton_server = None

        # Clean up processor
        if self._processor is not None:
            logger.debug("Cleaning up processor")
            del self._processor
            self._processor = None

        if self._model is not None:
            logger.debug("Cleaning up PyTorch model")
            try:
                # Move model to CPU to free GPU memory
                if hasattr(self._model, "cpu"):
                    self._model.cpu()
                    logger.debug("Moved PyTorch model to CPU")
            except Exception as e:
                logger.warning(f"Error moving model to CPU: {e}")

            # Delete model reference
            del self._model
            self._model = None
            logger.debug("PyTorch model reference deleted")

    @staticmethod
    def get_model_info(model_path: str, vlm_model_type: str = "") -> tuple[str, str, str]:
        """Get model identification information."""

        if model_path and os.path.exists(model_path):
            model_dir_name = os.path.basename(os.path.normpath(model_path))
            model_name = model_dir_name.lower()
            return model_name, "custom", "nvidia"
        else:
            return "cosmos-embed1-448p", "custom", "nvidia"

    @staticmethod
    def get_input_config(model_path: str, vlm_model_type: str = "") -> InputConfig:
        """Get input-specific configuration parameters for Cosmos Embed."""

        config_data = None
        if model_path and os.path.exists(model_path):
            config_path = os.path.join(model_path, "config.json")
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    config_data = json.load(f)
                logger.debug(f"Loaded config from {config_path}")

        if config_data:
            config = InputConfig(
                num_frames=config_data.get("num_video_frames", 8),
                use_jpeg_encoding=False,  # Uses raw tensor data
                width=config_data.get("resolution", 448),
                height=config_data.get("resolution", 448),
            )
            logger.debug(
                f"Using config from file: frames={config.num_frames}, "
                f"resolution={config.width}x{config.height}"
            )
            return config
        else:
            logger.debug("Using default config: frames=8, resolution=448x448")
            return InputConfig(
                num_frames=8,
                use_jpeg_encoding=False,  # Uses raw tensor data
                width=448,
                height=448,
            )

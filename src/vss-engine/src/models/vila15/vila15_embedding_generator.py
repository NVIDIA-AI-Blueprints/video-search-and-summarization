######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import concurrent.futures
import os
import sys

import tensorrt as trt
import torch
from transformers import AutoConfig, AutoModel

from via_logger import TimeMeasure, logger

sys.path.append(os.path.dirname(__file__) + "/VILA")

import llava.model.language_model.llava_llama  # noqa: F401, E402


def trt_dtype_to_torch(dtype):
    """Translate TRT datatype to torch data type"""
    if dtype == trt.float16:
        return torch.float16
    elif dtype == trt.float32:
        return torch.float32
    elif dtype == trt.int32:
        return torch.int32
    else:
        raise TypeError("%s is not supported" % dtype)


class Vila15EmbeddingGenerator:
    """Visual Embedding Generator for the VILA 1.5 model"""

    def __init__(
        self, model_path: str, use_trt=False, trt_engine_dir="", async_output=False
    ) -> None:
        """Vila15EmbeddingGenerator initializer

        Args:
            model_path: Path where the model is located
            trt_engine_dir: Path to the directory where the TRT engines for the model are located.
                            Defaults to "".
        """
        from tensorrt_llm.runtime import Session

        self._use_trt = use_trt
        self._config = AutoConfig.from_pretrained(model_path)

        with TimeMeasure("VILA Embeddings TRT Model load"):
            # Load TRT model from serialized engine
            vision_encoder_path = os.path.join(
                trt_engine_dir, "visual_engines", "visual_encoder.engine"
            )
            logger.info(f"Loading engine from {vision_encoder_path}")
            with open(vision_encoder_path, "rb") as f:
                engine_buffer = f.read()
            logger.info(f"Creating session from engine {vision_encoder_path}")
            self.visual_encoder_session = Session.from_serialized_engine(engine_buffer)

            # Load layers that are required for additional processing after
            # passing the frames through TRT engine
            device_map = {
                "model.vision_tower": "meta",
                "model.embed_tokens": "cuda",
                "model.layers": "meta",
                "model.norm": "meta",
                "lm_head": "meta",
                "model.mm_projector": "meta",
            }
            self._model = AutoModel.from_pretrained(
                model_path,
                low_cpu_mem_usage=True,
                device_map=device_map,
                # torch_dtype=torch.float16,
            )
            self.stream = torch.cuda.Stream(torch.cuda.current_device())
            torch.cuda.set_stream(self.stream)
        self._output_tpool = (
            concurrent.futures.ThreadPoolExecutor(max_workers=2) if async_output else None
        )

    def warmup(self):
        input_dims = self.visual_encoder_session._engine.get_tensor_profile_shape("input", 0)[-1]
        input_dims = [int(d) for d in input_dims[:4]]
        frame_input = torch.zeros(size=input_dims, dtype=torch.float16, device="cuda")
        self.get_embeddings(frame_input.unsqueeze(0))

    def get_embeddings(self, frames_tensor_batch: list):
        """Get embeddings for a batch of chunks. For each chunk a list of frames is needed.

        Args:
            frames_list_batch (list): List of list of frames per chunk

        Returns:
            List of embeddings tensor for all input chunks
        """
        with TimeMeasure("VILA Embeddings generation"):
            visual_outputs_batch = []
            for frames_tensor in frames_tensor_batch:
                # TRT mode
                from tensorrt_llm.runtime import TensorInfo

                visual_output_info = self.visual_encoder_session.infer_shapes(
                    [TensorInfo("input", trt.DataType.HALF, frames_tensor.shape)]
                )
                visual_outputs = {
                    t.name: torch.empty(
                        tuple(t.shape[:3]),
                        dtype=trt_dtype_to_torch(t.dtype),
                        device=frames_tensor.device,
                    )
                    for t in visual_output_info
                }
                ok = self.visual_encoder_session.run(
                    {"input": frames_tensor}, visual_outputs, self.stream.cuda_stream
                )
                assert ok, "Runtime execution failed for vision encoder session"
                visual_outputs_batch.append(visual_outputs["output"])
            self.stream.synchronize()

            return visual_outputs_batch

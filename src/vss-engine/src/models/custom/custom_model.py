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
import importlib
import os
from dataclasses import asdict
from typing import Dict, List

import torch
import yaml

from base_class import CustomModelBase, EmbeddingGeneratorBase, VlmGenerationConfig
from chunk_info import ChunkInfo
from via_logger import TimeMeasure, logger

MODULE_NAME = "inference"


class CustomEmbeddingGenerator(EmbeddingGeneratorBase):
    def __init__(self, model):
        self._model = model

    def get_embeddings(self, l_frames: List[List[torch.tensor]]) -> list[torch.Tensor]:
        embeds = []
        with TimeMeasure("Custom Embeddings generation"):
            for frames in l_frames:
                image_tensor_list = [f.squeeze(0) for f in frames]
                image_tensor = torch.stack(image_tensor_list)
                embeds.append(self._model.get_embeddings(image_tensor))
        return embeds


class CustomModel(CustomModelBase):

    def __init__(self, module, manifest: Dict):
        self._manifest = manifest
        infer_cls = getattr(module, "Inference") if hasattr(module, "Inference") else None
        self._inference = None if infer_cls is None else infer_cls()
        self._gen_embeddings = hasattr(self._inference, "get_embeddings")

    def get_embedding_generator(self) -> EmbeddingGeneratorBase:
        return CustomEmbeddingGenerator(self._inference) if self._gen_embeddings else None

    def generate(
        self,
        prompt: str,
        input_tensors: List[torch.Tensor],
        video_frames_times: List[List],
        generation_config: VlmGenerationConfig,
    ) -> List:
        summary = []
        if hasattr(self._inference, "generate"):
            with TimeMeasure("generate"):
                if isinstance(generation_config, dict):
                    configs = generation_config
                elif generation_config:
                    configs = asdict(generation_config)
                else:
                    configs = None
                for tensor in input_tensors:
                    result = self._inference.generate(prompt, tensor, configs)
                    summary.append(result)
        return summary


class CustomModelContext:

    def __init__(self, model: CustomModel):
        self._model = model

    def set_video_embeds(
        self,
        chunks: List[ChunkInfo],
        video_embeds: List[torch.Tensor],
        video_frames: List[torch.Tensor],
        video_frames_times: List[List],
    ):
        self._chunks = chunks
        self._video_embeds = video_embeds
        self._video_frames = video_frames
        self._video_frames_times = video_frames_times

    def ask(self, prompt: str, **kwargs) -> List:
        generation_config = kwargs["generation_config"] if "generation_config" in kwargs else None
        if self._video_frames:
            return self._model.generate(
                prompt, self._video_frames, self._video_frames_times, generation_config
            ), [
                {"input_tokens": 0, "output_tokens": 0},
            ]
        else:
            return self._model.generate(
                prompt, self._video_embeds, self._video_frames_times, generation_config
            ), [
                {"input_tokens": 0, "output_tokens": 0},
            ]


class CustomModuleLoader:

    def __init__(self, module_path: str):
        if not os.path.isabs(module_path):
            module_path = os.path.abspath(module_path)
        module_file_path = os.path.join(module_path, f"{MODULE_NAME}.py")
        spec = importlib.util.spec_from_file_location(MODULE_NAME, module_file_path)
        self._module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._module)
        self._manifest = {}
        try:
            with open(os.path.join(module_path, "manifest.yaml"), "r") as f:
                self._manifest = yaml.safe_load(f)
        except Exception as e:
            logger.warning(f"Unable to load manifest： {e}")
        self._module_path = module_path

    def load_model(self) -> CustomModelBase:
        return CustomModel(self._module, self._manifest)

    def manifest(self) -> Dict:
        return self._manifest

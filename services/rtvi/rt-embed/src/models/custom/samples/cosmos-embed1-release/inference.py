# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Use release ONNX towers without changing the historical video pipeline."""

import importlib.util
import os
from pathlib import Path

import torch


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_here = Path(__file__).resolve().parent
_historical = _load(
    "cosmos_historical", _here.parent / "cosmos-embed1" / "inference.py"
)
_contract = _load("cosmos_release_contract", _here / "input_contract.py")


class CosmosEmbedModel(_historical.CosmosEmbedModel):
    """The class name intentionally preserves Cosmos frame-getter selection."""

    def _initialize_model(self, **kwargs):
        if os.getenv("DISABLE_OPTIMIZATION", "false").lower() == "true":
            raise ValueError(
                "Release adapter requires validated TensorRT engines; no PyTorch fallback"
            )
        self.get_input_config(self.model_path)
        name = Path(self.model_path).name.lower()
        seal = _contract.verify_build(Path("/tmp/triton_model_repo") / name)
        release = Path(self.model_path).parent
        actual = _contract.verify_release(
            release, [release / p for p in seal["release_files"]]
        )
        if actual != seal["release_files"]:
            raise ValueError("Model artifacts changed since engine compilation")
        super()._initialize_model(**kwargs)

    @staticmethod
    def get_input_config(model_path, vlm_model_type=""):
        import json

        config = json.loads((Path(model_path) / "config.json").read_text())
        if (
            config.get("num_video_frames"),
            config.get("resolution"),
            config.get("embed_dim"),
        ) != (8, 448, 768):
            raise ValueError(
                "Only the verified Cosmos v1.2 8-frame/448/768 contract is supported"
            )
        inputs = _historical.CosmosEmbedModel.get_input_config(
            model_path, vlm_model_type
        )
        # Match the supplied HF processor and rounded linspace frame indices.
        # Other models and live streams retain their existing preprocessing.
        inputs.file_index_sampling = True
        inputs.file_resize_antialias = True
        return inputs

    def _get_video_embeddings_triton(self, video_list):
        responses = self._video_model.infer(
            inputs={"videos": _contract.prepare_video_batch(video_list)}
        )
        rows = []
        for response in responses:
            rows.extend(
                torch.from_dlpack(response.outputs["video_embedding"]).cpu().tolist()
            )
        return _contract.checked_embeddings(rows, len(video_list))

    def _get_text_embeddings_triton(self, text_list):
        responses = self._text_model.infer(
            inputs=_contract.prepare_text_inputs(self._processor, text_list)
        )
        rows = []
        for response in responses:
            rows.extend(
                torch.from_dlpack(response.outputs["text_embedding"]).cpu().tolist()
            )
        return _contract.checked_embeddings(rows, len(text_list))

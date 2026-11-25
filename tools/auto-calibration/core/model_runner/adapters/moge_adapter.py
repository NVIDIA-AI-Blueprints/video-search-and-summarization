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

import sys
import logging
import time
from pathlib import Path

import torch
import numpy as np
import cv2

from .base import BaseModelAdapter
from ..schemas import AdapterInput, AdapterOutput

logger = logging.getLogger(__name__)


class MoGeAdapter(BaseModelAdapter):
    """MoGe-2 adapter: extracts focal length from predicted intrinsics.

    MoGe outputs a normalized 3x3 intrinsic matrix where the principal
    point is at approximately (0.5, 0.5) in normalized coordinates.
    Focal length in pixels = fx_normalized * image_width.
    """

    def __init__(self, model_id: str, manifest_entry: dict):
        super().__init__(model_id, manifest_entry)
        self._model = None
        self._device = None

    @property
    def _project_root(self) -> Path:
        return Path(self.manifest.get("_project_root", "."))

    def validate_environment(self) -> bool:
        submodule_path = self._project_root / self.manifest["repo"]["clone_to"]
        if not submodule_path.exists():
            logger.warning(f"MoGe repo not found at {submodule_path}")
            return False
        return True

    @property
    def _weights_dir(self) -> Path:
        return self._project_root / self.manifest["weights"]["local_dir"]

    def load_model(self, device: str = "cuda") -> None:
        self._ensure_moge_on_path()
        from moge.model.v2 import MoGeModel

        pretrained = self.manifest.get("pretrained_name", "Ruicheng/moge-2-vitl")

        if (self._weights_dir / "model.pt").exists():
            load_from = str(self._weights_dir / "model.pt")
            logger.info(f"[{self.model_id}] Loading MoGe from local: {load_from}")
        else:
            load_from = pretrained
            logger.info(f"[{self.model_id}] Loading MoGe from HF hub: {load_from}")

        self._model = MoGeModel.from_pretrained(load_from).to(device)
        self._device = device
        self._loaded = True
        logger.info(f"[{self.model_id}] MoGe model loaded on {device}")

    def infer(self, inp: AdapterInput) -> AdapterOutput:
        t0 = time.perf_counter()

        img_bgr = cv2.imread(str(inp.image_path))
        if img_bgr is None:
            return AdapterOutput(
                status="error", error=f"Cannot read: {inp.image_path}"
            )
        img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
        img_h, img_w = img_rgb.shape[:2]

        input_tensor = (
            torch.tensor(img_rgb / 255.0, dtype=torch.float32, device=self._device)
            .permute(2, 0, 1)
        )

        with torch.no_grad():
            output = self._model.infer(input_tensor)

        intrinsics = output["intrinsics"]  # (3, 3) normalized
        fx_norm = intrinsics[0, 0].item()
        fy_norm = intrinsics[1, 1].item()
        focal_length_px = fx_norm * img_w

        runtime_ms = (time.perf_counter() - t0) * 1000

        extra = {
            "intrinsics_normalized": intrinsics.cpu().numpy().tolist(),
            "fx_norm": fx_norm,
            "fy_norm": fy_norm,
            "fx_px": focal_length_px,
            "fy_px": fy_norm * img_h,
            "image_size": [img_w, img_h],
        }
        if "depth" in output:
            extra["depth_shape"] = list(output["depth"].shape)
        if "points" in output:
            extra["points_shape"] = list(output["points"].shape)

        return AdapterOutput(
            focal_length_px=focal_length_px,
            extra=extra,
            runtime_ms=runtime_ms,
        )

    def _ensure_moge_on_path(self):
        submodule_path = str(self._project_root / self.manifest["repo"]["clone_to"])
        if submodule_path not in sys.path:
            sys.path.insert(0, submodule_path)

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


class VGGTAdapter(BaseModelAdapter):
    """VGGT adapter: extracts focal length from predicted camera intrinsics.

    VGGT is a multi-view model but works for single images.  It outputs
    extrinsic and intrinsic matrices.  The intrinsic matrix is at the VGGT
    inference resolution; we scale it back to the original image size using
    the principal-point ratio (same approach as core/VGGT/run_vggt_calib.py).
    """

    def __init__(self, model_id: str, manifest_entry: dict):
        super().__init__(model_id, manifest_entry)
        self._model = None
        self._device = None
        self._dtype = None

    @property
    def _project_root(self) -> Path:
        return Path(self.manifest.get("_project_root", "."))

    def validate_environment(self) -> bool:
        submodule_path = self._project_root / self.manifest["repo"]["clone_to"]
        if not submodule_path.exists():
            logger.warning(f"VGGT repo not found at {submodule_path}")
            return False
        weights_dir = self._project_root / self.manifest["weights"]["local_dir"]
        for wf in self.manifest["weights"]["files"]:
            if not (weights_dir / wf["name"]).exists():
                logger.warning(f"Weight file missing: {weights_dir / wf['name']}")
                return False
        return True

    def load_model(self, device: str = "cuda") -> None:
        self._ensure_vggt_on_path()
        from vggt.models.vggt import VGGT

        weights_dir = self._project_root / self.manifest["weights"]["local_dir"]
        weight_file = self.manifest["weights"]["files"][0]["name"]
        weight_path = str(weights_dir / weight_file)

        logger.info(f"[{self.model_id}] Loading VGGT from {weight_path}")
        self._model = VGGT()
        state_dict = torch.load(weight_path, map_location="cpu", weights_only=True)
        self._model.load_state_dict(state_dict)
        self._model = self._model.to(device)
        self._device = device

        try:
            cap = torch.cuda.get_device_capability()
            self._dtype = torch.bfloat16 if cap[0] >= 8 else torch.float16
        except (RuntimeError, AttributeError):
            self._dtype = torch.float16

        self._loaded = True
        logger.info(f"[{self.model_id}] VGGT model loaded on {device}")

    def infer(self, inp: AdapterInput) -> AdapterOutput:
        from vggt.utils.load_fn import load_and_preprocess_images
        from vggt.utils.pose_enc import pose_encoding_to_extri_intri

        t0 = time.perf_counter()

        img_bgr = cv2.imread(str(inp.image_path))
        if img_bgr is None:
            return AdapterOutput(status="error", error=f"Failed to read image: {inp.image_path}")
        img_h, img_w = img_bgr.shape[:2]

        images = load_and_preprocess_images([str(inp.image_path)]).to(self._device)

        with torch.no_grad():
            with torch.cuda.amp.autocast(dtype=self._dtype):
                predictions = self._model(images)

        extrinsic, intrinsic = pose_encoding_to_extri_intri(
            predictions["pose_enc"], images.shape[-2:]
        )

        K = intrinsic.squeeze(0).cpu().numpy()  # (N, 3, 3) -> (1, 3, 3) -> (3, 3)
        if K.ndim == 3:
            K = K[0]

        K_scaled = self._scale_intrinsic(K, img_w, img_h)
        focal_length_px = float(K_scaled[0, 0])

        runtime_ms = (time.perf_counter() - t0) * 1000

        extra = {
            "intrinsic_raw": K.tolist(),
            "intrinsic_scaled": K_scaled.tolist(),
            "fx_px": float(K_scaled[0, 0]),
            "fy_px": float(K_scaled[1, 1]),
            "cx_px": float(K_scaled[0, 2]),
            "cy_px": float(K_scaled[1, 2]),
            "image_size": [img_w, img_h],
        }

        E = extrinsic.squeeze(0).cpu().numpy()
        if E.ndim == 3:
            E = E[0]
        extra["extrinsic"] = E.tolist()

        return AdapterOutput(
            focal_length_px=focal_length_px,
            extra=extra,
            runtime_ms=runtime_ms,
        )

    @staticmethod
    def _scale_intrinsic(K: np.ndarray, img_w: int, img_h: int) -> np.ndarray:
        """Scale VGGT intrinsic matrix from inference resolution to original.

        VGGT's principal point (cx, cy) reflects the inference resolution.
        We compute a scale factor from principal point to original image center
        and apply it uniformly (same approach as core/VGGT/run_vggt_calib.py).
        """
        scale_factor = (img_w / 2.0 / K[0, 2] + img_h / 2.0 / K[1, 2]) / 2.0
        K_scaled = K * scale_factor
        K_scaled[2, 2] = 1.0
        return K_scaled

    def _ensure_vggt_on_path(self):
        submodule_path = str(self._project_root / self.manifest["repo"]["clone_to"])
        if submodule_path not in sys.path:
            sys.path.insert(0, submodule_path)

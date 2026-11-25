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

import os
import sys
import logging
import time
from pathlib import Path

import torch
import numpy as np

from .base import BaseModelAdapter
from ..schemas import AdapterInput, AdapterOutput

logger = logging.getLogger(__name__)


class GeoCalibAdapter(BaseModelAdapter):
    """Wraps GeoCalib behind the adapter interface.

    When adaptive_geocalib is enabled in the config, a single infer() call
    runs multiple calibration iterations internally and returns a stabilized
    (representative) result.  This is the recommended mode for AMC production
    because GeoCalib shows variance across runs for the same input.
    """

    def __init__(self, model_id: str, manifest_entry: dict):
        super().__init__(model_id, manifest_entry)
        self._model = None
        self._device = None
        self._loaded_weights_name: str | None = None
        self._GeoCalib_cls = None

    @property
    def _project_root(self) -> Path:
        return Path(self.manifest.get("_project_root", "."))

    @property
    def _weights_dir(self) -> Path:
        return self._project_root / self.manifest["weights"]["local_dir"]

    # --- BaseModelAdapter implementation ---

    def validate_environment(self) -> bool:
        submodule_path = self._project_root / self.manifest["repo"]["clone_to"]
        geocalib_pkg = submodule_path / self.manifest["repo"].get("subdirectory", "")
        if not geocalib_pkg.exists():
            logger.warning(f"GeoCalib package not found at {geocalib_pkg}")
            return False
        for wf in self.manifest["weights"]["files"]:
            if not (self._weights_dir / wf["name"]).exists():
                logger.warning(f"Weight file missing: {self._weights_dir / wf['name']}")
                return False
        return True

    def load_model(self, device: str = "cuda") -> None:
        self._ensure_geocalib_on_path()
        from geocalib import GeoCalib
        self._device = device
        self._GeoCalib_cls = GeoCalib
        self._loaded_weights_name = None
        self._loaded = True
        logger.info(f"[{self.model_id}] GeoCalib class ready on {device}")

    def infer(self, inp: AdapterInput) -> AdapterOutput:
        """Run GeoCalib inference.

        If adaptive_geocalib.enabled is true in the config, runs multiple
        iterations with CV-based convergence and returns the representative
        (closest-to-median) result for stability.  Otherwise, runs once.
        """
        adaptive_cfg = inp.config.get("adaptive_geocalib", {})
        if adaptive_cfg.get("enabled", False):
            return self._infer_adaptive(inp, adaptive_cfg)
        return self._infer_single(inp)

    def unload_model(self) -> None:
        self._loaded_weights_name = None
        super().unload_model()

    # --- Single-run inference ---

    def _infer_single(self, inp: AdapterInput) -> AdapterOutput:
        from geocalib.utils import rad2deg
        t0 = time.perf_counter()

        weights_name = self._weights_name_for(inp.camera_model)
        self._ensure_weights_loaded(weights_name)

        image = self._model.load_image(str(inp.image_path)).to(self._device)
        result = self._model.calibrate(image, camera_model=inp.camera_model)

        camera, gravity = result["camera"], result["gravity"]
        roll, pitch = rad2deg(gravity.rp).unbind(-1)

        return AdapterOutput(
            focal_length_px=camera.f[0, 1].item(),
            roll_deg=roll.item(),
            pitch_deg=pitch.item(),
            vfov_deg=rad2deg(camera.vfov).item(),
            uncertainty={
                "roll_deg": rad2deg(result["roll_uncertainty"]).item(),
                "pitch_deg": rad2deg(result["pitch_uncertainty"]).item(),
                "vfov_deg": rad2deg(result["vfov_uncertainty"]).item(),
                "focal_px": result["focal_uncertainty"].item(),
            },
            distortion=self._extract_distortion(camera),
            extra={"raw_result": result},
            runtime_ms=(time.perf_counter() - t0) * 1000,
        )

    # --- Adaptive multi-run inference (stabilized output) ---

    def _infer_adaptive(self, inp: AdapterInput, adaptive_cfg: dict) -> AdapterOutput:
        """Run GeoCalib multiple times until focal CV converges, return
        the result closest to the representative (median) focal length."""
        from geocalib.utils import rad2deg

        min_iters = adaptive_cfg.get("min_iterations", 20)
        max_iters = adaptive_cfg.get("max_iterations", 50)
        increment = adaptive_cfg.get("iteration_increment", 5)
        cv_threshold = adaptive_cfg.get("cv_threshold", 0.8)
        early_stop_cv = adaptive_cfg.get("early_stop_cv", 0.5)

        t0 = time.perf_counter()

        weights_name = self._weights_name_for(inp.camera_model)
        self._ensure_weights_loaded(weights_name)

        image = self._model.load_image(str(inp.image_path)).to(self._device)

        all_results = []
        focal_lengths: list[float] = []

        logger.info(f"[{self.model_id}] Adaptive iteration: min={min_iters}, max={max_iters}")

        for i in range(min_iters):
            r = self._model.calibrate(image, camera_model=inp.camera_model)
            f = r["camera"].f[0, 1].item()
            focal_lengths.append(f)
            all_results.append(r)

        while len(focal_lengths) < max_iters:
            cv = self._cv(focal_lengths)
            logger.info(f"[{self.model_id}] iter={len(focal_lengths)}, CV={cv:.2f}%")

            if cv < early_stop_cv:
                logger.info(f"[{self.model_id}] Early stop: CV={cv:.2f}% < {early_stop_cv}%")
                break
            if cv < cv_threshold:
                logger.info(f"[{self.model_id}] Converged: CV={cv:.2f}% < {cv_threshold}%")
                break

            n_add = min(increment, max_iters - len(focal_lengths))
            for _ in range(n_add):
                r = self._model.calibrate(image, camera_model=inp.camera_model)
                focal_lengths.append(r["camera"].f[0, 1].item())
                all_results.append(r)

        representative_focal = float(np.median(focal_lengths))
        diffs = [abs(fl - representative_focal) for fl in focal_lengths]
        best_idx = int(np.argmin(diffs))
        best_result = all_results[best_idx]

        camera, gravity = best_result["camera"], best_result["gravity"]
        roll, pitch = rad2deg(gravity.rp).unbind(-1)
        final_cv = self._cv(focal_lengths)

        from ..stats import remove_outliers_iqr
        cleaned_focals, outlier_info = remove_outliers_iqr(focal_lengths)
        focal_std = float(np.std(cleaned_focals))

        search_range = self._compute_adaptive_search_range(
            focal_std, final_cv, len(cleaned_focals),
            inp.config.get("focal_length_search_range", 30),
            adaptive_cfg,
        )

        adaptive_stats = {
            "total_iterations": len(focal_lengths),
            "final_cv": final_cv,
            "representative_focal": representative_focal,
            "selected_iteration": best_idx + 1,
            "all_focal_lengths": focal_lengths,
            "outlier_info": outlier_info,
            "adaptive_search_range": search_range,
        }

        logger.info(
            f"[{self.model_id}] Adaptive done: {len(focal_lengths)} iters, "
            f"CV={final_cv:.2f}%, median={representative_focal:.1f} px, "
            f"search_range=±{search_range:.1f}"
        )

        return AdapterOutput(
            focal_length_px=representative_focal,
            roll_deg=roll.item(),
            pitch_deg=pitch.item(),
            vfov_deg=rad2deg(camera.vfov).item(),
            uncertainty={
                "roll_deg": rad2deg(best_result["roll_uncertainty"]).item(),
                "pitch_deg": rad2deg(best_result["pitch_uncertainty"]).item(),
                "vfov_deg": rad2deg(best_result["vfov_uncertainty"]).item(),
                "focal_px": best_result["focal_uncertainty"].item(),
            },
            distortion=self._extract_distortion(camera),
            extra={"raw_result": best_result, "adaptive_stats": adaptive_stats},
            runtime_ms=(time.perf_counter() - t0) * 1000,
        )

    # --- Private helpers ---

    def _ensure_geocalib_on_path(self):
        submodule_path = str(self._project_root / self.manifest["repo"]["clone_to"])
        if submodule_path not in sys.path:
            sys.path.insert(0, submodule_path)

    def _weights_name_for(self, camera_model: str) -> str:
        return "pinhole" if camera_model == "pinhole" else "distorted"

    def _ensure_weights_loaded(self, weights_name: str):
        if self._loaded_weights_name == weights_name:
            return
        tar_path = str(self._weights_dir / f"geocalib-{weights_name}.tar")
        logger.info(f"[{self.model_id}] Loading weights: {tar_path}")
        self._model = self._GeoCalib_cls(weights=tar_path).to(self._device)
        self._loaded_weights_name = weights_name

    def _extract_distortion(self, camera) -> dict:
        if not hasattr(camera, 'k1'):
            return {}
        k1_geocalib = camera.k1[0].item()
        f = camera.f[0, 1].item()
        k1_amc = k1_geocalib / (f ** 2)
        distortion = {
            "model": camera.name() if hasattr(camera, 'name') else "unknown",
            "k1": k1_amc,
            "k1_geocalib": k1_geocalib,
            "focal_length": f,
            "source": "geocalib",
        }
        if hasattr(camera, 'k2'):
            k2_geocalib = camera.k2[0].item()
            distortion["k2"] = k2_geocalib / (f ** 4)
            distortion["k2_geocalib"] = k2_geocalib
        return distortion

    @staticmethod
    def _cv(values: list[float]) -> float:
        if len(values) < 2:
            return float("inf")
        arr = np.array(values)
        return float((np.std(arr) / np.mean(arr)) * 100) if np.mean(arr) != 0 else float("inf")

    @staticmethod
    def _compute_adaptive_search_range(
        focal_std: float, cv: float, n_samples: int,
        fixed_range: float, adaptive_cfg: dict,
    ) -> float:
        """Compute tightened focal search range based on iteration statistics.
        Mirrors the confidence_interval strategy from run_geocalib.py."""
        sem = focal_std / np.sqrt(n_samples) if n_samples > 0 else focal_std

        if cv < 0.3:
            ci = 2.58 * sem
            uncertainty = max(ci + 1.0, 3.0)
        elif cv < 0.5:
            ci = 1.96 * sem
            uncertainty = max(ci + 2.0, 4.0)
        elif cv < 0.8:
            ci = 1.645 * sem
            uncertainty = max(ci + 3.0, 5.0)
        elif cv < 1.2:
            uncertainty = max(focal_std + 3.0, 6.0)
        else:
            uncertainty = max(1.5 * focal_std + 4.0, 8.0)

        return uncertainty

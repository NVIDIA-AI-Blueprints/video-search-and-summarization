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

import json
import logging
from pathlib import Path

import yaml

from ..schemas import RunnerResult

logger = logging.getLogger(__name__)


class AmcExporter:
    """Writes AMC-compatible artifacts from RunnerResult."""

    def export(self, result: RunnerResult, output_dir: Path, global_config: dict):
        output_dir.mkdir(parents=True, exist_ok=True)
        self._export_sv_config(result, output_dir, global_config)
        self._export_distortion(result, output_dir)
        self._export_undistorted_image(result, output_dir)
        self._export_provenance(result, output_dir)

    def _export_sv_config(self, result: RunnerResult, output_dir: Path, config: dict):
        gc = result.per_model.get("geocalib")
        if gc is None or not gc.outputs:
            raise RuntimeError("GeoCalib result required for AMC export")

        out = gc.outputs[0]
        if out.pitch_deg is None or out.roll_deg is None:
            raise RuntimeError("GeoCalib output missing pitch/roll — cannot build AMC config")
        f_range = config.get("focal_length_search_range", 30)

        # Determine focal: use consolidated value if available, else GeoCalib
        if result.aggregate and result.aggregate.get("selected_focal_length_px") is not None:
            focal = result.aggregate["selected_focal_length_px"]
            logger.info(f"Using consolidated focal: {focal:.1f} px ({result.focal_source})")
        else:
            adaptive_info = out.extra.get("adaptive_stats", {})
            if adaptive_info:
                focal = adaptive_info.get("representative_focal", out.focal_length_px)
            elif gc.stats and "median" in gc.stats:
                focal = gc.stats["median"]
            else:
                focal = out.focal_length_px

        # Pose/rotation always from GeoCalib
        config_params = {
            "focal_length": [float(focal - f_range), float(focal + f_range)],
            "rotation_x": [
                float(out.pitch_deg - out.uncertainty.get("pitch_deg", 5)),
                float(out.pitch_deg + out.uncertainty.get("pitch_deg", 5)),
            ],
            "rotation_z": [
                float(-out.roll_deg - out.uncertainty.get("roll_deg", 5)),
                float(-out.roll_deg + out.uncertainty.get("roll_deg", 5)),
            ],
            "translation_y": [0.01, 100],
            "human_radius": [0.3, 0.35],
            "iterations": config.get("iterations", 5000),
            "focal_length_init": float(focal),
            "weight_iou": config.get("weight_iou", 0.5),
        }

        path = output_dir / "config_sv_amc.yaml"
        with open(path, "w") as f:
            yaml.dump(config_params, f, default_flow_style=False, sort_keys=False)
        logger.info(f"Wrote {path}")

    def _export_distortion(self, result: RunnerResult, output_dir: Path):
        gc = result.per_model.get("geocalib")
        if gc is None or not gc.outputs:
            return
        dist = gc.outputs[0].distortion
        if not dist:
            return
        path = output_dir / "geocalib_distortion.yaml"
        with open(path, "w") as f:
            yaml.dump(dist, f, sort_keys=False)
        logger.info(f"Wrote {path}")

    def _export_undistorted_image(self, result: RunnerResult, output_dir: Path):
        gc = result.per_model.get("geocalib")
        if gc is None or not gc.outputs:
            return
        raw = gc.outputs[0].extra.get("raw_result")
        if raw is None:
            return
        camera = raw.get("camera")
        if camera is None or not hasattr(camera, 'k1'):
            return
        try:
            import cv2
            import torch
            img_path = result.preprocessed_image
            if img_path is None:
                return
            img_cv = cv2.imread(str(img_path))
            if img_cv is None:
                return
            img_rgb = cv2.cvtColor(img_cv, cv2.COLOR_BGR2RGB)
            device = camera.device
            img_t = torch.from_numpy(img_rgb).permute(2, 0, 1).float().div(255).unsqueeze(0).to(device)
            undist = camera.undistort_image(img_t)
            undist_np = undist.squeeze(0).permute(1, 2, 0).cpu().numpy()
            undist_np = (undist_np * 255).clip(0, 255).astype("uint8")
            undist_bgr = cv2.cvtColor(undist_np, cv2.COLOR_RGB2BGR)
            out_path = output_dir / "geocalib_undistorted.jpg"
            cv2.imwrite(str(out_path), undist_bgr)
            logger.info(f"Wrote {out_path}")
        except Exception as e:
            logger.warning(f"Failed to write undistorted image: {e}")

    def _export_provenance(self, result: RunnerResult, output_dir: Path):
        path = output_dir / "runner_provenance.json"
        with open(path, "w") as f:
            json.dump(result.provenance, f, indent=2, default=str)
        logger.info(f"Wrote {path}")

        self._export_runner_result(result, output_dir)

    def _export_runner_result(self, result: RunnerResult, output_dir: Path):
        """Write a comprehensive result file with per-model estimates and config."""
        per_model_summary = {}
        for mid, mr in result.per_model.items():
            focals = [o.focal_length_px for o in mr.outputs if o.focal_length_px is not None]
            runtimes = [o.runtime_ms for o in mr.outputs]
            entry = {
                "n_runs": len(mr.outputs),
                "status": mr.outputs[0].status if mr.outputs else "no_output",
            }
            if focals:
                import numpy as np
                entry["focal_length_px"] = {
                    "median": float(np.median(focals)),
                    "mean": float(np.mean(focals)),
                    "std": float(np.std(focals)),
                    "all_values": [round(f, 2) for f in focals],
                }
            if runtimes:
                import numpy as np
                entry["runtime_ms"] = {
                    "mean": round(float(np.mean(runtimes)), 1),
                    "total": round(float(sum(runtimes)), 1),
                }
            if mr.outputs and mr.outputs[0].roll_deg is not None:
                o = mr.outputs[0]
                entry["pose"] = {
                    "roll_deg": round(o.roll_deg, 2),
                    "pitch_deg": round(o.pitch_deg, 2),
                    "vfov_deg": round(o.vfov_deg, 2) if o.vfov_deg else None,
                }
            if mr.outputs and mr.outputs[0].error:
                entry["error"] = mr.outputs[0].error
            per_model_summary[mid] = entry

        doc = {
            "request_id": result.request_id,
            "provenance": result.provenance,
            "per_model": per_model_summary,
            "consolidation": {
                "enabled": result.aggregate is not None,
                "policy": result.focal_source,
                "result": result.aggregate,
            },
            "exported_focal_source": result.focal_source,
            "pose_source": result.pose_source,
        }

        path = output_dir / "runner_result.json"
        with open(path, "w") as f:
            json.dump(doc, f, indent=2, default=str)
        logger.info(f"Wrote {path}")

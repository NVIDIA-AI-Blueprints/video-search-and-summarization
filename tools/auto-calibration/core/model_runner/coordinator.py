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

import logging
import signal
import time
from pathlib import Path
from typing import Optional

from .schemas import (
    AdapterInput, AdapterOutput, RunnerRequest, RunnerResult, ModelRunResult,
)
from .registry import ModelRegistry
from .preprocessing import extract_image_from_video
from .stats import compute_focal_stats
from .adapters.base import BaseModelAdapter

logger = logging.getLogger(__name__)


class _InferenceTimeout(Exception):
    pass


class ModelCoordinator:

    def __init__(
        self,
        manifest_path: Path,
        runner_config: Optional[dict] = None,
        project_root: Optional[Path] = None,
    ):
        self._registry = ModelRegistry(manifest_path, project_root)
        self._runner_config = runner_config or {}
        self._active_adapter: Optional[BaseModelAdapter] = None

    def run(self, request: RunnerRequest) -> RunnerResult:
        result = RunnerResult()

        image_path = self._resolve_image(request)
        result.preprocessed_image = image_path

        if request.enabled_models:
            model_ids = list(request.enabled_models)
        else:
            model_ids = self._select_models_from_config()

        required = set(self._registry.list_required_models())
        if request.require_pose_from_geocalib and "geocalib" not in model_ids:
            model_ids.insert(0, "geocalib")

        ordered = sorted(model_ids, key=lambda m: (m not in required, m))

        for model_id in ordered:
            try:
                model_result = self._run_single_model(model_id, image_path, request)
                result.per_model[model_id] = model_result
            except Exception as e:
                if model_id in required:
                    raise RuntimeError(f"Required model '{model_id}' failed: {e}") from e
                logger.error(f"Optional model '{model_id}' failed: {e}")
                result.per_model[model_id] = ModelRunResult(
                    model_id=model_id, outputs=[], stats={"error": str(e)},
                )

        self._unload_active()

        result = self._apply_consolidation(result, request)
        result.provenance = self._build_provenance(model_ids, request)

        return result

    # ------------------------------------------------------------------

    def _resolve_image(self, request: RunnerRequest) -> Path:
        if request.input_image is not None:
            return request.input_image
        return extract_image_from_video(
            request.input_video,
            request.output_dir,
            frame_index=request.video_frame_index,
        )

    def _run_single_model(
        self, model_id: str, image_path: Path, request: RunnerRequest,
    ) -> ModelRunResult:
        self._unload_active()

        adapter = self._registry.create_adapter(model_id)

        if not adapter.validate_environment():
            raise EnvironmentError(f"Environment not ready for '{model_id}'")

        device = "cuda"
        adapter.load_model(device)
        self._active_adapter = adapter

        if request.save_per_model_output:
            model_output_dir = request.output_dir / model_id
            model_output_dir.mkdir(parents=True, exist_ok=True)
        else:
            model_output_dir = request.output_dir

        model_config = self._resolve_model_config(model_id, request.config)

        if request.seed is not None:
            adapter.set_seed(request.seed)

        adapter_input = AdapterInput(
            image_path=image_path,
            output_dir=model_output_dir,
            config=model_config,
            camera_model=request.camera_model,
        )

        manifest = self._registry.get_manifest_entry(model_id)
        timeout_s = manifest.get("timeout_s", 120)
        max_retries = manifest.get("max_retries", 0)

        outputs: list[AdapterOutput] = []
        for i in range(request.n_runs):
            if request.seed is not None:
                adapter.set_seed(request.seed + i)
            logger.info(f"[{model_id}] Run {i + 1}/{request.n_runs}")
            out = self._infer_with_timeout_retry(adapter, adapter_input, timeout_s, max_retries)
            outputs.append(out)
            if out.focal_length_px is not None:
                logger.info(f"  focal={out.focal_length_px:.1f} px  ({out.runtime_ms:.0f} ms)")

        stats = compute_focal_stats(outputs) if len(outputs) > 1 else None
        mr = ModelRunResult(model_id=model_id, outputs=outputs, stats=stats)

        if request.save_per_model_output:
            self._save_per_model_output(mr, model_output_dir)

        return mr

    def _unload_active(self):
        if self._active_adapter is not None and self._active_adapter.is_loaded:
            self._active_adapter.unload_model()
            self._active_adapter = None

    def _infer_with_timeout_retry(
        self, adapter: BaseModelAdapter, inp: AdapterInput,
        timeout_s: int, max_retries: int,
    ) -> AdapterOutput:
        # NOTE: signal.SIGALRM is Unix-only and requires the main thread.
        # This works for CLI usage and single-process uvicorn, but will raise
        # ValueError if called from a worker thread (e.g. threaded ASGI server).
        # A subprocess-based timeout would fix this but adds complexity around
        # CUDA context isolation. Tracked as a known limitation.
        last_error = None
        for attempt in range(1 + max_retries):
            if attempt > 0:
                logger.warning(f"[{adapter.model_id}] Retry {attempt}/{max_retries}")
            try:
                def _timeout_handler(signum, frame):
                    raise _InferenceTimeout(f"Inference exceeded {timeout_s}s timeout")
                prev = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(timeout_s)
                try:
                    out = adapter.infer(inp)
                finally:
                    signal.alarm(0)
                    signal.signal(signal.SIGALRM, prev)
                return out
            except _InferenceTimeout as e:
                last_error = str(e)
                logger.error(f"[{adapter.model_id}] {e}")
            except Exception as e:
                last_error = str(e)
                logger.error(f"[{adapter.model_id}] Inference error: {e}")
        return AdapterOutput(status="error", error=f"Failed after {1 + max_retries} attempts: {last_error}")

    @staticmethod
    def _save_per_model_output(mr: ModelRunResult, output_dir: Path):
        """Dump per-model intermediate results to JSON."""
        import json
        import numpy as np

        data: dict = {"model_id": mr.model_id, "n_runs": len(mr.outputs)}

        runs = []
        for i, out in enumerate(mr.outputs):
            run_data: dict = {
                "run": i + 1,
                "focal_length_px": out.focal_length_px,
                "runtime_ms": round(out.runtime_ms, 1),
                "status": out.status,
            }
            if out.roll_deg is not None:
                run_data["roll_deg"] = round(out.roll_deg, 4)
                run_data["pitch_deg"] = round(out.pitch_deg, 4)
            if out.vfov_deg is not None:
                run_data["vfov_deg"] = round(out.vfov_deg, 4)
            if out.uncertainty:
                run_data["uncertainty"] = out.uncertainty
            if out.distortion:
                run_data["distortion"] = out.distortion
            extra_safe = {k: v for k, v in out.extra.items() if k != "raw_result"}
            if extra_safe:
                run_data["extra"] = extra_safe
            if out.error:
                run_data["error"] = out.error
            runs.append(run_data)
        data["runs"] = runs

        if mr.stats:
            data["stats"] = mr.stats

        path = output_dir / f"{mr.model_id}_output.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
        logger.info(f"[{mr.model_id}] Saved per-model output -> {path}")

    def _select_models_from_config(self) -> list[str]:
        """Derive which models to run from consolidation config.

        - consolidation disabled → GeoCalib only
        - single_source → GeoCalib + the specified source model
        - any multi-model policy → all enabled models from manifest
        """
        all_enabled = self._registry.list_enabled_models()
        consolidation_cfg = self._runner_config.get("consolidation", {})

        if not consolidation_cfg.get("enabled", False):
            logger.info("Consolidation disabled -- running GeoCalib only")
            return ["geocalib"]

        policy = consolidation_cfg.get("policy", "single_source")

        if policy == "single_source":
            source = consolidation_cfg.get("policy_params", {}).get("model_id", "geocalib")
            models = ["geocalib"]
            if source != "geocalib" and source in all_enabled:
                models.append(source)
            logger.info(f"Consolidation single_source (model_id={source}) -- running: {models}")
            return models

        logger.info(f"Consolidation policy '{policy}' -- running all enabled: {all_enabled}")
        return all_enabled

    def _resolve_model_config(self, model_id: str, global_config: dict) -> dict:
        base = dict(global_config)
        per_model = self._runner_config.get("models", {}).get(model_id, {})
        base.update(per_model)
        return base

    def _apply_consolidation(self, result: RunnerResult, request: RunnerRequest) -> RunnerResult:
        consolidation_cfg = self._runner_config.get("consolidation", {})
        if not consolidation_cfg.get("enabled", False):
            return result
        from .consolidation import get_policy
        policy = get_policy(consolidation_cfg)
        result.aggregate = policy.select_focal(result.per_model)
        result.focal_source = consolidation_cfg.get("policy", "unknown")
        return result

    def _build_provenance(self, model_ids: list[str], request: RunnerRequest) -> dict:
        import datetime
        return {
            "timestamp": datetime.datetime.now().isoformat(),
            "models_run": model_ids,
            "n_runs": request.n_runs,
            "seed": request.seed,
            "manifest": str(self._registry._manifest_path),
        }

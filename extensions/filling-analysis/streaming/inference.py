# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reuse the frozen recorded models/height helper, without opening recorded media."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np

from segmentation.service import Worker, clip_liquid, polygons
from segmentation.measurements import measure_masks


class FrameEngine:
    def __init__(self):
        self.worker = Worker()
        self.live_code_hash = hashlib.sha256(b"".join(
            path.read_bytes() for path in sorted(Path(__file__).parent.rglob("*.py"))
        )).hexdigest()
        self.calibration = json.loads((Path(__file__).parent / "reference/neural_measurement_calibration.json").read_text())

    def load(self):
        self.worker.load()
        if not self.worker.ready:
            raise RuntimeError(self.worker.loading_error or "CUDA model loading failed")
        actual = {name: record["checkpoint_sha256"] for name, record in self.worker.models.items()}
        if actual != self.calibration["model_hashes"]:
            raise RuntimeError("Loaded model pair differs from the reviewed live calibration")

    def identity(self):
        return {"models": self.worker.models, "recorded_pipeline_sha256": self.worker.code_hash,
                "live_pipeline_sha256": self.live_code_hash, "engine": "rfdetr",
                "algorithm": "rfdetr-live-cycle-v1", "overflow_engine": "calibrated-exterior-color-signal-v1"}

    def predict(self, frame, exterior):
        height, width = frame.shape[:2]
        detections = self.worker.bottle.predict(frame, color_order="BGR")
        instances = []
        for i, detected in enumerate(sorted(detections, key=lambda d: d.box[0])):
            bottle = detected.raw_mask.astype(bool)
            ys, xs = np.where(bottle)
            if not len(xs):
                continue
            bx0, bx1, by0, by1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
            px, py = int((bx1-bx0)*.15), int((by1-by0)*.05)
            x0, x1, y0, y1 = max(0, bx0-px), min(width, bx1+px), max(0, by0-py), min(height, by1+py)
            crop = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2RGB)
            prediction = (self.worker.liquid.predict(crop) if min(crop.shape[:2]) >= 8 else
                          {"mask": np.zeros(crop.shape[:2], bool), "confidence": 0., "status": "insufficient_pixels"})
            liquid = clip_liquid(prediction["mask"], bottle[y0:y1, x0:x1])
            raw_liquid = np.asarray(prediction["mask"], dtype=bool)
            outside = int((raw_liquid & ~bottle[y0:y1, x0:x1]).sum())
            mask_quality = {"liquid_outside_fraction": outside/max(1, int(raw_liquid.sum())),
                            "liquid_outside_pixels": outside, "raw_liquid_pixels": int(raw_liquid.sum())}
            full = np.zeros_like(bottle)
            full[y0:y1, x0:x1] = liquid
            measurement = measure_masks(bottle, full, float(detected.confidence), float(prediction["confidence"]))
            instances.append({"id": f"instance-{i}", "box": list(detected.box),
                "bottle_mask": detected.bottle_mask, "liquid_mask": polygons(liquid, (x0, y0), (height, width)),
                "bottle_confidence": float(detected.confidence), "liquid_confidence": float(prediction["confidence"]),
                "mask_quality": mask_quality,
                "liquid_status": prediction.get("status", "predicted"), "measurement": measurement})
        return {"instances": instances}

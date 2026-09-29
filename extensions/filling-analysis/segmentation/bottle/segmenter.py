# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Genuine RF-DETR-Seg bottle masks. No geometry/colour-mask fallback."""
from __future__ import annotations

import hashlib
import importlib.metadata
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np


@dataclass
class BottleInstance:
    """One frame-local instance; IDs are not temporal tracking identities."""
    id: str
    box: list[float]  # normalized x, y, width, height in the original frame
    bottle_mask: list[list[list[float]]]  # external polygon rings, normalized x/y
    confidence: float
    raw_mask: np.ndarray  # bool[original_height, original_width]
    model_identity: dict[str, Any]

    @property
    def box_pixels(self) -> list[int]:
        """Original-frame xywh bounding box for downstream crop extraction."""
        height, width = self.raw_mask.shape
        x, y, w, h = self.box
        return [round(x * width), round(y * height), round(w * width), round(h * height)]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "class_name": "bottle", "box": self.box,
            "box_pixels": self.box_pixels,
            "bottle_mask": self.bottle_mask, "confidence": self.confidence,
            "mask_area_pixels": int(self.raw_mask.sum()),
            "model_identity": self.model_identity,
        }


def mask_to_polygons(mask: np.ndarray) -> list[list[list[float]]]:
    """Convert actual binary-mask contours, preserving all external components."""
    if mask.ndim != 2:
        raise ValueError("A mask must have shape [height, width]")
    height, width = mask.shape
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL,
                                  cv2.CHAIN_APPROX_SIMPLE)
    rings = []
    for contour in contours:
        if len(contour) < 3 or cv2.contourArea(contour) < 2:
            continue
        # Pixel-level mask remains available; small simplification only for UI transport.
        contour = cv2.approxPolyDP(contour, max(0.5, min(height, width) * 0.0006), True)
        rings.append([[round(float(x) / width, 6), round(float(y) / height, 6)]
                      for x, y in contour.reshape(-1, 2)])
    return rings


class BottleSegmenter:
    """Pinned RF-DETR-Seg 2XLarge COCO inference on RGB or BGR uint8 frames.

    Instantiate once per worker. GPU selection belongs to CUDA_VISIBLE_DEVICES.
    Returned raw masks use the ORIGINAL frame dimensions. Full-frame inference
    may resize the decoded frame to max_side before the model's own 768px resize;
    the actual predicted mask is resized back, never replaced by a rectangle.
    """

    def __init__(self, checkpoint: str | Path | None = None, *,
                 device: str = "cuda", threshold: float = 0.4,
                 max_side: int = 1280, memory_fraction: float = 0.12) -> None:
        import torch
        from rfdetr import RFDETRSeg2XLarge
        from rfdetr.assets.coco_classes import COCO_CLASSES

        if not 0 < threshold <= 1 or max_side < 504:
            raise ValueError("Invalid confidence threshold or max_side")
        self.threshold, self.max_side = threshold, max_side
        self.device = device
        self._torch = torch
        if device.startswith("cuda"):
            if not torch.cuda.is_available():
                raise RuntimeError("CUDA requested but unavailable")
            torch.cuda.set_per_process_memory_fraction(memory_fraction)
        kwargs: dict[str, Any] = {"device": device}
        if checkpoint is not None:
            checkpoint_path = Path(checkpoint).resolve()
            if not checkpoint_path.is_file():
                raise FileNotFoundError(f"Required local checkpoint not found: {checkpoint_path}")
            kwargs["pretrain_weights"] = str(checkpoint_path)
        started = time.perf_counter()
        self.model = RFDETRSeg2XLarge(**kwargs)
        weights = Path(self.model.model_config.pretrain_weights)
        self.identity = {
            "architecture": "RF-DETR-Seg 2XLarge", "model_id": "rfdetr-seg-2xlarge",
            "package_version": importlib.metadata.version("rfdetr"),
            "checkpoint_filename": weights.name,
            "checkpoint_sha256": hashlib.file_digest(weights.open("rb"), "sha256").hexdigest(),
            "pretraining": "COCO instance segmentation", "license": "Apache-2.0",
            "model_resolution": self.model.model_config.resolution,
            "dtype": "float16" if device.startswith("cuda") else "float32",
        }
        self.model.inference(compile=False, inplace=True, dtype=self.identity["dtype"])
        self.load_seconds = time.perf_counter() - started
        self.coco_classes = COCO_CLASSES
        self.last_timing: dict[str, Any] = {}

    def predict(self, frame: np.ndarray, *, color_order: str = "BGR") -> list[BottleInstance]:
        if frame.ndim != 3 or frame.shape[2] != 3 or frame.dtype != np.uint8:
            raise ValueError("Expected uint8 HxWx3 image")
        if color_order not in {"RGB", "BGR"}:
            raise ValueError("color_order must be RGB or BGR")
        height, width = frame.shape[:2]
        scale = min(1.0, self.max_side / max(height, width))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if color_order == "BGR" else frame
        if scale < 1:
            rgb = cv2.resize(rgb, (round(width * scale), round(height * scale)),
                             interpolation=cv2.INTER_AREA)
        if self.device.startswith("cuda"):
            self._torch.cuda.synchronize()
        started = time.perf_counter()
        detections = self.model.predict(rgb, threshold=self.threshold)
        if self.device.startswith("cuda"):
            self._torch.cuda.synchronize()
        inference_ms = (time.perf_counter() - started) * 1000
        if detections.mask is None and len(detections):
            raise RuntimeError("Segmentation model returned no instance mask tensor")
        bottles: list[BottleInstance] = []
        for index, class_id in enumerate(detections.class_id):
            if self.coco_classes[int(class_id)] != "bottle":
                continue
            raw = detections.mask[index].astype(np.uint8)
            if raw.shape != (height, width):
                raw = cv2.resize(raw, (width, height), interpolation=cv2.INTER_NEAREST)
            mask = raw.astype(bool)
            ys, xs = np.nonzero(mask)
            if not len(xs):
                continue
            x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1
            bottles.append(BottleInstance(
                id=f"bottle-{len(bottles) + 1}",
                box=[x0 / width, y0 / height, (x1 - x0) / width, (y1 - y0) / height],
                bottle_mask=mask_to_polygons(mask),
                confidence=float(detections.confidence[index]),
                raw_mask=mask,
                model_identity=self.identity,
            ))
        self.last_timing = {
            "inference_ms": inference_ms,
            "total_ms": (time.perf_counter() - started) * 1000,
            "original_size": [width, height], "inference_frame_size": [rgb.shape[1], rgb.shape[0]],
            "all_detected_instances": len(detections), "bottle_instances": len(bottles),
            "gpu_peak_allocated_mib": (self._torch.cuda.max_memory_allocated() / 1024**2
                                       if self.device.startswith("cuda") else 0),
        }
        return bottles

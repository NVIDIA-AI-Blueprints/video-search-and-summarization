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
"""The offline open-vocabulary detector for Evaluation V2 box references: OWLv2 base on CPU, as a subprocess speaking
``evalv2-detector-v1`` (``evalv2/references/detector.py``).

One JSON request on stdin, ``{"protocol", "frames": [path, ...], "queries": [text, ...]}``, and one JSON reply on
stdout, ``{"detector": {"name", "version", "weights_sha256", ...}, "frames": {path: [{"box", "score", "label"}]}}``.
Boxes use the agent's convention, ``[ymin, xmin, ymax, xmax]`` normalised to 0-1000; ``label`` is the query a box
answers. The detector only proposes: a generator picks among the proposals or says none fits.

- ``--backend owlv2`` (the real one) runs in the throwaway environment ``v2_detector_env.py`` builds. It loads
  ``google/owlv2-base-patch16-ensemble`` from ``--weights-dir`` with ``local_files_only`` (it never downloads), and
  only after re-hashing every file against the lock the approved download wrote (``v2_detector_weights.py``).
- ``--backend stub`` needs no weights and no third-party package but numpy: synthetic logits and boxes derived from
  each frame's bytes go through the same candidate extraction and post-processing. Its identity says ``stub``;
  it is for tests and plumbing, never for references.

Post-processing per frame: sigmoid scores per (patch, query) at or above ``--threshold``; boxes converted from
OWLv2's padded square (the image sits top-left, padding right and bottom) to the image; boxes thinner than 4 units
dropped (``evalv2.references.boxes.MIN_SIDE``); non-maximum suppression per query; the ``--top-k`` best per query;
at most ``--max-per-frame`` overall. The parameters are part of the detector's identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import struct
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

RUNNER_VERSION = "evalv2-owlv2-runner-v1"
PROTOCOL = "evalv2-detector-v1"
MODEL_REPO = "google/owlv2-base-patch16-ensemble"
MODEL_LICENSE = "apache-2.0"
LOCK_NAME = "evalv2-weights.lock.json"
LOCK_VERSION = "evalv2-detector-weights-v1"
WEIGHTS_FILE = "model.safetensors"
SCALE = 1000
MIN_SIDE = 4  # evalv2.references.boxes.MIN_SIDE; a test keeps the two equal
MAX_QUERY_TOKENS = 16  # OWLv2's text encoder length, start and end tokens included
PRE_NMS_PER_QUERY = 200  # the best candidates per query kept before suppression (bounds the work per frame)
DEFAULT_PARAMS = {"threshold": 0.1, "nms_iou": 0.5, "top_k": 5, "max_per_frame": 25}
STUB_WEIGHTS_SHA256 = hashlib.sha256(b"evalv2-stub-detector: no weights").hexdigest()


class DetectorFailure(RuntimeError):
    """The request, the weights or a frame is not usable; the runner exits non-zero and replies nothing."""


# ---------------------------------------------------------------------------------------------------------------
# Weights lock
# ---------------------------------------------------------------------------------------------------------------


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def read_lock(weights_dir: Path) -> dict[str, Any]:
    path = Path(weights_dir) / LOCK_NAME
    if not path.is_file():
        raise DetectorFailure(f"no weights lock at {path}: the weights are not an approved, verified download "
                              "(scripts/v2_detector_weights.py)")
    lock = json.loads(path.read_text(encoding="utf-8"))
    if lock.get("lock") != LOCK_VERSION or lock.get("repo_id") != MODEL_REPO or WEIGHTS_FILE not in lock.get(
            "files", {}):
        raise DetectorFailure(f"{path} is not an {LOCK_VERSION} lock for {MODEL_REPO}")
    return lock


def verify_lock(weights_dir: Path) -> dict[str, Any]:
    """The lock, after every file it names is re-hashed and found unchanged; refuses any difference."""
    lock = read_lock(weights_dir)
    for name, entry in sorted(lock["files"].items()):
        path = Path(weights_dir) / name
        if not path.is_file():
            raise DetectorFailure(f"{name} named in the lock is missing")
        if path.stat().st_size != entry["bytes"] or sha256_file(path) != entry["sha256"]:
            raise DetectorFailure(f"{name} differs from the lock (size or sha256): refusing to load it")
    if lock["files"][WEIGHTS_FILE]["sha256"] != lock.get("weights_sha256"):
        raise DetectorFailure("the lock's weights_sha256 is not the weights file's sha256")
    return lock


# ---------------------------------------------------------------------------------------------------------------
# Post-processing (pure, shared by every backend)
# ---------------------------------------------------------------------------------------------------------------


def agent_box(cx: float, cy: float, w: float, h: float, width: int, height: int) -> list[float] | None:
    """An OWLv2 box (centre and size, normalised to the padded square of side ``max(width, height)``) as an agent
    box on the image, clipped to it and rounded to 0.1; ``None`` when what is left is not a box."""
    side = float(max(width, height))
    x0, x1 = (cx - w / 2) * side, (cx + w / 2) * side
    y0, y1 = (cy - h / 2) * side, (cy + h / 2) * side
    x0, x1 = min(max(x0, 0.0), float(width)), min(max(x1, 0.0), float(width))
    y0, y1 = min(max(y0, 0.0), float(height)), min(max(y1, 0.0), float(height))
    ymin, xmin = round(y0 / height * SCALE, 1), round(x0 / width * SCALE, 1)
    ymax, xmax = round(y1 / height * SCALE, 1), round(x1 / width * SCALE, 1)
    if not (0 <= ymin < ymax <= SCALE and 0 <= xmin < xmax <= SCALE) or min(ymax - ymin, xmax - xmin) < MIN_SIDE:
        return None
    return [ymin, xmin, ymax, xmax]


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    inter_h = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    inter_w = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    intersection = inter_h * inter_w
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - intersection
    return intersection / union if union > 0 else 0.0


def _order(detection: Mapping[str, Any]) -> tuple[Any, ...]:
    return (-detection["score"], detection["label"], tuple(detection["box"]))


def nms(detections: Iterable[Mapping[str, Any]], iou_threshold: float) -> list[dict[str, Any]]:
    """Greedy suppression within each label: a box overlapping a better one of the same label at or above the
    threshold is dropped. Ties are broken by label and box, so the result is deterministic."""
    kept: list[dict[str, Any]] = []
    for detection in sorted(detections, key=_order):
        if all(k["label"] != detection["label"] or iou(k["box"], detection["box"]) < iou_threshold for k in kept):
            kept.append(dict(detection))
    return kept


def select(detections: Iterable[Mapping[str, Any]], *, top_k: int, max_per_frame: int) -> list[dict[str, Any]]:
    """The ``top_k`` best per label, then the ``max_per_frame`` best overall, best first."""
    per_label: dict[str, int] = {}
    chosen = []
    for detection in sorted(detections, key=_order):
        if per_label.get(detection["label"], 0) < top_k:
            per_label[detection["label"]] = per_label.get(detection["label"], 0) + 1
            chosen.append(dict(detection))
    return chosen[:max_per_frame]


def frame_detections(candidates: Iterable[tuple[Sequence[float], float, int]], labels: Sequence[str],
                     width: int, height: int, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """One frame's proposals from ``(padded box, score, query index)`` candidates."""
    detections = []
    for box, score, query in candidates:
        converted = agent_box(*box, width, height)
        if converted is not None and score >= params["threshold"]:
            detections.append({"box": converted, "score": round(float(score), 4), "label": labels[query]})
    kept = nms(detections, params["nms_iou"])
    return select(kept, top_k=params["top_k"], max_per_frame=params["max_per_frame"])


def candidates_from_arrays(logits: Any, boxes: Any, threshold: float) -> list[tuple[list[float], float, int]]:
    """``(box, score, query)`` for every patch and query scoring at least ``threshold`` (sigmoid of the logit), at
    most :data:`PRE_NMS_PER_QUERY` per query. ``logits`` is patches x queries, ``boxes`` patches x 4."""
    import numpy as np

    scores = 1.0 / (1.0 + np.exp(-np.asarray(logits, dtype=np.float64)))
    boxes = np.asarray(boxes, dtype=np.float64)
    out: list[tuple[list[float], float, int]] = []
    for query in range(scores.shape[1]):
        column = scores[:, query]
        keep = np.nonzero(column >= threshold)[0]
        if len(keep) > PRE_NMS_PER_QUERY:
            keep = keep[np.argsort(-column[keep], kind="stable")[:PRE_NMS_PER_QUERY]]
        out.extend((boxes[patch].tolist(), float(column[patch]), query) for patch in keep.tolist())
    return out


def image_size(path: Path) -> tuple[int, int]:
    """``(width, height)`` from a PNG or JPEG header, without an image library (the stub backend)."""
    data = Path(path).read_bytes()
    if data[:8] == b"\x89PNG\r\n\x1a\n" and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return int(width), int(height)
    if data[:2] == b"\xff\xd8":
        index = 2
        while index + 9 < len(data):
            if data[index] != 0xFF:
                index += 1
                continue
            marker = data[index + 1]
            length = struct.unpack(">H", data[index + 2:index + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[index + 5:index + 9])
                return int(width), int(height)
            index += 2 + length
    raise DetectorFailure(f"{path}: not a PNG or JPEG frame")


# ---------------------------------------------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------------------------------------------


class StubBackend:
    """No weights: per frame, synthetic logits and boxes seeded by the frame's bytes and the queries."""

    patches = 64

    def __init__(self, params: Mapping[str, Any]) -> None:
        self.params = dict(params)
        self.identity = {"name": "stub-owlv2", "version": f"{RUNNER_VERSION};stub", "weights_sha256":
                         STUB_WEIGHTS_SHA256, "stub": True, "params": self.params}

    def fit_queries(self, queries: Sequence[str]) -> tuple[list[str], list[str]]:
        return list(queries), []

    def detect(self, path: Path, queries: Sequence[str]) -> tuple[list[tuple[list[float], float, int]], int, int]:
        width, height = image_size(path)
        seed = hashlib.sha256(Path(path).read_bytes() + "\x1f".join(queries).encode()).hexdigest()
        rng = random.Random(seed)
        logits = [[rng.uniform(-6.0, 3.0) for _ in queries] for _ in range(self.patches)]
        boxes = [[rng.uniform(0.15, 0.85), rng.uniform(0.15, 0.85), rng.uniform(0.02, 0.4), rng.uniform(0.02, 0.4)]
                 for _ in range(self.patches)]
        return candidates_from_arrays(logits, boxes, self.params["threshold"]), width, height


class Owlv2Backend:
    """OWLv2 base from a verified local download; CPU, deterministic algorithms, a fixed thread count."""

    def __init__(self, weights_dir: Path, params: Mapping[str, Any], *, threads: int) -> None:
        lock = verify_lock(weights_dir)  # before anything is loaded
        import torch
        import transformers
        from PIL import Image
        from transformers import Owlv2ForObjectDetection, Owlv2Processor

        torch.set_num_threads(threads)
        torch.use_deterministic_algorithms(True, warn_only=True)
        self._torch, self._image = torch, Image
        self.processor = Owlv2Processor.from_pretrained(str(weights_dir), local_files_only=True)
        self.model = Owlv2ForObjectDetection.from_pretrained(str(weights_dir), local_files_only=True,
                                                             use_safetensors=True).eval()
        self.params = dict(params)
        processor_class = type(getattr(self.processor, "image_processor", self.processor)).__name__
        self.identity = {
            "name": MODEL_REPO.split("/")[-1],
            "version": (f"{RUNNER_VERSION};revision={lock['revision']};transformers={transformers.__version__};"
                        f"torch={torch.__version__};processor={processor_class}"),
            "weights_sha256": lock["weights_sha256"], "repo_id": lock["repo_id"], "revision": lock["revision"],
            "license": lock.get("license"), "params": self.params, "threads": threads, "device": "cpu"}

    def fit_queries(self, queries: Sequence[str]) -> tuple[list[str], list[str]]:
        """Each query cut at a word boundary to OWLv2's 16 text tokens; the cut ones are reported."""
        used, truncated = [], []
        for query in queries:
            words = query.split()
            while len(words) > 1 and len(self.processor.tokenizer(" ".join(words))["input_ids"]) > MAX_QUERY_TOKENS:
                words.pop()
            fitted = " ".join(words)
            used.append(fitted)
            if fitted != query:
                truncated.append(query)
        return used, truncated

    def detect(self, path: Path, queries: Sequence[str]) -> tuple[list[tuple[list[float], float, int]], int, int]:
        image = self._image.open(path).convert("RGB")
        width, height = image.size
        inputs = self.processor(text=[list(queries)], images=image, return_tensors="pt")
        with self._torch.inference_mode():
            outputs = self.model(**inputs)
        logits = outputs.logits[0].float().cpu().numpy()
        boxes = outputs.pred_boxes[0].float().cpu().numpy()
        return candidates_from_arrays(logits, boxes, self.params["threshold"]), width, height


# ---------------------------------------------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------------------------------------------


def check_request(request: Any) -> tuple[list[str], list[str]]:
    if not isinstance(request, dict) or request.get("protocol") != PROTOCOL:
        raise DetectorFailure(f"the request must speak {PROTOCOL}")
    frames, queries = request.get("frames"), request.get("queries")
    if not isinstance(frames, list) or not all(isinstance(f, str) and f for f in frames):
        raise DetectorFailure("frames must be a list of paths")
    if not isinstance(queries, list) or not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise DetectorFailure("queries must be a non-empty list of texts")
    return frames, list(dict.fromkeys(q.strip() for q in queries))


def run(request: Any, backend: Any) -> dict[str, Any]:
    """The reply to one request: every frame's proposals, labelled with the query each box answers."""
    frames, labels = check_request(request)
    used, truncated = backend.fit_queries(labels)
    started = time.perf_counter()
    out: dict[str, list[dict[str, Any]]] = {}
    for frame in frames:
        path = Path(frame)
        if not path.is_file():
            raise DetectorFailure(f"frame {frame} not found")
        candidates, width, height = backend.detect(path, used)
        out[frame] = frame_detections(candidates, labels, width, height, backend.params)
    elapsed = time.perf_counter() - started
    return {"detector": backend.identity, "frames": out,
            "queries": {"given": labels, "used": used, "truncated": truncated},
            "timing": {"frames": len(frames), "seconds": round(elapsed, 3),
                       "per_frame_s": round(elapsed / len(frames), 3) if frames else None}}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=("owlv2", "stub"), required=True)
    parser.add_argument("--weights-dir", type=Path, help="the verified download (with its lock); owlv2 only")
    parser.add_argument("--threshold", type=float, default=DEFAULT_PARAMS["threshold"])
    parser.add_argument("--nms-iou", type=float, default=DEFAULT_PARAMS["nms_iou"])
    parser.add_argument("--top-k", type=int, default=DEFAULT_PARAMS["top_k"])
    parser.add_argument("--max-per-frame", type=int, default=DEFAULT_PARAMS["max_per_frame"])
    parser.add_argument("--threads", type=int, default=6)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    params = {"threshold": args.threshold, "nms_iou": args.nms_iou, "top_k": args.top_k,
              "max_per_frame": args.max_per_frame}
    try:
        if not (0.0 < params["threshold"] < 1.0 and 0.0 < params["nms_iou"] <= 1.0 and params["top_k"] > 0
                and params["max_per_frame"] > 0):
            raise DetectorFailure(f"invalid parameters {params}")
        request = json.loads(sys.stdin.read())
        if args.backend == "owlv2":
            if args.weights_dir is None:
                raise DetectorFailure("--weights-dir is required for the owlv2 backend")
            backend: Any = Owlv2Backend(args.weights_dir, params, threads=args.threads)
        else:
            backend = StubBackend(params)
        reply = run(request, backend)
    except (DetectorFailure, json.JSONDecodeError, OSError) as exc:
        print(f"v2_detector: {exc}", file=sys.stderr)
        return 1
    sys.stdout.write(json.dumps(reply, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

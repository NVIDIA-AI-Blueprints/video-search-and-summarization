# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Boundary checks for the merged Cosmos v1.2 ONNX release."""

import hashlib
import json
from pathlib import Path

import numpy as np

VISION = "ce1_448p_vitlora20_2stage_vision.onnx"
TEXT = "ce1_448p_vitlora20_2stage_text.onnx"
HF_NAME = "cosmos_embed1_448p_lora8_vitlora20_2stage_6640iter_hf"


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_release(root, paths):
    """Only use files explicitly covered by the supplier's manifest."""
    root = Path(root).resolve()
    manifest = {}
    for line in (root / "MANIFEST.sha256").read_text().splitlines():
        if not line.strip():
            continue
        digest, name = line.split(maxsplit=1)
        relative = name.strip().removeprefix("./")
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("Malformed release checksum")
        if relative in manifest:
            raise ValueError("Duplicate release checksum entry")
        manifest[relative] = digest
    result = {}
    for path in paths:
        path = Path(path).resolve()
        relative = path.relative_to(root).as_posix()
        if relative not in manifest or sha256(path) != manifest[relative]:
            raise ValueError(f"Release checksum mismatch or missing entry: {relative}")
        result[relative] = manifest[relative]
    return result


def prepare_video_batch(frames):
    """Arrange and scale RGB clips selected/resized by the shared video pipeline."""
    import torch

    if not frames or any(
        f.dtype != torch.uint8 or tuple(f.shape) != (8, 448, 448, 3) for f in frames
    ):
        raise ValueError("Expected nonempty list of uint8 RGB [8,448,448,3] clips")
    return (
        torch.stack(frames)
        .permute(0, 4, 1, 2, 3)
        .to(torch.float32)
        .div_(255)
        .contiguous()
    )


def prepare_text_inputs(processor, texts):
    if not texts or any(not isinstance(t, str) or not t.strip() for t in texts):
        raise ValueError("Expected nonempty text batch")
    encoded = processor(text=texts, return_tensors="pt", max_txt_len=128)
    inputs = {
        name: getattr(encoded, name).cpu().numpy().astype(np.int64)
        for name in ("input_ids", "attention_mask")
    }
    if any(value.shape != (len(texts), 128) for value in inputs.values()):
        raise ValueError("Release text input must be padded/truncated to 128 tokens")
    return inputs


def checked_embeddings(rows, expected_count):
    array = np.asarray(rows, dtype=np.float32)
    if array.shape != (expected_count, 768) or not np.isfinite(array).all():
        raise ValueError("Release returned malformed/nonfinite embeddings")
    if not np.allclose(np.linalg.norm(array, axis=1), 1.0, atol=0.001, rtol=0):
        raise ValueError(
            "Release returned embeddings without expected L2 normalization"
        )
    return array.tolist()


def verify_build(repository):
    repository = Path(repository)
    seal = json.loads((repository / "build.json").read_text())
    if seal.get("contract") != "cosmos-v1.2-bcthw-fp32-128-v1":
        raise ValueError("Unrecognized release engine contract")
    for name, digest in seal["artifacts"].items():
        path = (repository / name).resolve()
        path.relative_to(repository.resolve())
        if sha256(path) != digest:
            raise ValueError(f"Engine/configuration identity mismatch: {name}")
    return seal

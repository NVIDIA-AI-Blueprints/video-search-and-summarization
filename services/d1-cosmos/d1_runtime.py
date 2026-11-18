# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Load the checkpoint provisioned at the pinned local snapshot."""

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read_lock():
    return json.loads((ROOT / "model-lock.json").read_text())


def model_location(lock):
    return Path(os.getenv("D1_MODEL_DIR", ROOT / "models")) / lock["revision"]


def load_model():
    import torch
    from transformers import AutoModel

    lock = read_lock()
    path = model_location(lock)
    if not (path / "model.safetensors").is_file():
        raise RuntimeError("The pinned snapshot has not been downloaded into the model volume.")
    if not torch.cuda.is_available():
        raise RuntimeError("CUDA is not available inside this container.")
    model = AutoModel.from_pretrained(
        str(path), trust_remote_code=True, local_files_only=True, dtype=torch.bfloat16
    ).to("cuda").eval()
    return model, lock

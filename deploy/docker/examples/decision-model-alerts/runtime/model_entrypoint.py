# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fetch the pinned D1 snapshot into the model volume and start inference."""

from pathlib import Path

from d1_runtime import model_location, read_lock
from server import main as serve


def ensure_snapshot(lock, location: Path):
    completed = location / ".d1-snapshot-ready"
    required = ("config.json", "model.safetensors")
    if completed.is_file() and all((location / name).is_file() for name in required):
        return
    from huggingface_hub import snapshot_download

    snapshot_download(repo_id=lock["model_id"], revision=lock["revision"], local_dir=location)
    if not all((location / name).is_file() for name in required):
        raise RuntimeError("The downloaded D1 snapshot lacks its required configuration or weights.")
    completed.touch()


if __name__ == "__main__":
    lock = read_lock()
    ensure_snapshot(lock, model_location(lock))
    serve()

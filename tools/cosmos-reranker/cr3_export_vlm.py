#!/usr/bin/env python3
"""Export the Cosmos3-Nano VLM from the already-downloaded local snapshot.

Drives cosmos_reranker.finetuning.model.export_vlm against artifacts/base by
no-op'ing the huggingface_hub.snapshot_download calls inside it (every file
it requests is already on disk). Writes artifacts/models/Cosmos3-Nano-VLM.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "artifacts/base"
DEST = ROOT / "artifacts/models/Cosmos3-Nano-VLM"


def main() -> None:
    if DEST.exists():
        raise SystemExit(f"Destination exists: {DEST}")
    if not (BASE / "model.safetensors.index.json").is_file():
        raise SystemExit(f"Missing snapshot under {BASE}")

    import cosmos_reranker.finetuning.model as model_mod
    import huggingface_hub

    def fake_snapshot_download(repo_id, *args, **kwargs):  # noqa: ANN001, ANN202
        print(f"snapshot_download({repo_id}, {kwargs.get('allow_patterns', [])}) -> local {BASE}")
        return str(BASE)

    huggingface_hub.snapshot_download = fake_snapshot_download
    model_mod.snapshot_download = fake_snapshot_download

    temp = ROOT / "artifacts/models/.export-tmp"
    if temp.exists():
        raise SystemExit(f"Temp export dir exists: {temp}")
    temp.mkdir(parents=True)
    model_mod.export_vlm(BASE, temp)
    temp.rename(DEST)
    print(f"Exported VLM: {DEST}")


if __name__ == "__main__":
    sys.exit(main())

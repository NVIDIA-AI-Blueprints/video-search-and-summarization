# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Patch vLLM's Qwen3-Omni video-only processor compatibility bug.

The affected vLLM nightly unconditionally indexes ``use_audio_in_video`` in
the per-video multimodal fields, although that field is absent for normal
video-only requests. Treat a missing field as ``False``, matching the
Qwen2.5-Omni processor's handling.
"""

from __future__ import annotations

import os
from pathlib import Path


VLLM_ROOT = Path(os.environ.get("VLLM_ROOT", "/usr/local/lib/python3.12/dist-packages/vllm"))
TARGET = VLLM_ROOT / "model_executor/models/qwen3_omni_moe_thinker.py"
UNSAFE = 'if item and item["use_audio_in_video"].data:'
SAFE = 'if item and item.get("use_audio_in_video") and item["use_audio_in_video"].data:'


def apply_patch() -> None:
    content = TARGET.read_text(encoding="utf-8")
    if SAFE in content:
        print("  ✓ Qwen3-Omni audio patch already applied, skipping.")
        return
    if UNSAFE not in content:
        raise RuntimeError(f"PATCH ANCHOR NOT FOUND in {TARGET}: {UNSAFE!r}")
    TARGET.write_text(content.replace(UNSAFE, SAFE, 1), encoding="utf-8")
    print("  ✓ Qwen3-Omni video-only requests tolerate missing audio flag")


if __name__ == "__main__":
    print("Applying vLLM Qwen3-Omni audio-in-video compatibility patch...")
    apply_patch()
    print("Done.")

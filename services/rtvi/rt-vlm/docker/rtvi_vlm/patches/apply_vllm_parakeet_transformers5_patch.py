######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
######################################################################################################

"""Make vLLM's Parakeet config compatible with Transformers 5 dataclasses."""

from __future__ import annotations

import os
from pathlib import Path


VLLM_ROOT = Path(os.environ.get("VLLM_ROOT", "/usr/local/lib/python3.12/dist-packages/vllm"))
TARGET = VLLM_ROOT / "transformers_utils/configs/parakeet.py"
IMPORT = "from dataclasses import dataclass, field"
ORIGINAL_IMPORT = "from dataclasses import dataclass"
FIELDS = (
    "llm_hidden_size",
    "projection_hidden_size",
    "projection_bias",
    "sampling_rate",
)


def apply_patch() -> None:
    content = TARGET.read_text(encoding="utf-8")
    if IMPORT not in content:
        if ORIGINAL_IMPORT not in content:
            raise RuntimeError(f"PATCH ANCHOR NOT FOUND in {TARGET}: {ORIGINAL_IMPORT!r}")
        content = content.replace(ORIGINAL_IMPORT, IMPORT, 1)

    changed = False
    for name in FIELDS:
        original = f"    {name}: " + ("bool" if name == "projection_bias" else "int")
        replacement = original + " = field(kw_only=True)"
        if replacement in content:
            continue
        if original not in content:
            raise RuntimeError(f"PATCH ANCHOR NOT FOUND in {TARGET}: {original!r}")
        content = content.replace(original, replacement, 1)
        changed = True

    if not changed:
        print("  ✓ Parakeet Transformers 5 patch already applied, skipping.")
        return
    TARGET.write_text(content, encoding="utf-8")
    print("  ✓ Parakeet config supports Transformers 5 dataclass inheritance")


if __name__ == "__main__":
    print("Applying vLLM Parakeet Transformers 5 compatibility patch...")
    apply_patch()
    print("Done.")

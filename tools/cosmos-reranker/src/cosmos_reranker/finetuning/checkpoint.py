#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reassemble and verify the bundled fine-tuned CR3 adapters."""

from __future__ import annotations

import hashlib
from pathlib import Path


EXPECTED_SHA256 = "c6c994316eb1a727f349aa6ad0b6a84c52a88c9858fb952f95b8a9d6d341e750"
VISUAL_SHA256 = "a5e4724d5212d5a1a52df0f957d650e7988b7b23bfe4d3540fde1db402a0caaa"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def materialize(root: Path) -> None:
    parts = sorted((root / "language").glob("adapter_model.safetensors.part-*"))
    output = root / "language" / "adapter_model.safetensors"
    if len(parts) != 2 and not output.is_file():
        raise SystemExit(f"Expected two language adapter parts; found {len(parts)}")
    visual = root / "visual" / "adapter_model.safetensors"
    if sha256(visual) != VISUAL_SHA256:
        raise SystemExit(f"Visual adapter checksum mismatch: {visual}")
    if not output.exists() or sha256(output) != EXPECTED_SHA256:
        temporary = output.with_suffix(".safetensors.tmp")
        with temporary.open("wb") as destination:
            for part in parts:
                with part.open("rb") as source:
                    for block in iter(lambda: source.read(8 * 1024 * 1024), b""):
                        destination.write(block)
        if sha256(temporary) != EXPECTED_SHA256:
            temporary.unlink()
            raise SystemExit("Language adapter checksum mismatch after reconstruction")
        temporary.replace(output)
    print(f"Verified language adapter: {output}")
    print(f"Verified visual adapter: {visual}")

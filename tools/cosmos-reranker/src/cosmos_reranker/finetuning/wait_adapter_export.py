#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wait until a step checkpoint's joint LoRA adapter is safe to load."""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from safetensors import safe_open


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adapter-dir", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=1200)
    args = parser.parse_args()
    root = args.adapter_dir
    adapter = root / "adapter_model.safetensors"
    required = (adapter, root / "adapter_config.json", root / "model.safetensors.index.json")
    previous_size = None
    stable_checks = 0
    deadline = time.monotonic() + args.timeout_seconds
    while time.monotonic() < deadline:
        if all(path.is_file() and path.stat().st_size > 0 for path in required):
            size = adapter.stat().st_size
            stable_checks = stable_checks + 1 if size == previous_size else 0
            previous_size = size
            if stable_checks >= 2:
                try:
                    with safe_open(str(adapter), framework="pt", device="cpu") as saved:
                        keys = list(saved.keys())
                        if not keys:
                            raise ValueError("Adapter contains no tensors")
                        saved.get_tensor(keys[-1])
                    print(f"Complete adapter export: {adapter}")
                    return
                except Exception:
                    pass
        time.sleep(10)
    parser.error(f"Adapter export did not complete within {args.timeout_seconds}s: {adapter}")


if __name__ == "__main__":
    main()

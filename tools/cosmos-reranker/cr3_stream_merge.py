#!/usr/bin/env python3
"""Stream the visual LoRA merge of the fine-tuned CR3 checkpoint.

Replicates finetuning/merge_adapter.py (scale = alpha/r, merge fp32, save fp16)
without loading the whole model into RAM: shards are converted one at a time
and each consumed base shard is deleted immediately, so base + output never
coexist at full size. Output config keeps the fp16 dtype marker vLLM follows.
"""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier-Identifier: Apache-2.0

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

import torch
from safetensors.torch import load_file, save_file

MERGE_DTYPE = torch.float32
SAVE_DTYPE = torch.float16


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--adapter", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    base, adapter_dir, output = args.base, args.adapter, args.output
    if output.exists():
        raise SystemExit(f"Output exists: {output}")
    output.mkdir(parents=True)

    adapter_cfg = json.loads((adapter_dir / "adapter_config.json").read_text())
    scale = float(adapter_cfg["lora_alpha"]) / int(adapter_cfg["r"])
    if adapter_cfg.get("use_rslora"):
        scale /= float(adapter_cfg["r"]) ** 0.5
    assert not adapter_cfg.get("alpha_pattern") and not adapter_cfg.get("r_pattern")

    adapter = load_file(str(adapter_dir / "adapter_model.safetensors"))
    deltas: dict[str, torch.Tensor] = {}
    for key, tensor in adapter.items():
        if not key.startswith("base_model.model.") or ".lora_" not in key:
            continue
        module, kind = key[len("base_model.model.") :].rsplit(".lora_", 1)
        weight_name = f"{module}.weight"
        if kind.startswith("A"):
            b = adapter[f"base_model.model.{module}.lora_B.weight"].to(MERGE_DTYPE)
            delta = (b @ tensor.to(MERGE_DTYPE)) * scale
            if weight_name in deltas:
                deltas[weight_name] += delta
            else:
                deltas[weight_name] = delta
    patched = set(deltas)
    print(f"Visual adapter: {len(patched)} target weights, scale={scale}")

    index_path = base / "model.safetensors.index.json"
    if not index_path.is_file():
        shards = [base / "model.safetensors"]
    else:
        index = json.loads(index_path.read_text())
        shard_map = index["weight_map"]
        shards = [base / name for name in sorted(set(shard_map.values()))]
        shard_names = sorted(set(shard_map.values()))
        assert [p.name for p in shards] == shard_names

    total, patched_count = 0, 0
    for shard in shards:
        tensors = load_file(str(shard))
        for name, tensor in tensors.items():
            if name in deltas:
                tensors[name] = (tensor.to(MERGE_DTYPE) + deltas[name]).to(SAVE_DTYPE)
                patched_count += 1
            elif tensor.is_floating_point():
                tensors[name] = tensor.to(SAVE_DTYPE)
            total += 1
        save_file(tensors, str(output / shard.name), metadata={"format": "pt"})
        del tensors
        shard.unlink()  # free disk as we go
        print(f"shard {shard.name}: merged, base shard deleted")

    if index_path.is_file():
        shutil.copy2(index_path, output / index_path.name)
    for item in sorted(base.iterdir()):
        if item.suffix in {".safetensors", ".json"} and item.suffix == ".safetensors":
            continue
        if item.suffix == ".json" and item.name in {"model.safetensors.index.json"}:
            continue
        dest = output / item.name
        if item.is_dir():
            shutil.copytree(item, dest)
        else:
            shutil.copy2(item, dest)

    config_path = output / "config.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text())
        config["dtype"] = str(SAVE_DTYPE).removeprefix("torch.")
        config_path.write_text(json.dumps(config, indent=2))

    if patched_count != len(patched):
        raise SystemExit(f"Patched {patched_count} weights, expected {len(patched)}")
    print(f"Done: {total} tensors written to {output} ({patched_count} LoRA-patched)")


if __name__ == "__main__":
    main()

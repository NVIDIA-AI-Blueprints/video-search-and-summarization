# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download Nano weights and export the language/vision model used for training."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

MODEL_ID = "nvidia/Cosmos3-Nano"
_METADATA = (
    "config.json",
    "model.safetensors.index.json",
    "*tokenizer*",
    "*preprocessor*",
    "*chat_template*",
    "special_tokens_map.json",
    "added_tokens.json",
    "vocab.json",
    "merges.txt",
)
_ATTENTION_NAMES = {
    "to_q": "q_proj",
    "to_k": "k_proj",
    "to_v": "v_proj",
    "to_out": "o_proj",
    "norm_q": "q_norm",
    "norm_k": "k_norm",
}


def vlm_key(key: str, shard: str) -> str | None:
    """Keep Nano's understanding weights and rename them for its VLM architecture."""
    if shard.startswith("vision_encoder/"):
        return "model.visual." + key
    if not shard.startswith("transformer/"):
        return None
    if key == "lm_head.weight":
        return key
    if key.startswith(("embed_tokens.", "layers.", "norm.")):
        parts = key.split(".")
        if "self_attn" in parts:
            index = parts.index("self_attn") + 1
            parts[index] = _ATTENTION_NAMES.get(parts[index], parts[index])
        return "model.language_model." + ".".join(parts)
    return None


def export_vlm(snapshot: Path, destination: Path) -> None:
    """Export one source shard at a time; reject incomplete or mismatched weights."""
    import torch
    from safetensors import safe_open
    from safetensors.torch import save_file
    from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration

    source = json.loads((snapshot / "config.json").read_text())
    config = Qwen3VLConfig(
        text_config=source["text_config"],
        vision_config=source["vision_config"],
        **{
            key: source[key]
            for key in (
                "image_token_id",
                "video_token_id",
                "vision_start_token_id",
                "vision_end_token_id",
                "tie_word_embeddings",
            )
        },
    )
    config.architectures = ["Qwen3VLForConditionalGeneration"]
    config.torch_dtype = torch.bfloat16
    with torch.device("meta"):
        model = Qwen3VLForConditionalGeneration(config)
    shapes = {key: tuple(value.shape) for key, value in model.state_dict().items()}
    del model
    source_index = json.loads((snapshot / "model.safetensors.index.json").read_text())
    selected: dict[str, dict[str, str]] = {}
    covered = set()
    for key, shard in source_index["weight_map"].items():
        target = vlm_key(key, shard)
        if target not in shapes:
            continue
        if target in covered:
            raise ValueError(f"Duplicate Nano weight: {target}")
        covered.add(target)
        selected.setdefault(shard, {})[key] = target
    if covered != set(shapes):
        raise ValueError(f"Nano is missing VLM weights: {sorted(set(shapes) - covered)[:10]}")

    from huggingface_hub import snapshot_download

    snapshot_download(MODEL_ID, revision=snapshot.name, allow_patterns=sorted(selected))
    destination.mkdir(parents=True, exist_ok=True)
    output_index = {"metadata": {"total_size": 0}, "weight_map": {}}
    for number, (shard, names) in enumerate(sorted(selected.items()), 1):
        weights = {}
        with safe_open(snapshot / shard, framework="pt", device="cpu") as handle:
            for key, target in names.items():
                tensor = handle.get_tensor(key)
                if tuple(tensor.shape) != shapes[target]:
                    raise ValueError(f"Nano weight shape differs: {key}")
                weights[target] = tensor.to(torch.bfloat16).contiguous()
        filename = f"model-{number:05d}-of-{len(selected):05d}.safetensors"
        save_file(weights, destination / filename, metadata={"format": "pt"})
        output_index["weight_map"].update({key: filename for key in weights})
        output_index["metadata"]["total_size"] += sum(
            tensor.numel() * tensor.element_size() for tensor in weights.values()
        )
        del weights
    for path in snapshot.iterdir():
        if path.is_file() and path.name not in ("config.json", "model.safetensors.index.json"):
            shutil.copyfile(path, destination / path.name)
    config.save_pretrained(destination)
    (destination / "model.safetensors.index.json").write_text(json.dumps(output_index, indent=2))


def resolve_model(model: Path | None, root: Path) -> Path:
    """Reuse a local VLM export, or download and atomically prepare one."""
    from filelock import FileLock
    from huggingface_hub import snapshot_download

    destination = (model or root / "artifacts/models/Cosmos3-Nano-VLM").expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(destination) + ".lock"):
        if destination.exists():
            index_path = destination / "model.safetensors.index.json"
            if (destination / "config.json").is_file():
                if (destination / "model.safetensors").is_file():
                    return destination
                if index_path.is_file():
                    index = json.loads(index_path.read_text())
                    if index["weight_map"] and all(
                        (destination / shard).is_file()
                        for shard in set(index["weight_map"].values())
                    ):
                        return destination
            raise ValueError(f"Incomplete model directory; use a fresh destination: {destination}")
        snapshot = Path(snapshot_download(MODEL_ID, allow_patterns=list(_METADATA)))
        temporary = Path(tempfile.mkdtemp(prefix=destination.name + ".", dir=destination.parent))
        try:
            export_vlm(snapshot, temporary)
            temporary.rename(destination)
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)
    return destination

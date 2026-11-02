# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

import pytest
import torch
from safetensors.torch import save_file
from transformers import Qwen3VLConfig, Qwen3VLForConditionalGeneration

from cosmos_reranker.finetuning import model as assets


def test_export_preserves_every_language_and_vision_tensor(tmp_path, monkeypatch):
    config = Qwen3VLConfig(
        text_config={
            "hidden_size": 16,
            "intermediate_size": 32,
            "num_hidden_layers": 2,
            "num_attention_heads": 2,
            "num_key_value_heads": 2,
            "head_dim": 8,
            "vocab_size": 32,
            "rope_scaling": {"rope_type": "default", "mrope_section": [1, 1, 2]},
        },
        vision_config={
            "hidden_size": 16,
            "intermediate_size": 32,
            "depth": 2,
            "num_heads": 2,
            "out_hidden_size": 16,
            "num_position_embeddings": 16,
            "deepstack_visual_indexes": [0],
        },
        image_token_id=28,
        video_token_id=29,
        vision_start_token_id=30,
        vision_end_token_id=31,
    )
    model = Qwen3VLForConditionalGeneration(config).to(torch.bfloat16)
    expected = model.state_dict()
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / "config.json").write_text(config.to_json_string())
    (snapshot / "tokenizer.json").write_text("{}")
    mapping = {}
    tensors = {"transformer/language.safetensors": {}, "vision_encoder/model.safetensors": {}}
    inverse = {value: key for key, value in assets._ATTENTION_NAMES.items()}
    for key, tensor in expected.items():
        if key.startswith("model.visual."):
            shard = "vision_encoder/model.safetensors"
            source = key.removeprefix("model.visual.")
        else:
            shard = "transformer/language.safetensors"
            parts = key.removeprefix("model.language_model.").split(".")
            if "self_attn" in parts:
                position = parts.index("self_attn") + 1
                parts[position] = inverse.get(parts[position], parts[position])
            source = ".".join(parts)
        mapping[source] = shard
        tensors[shard][source] = tensor
    # Generation experts must not replace the understanding weights.
    mapping["layers.0.mlp_moe_gen.up_proj.weight"] = "transformer/generation.safetensors"
    mapping["layers.0.self_attn.add_q_proj.weight"] = "transformer/generation.safetensors"
    (snapshot / "model.safetensors.index.json").write_text(json.dumps({"weight_map": mapping}))
    for shard, weights in tensors.items():
        (snapshot / shard).parent.mkdir(exist_ok=True)
        save_file(weights, snapshot / shard)
    requests = []
    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda *a, **k: requests.append(k))
    output = tmp_path / "export"
    assets.export_vlm(snapshot, output)
    assert requests[0]["allow_patterns"] == sorted(tensors)
    restored = Qwen3VLForConditionalGeneration.from_pretrained(output, local_files_only=True)
    assert set(restored.state_dict()) == set(expected)
    for key, tensor in restored.state_dict().items():
        torch.testing.assert_close(tensor, expected[key].to(tensor.dtype), rtol=0, atol=0)
    assert (output / "tokenizer.json").is_file()


def test_existing_model_does_not_download(tmp_path, monkeypatch):
    path = tmp_path / "existing"
    path.mkdir()
    (path / "config.json").write_text("{}")
    (path / "model.safetensors").touch()
    monkeypatch.setattr(
        "huggingface_hub.snapshot_download", lambda *a, **k: pytest.fail("download")
    )
    assert assets.resolve_model(path, tmp_path) == path


def test_failed_export_is_not_reused(tmp_path, monkeypatch):
    monkeypatch.setattr("huggingface_hub.snapshot_download", lambda *a, **k: str(tmp_path))

    def fail_export(snapshot, output):
        (output / "config.json").touch()
        raise ValueError("incomplete source")

    monkeypatch.setattr(assets, "export_vlm", fail_export)
    path = tmp_path / "missing"
    with pytest.raises(ValueError, match="incomplete source"):
        assets.resolve_model(path, tmp_path)
    assert not path.exists()
    assert not list(tmp_path.glob("missing.*[!.lock]"))

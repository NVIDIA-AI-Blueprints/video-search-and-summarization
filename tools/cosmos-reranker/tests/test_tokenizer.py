# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from cosmos_reranker.evaluation.embedders.siglip_v2_checkpoint import resolve_tokenizer_dir


def test_tokenizer_downloads_only_assets_then_reuses_them(tmp_path, monkeypatch):
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    required = {"tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"}
    for filename in required:
        (snapshot / filename).write_text("{}")
    calls = []

    def download(repo_id, **kwargs):
        calls.append((repo_id, kwargs))
        return str(snapshot)

    monkeypatch.setattr("huggingface_hub.snapshot_download", download)
    local = tmp_path / "local"
    assert resolve_tokenizer_dir(local) == local
    assert len(calls) == 1
    assert calls[0][0] == "google/siglip2-so400m-patch16-256"
    assert set(calls[0][1]["allow_patterns"]) == required
    assert {p.name for p in local.iterdir()} == required
    assert resolve_tokenizer_dir(local) == local
    assert len(calls) == 1
    assert resolve_tokenizer_dir() == snapshot

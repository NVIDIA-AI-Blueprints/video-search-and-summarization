# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Offline checks for a cold model volume and container runtime configuration."""

import sys
from types import SimpleNamespace

import pytest

from model_entrypoint import ensure_snapshot
from worker import configure_vss


def test_pinned_download_is_cached(monkeypatch, tmp_path):
    calls = []
    location = tmp_path / "snapshot"
    lock = {"model_id": "LiquidAI/d1-2B", "revision": "pinned"}

    def download(**kwargs):
        calls.append(kwargs)
        location.mkdir()
        (location / "config.json").write_text("{}")
        (location / "model.safetensors").write_bytes(b"weights")

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=download))
    ensure_snapshot(lock, location)
    ensure_snapshot(lock, location)
    assert calls == [{"repo_id": "LiquidAI/d1-2B", "revision": "pinned", "local_dir": location}]


def test_incomplete_download_is_not_marked_ready(monkeypatch, tmp_path):
    location = tmp_path / "snapshot"
    location.mkdir()
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(snapshot_download=lambda **_: None))
    with pytest.raises(RuntimeError, match="configuration or weights"):
        ensure_snapshot({"model_id": "LiquidAI/d1-2B", "revision": "pinned"}, location)
    assert not (location / ".d1-snapshot-ready").exists()


def test_worker_configures_the_vss_cli_once(monkeypatch):
    calls = []
    monkeypatch.delenv("VSS_CLI", raising=False)
    monkeypatch.setattr("worker.subprocess.run", lambda argv, **kwargs: calls.append((argv, kwargs)))
    configure_vss("http://vss-ingress:7777")
    argv, kwargs = calls[0]
    assert argv == ["vss", "configure", "--base-url", "http://vss-ingress:7777"]
    assert kwargs["check"] is True
    with pytest.raises(SystemExit):
        configure_vss("http://user:password@vss-ingress:7777")  # pragma: allowlist secret; trufflehog:ignore (dummy rejection fixture)
    assert len(calls) == 1

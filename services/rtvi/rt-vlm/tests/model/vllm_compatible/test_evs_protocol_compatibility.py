######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material without an express license
# agreement from NVIDIA CORPORATION or its affiliates is strictly prohibited.
######################################################################################################

"""Regression coverage for the public EVS protocol compatibility layer."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
PATCH_PATH = (
    REPO_ROOT / "docker/rtvi_vlm/patches/apply_vllm_evs_protocol_patch.py"
)


def _load_patch_module():
    spec = importlib.util.spec_from_file_location("evs_protocol_patch_under_test", PATCH_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_protocol_patch_installs_session_types_idempotently(tmp_path, monkeypatch):
    """The public compatibility patch supplies the protected EVS API contract."""
    vllm_root = tmp_path / "vllm"
    protocol = vllm_root / "entrypoints/openai/engine/protocol.py"
    protocol.parent.mkdir(parents=True)
    protocol.write_text("# stock vLLM protocol\n")
    monkeypatch.setenv("VLLM_EVS_TARGET", str(vllm_root))

    patch = _load_patch_module()
    patch.main()
    first_install = protocol.read_text()

    for name in (
        "EvsAdvancedConfig",
        "VideoSessionSamplingParams",
        "VideoSessionCreateRequest",
        "VideoSessionCreateResponse",
        "VideoClipAddRequest",
        "VideoClipResponse",
        "build_session_sampling_params",
    ):
        assert f"{name}" in first_install

    patch.main()
    assert protocol.read_text() == first_install


@pytest.mark.no_gpu
def test_protected_evs_session_module_imports_with_protocol_compatibility():
    """The shipped EVS extension must import against the installed protocol API.

    This test runs in the built RT-VLM image.  The Dockerfile carries the same
    import as a build-time guard so a generic host test environment need not
    have the protected EVS extension installed.
    """
    pytest.importorskip("vllm", reason="requires a built RT-VLM image")

    from vllm.entrypoints.openai.engine.protocol import VideoClipAddRequest
    from vllm.entrypoints.openai.serving_video_sessions import OpenAIServingVideoSessions

    assert VideoClipAddRequest.__module__ == "vllm.entrypoints.openai.engine.protocol"
    assert OpenAIServingVideoSessions.__module__ == "vllm.entrypoints.openai.serving_video_sessions"

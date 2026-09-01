# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

_POLICY_PATH = Path(__file__).parents[1] / "src" / "vlm_pipeline" / "model_path_policy.py"
_PACKAGE_FILE_LISTS = {
    Path(__file__).parents[1]
    / "docker"
    / "alert_verification"
    / "package_file_list.txt": ("vlm_pipeline/model_path_policy.py"),
    Path(__file__).parents[1]
    / "docker"
    / "rtvi_embed"
    / "package_file_list.txt": "vlm_pipeline/model_path_policy.py",
    Path(__file__).parents[1]
    / "docker"
    / "rtvi_vlm"
    / "package_file_list.txt": ("vlm_pipeline/model_path_policy.py"),
    Path(__file__).parents[1]
    / "scripts"
    / "rt_embed_release_file_list.txt": "src/vlm_pipeline/model_path_policy.py",
    Path(__file__).parents[1]
    / "scripts"
    / "rt_vlm_release_file_list.txt": "src/vlm_pipeline/model_path_policy.py",
}
_SPEC = spec_from_file_location("model_path_policy", _POLICY_PATH)
assert _SPEC and _SPEC.loader
_POLICY = module_from_spec(_SPEC)
_SPEC.loader.exec_module(_POLICY)

validate_model_config = _POLICY.validate_model_config
validate_model_path_source = _POLICY.validate_model_path_source


def test_model_path_policy_is_included_in_all_runtime_package_manifests():
    for package_file_list, policy_path in _PACKAGE_FILE_LISTS.items():
        assert policy_path in package_file_list.read_text(encoding="utf-8")


def test_model_path_allowlist_is_opt_in(monkeypatch):
    monkeypatch.delenv("RTVI_ENFORCE_MODEL_PATH_ALLOWLIST", raising=False)
    monkeypatch.delenv("RTVI_MODEL_PATH_ALLOWLIST", raising=False)

    validate_model_path_source("git:https://huggingface.co/untrusted/model")


def test_model_path_allowlist_rejects_unlisted_source(monkeypatch):
    monkeypatch.setenv("RTVI_ENFORCE_MODEL_PATH_ALLOWLIST", "true")
    monkeypatch.setenv(
        "RTVI_MODEL_PATH_ALLOWLIST",
        "ngc:nim/nvidia/cosmos-reason2-8b:*\n"
        "git:https://huggingface.co/nvidia/Cosmos-Embed1-448p",
    )

    model_path = "git:https://token@example.com/untrusted/model"
    with pytest.raises(ValueError, match="not allowlisted") as exc_info:
        validate_model_path_source(model_path)
    assert model_path not in str(exc_info.value)


def test_model_path_allowlist_allows_matching_source(monkeypatch):
    monkeypatch.setenv("RTVI_MODEL_PATH_ALLOWLIST", "ngc:nim/nvidia/cosmos-reason2-8b:*")

    validate_model_path_source("ngc:nim/nvidia/cosmos-reason2-8b:0303-fp8-dynamic-kv8")


def test_trust_remote_code_requires_an_allowlist(monkeypatch):
    monkeypatch.delenv("RTVI_ENFORCE_MODEL_PATH_ALLOWLIST", raising=False)
    monkeypatch.delenv("RTVI_MODEL_PATH_ALLOWLIST", raising=False)
    monkeypatch.setenv("VLM_TRUST_REMOTE_CODE", "true")

    with pytest.raises(ValueError, match="RTVI_MODEL_PATH_ALLOWLIST"):
        validate_model_path_source("git:https://huggingface.co/nvidia/trusted-model")


def test_model_config_rejects_transformers_internal_kernel_fields(tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"text_config": {"_attn_implementation_internal": "evil/repo"}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="_attn_implementation_internal"):
        validate_model_config(str(tmp_path))


def test_unsafe_config_acknowledgement_without_remote_code_still_rejects(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"text_config": {"_attn_implementation_internal": "trusted/kernel"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("RTVI_ALLOW_UNSAFE_MODEL_CONFIG", "true")

    with pytest.raises(ValueError, match="_attn_implementation_internal"):
        validate_model_config(str(tmp_path))


def test_trusted_remote_code_allows_blocked_config_for_allowlisted_source(monkeypatch, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"text_config": {"_attn_implementation_internal": "trusted/kernel"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("VLM_TRUST_REMOTE_CODE", "true")
    monkeypatch.setenv("RTVI_ALLOW_UNSAFE_MODEL_CONFIG", "true")
    monkeypatch.setenv("RTVI_MODEL_PATH_ALLOWLIST", "git:https://huggingface.co/nvidia/*")

    validate_model_config(
        str(tmp_path), model_source="git:https://huggingface.co/nvidia/trusted-model"
    )

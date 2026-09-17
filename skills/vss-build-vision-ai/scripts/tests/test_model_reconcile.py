# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Behavior contract for .openclaw/vss-model-reconcile.py.

Pins the startup reconcile the harness image runs as the sandbox user (NemoClaw's
own root-gated reconcile no-ops there): name sync from the override env or the
gateway probe, the baked-vs-operator rule for contextWindow/maxTokens, hash
refresh, idempotence, and the fail-open runtime contract.
"""

import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_SPEC = importlib.util.spec_from_file_location(
    "vss_model_reconcile", REPO_ROOT / ".openclaw" / "vss-model-reconcile.py"
)
mr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mr)

BAKED_PRIMARY = "inference/nvidia/nemotron-3-super-120b-a12b"


def base_config():
    return {
        "agents": {"defaults": {"model": {"primary": BAKED_PRIMARY}}},
        "models": {
            "providers": {
                "inference": {
                    "models": [
                        {
                            "id": "nvidia/nemotron-3-super-120b-a12b",
                            "name": BAKED_PRIMARY,
                            "contextWindow": 131072,
                            "maxTokens": 4096,
                        }
                    ]
                }
            }
        },
    }


@pytest.fixture
def paths(tmp_path):
    config = tmp_path / "openclaw.json"
    hash_path = tmp_path / ".config-hash"
    baked = tmp_path / "baked-model.json"
    config.write_text(json.dumps(base_config(), indent=2))
    hash_path.write_text("stale\n")
    baked.write_text(json.dumps({
        "primary": BAKED_PRIMARY,
        "env_context_window": "131072",
        "env_max_tokens": "4096",
    }))
    return config, hash_path, baked


def run(paths, env=None, gateway=""):
    config, hash_path, baked = paths
    return mr.reconcile(
        config=str(config), hash_path=str(hash_path), baked=str(baked),
        env=env or {}, probe=lambda: gateway,
    )


def read(config):
    return json.loads(Path(config).read_text())


# --- name sync ------------------------------------------------------------------

def test_gateway_model_rewrites_names_and_drops_stale_limits(paths):
    assert run(paths, gateway="aws/anthropic/bedrock-claude-opus-4-8") is True
    cfg = read(paths[0])
    assert cfg["agents"]["defaults"]["model"]["primary"] == "inference/aws/anthropic/bedrock-claude-opus-4-8"
    first = cfg["models"]["providers"]["inference"]["models"][0]
    assert first["id"] == "aws/anthropic/bedrock-claude-opus-4-8"
    assert first["name"] == "inference/aws/anthropic/bedrock-claude-opus-4-8"
    # The baked limits belonged to nemotron; with no operator override they go.
    assert "contextWindow" not in first and "maxTokens" not in first


def test_override_env_wins_over_gateway(paths):
    assert run(paths, env={"NEMOCLAW_MODEL_OVERRIDE": "my/model"}, gateway="other/model") is True
    assert read(paths[0])["agents"]["defaults"]["model"]["primary"] == "inference/my/model"


def test_invalid_override_falls_back_to_gateway(paths):
    assert run(paths, env={"NEMOCLAW_MODEL_OVERRIDE": "bad\x01model"}, gateway="good/model") is True
    assert read(paths[0])["agents"]["defaults"]["model"]["primary"] == "inference/good/model"


def test_no_model_known_is_a_noop(paths):
    before = paths[0].read_text()
    assert run(paths, gateway="") is False
    assert paths[0].read_text() == before


def test_same_model_is_idempotent(paths):
    before = paths[0].read_text()
    assert run(paths, gateway="nvidia/nemotron-3-super-120b-a12b") is False
    assert paths[0].read_text() == before


# --- limits ---------------------------------------------------------------------

def test_operator_env_differing_from_baked_sets_limits(paths):
    env = {"NEMOCLAW_CONTEXT_WINDOW": "200000", "NEMOCLAW_MAX_TOKENS": "32000"}
    assert run(paths, env=env, gateway="aws/anthropic/bedrock-claude-opus-4-8") is True
    first = read(paths[0])["models"]["providers"]["inference"]["models"][0]
    assert first["contextWindow"] == 200000 and first["maxTokens"] == 32000


def test_env_equal_to_baked_is_not_an_operator_override(paths):
    # The base image bakes these ENVs, so equality means "not operator-set".
    env = {"NEMOCLAW_CONTEXT_WINDOW": "131072", "NEMOCLAW_MAX_TOKENS": "4096"}
    assert run(paths, env=env, gateway="aws/anthropic/bedrock-claude-opus-4-8") is True
    first = read(paths[0])["models"]["providers"]["inference"]["models"][0]
    assert "contextWindow" not in first and "maxTokens" not in first


def test_vss_env_equal_to_baked_is_still_honored(paths):
    # The greptile P1 case: operator wants exactly the baked value under a new
    # model. The VSS_OPENCLAW_* form is never baked, so it is unambiguous.
    env = {"VSS_OPENCLAW_MAX_TOKENS": "4096", "VSS_OPENCLAW_CONTEXT_WINDOW": "131072"}
    assert run(paths, env=env, gateway="aws/anthropic/bedrock-claude-opus-4-8") is True
    first = read(paths[0])["models"]["providers"]["inference"]["models"][0]
    assert first["contextWindow"] == 131072 and first["maxTokens"] == 4096


def test_vss_env_wins_over_nemoclaw_env(paths):
    env = {"VSS_OPENCLAW_MAX_TOKENS": "9000", "NEMOCLAW_MAX_TOKENS": "16000"}
    assert run(paths, env=env, gateway="aws/anthropic/bedrock-claude-opus-4-8") is True
    first = read(paths[0])["models"]["providers"]["inference"]["models"][0]
    assert first["maxTokens"] == 9000
    assert "contextWindow" not in first  # no override for it -> dropped


def test_limits_kept_without_baked_snapshot(paths):
    config, hash_path, baked = paths
    baked.unlink()
    assert run(paths, gateway="aws/anthropic/bedrock-claude-opus-4-8") is True
    first = read(config)["models"]["providers"]["inference"]["models"][0]
    # No snapshot -> cannot prove the limits are stale -> names only.
    assert first["contextWindow"] == 131072 and first["maxTokens"] == 4096


# --- hash + safety --------------------------------------------------------------

def test_hash_refreshed_to_sha256_of_config(paths):
    config, hash_path, _ = paths
    assert run(paths, gateway="new/model") is True
    digest = hashlib.sha256(config.read_bytes()).hexdigest()
    assert hash_path.read_text() == f"{digest}  {config}\n"


def test_symlinked_config_is_refused(paths, tmp_path):
    config, hash_path, baked = paths
    link = tmp_path / "link.json"
    link.symlink_to(config)
    assert mr.reconcile(config=str(link), hash_path=str(hash_path),
                        baked=str(baked), env={}, probe=lambda: "x/y") is False


def test_runtime_mode_fails_open(paths, monkeypatch):
    config, hash_path, baked = paths
    config.write_text("{not json")
    monkeypatch.setattr(mr, "CONFIG", str(config))
    monkeypatch.setattr(mr, "HASH", str(hash_path))
    monkeypatch.setattr(mr, "BAKED", str(baked))
    monkeypatch.setattr(mr, "gateway_model", lambda: "x/y")
    assert mr.main([]) == 0


# --- snapshot -------------------------------------------------------------------

def test_snapshot_records_primary_and_env(paths, tmp_path):
    config, _, _ = paths
    out = tmp_path / "etc" / "baked-model.json"
    mr.snapshot(config=str(config), baked=str(out),
                env={"NEMOCLAW_CONTEXT_WINDOW": "131072", "NEMOCLAW_MAX_TOKENS": "4096"})
    data = json.loads(out.read_text())
    assert data == {"primary": BAKED_PRIMARY,
                    "env_context_window": "131072", "env_max_tokens": "4096"}


def test_snapshot_fails_loudly_without_primary(tmp_path):
    config = tmp_path / "openclaw.json"
    config.write_text("{}")
    with pytest.raises(SystemExit):
        mr.snapshot(config=str(config), baked=str(tmp_path / "b.json"), env={})

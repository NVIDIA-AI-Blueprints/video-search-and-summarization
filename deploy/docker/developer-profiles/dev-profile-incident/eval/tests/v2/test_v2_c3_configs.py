# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""The frozen C3 configs: test half, final purpose, the freeze manifest's hash, and the pre-registered settings."""

import hashlib
import json
from pathlib import Path

import pytest

from evalv2.core.config import load_config

EVAL = Path(__file__).resolve().parents[2]
C3 = EVAL / "experiments" / "v2" / "c3"
MANIFEST = EVAL / "methodology" / "v2-c2-freeze-manifest.json"
NAMES = ("c3-track-a-switchyard", "c3-track-a-openrouter", "c3-track-b-historical", "c3-dashboard", "c3-chat")


def test_the_c3_directory_holds_exactly_the_registered_configs():
    assert sorted(p.stem for p in C3.glob("*.json")) == sorted(NAMES)


@pytest.mark.parametrize("name", NAMES)
def test_a_c3_config_is_a_final_test_half_run_naming_the_manifest(name):
    config = load_config(C3 / f"{name}.json")
    assert config.split.half == "test" and config.purpose == "final" and config.budget_mode == "hard_stop"
    if MANIFEST.exists():
        assert config.freeze_manifest == hashlib.sha256(MANIFEST.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", NAMES)
def test_the_manifest_pins_each_config_content(name):
    if not MANIFEST.exists():
        pytest.skip("manifest not built yet")
    raw = json.loads((C3 / f"{name}.json").read_text())
    blank = json.dumps(dict(raw, freeze_manifest=None), sort_keys=True, separators=(",", ":")).encode()
    assert json.loads(MANIFEST.read_text())["configs"][name]["content_sha256"] == hashlib.sha256(blank).hexdigest()


def test_pre_registered_settings():
    a = json.loads((C3 / "c3-track-a-switchyard.json").read_text())
    o = json.loads((C3 / "c3-track-a-openrouter.json").read_text())
    b = json.loads((C3 / "c3-track-b-historical.json").read_text())
    d = json.loads((C3 / "c3-dashboard.json").read_text())
    c = json.loads((C3 / "c3-chat.json").read_text())
    assert [s["model"] for s in a["subjects"] + o["subjects"]] == [
        "nvidia/cosmos-3-nano-reasoner", "nvidia/cosmos-3-super-reasoner", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "qwen/qwen3.5-9b", "google/gemma-4-31b-it"]
    assert a["repeats"] == o["repeats"] == 2 and b["repeats"] == d["repeats"] == c["repeats"] == 1
    assert {s["profile"] for s in b["subjects"]} == {"production-v4-captions"}
    assert d["datasets"] == {"dashboard_references": "dashboard-references-test-v1"}
    assert d["tasks"]["dashboard"]["options"]["request_shape"] == "agent-route-v3"
    assert c["tasks"]["chat"]["options"]["system"]["run"] == "v2-c3-track-b-historical"
    for cfg in (a, o, b):
        task = next(iter(cfg["tasks"].values()))
        assert task["options"]["judges"] == {"primary": "openrouter-gpt-oss-20b-c3-v1"}
    for s in o["subjects"]:
        prov = s["request"]["extra_body"]["provider"]
        assert prov["allow_fallbacks"] is False and prov["data_collection"] == "deny"

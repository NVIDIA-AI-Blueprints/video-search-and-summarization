# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Unit tests for pure helpers and Pydantic models in orchestrator/tools.py."""

from pydantic import ValidationError
import pytest

from vss_agents.orchestrator.tools import ContainerLogsInput
from vss_agents.orchestrator.tools import HardwareResolutionConfig
from vss_agents.orchestrator.tools import LruRegistry
from vss_agents.orchestrator.tools import ModelArtifactEntry
from vss_agents.orchestrator.tools import ModelPackageConfig
from vss_agents.orchestrator.tools import OrchestratorToolConfig
from vss_agents.orchestrator.tools import _truncate_text_to_max_bytes


class TestLruRegistry:
    def test_get_set_and_touch_updates_recency(self):
        registry: LruRegistry[str, int] = LruRegistry(max_entries=3)
        registry.set("a", 1)
        registry.set("b", 2)
        registry.set("c", 3)

        assert registry.get("a") == 1
        registry.set("d", 4)

        assert registry.peek("a") == 1
        assert registry.peek("b") is None
        assert registry.get("c") == 3
        assert registry.get("d") == 4

    def test_peek_does_not_update_recency(self):
        registry: LruRegistry[str, int] = LruRegistry(max_entries=2)
        registry.set("a", 1)
        registry.set("b", 2)

        assert registry.peek("a") == 1
        registry.set("c", 3)

        assert registry.peek("a") is None
        assert registry.get("b") == 2

    def test_get_without_touch_leaves_eviction_order(self):
        registry: LruRegistry[str, int] = LruRegistry(max_entries=2)
        registry.set("a", 1)
        registry.set("b", 2)

        assert registry.get("a", touch=False) == 1
        registry.set("c", 3)

        assert registry.peek("a") is None
        assert registry.get("b") == 2

    def test_can_evict_blocks_removal(self):
        registry: LruRegistry[str, str] = LruRegistry(
            max_entries=2,
            can_evict=lambda key, _value: key != "pinned",
        )
        registry.set("pinned", "keep")
        registry.set("other", "drop")

        registry.set("new", "value")

        assert registry.peek("pinned") == "keep"
        assert registry.peek("other") is None
        assert registry.peek("new") == "value"

    def test_touch_missing_key_is_noop(self):
        registry: LruRegistry[str, int] = LruRegistry(max_entries=2)
        registry.touch("missing")
        registry.set("a", 1)
        assert registry.get("a") == 1

    def test_evict_stops_when_no_evictable_entries(self):
        registry: LruRegistry[str, int] = LruRegistry(
            max_entries=1,
            can_evict=lambda _key, _value: False,
        )
        registry.set("a", 1)
        registry.set("b", 2)
        assert registry.peek("a") == 1
        assert registry.peek("b") == 2

    def test_values_exposes_live_entries(self):
        registry: LruRegistry[str, int] = LruRegistry(max_entries=2)
        registry.set("a", 1)
        registry.set("b", 2)
        assert list(registry.values()) == [1, 2]


class TestHardwareResolutionConfigCoercion:
    def test_coerce_validator_returns_non_dict_input_unchanged(self):
        assert HardwareResolutionConfig._coerce_null_overrides_to_empty("invalid") == "invalid"

    def test_coerce_validator_preserves_scalar_nested_values(self):
        coerced = HardwareResolutionConfig._coerce_null_overrides_to_empty(
            {
                "H100": {"MODE": "verification"},
            }
        )
        assert coerced == {"H100": {"MODE": "verification"}}


class TestTruncateTextToMaxBytes:
    def test_returns_original_when_under_limit(self):
        text = "hello world"
        assert _truncate_text_to_max_bytes(text, max_bytes=1024) == text

    def test_truncates_with_summary_suffix(self):
        text = "x" * 2000
        truncated = _truncate_text_to_max_bytes(text, max_bytes=100)
        assert len(truncated.encode("utf-8")) <= 100
        assert "[truncated, original size" in truncated

    def test_very_small_budget_returns_suffix_only(self):
        long_text = "hello world" * 50
        truncated = _truncate_text_to_max_bytes(long_text, max_bytes=10)
        assert len(truncated.encode("utf-8")) <= 10

    def test_utf8_safe_at_boundary(self):
        text = "café" * 500
        truncated = _truncate_text_to_max_bytes(text, max_bytes=64)
        truncated.encode("utf-8")
        assert "[truncated, original size" in truncated


class TestContainerLogsInput:
    def test_rejects_container_name_starting_with_dash(self):
        with pytest.raises(ValidationError, match="must not begin with '-'"):
            ContainerLogsInput(container_name="-evil")

    def test_accepts_valid_container_name(self):
        inp = ContainerLogsInput(container_name="vss-agent-1", tail=5)
        assert inp.container_name == "vss-agent-1"
        assert inp.tail == 5


class TestHardwareResolutionConfig:
    def test_coerces_null_overrides_to_empty_dict(self):
        cfg = HardwareResolutionConfig.model_validate(
            {
                "edge_profiles": ["DGX-SPARK"],
                "edge_allowed_profiles": ["base"],
                "edge_device_ids": {"llm": "0"},
                "hardware_profiles": {
                    "H100": None,
                    "OTHER": {"nested": None},
                },
            }
        )
        assert cfg.hardware_profiles["H100"] == {}
        assert cfg.hardware_profiles["OTHER"] == {"nested": {}}


class TestOrchestratorToolConfig:
    def test_rejects_unknown_model_artifact_profile(self):
        with pytest.raises(ValidationError, match="unsupported profile key"):
            OrchestratorToolConfig(
                deployments_dir="/tmp/deploy",
                source_compose_yaml="/tmp/compose.yml",
                source_env="/tmp/{profile}.env",
                mdx_data_dir="/tmp/mdx",
                output_dir="/tmp/out",
                mdx_data_directories=("models",),
                model_artifacts={
                    "not-a-profile": (
                        ModelPackageConfig(
                            package_ref="nvidia/pkg:1",
                            artifacts=(ModelArtifactEntry(src="a", out="b", kind="file"),),
                        ),
                    )
                },
                model_resolution={
                    "hardware": {
                        "edge_profiles": ["DGX-SPARK"],
                        "edge_allowed_profiles": ["base"],
                        "edge_device_ids": {"llm": "0"},
                        "hardware_profiles": {"H100": {}},
                    }
                },
            )

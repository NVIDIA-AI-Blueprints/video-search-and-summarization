# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for LVS benchmark GPU count/topology parsing (vss_perf_benchmark.py)."""

import pytest


@pytest.fixture
def bench_module():
    import vss_perf_benchmark as m

    return m


class TestSplitConfigId:
    def test_dash_separated(self, bench_module):
        assert bench_module._split_config_id("h100-4-2x2-cr2-8b") == [
            "h100",
            "4",
            "2x2",
            "cr2",
            "8b",
        ]

    def test_underscore_when_no_dash(self, bench_module):
        assert bench_module._split_config_id("lvs_tp1_9k") == ["lvs", "tp1", "9k"]

    def test_empty(self, bench_module):
        assert bench_module._split_config_id("") == []
        assert bench_module._split_config_id("   ") == []


class TestGpuCountFromConfig:
    @pytest.mark.parametrize(
        "config_id,expected",
        [
            ("h100-1-1-cr2-8b-rtvi-nemonano9b-9k", 1),
            ("h100-2-1x1-cr2-8b-rtvi-nemo3nano-9k", 2),
            ("h100-4-2x2-cr2-8b-rtvi-nemo3nano-9k", 4),
        ],
    )
    def test_ci_config_ids(self, bench_module, config_id, expected):
        assert bench_module._gpu_count_from_config(config_id, {}) == expected

    def test_explicit_global_gpu_count(self, bench_module):
        assert bench_module._gpu_count_from_config("h100-4-2x2-cr2-8b", {"gpu_count": 8}) == 8

    def test_vlm_llm_gpu_lists_fallback(self, bench_module):
        global_config = {"vlm_gpus": [0, 1], "llm_gpus": [2, 3]}
        assert bench_module._gpu_count_from_config("legacy-no-digit-id", global_config) == 4

    def test_vlm_llm_deduplicates(self, bench_module):
        global_config = {"vlm_gpus": [0], "llm_gpus": [0, 1]}
        assert bench_module._gpu_count_from_config("legacy-no-digit-id", global_config) == 2

    def test_raises_when_unknown(self, bench_module):
        with pytest.raises(ValueError, match="Cannot determine gpu_count"):
            bench_module._gpu_count_from_config("legacy-no-digit-id", {})


class TestGpuTopologyFromConfig:
    @pytest.mark.parametrize(
        "config_id,expected",
        [
            ("h100-1-1-cr2-8b-rtvi-nemonano9b-9k", "1"),
            ("h100-2-1x1-cr2-8b-rtvi-nemo3nano-9k", "1x1"),
            ("h100-4-2x2-cr2-8b-rtvi-nemo3nano-9k", "2x2"),
        ],
    )
    def test_ci_config_ids(self, bench_module, config_id, expected):
        assert bench_module._gpu_topology_from_config(config_id, {}) == expected

    def test_explicit_global_topology(self, bench_module):
        assert (
            bench_module._gpu_topology_from_config("h100-4-2x2-cr2-8b", {"topology": "4x4"})
            == "4x4"
        )

    def test_short_config_id_returns_empty(self, bench_module):
        assert bench_module._gpu_topology_from_config("h100-4", {}) == ""

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for perf config lookup and metadata helpers."""

from __future__ import annotations

from vss_perf_common import (
    gpu_family_prefix_from_smi,
    gpu_topology_from_assignments,
    load_perf_config_by_id,
    resolve_perf_config,
    resolve_perf_config_from_compose,
)


class TestGpuTopologyFromAssignments:
    def test_split_two_gpu(self):
        assert gpu_topology_from_assignments([0], [1]) == "1x1"

    def test_shared_single_gpu(self):
        assert gpu_topology_from_assignments([0], [0]) == "1"

    def test_four_gpu_split(self):
        assert gpu_topology_from_assignments([0, 1], [2, 3]) == "2x2"

    def test_empty(self):
        assert gpu_topology_from_assignments(None, None) == ""


class TestLoadPerfConfigById:
    def test_h100_two_gpu(self):
        row = load_perf_config_by_id("h100-2-1x1-cr3-nano-rtvi-nemo3nano-9k")
        assert row is not None
        assert row["vlmModel"] == "cosmos-reason3-nano"
        assert row["llmModel"] == "nemotron-3-nano"
        assert row["vlmGpus"] == "0"
        assert row["llmGpus"] == "1"

    def test_rtxpro6000_prefix(self):
        row = load_perf_config_by_id("rtxpro6000-2-1x1-cr3-nano-rtvi-nemo3nano-9k")
        assert row is not None
        assert row["nodeLabel"] == "RTXPRO6000BW-SE"
        assert row["enabled"] is True

    def test_unknown_id_returns_none(self):
        assert load_perf_config_by_id("does-not-exist") is None

    def test_empty_id_returns_none(self):
        assert load_perf_config_by_id("") is None


class TestResolvePerfConfigFromCompose:
    def test_h100_four_gpu(self):
        row = resolve_perf_config_from_compose(
            "compose/x86-rtvi-cr-nemo3-nano_4gpu.yaml",
            "NVIDIA H100 80GB HBM3",
        )
        assert row is not None
        assert row["id"] == "h100-4-2x2-cr3-nano-rtvi-nemo3nano-9k"

    def test_rtxpro6000_without_blackwell_suffix(self):
        row = resolve_perf_config_from_compose(
            "compose/x86-rtvi-cr-nemo3-nano_2gpu.yaml",
            "NVIDIA RTX PRO 6000",
        )
        assert row is not None
        assert row["id"] == "rtxpro6000-2-1x1-cr3-nano-rtvi-nemo3nano-9k"

    def test_rtxpro6000_blackwell_suffix(self):
        row = resolve_perf_config_from_compose(
            "compose/x86-rtvi-cr-nemo3-nano_2gpu.yaml",
            "NVIDIA RTX PRO 6000 Blackwell",
        )
        assert row is not None
        assert row["id"] == "rtxpro6000-2-1x1-cr3-nano-rtvi-nemo3nano-9k"

    def test_gpu_family_prefix_rtxpro4500_before_6000(self):
        assert gpu_family_prefix_from_smi("NVIDIA RTX PRO 4500") == "rtxpro4500"
        assert gpu_family_prefix_from_smi("NVIDIA RTX PRO 6000") == "rtxpro6000"

    def test_rtxpro4500_distinct_from_6000(self):
        row = resolve_perf_config_from_compose(
            "compose/x86-rtvi-cr-nemotron-9b_1gpu.yaml",
            "NVIDIA RTX PRO 4500 Blackwell",
        )
        assert row is not None
        assert row["id"] == "rtxpro4500-1-1-cr3-nano-rtvi-nemonano9b-9k"

    def test_compose_by_id_ci_path(self):
        row = resolve_perf_config(
            config_id="l40s-2-1x1-cr3-nano-rtvi-nemo3nano-9k",
            compose_path="compose/x86-rtvi-cr-nemo3-nano_2gpu.yaml",
            gpu_name="unused",
        )
        assert row is not None
        assert row["id"] == "l40s-2-1x1-cr3-nano-rtvi-nemo3nano-9k"

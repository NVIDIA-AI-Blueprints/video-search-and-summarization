# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression tests for paths changed by the service-owned perf layout."""

import warnings
from pathlib import Path

from vss_perf_common import perf_configs_path

with warnings.catch_warnings():
    warnings.simplefilter("ignore", FutureWarning)
    from base import BenchmarkBase


def test_defaults_load_from_service_perf_configs(monkeypatch):
    monkeypatch.delenv("PERF_DEFAULTS_YAML", raising=False)

    defaults = BenchmarkBase._load_defaults(object())

    assert "summarize_api_params" in defaults


def test_defaults_path_override(monkeypatch, tmp_path):
    defaults_path = tmp_path / "defaults.yaml"
    defaults_path.write_text("summarize_api_params:\n  prompt: test\n", encoding="utf-8")
    monkeypatch.setenv("PERF_DEFAULTS_YAML", str(defaults_path))

    defaults = BenchmarkBase._load_defaults(object())

    assert defaults["summarize_api_params"]["prompt"] == "test"


def test_perf_configs_uses_service_owned_path(monkeypatch):
    monkeypatch.delenv("PERF_CONFIGS_YAML", raising=False)

    path = perf_configs_path()

    assert path == Path(__file__).resolve().parents[2] / "perf" / "configs" / "perf-configs.yaml"
    assert path.is_file()

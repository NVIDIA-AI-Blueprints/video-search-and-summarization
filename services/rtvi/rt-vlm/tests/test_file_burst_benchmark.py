######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

# isort: skip_file

import sys
import types
from importlib.machinery import ModuleSpec
from pathlib import Path

import pytest
import requests

BENCHMARK_DIR = Path(__file__).resolve().parents[1] / "perf" / "benchmark"
sys.path.insert(0, str(BENCHMARK_DIR))

# The helpers tested here do not use pandas, but benchmark modules import it at
# module load time for report generation.
pd_stub = types.ModuleType("pandas")
pd_stub.DataFrame = object
pd_stub.__spec__ = ModuleSpec("pandas", loader=None)
sys.modules.setdefault("pandas", pd_stub)

from file_burst_benchmark import (  # noqa: E402
    FileBurstBenchmark,
    bool_config_value,
    count_failed_file_burst_requests,
    file_burst_results_success,
)
from base import BenchmarkCleanupError  # noqa: E402


def test_file_burst_results_success_requires_zero_failed_files():
    assert file_burst_results_success(
        [
            {"concurrency_level": 64, "failed_files": 0},
            {"concurrency_level": 128, "failed_files": 0},
        ]
    )

    assert not file_burst_results_success(
        [
            {"concurrency_level": 64, "failed_files": 0},
            {"concurrency_level": 128, "failed_files": 3},
        ]
    )


def test_file_burst_results_success_rejects_empty_results():
    assert not file_burst_results_success([])


def test_count_failed_file_burst_requests_sums_missing_as_zero():
    assert (
        count_failed_file_burst_requests(
            [
                {"concurrency_level": 1},
                {"concurrency_level": 16, "failed_files": 2},
                {"concurrency_level": 32, "failed_files": 1},
            ]
        )
        == 3
    )


def test_bool_config_value_parses_common_strings():
    assert bool_config_value(None, default=True)
    assert bool_config_value("true")
    assert bool_config_value("1")
    assert not bool_config_value("false")
    assert not bool_config_value("")


def test_file_burst_reuse_defaults_to_true_with_concurrency_sized_pool():
    benchmark = FileBurstBenchmark.__new__(FileBurstBenchmark)

    assert benchmark._reuse_uploaded_files({}, {}) is True
    assert benchmark._preupload_pool_size({}, {}, 64) == 64
    assert benchmark._preupload_pool_size({"preupload_file_pool_size": "8"}, {}, 64) == 8
    assert benchmark._preupload_pool_size({}, {"preupload_file_pool_size": 0}, 64) == 1


def test_upload_file_burst_asset_uses_configurable_timeout(monkeypatch):
    benchmark = FileBurstBenchmark.__new__(FileBurstBenchmark)
    benchmark.active_resources = []
    benchmark.DEFAULT_CONNECT_TIMEOUT = 10
    benchmark.DEFAULT_READ_TIMEOUT = 180
    benchmark.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    captured_calls = []

    class FakeResponse:
        def json(self):
            return {"id": "file-123"}

    def fake_make_api_call(endpoint, method="GET", files=None, timeout=None, **kwargs):
        captured_calls.append(
            {
                "endpoint": endpoint,
                "method": method,
                "files": files,
                "timeout": timeout,
            }
        )
        return FakeResponse()

    benchmark.make_api_call = fake_make_api_call
    monkeypatch.setenv("RTVI_BENCHMARK_FILE_UPLOAD_TIMEOUT_SEC", "12.5")

    file_id = benchmark._upload_file_burst_asset({"filepath": "/tmp/video.mp4"})

    assert file_id == "file-123"
    assert benchmark.active_resources == ["file_file-123"]
    assert captured_calls[0]["endpoint"] == "/files"
    assert captured_calls[0]["method"] == "POST"
    assert captured_calls[0]["timeout"] == (10, 12.5)
    assert captured_calls[0]["files"]["filename"][1] == "/tmp/video.mp4"


def test_file_upload_timeout_rejects_non_finite_values(monkeypatch):
    benchmark = FileBurstBenchmark.__new__(FileBurstBenchmark)
    benchmark.DEFAULT_READ_TIMEOUT = 180
    warnings = []
    benchmark.logger = types.SimpleNamespace(
        warning=lambda *args, **kwargs: warnings.append((args, kwargs))
    )

    for raw_timeout in ("nan", "inf", "-inf"):
        monkeypatch.setenv("RTVI_BENCHMARK_FILE_UPLOAD_TIMEOUT_SEC", raw_timeout)
        assert benchmark._file_upload_timeout_sec() == 180

    assert len(warnings) == 3


def test_file_burst_cleanup_keeps_resource_tracked_on_conflict():
    benchmark = FileBurstBenchmark.__new__(FileBurstBenchmark)
    benchmark.active_resources = ["file_file-123"]
    benchmark.logger = types.SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    benchmark._fetch_active_file_ids = lambda: {"file-123"}

    response = requests.Response()
    response.status_code = 409

    def fail_delete(*args, **kwargs):
        raise requests.exceptions.HTTPError(response=response)

    benchmark.make_api_call = fail_delete

    benchmark._delete_file_burst_asset("file-123")

    assert benchmark.active_resources == ["file_file-123"]


def test_file_burst_pool_cleanup_raises_after_attempting_all_assets():
    benchmark = FileBurstBenchmark.__new__(FileBurstBenchmark)
    benchmark.active_resources = ["file_file-1", "file_file-2"]
    attempted = []

    def delete_asset(file_id):
        attempted.append(file_id)
        if file_id == "file-2":
            benchmark.active_resources.remove("file_file-2")

    benchmark._delete_file_burst_asset = delete_asset

    with pytest.raises(BenchmarkCleanupError, match="1/2 file-burst assets remain"):
        benchmark._cleanup_uploaded_file_pool(["file-1", "file-2"])

    assert attempted == ["file-1", "file-2"]

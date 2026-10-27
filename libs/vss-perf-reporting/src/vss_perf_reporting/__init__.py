# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared reporting contract for VSS performance benchmarks."""

from .naming import generate_result_filename, generate_run_id, normalize_service_name
from .platform import (
    discover_platform,
    get_cpu_info,
    get_cuda_version,
    get_gpu_info,
    get_memory_gb,
    get_os_info,
)
from .result import build_and_save, build_result, validate_result
from .schema import (
    DEFAULT_GPU_MODEL_UNKNOWN,
    DEFAULT_PLATFORM_PLACEHOLDER,
    DEFAULT_SCHEMA_VERSION,
    DEFAULT_TRIGGERED_BY,
    BenchmarkMetadata,
    BenchmarkStatus,
    BenchmarkSummary,
    CPUInfo,
    GPUInfo,
    PlatformInfo,
    RunInfo,
    TriggerSource,
    VSSBenchmarkResult,
)
from .storage import (
    DEFAULT_MINIO_ACCESS_KEY,
    DEFAULT_MINIO_BUCKET,
    DEFAULT_MINIO_SECRET_KEY,
    MinIOUploader,
    get_minio_bucket,
    get_minio_endpoint,
    publish_result_file,
    upload_result_file,
)

__all__ = [name for name in globals() if not name.startswith("_")]

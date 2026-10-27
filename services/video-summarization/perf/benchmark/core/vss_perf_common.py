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

"""
COMMON: VSS perf results schema, result build, and MinIO upload. Used by any microservice (LVS, RTVI, etc.).
Each service has its own result adapter (e.g. vss_perf_lvs_adapter.py) that produces test_cases;
this module provides the shared schema, build_and_save, and upload.

- Pydantic: RunInfo, PlatformInfo (GPUInfo, CPUInfo), BenchmarkMetadata, BenchmarkSummary, VSSBenchmarkResult.
- Enums: TriggerSource, BenchmarkStatus.
- generate_run_id, build_result, build_and_save: build/write result JSON.
- discover_platform: optional system discovery (CPU, OS, GPU, CUDA) for platform dict.
- MinIOUploader, upload_result_file: upload result files to MinIO.

MinIO: MINIO_ENDPOINT is required unless an endpoint is passed explicitly.
Keys optional: default minioadmin/minioadmin if MINIO_ACCESS_KEY / MINIO_SECRET_KEY not set.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from minio import Minio
from minio.error import S3Error
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# Enums
# -----------------------------------------------------------------------------


class BenchmarkStatus(str, Enum):
    """Overall test execution status."""

    PASS = "PASS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"


class TriggerSource(str, Enum):
    """Source that triggered the benchmark run."""

    CI_PIPELINE = "ci_pipeline"
    MANUAL = "manual"
    SCHEDULED = "scheduled"
    SQA = "sqa"
    PERF_LAB = "perf-lab"


# -----------------------------------------------------------------------------
# Default configuration (single place for build_result / build_and_save / MinIO)
# -----------------------------------------------------------------------------
DEFAULT_SCHEMA_VERSION = "0.1"
DEFAULT_TRIGGERED_BY = TriggerSource.MANUAL.value  # "manual"
DEFAULT_GPU_MODEL_UNKNOWN = "Unknown"
DEFAULT_PLATFORM_PLACEHOLDER: Dict[str, Any] = {
    "gpu": {"model": DEFAULT_GPU_MODEL_UNKNOWN, "count": 0}
}
# MinIO endpoint is required via MINIO_ENDPOINT or an explicit function argument.
DEFAULT_MINIO_BUCKET = "perf-results"
DEFAULT_MINIO_ACCESS_KEY = "admin"
DEFAULT_MINIO_SECRET_KEY = "vss_automation"


# -----------------------------------------------------------------------------
# VSS result schema (Pydantic) – top-level result validation
# -----------------------------------------------------------------------------


class RunInfo(BaseModel):
    """Run execution info."""

    run_id: str = Field(..., description="Unique run id")
    timestamp: str = Field(..., description="ISO 8601 timestamp (UTC)")
    duration_seconds: Optional[float] = Field(None, description="Scenario duration (s)")
    triggered_by: str = Field(
        DEFAULT_TRIGGERED_BY,
        description="ci_pipeline | manual | scheduled | sqa | perf-lab",
    )
    pipeline_url: str = Field("", description="Optional CI pipeline URL")
    release: str = Field("", description="Release identifier (e.g. EA2)")


class GPUInfo(BaseModel):
    """GPU hardware info (optional fields for discovery)."""

    model: str = Field(DEFAULT_GPU_MODEL_UNKNOWN, description="GPU model name (e.g. H100, A100)")
    full_name: str = Field("", description="Full name from driver")
    count: int = Field(0, ge=0, description="Number of GPUs")
    memory_gb: Optional[float] = Field(None, ge=0, description="GPU memory per device (GB)")
    driver_version: str = Field("", description="NVIDIA driver version")
    cuda_version: str = Field("", description="CUDA toolkit version")


class CPUInfo(BaseModel):
    """CPU hardware info."""

    model: str = Field("", description="CPU model name")
    cores: int = Field(0, ge=0, description="Number of CPU cores")


class PlatformInfo(BaseModel):
    """Platform summary (GPU, optional CPU/OS/memory)."""

    gpu: GPUInfo = Field(
        default_factory=lambda: GPUInfo(model=DEFAULT_GPU_MODEL_UNKNOWN, count=0),
        description="GPU info",
    )
    cpu: Optional[CPUInfo] = Field(None, description="CPU info (optional)")
    memory_gb: Optional[float] = Field(None, ge=0, description="Total system memory (GB)")
    os: Optional[str] = Field(None, description="OS pretty name")


class BenchmarkMetadata(BaseModel):
    """Top-level metadata."""

    service: str = Field(..., description="Service name (e.g. LVS)")
    run_info: RunInfo = Field(..., description="Run info")
    platform: PlatformInfo = Field(default_factory=PlatformInfo)
    config: Dict[str, Any] = Field(default_factory=dict)
    benchmark_name: str = Field("", description="Scenario name")
    benchmark_mode: str = Field("", description="Execution mode (e.g. single_file, file_burst)")


class BenchmarkSummary(BaseModel):
    """Aggregate summary."""

    total_tests: int = Field(0, ge=0)
    passed: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    pass_rate: float = Field(0.0, ge=0, le=100)
    overall_status: str = Field("PASS", description="PASS | FAIL | PARTIAL")

    @model_validator(mode="after")
    def validate_totals(self) -> "BenchmarkSummary":
        if self.total_tests and self.passed + self.failed != self.total_tests:
            raise ValueError(
                f"passed ({self.passed}) + failed ({self.failed}) must equal total_tests ({self.total_tests})"
            )
        return self


class VSSBenchmarkResult(BaseModel):
    """Root VSS benchmark result. test_cases from service adapter (e.g. vss_perf_lvs_adapter)."""

    schema_version: str = Field("0.1", description="Schema version")
    metadata: BenchmarkMetadata = Field(..., description="Metadata")
    summary: BenchmarkSummary = Field(..., description="Summary")
    test_cases: List[Dict[str, Any]] = Field(
        default_factory=list, description="Test cases from service adapter"
    )

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON write."""
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VSSBenchmarkResult":
        """Validate and load from dict (e.g. json.load)."""
        return cls.model_validate(data)

    @classmethod
    def load_from_file(cls, path: Union[str, Path]) -> "VSSBenchmarkResult":
        """Load and validate from JSON file."""
        path = Path(path)
        with open(path, "r") as f:
            return cls.model_validate(json.load(f))


def get_minio_endpoint() -> str:
    """Return the required MinIO host:port supplied by the environment."""
    endpoint = os.environ.get("MINIO_ENDPOINT", "").strip()
    if not endpoint:
        raise ValueError("MINIO_ENDPOINT is required for MinIO uploads")
    return endpoint


def get_minio_bucket() -> str:
    """Bucket name. Use MINIO_BUCKET env or default."""
    return os.environ.get("MINIO_BUCKET", DEFAULT_MINIO_BUCKET)


def generate_run_id(service: str, config_id: str = "", *, suffix: str = "") -> str:
    """Standard run_id: {service}_{config_id}_{YYYYMMDD}_{HHMMSS}."""
    now = datetime.now(timezone.utc)
    safe = service.replace(" ", "-").replace("_", "-").lower()
    parts = [safe]
    if config_id:
        parts.append(config_id.strip().replace(" ", "-"))
    parts.append(now.strftime("%Y%m%d"))
    parts.append(now.strftime("%H%M%S"))
    run_id = "_".join(parts)
    return f"{run_id}_{suffix.strip()}" if suffix else run_id


# -----------------------------------------------------------------------------
# Optional system discovery (CPU, OS, GPU, CUDA)
# -----------------------------------------------------------------------------


def get_os_info() -> str:
    """Return OS pretty name from /etc/os-release when available."""
    try:
        with open("/etc/os-release", "r") as f:
            data = f.read()
        match = re.search(r'PRETTY_NAME="([^"]+)"', data)
        return match.group(1) if match else data.strip().splitlines()[0]
    except Exception:
        return ""


def get_cpu_info() -> Dict[str, Any]:
    """Return CPU model and core count (from /proc/cpuinfo)."""
    model, cores = "", 0
    try:
        with open("/proc/cpuinfo", "r") as f:
            for line in f:
                if line.startswith("model name") and not model:
                    model = line.split(":", 1)[1].strip()
                if line.startswith("processor"):
                    cores += 1
    except Exception:
        pass
    return {"model": model, "cores": cores}


def get_memory_gb() -> float:
    """Return total system memory in GB (from /proc/meminfo)."""
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal"):
                    kb = float(line.split(":")[1].strip().split()[0])
                    return round(kb / (1024 * 1024), 2)
    except Exception:
        pass
    return 0.0


def get_cuda_version() -> str:
    """Return CUDA version from nvidia-smi if available."""
    try:
        out = subprocess.check_output(["nvidia-smi"], text=True)
        match = re.search(r"CUDA Version:\s*([0-9.]+)", out)
        return match.group(1) if match else ""
    except Exception:
        return ""


# -----------------------------------------------------------------------------
# perf-configs.yaml lookup (perf/configs/perf-configs.yaml)
# -----------------------------------------------------------------------------

_PERF_CONFIGS_CACHE: Optional[List[Dict[str, Any]]] = None


def _perf_root() -> Path:
    """Service-owned perf root containing benchmark/, configs/, and setup/."""
    return Path(__file__).resolve().parents[2]


def perf_configs_path() -> Path:
    """Path to the service-owned perf-configs.yaml."""
    override = os.environ.get("PERF_CONFIGS_YAML", "").strip()
    if override:
        return Path(override)
    return _perf_root() / "configs" / "perf-configs.yaml"


def _normalize_compose_path(compose_path: str) -> str:
    """Normalize to compose/<basename> for perf-configs composePath matching."""
    text = (compose_path or "").strip().replace("\\", "/")
    if not text:
        return ""
    if text.startswith("compose/"):
        return text
    name = Path(text).name
    return f"compose/{name}" if name else text


def load_perf_configs(*, refresh: bool = False) -> List[Dict[str, Any]]:
    """Load all rows from the service-owned perf-configs.yaml."""
    global _PERF_CONFIGS_CACHE
    if _PERF_CONFIGS_CACHE is not None and not refresh:
        return _PERF_CONFIGS_CACHE
    import yaml

    path = perf_configs_path()
    if not path.is_file():
        raise FileNotFoundError(f"perf-configs not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    rows = data.get("perf_configs") or []
    if not isinstance(rows, list):
        raise ValueError(f"perf_configs must be a list in {path}")
    _PERF_CONFIGS_CACHE = rows
    return rows


def load_perf_config_by_id(config_id: str) -> Optional[Dict[str, Any]]:
    """Return one perf_configs row by canonical id, or None."""
    cid = (config_id or "").strip()
    if not cid:
        return None
    for row in load_perf_configs():
        if str(row.get("id", "")).strip() == cid:
            return row
    return None


def get_nvidia_smi_gpu_name(device_index: int = 0) -> str:
    """Return GPU device name from nvidia-smi (GPU 0 by default)."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                f"--query-gpu=name",
                "--format=csv,noheader,nounits",
                f"-i",
                str(device_index),
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip().splitlines()[0].strip() if out.strip() else ""
    except Exception:
        return ""


def gpu_family_prefix_from_smi(gpu_name: str) -> str:
    """Map nvidia-smi GPU name to perf_configs id prefix (e.g. h100, rtxpro6000)."""
    n = (gpu_name or "").lower()
    if "rtx pro 4500" in n:
        return "rtxpro4500"
    if "rtx pro 6000" in n:
        return "rtxpro6000"
    if "gh200" in n:
        return "gh200"
    if "h200" in n:
        return "h200"
    if "gb200" in n or "grace blackwell" in n:
        return "gb200"
    if "b200" in n:
        return "b200"
    if "h100" in n:
        return "h100"
    if "l40s" in n:
        return "l40s"
    if "gb10" in n or "spark" in n:
        return "spark"
    if "t5000" in n or "thor" in n:
        return "thor"
    return ""


def gpu_topology_from_assignments(
    vlm_gpus: Optional[List[int]],
    llm_gpus: Optional[List[int]],
) -> str:
    """Derive topology from VLM/LLM GPU index lists (e.g. [0],[1] -> 1x1; [0],[0] -> 1)."""
    if not vlm_gpus or not llm_gpus:
        return ""
    vlm_set = set(vlm_gpus)
    llm_set = set(llm_gpus)
    if vlm_set == llm_set and len(vlm_set) == 1:
        return "1"
    return f"{len(vlm_set)}x{len(llm_set)}"


def _parse_gpu_indices_csv(value: Any) -> List[int]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [int(x.strip()) for x in text.split(",") if x.strip()]


def resolve_perf_config_from_compose(
    compose_path: str,
    gpu_name: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Resolve a perf_configs row from compose path + GPU family (id prefix from nvidia-smi).
    Returns None when no row matches.
    """
    compose_norm = _normalize_compose_path(compose_path)
    if not compose_norm:
        return None

    prefix = gpu_family_prefix_from_smi(gpu_name)
    if not prefix:
        return None

    for row in load_perf_configs():
        if _normalize_compose_path(str(row.get("composePath", ""))) != compose_norm:
            continue
        row_id = str(row.get("id", ""))
        if row_id.startswith(f"{prefix}-"):
            return row
    return None


def resolve_perf_config(
    *,
    config_id: str = "",
    compose_path: str = "",
    gpu_name: str = "",
) -> Optional[Dict[str, Any]]:
    """Resolve perf row by config_id first, else compose+GPU."""
    cid = (config_id or "").strip()
    if cid:
        row = load_perf_config_by_id(cid)
        if row:
            return row
    if compose_path:
        return resolve_perf_config_from_compose(compose_path, gpu_name=gpu_name)
    return None


def perf_row_metadata(
    row: Dict[str, Any],
    *,
    vlm_gpus: Optional[List[int]] = None,
    llm_gpus: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Extract canonical JSON metadata fields from a perf_configs row."""
    vlm = vlm_gpus if vlm_gpus is not None else _parse_gpu_indices_csv(row.get("vlmGpus"))
    llm = llm_gpus if llm_gpus is not None else _parse_gpu_indices_csv(row.get("llmGpus"))
    topo = gpu_topology_from_assignments(vlm, llm)
    return {
        "config_id": str(row.get("id", "")).strip(),
        "vlm_model": str(row.get("vlmModel", "")).strip(),
        "llm_model": str(row.get("llmModel", "")).strip(),
        "vision_input_tokens": str(row.get("vision_input_tokens", "9k")).strip(),
        "gpu_topology": topo,
        "vlm_gpu_ids": vlm,
        "llm_gpu_ids": llm,
    }


def get_gpu_info() -> Dict[str, Any]:
    """Return GPU info from NVML (pynvml) for first device, or minimal dict on failure."""
    try:
        import pynvml

        pynvml.nvmlInit()
        count = pynvml.nvmlDeviceGetCount()
        if count == 0:
            return {"model": "", "full_name": "", "count": 0, "memory_gb": 0, "driver_version": ""}
        handle0 = pynvml.nvmlDeviceGetHandleByIndex(0)
        full_name = pynvml.nvmlDeviceGetName(handle0)
        if isinstance(full_name, bytes):
            full_name = full_name.decode("utf-8", errors="replace")
        driver_version = pynvml.nvmlSystemGetDriverVersion()
        if isinstance(driver_version, bytes):
            driver_version = driver_version.decode("utf-8", errors="replace")
        model = (full_name.split()[1] if full_name else "").upper()
        try:
            mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle0)
            memory_gb = round(mem_info.total / (1024**3), 2)
        except pynvml.NVMLError_NotSupported:
            memory_gb = None
        return {
            "model": model,
            "full_name": full_name,
            "count": count,
            "memory_gb": memory_gb,
            "driver_version": driver_version,
        }
    except Exception:
        return {"model": "", "full_name": "", "count": 0, "memory_gb": 0, "driver_version": ""}


def discover_platform(
    config_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Build a platform dict from system discovery (OS, CPU, memory, GPU, CUDA).
    """
    gpu = get_gpu_info()
    cuda = get_cuda_version()
    if cuda:
        gpu["cuda_version"] = cuda
    cpu = get_cpu_info()
    memory_gb = get_memory_gb()
    os_name = get_os_info()
    return {
        "gpu": gpu,
        "cpu": cpu,
        "memory_gb": memory_gb,
        "os": os_name,
    }


def build_result(
    service: str,
    run_id: str,
    test_cases: List[Dict[str, Any]],
    *,
    timestamp: Optional[str] = None,
    platform: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    benchmark_name: str = "",
    benchmark_mode: str = "",
    triggered_by: str = DEFAULT_TRIGGERED_BY,
    pipeline_url: str = "",
    duration_seconds: Optional[float] = None,
    release: str = "",
    passed: Optional[int] = None,
    failed: Optional[int] = None,
) -> Dict[str, Any]:
    """Build standard VSS result dict (schema_version from DEFAULT_SCHEMA_VERSION)."""
    now = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = len(test_cases)
    p = passed if passed is not None else total
    f = failed if failed is not None else 0
    total_tests = p + f  # validator requires passed+failed==total_tests
    pass_rate = round(p / total_tests * 100, 1) if total_tests > 0 else 0
    if f == 0 and total_tests > 0:
        overall_status = BenchmarkStatus.PASS.value
    elif p == 0:
        overall_status = BenchmarkStatus.FAIL.value
    else:
        overall_status = BenchmarkStatus.PARTIAL.value
    run_info: Dict[str, Any] = {
        "run_id": run_id,
        "timestamp": now,
        "duration_seconds": duration_seconds,
        "triggered_by": triggered_by,
        "release": release,
    }
    if pipeline_url:
        run_info["pipeline_url"] = pipeline_url
    plat = platform or DEFAULT_PLATFORM_PLACEHOLDER
    metadata: Dict[str, Any] = {
        "service": service,
        "run_info": run_info,
        "platform": plat,
        "config": config or {},
        "benchmark_name": benchmark_name,
        "benchmark_mode": benchmark_mode,
    }
    result = {
        "schema_version": DEFAULT_SCHEMA_VERSION,
        "metadata": metadata,
        "summary": {
            "total_tests": total_tests,
            "passed": p,
            "failed": f,
            "pass_rate": pass_rate,
            "overall_status": overall_status,
        },
        "test_cases": test_cases,
    }
    # Validate shape with Pydantic (raises on invalid)
    VSSBenchmarkResult.model_validate(result)
    return result


def build_and_save(
    output_path: str | Path,
    service: str,
    test_cases: List[Dict[str, Any]],
    *,
    config_id: str = "",
    platform: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
    run_id: Optional[str] = None,
    benchmark_name: str = "",
    benchmark_mode: str = "",
    triggered_by: str = DEFAULT_TRIGGERED_BY,
    pipeline_url: str = "",
    duration_seconds: Optional[float] = None,
    release: str = "",
    passed: Optional[int] = None,
    failed: Optional[int] = None,
) -> Path:
    """Write standard VSS result JSON to file. Returns path."""
    path = Path(output_path)
    rid = run_id or generate_run_id(service, config_id)
    if path.suffix != ".json":
        path = path / f"{rid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    result = build_result(
        service=service,
        run_id=rid,
        test_cases=test_cases,
        platform=platform,
        config=config,
        benchmark_name=benchmark_name,
        benchmark_mode=benchmark_mode,
        triggered_by=triggered_by,
        pipeline_url=pipeline_url,
        duration_seconds=duration_seconds,
        release=release,
        passed=passed,
        failed=failed,
    )
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    return path


# -----------------------------------------------------------------------------
# MinIO upload
# -----------------------------------------------------------------------------


class MinIOUploader:
    """Upload benchmark result files to MinIO object storage."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        access_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        secure: bool = False,
    ):
        self.endpoint = endpoint if endpoint is not None else get_minio_endpoint()
        self.access_key = access_key or os.getenv("MINIO_ACCESS_KEY", DEFAULT_MINIO_ACCESS_KEY)
        self.secret_key = secret_key or os.getenv("MINIO_SECRET_KEY", DEFAULT_MINIO_SECRET_KEY)
        self.secure = secure
        try:
            self.client = Minio(
                endpoint=self.endpoint,
                access_key=self.access_key,
                secret_key=self.secret_key,
                secure=self.secure,
            )
            logger.debug("Initialized MinIO client for %s", self.endpoint)
        except Exception as e:
            logger.error("Failed to initialize MinIO client: %s", e)
            raise

    def upload_file(
        self,
        local_file_path: str,
        bucket_name: str,
        object_name: Optional[str] = None,
        content_type: str = "application/json",
    ) -> bool:
        local_path = Path(local_file_path)
        if not local_path.exists():
            logger.error("Local file does not exist: %s", local_file_path)
            return False
        if object_name is None:
            object_name = local_path.name
        try:
            if not self.client.bucket_exists(bucket_name):
                logger.warning("Bucket %r does not exist. Creating...", bucket_name)
                self.client.make_bucket(bucket_name)
            self.client.fput_object(
                bucket_name=bucket_name,
                object_name=object_name,
                file_path=str(local_path),
                content_type=content_type,
            )
            logger.info("Uploaded to MinIO: s3://%s/%s", bucket_name, object_name)
            return True
        except S3Error as e:
            logger.error("MinIO S3 error during upload: %s", e)
            return False
        except Exception as e:
            logger.error("Failed to upload file to MinIO: %s", e)
            return False

    def upload_benchmark_result(
        self,
        local_file_path: str,
        service: str,
        bucket_name: Optional[str] = None,
    ) -> bool:
        if bucket_name is None:
            bucket_name = get_minio_bucket()
        local_path = Path(local_file_path)
        object_name = f"{service}/{local_path.name}"
        return self.upload_file(
            local_file_path=str(local_path),
            bucket_name=bucket_name,
            object_name=object_name,
            content_type="application/json",
        )

    def list_objects(self, bucket_name: str, prefix: str = "") -> list:
        try:
            objects = self.client.list_objects(bucket_name, prefix=prefix, recursive=True)
            return [obj.object_name for obj in objects]
        except S3Error as e:
            logger.error("Failed to list objects: %s", e)
            return []


def upload_result_file(
    local_file_path: str,
    service: str,
    endpoint: Optional[str] = None,
    bucket: Optional[str] = None,
    access_key: Optional[str] = None,
    secret_key: Optional[str] = None,
) -> bool:
    """
    Upload a VSS result JSON file to MinIO.
    Uses MINIO_ENDPOINT, MINIO_BUCKET, MINIO_ACCESS_KEY, MINIO_SECRET_KEY from env when not passed.
    """
    try:
        uploader = MinIOUploader(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
        )
        return uploader.upload_benchmark_result(
            local_file_path=local_file_path,
            service=service,
            bucket_name=bucket,
        )
    except Exception as e:
        logger.error("Failed to upload to MinIO: %s", e)
        return False


# Keep this service module as a compatibility facade while reporting is shared.
# The full VSS checkout is available both locally and on Jenkins bare-metal
# nodes, so load the repository-owned package directly without requiring a
# separately published wheel. LVS-only perf-config resolution helpers above
# deliberately remain service-owned.
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SHARED_REPORTING_SRC = _REPO_ROOT / "libs" / "vss-perf-reporting" / "src"
if str(_SHARED_REPORTING_SRC) not in sys.path:
    sys.path.insert(0, str(_SHARED_REPORTING_SRC))

from vss_perf_reporting import (  # noqa: E402
    DEFAULT_GPU_MODEL_UNKNOWN as _SHARED_DEFAULT_GPU_MODEL_UNKNOWN,
    DEFAULT_MINIO_ACCESS_KEY as _SHARED_DEFAULT_MINIO_ACCESS_KEY,
    DEFAULT_MINIO_BUCKET as _SHARED_DEFAULT_MINIO_BUCKET,
    DEFAULT_MINIO_SECRET_KEY as _SHARED_DEFAULT_MINIO_SECRET_KEY,
    DEFAULT_PLATFORM_PLACEHOLDER as _SHARED_DEFAULT_PLATFORM_PLACEHOLDER,
    DEFAULT_SCHEMA_VERSION as _SHARED_DEFAULT_SCHEMA_VERSION,
    DEFAULT_TRIGGERED_BY as _SHARED_DEFAULT_TRIGGERED_BY,
    BenchmarkMetadata as _SharedBenchmarkMetadata,
    BenchmarkStatus as _SharedBenchmarkStatus,
    BenchmarkSummary as _SharedBenchmarkSummary,
    CPUInfo as _SharedCPUInfo,
    GPUInfo as _SharedGPUInfo,
    MinIOUploader as _SharedMinIOUploader,
    PlatformInfo as _SharedPlatformInfo,
    RunInfo as _SharedRunInfo,
    TriggerSource as _SharedTriggerSource,
    VSSBenchmarkResult as _SharedVSSBenchmarkResult,
    build_and_save as _shared_build_and_save,
    build_result as _shared_build_result,
    generate_run_id as _shared_generate_run_id,
    get_minio_bucket as _shared_get_minio_bucket,
    get_minio_endpoint as _shared_get_minio_endpoint,
    upload_result_file as _shared_upload_result_file,
)

BenchmarkStatus = _SharedBenchmarkStatus
TriggerSource = _SharedTriggerSource
RunInfo = _SharedRunInfo
GPUInfo = _SharedGPUInfo
CPUInfo = _SharedCPUInfo
PlatformInfo = _SharedPlatformInfo
BenchmarkMetadata = _SharedBenchmarkMetadata
BenchmarkSummary = _SharedBenchmarkSummary
VSSBenchmarkResult = _SharedVSSBenchmarkResult
DEFAULT_SCHEMA_VERSION = _SHARED_DEFAULT_SCHEMA_VERSION
DEFAULT_TRIGGERED_BY = _SHARED_DEFAULT_TRIGGERED_BY
DEFAULT_GPU_MODEL_UNKNOWN = _SHARED_DEFAULT_GPU_MODEL_UNKNOWN
DEFAULT_PLATFORM_PLACEHOLDER = _SHARED_DEFAULT_PLATFORM_PLACEHOLDER
DEFAULT_MINIO_BUCKET = _SHARED_DEFAULT_MINIO_BUCKET
DEFAULT_MINIO_ACCESS_KEY = _SHARED_DEFAULT_MINIO_ACCESS_KEY
DEFAULT_MINIO_SECRET_KEY = _SHARED_DEFAULT_MINIO_SECRET_KEY
generate_run_id = _shared_generate_run_id
build_result = _shared_build_result
build_and_save = _shared_build_and_save
get_minio_endpoint = _shared_get_minio_endpoint
get_minio_bucket = _shared_get_minio_bucket
MinIOUploader = _SharedMinIOUploader
upload_result_file = _shared_upload_result_file


# -----------------------------------------------------------------------------
# CLI: python vss_perf_common.py file.json --service LVS
# -----------------------------------------------------------------------------


def _upload_cli_main() -> None:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(description="Upload VSS result JSON file(s) to MinIO")
    parser.add_argument("files", nargs="+", help="JSON file(s) to upload")
    parser.add_argument("--service", required=True, help="Service name (e.g. LVS)")
    parser.add_argument(
        "--endpoint", default=None, help="MinIO endpoint (required here or via MINIO_ENDPOINT)"
    )
    parser.add_argument("--bucket", default=None, help="MinIO bucket (default: MINIO_BUCKET env)")
    parser.add_argument(
        "--triggered-by",
        default=None,
        choices=[t.value for t in TriggerSource],
        help="Stamp metadata.run_info.triggered_by in each JSON before upload "
        "(default: keep whatever is in the file).",
    )
    args = parser.parse_args()
    ok = 0
    for f in args.files:
        if args.triggered_by:
            try:
                with open(f, "r") as fh:
                    data = json.load(fh)
                data.setdefault("metadata", {}).setdefault("run_info", {})[
                    "triggered_by"
                ] = args.triggered_by
                with open(f, "w") as fh:
                    json.dump(data, fh, indent=2)
            except Exception as e:
                logger.error("Failed to stamp triggered_by on %s: %s", f, e)
                continue
        if upload_result_file(
            f,
            args.service,
            endpoint=args.endpoint or None,
            bucket=args.bucket or None,
        ):
            ok += 1
        else:
            logger.error("Upload failed: %s", f)
    sys.exit(0 if ok == len(args.files) else 1)


if __name__ == "__main__":
    _upload_cli_main()

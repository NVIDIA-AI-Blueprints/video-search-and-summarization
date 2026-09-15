######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2025-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################
"""
Base class for RTVI performance benchmarks.
"""

import json
import logging
import math
import os
import shutil
import threading
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

import pandas as pd
import requests
import yaml
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

try:
    from gpu_monitor import GPUMonitor
except ImportError:
    GPUMonitor = None

try:
    from prometheus_gpu_collector import PrometheusGPUCollector
except ImportError:
    PrometheusGPUCollector = None

try:
    from node_exporter_collector import NodeExporterCollector
except ImportError:
    NodeExporterCollector = None


class CPUMonitor:
    """
    Polls Prometheus Node Exporter (via Prometheus HTTP API) for CPU and memory metrics.

    Runs a background thread that queries Prometheus every `interval_seconds` and stores
    samples. Call get_stats() to retrieve mean/std/p90 aggregates.

    Requires Node Exporter to be scraped by Prometheus. Typical setup:
        docker run -d --net=host prom/node-exporter
        # and prometheus.yml scrape job pointing to node-exporter:9100
    """

    def __init__(
        self, prometheus_url: str = "http://localhost:9090", interval_seconds: float = 5.0
    ):
        self.prometheus_url = prometheus_url.rstrip("/")
        self.interval_seconds = interval_seconds
        self._cpu_samples: List[float] = []  # Overall (avg across cores)
        self._mem_samples: List[float] = []  # Memory %
        self._per_core_samples: Dict[str, List[float]] = {}  # {cpu_id: [samples]}
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self.logger = logging.getLogger(self.__class__.__name__)

    def start(self):
        """Start background polling thread."""
        self._cpu_samples.clear()
        self._mem_samples.clear()
        self._per_core_samples.clear()
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()

    def stop(self):
        """Stop background polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=max(self.interval_seconds * 2, 10))

    def reset(self):
        """Clear all collected samples (without stopping the thread)."""
        self._cpu_samples.clear()
        self._mem_samples.clear()
        self._per_core_samples.clear()

    def _query_scalar(self, promql: str) -> Optional[float]:
        """Execute an instant PromQL query and return the scalar result."""
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": promql},
                timeout=5,
            )
            resp.raise_for_status()
            result = resp.json().get("data", {}).get("result", [])
            if result:
                return float(result[0]["value"][1])
        except Exception as e:
            self.logger.debug(f"Prometheus query failed ({promql[:60]}): {e}")
        return None

    def _query_vector(self, promql: str) -> Dict[str, float]:
        """Execute a vector PromQL query and return {label_value: float} dict."""
        try:
            resp = requests.get(
                f"{self.prometheus_url}/api/v1/query",
                params={"query": promql},
                timeout=5,
            )
            resp.raise_for_status()
            result = resp.json().get("data", {}).get("result", [])
            return {
                item["metric"].get("cpu", str(i)): float(item["value"][1])
                for i, item in enumerate(result)
            }
        except Exception as e:
            self.logger.debug(f"Prometheus vector query failed: {e}")
        return {}

    def _poll_loop(self):
        """Background thread: collect overall CPU, per-core CPU, and memory samples."""
        while self._running:
            # Overall CPU utilisation % (avg across all cores, 1-min irate window)
            cpu_pct = self._query_scalar(
                '100 - (avg(irate(node_cpu_seconds_total{mode="idle"}[1m])) * 100)'
            )
            if cpu_pct is not None:
                self._cpu_samples.append(cpu_pct)

            # Per-core CPU utilisation %
            per_core = self._query_vector(
                '100 - (irate(node_cpu_seconds_total{mode="idle"}[1m]) * 100)'
            )
            for cpu_id, pct in per_core.items():
                if cpu_id not in self._per_core_samples:
                    self._per_core_samples[cpu_id] = []
                self._per_core_samples[cpu_id].append(pct)

            # Memory utilisation % (used / total)
            mem_pct = self._query_scalar(
                "100 * (1 - (node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes))"
            )
            if mem_pct is not None:
                self._mem_samples.append(mem_pct)

            time.sleep(self.interval_seconds)

    def get_stats(self) -> Dict[str, Any]:
        """
        Return aggregated CPU and memory statistics over collected samples.

        Returns:
            Dictionary with:
            - cpu_usage_mean/std/p90: overall (avg across all cores), in %
            - mem_usage_mean/std/p90: memory utilisation, in %
            - per_core: dict of {cpu<N>_usage_mean, cpu<N>_usage_p90} for each core
        """
        import numpy as np

        stats: Dict[str, Any] = {
            "cpu_usage_mean": 0.0,
            "cpu_usage_std": 0.0,
            "cpu_usage_p90": 0.0,
            "mem_usage_mean": 0.0,
            "mem_usage_std": 0.0,
            "mem_usage_p90": 0.0,
            "cpu_samples": len(self._cpu_samples),
        }
        if self._cpu_samples:
            arr = self._cpu_samples
            stats["cpu_usage_mean"] = round(float(np.mean(arr)), 2)
            stats["cpu_usage_std"] = round(float(np.std(arr)), 2)
            stats["cpu_usage_p90"] = round(float(np.percentile(arr, 90)), 2)
        if self._mem_samples:
            arr = self._mem_samples
            stats["mem_usage_mean"] = round(float(np.mean(arr)), 2)
            stats["mem_usage_std"] = round(float(np.std(arr)), 2)
            stats["mem_usage_p90"] = round(float(np.percentile(arr, 90)), 2)

        # Per-core breakdown: sort core IDs numerically where possible
        per_core_stats: Dict[str, float] = {}
        max_any_core_pct = 0.0
        cores_over_90: List[str] = []
        cores_over_100: List[str] = []

        for cpu_id in sorted(self._per_core_samples, key=lambda x: int(x) if x.isdigit() else x):
            samples = self._per_core_samples[cpu_id]
            if samples:
                core_mean = round(float(np.mean(samples)), 2)
                core_p90 = round(float(np.percentile(samples, 90)), 2)
                core_max = round(float(np.max(samples)), 2)
                per_core_stats[f"cpu{cpu_id}_usage_mean"] = core_mean
                per_core_stats[f"cpu{cpu_id}_usage_p90"] = core_p90
                # max reveals saturation spikes that mean/p90 can miss
                per_core_stats[f"cpu{cpu_id}_usage_max"] = core_max
                if core_max > max_any_core_pct:
                    max_any_core_pct = core_max
                if core_max >= 90.0:
                    cores_over_90.append(cpu_id)
                if core_max >= 99.0:  # treat >=99% as "hit 100%"
                    cores_over_100.append(cpu_id)

        if per_core_stats:
            stats["per_core"] = per_core_stats
            # Saturation signals: visible even if overall avg looks fine
            stats["max_any_core_pct"] = max_any_core_pct
            stats["cores_over_90pct_count"] = len(cores_over_90)
            stats["cores_over_90pct"] = cores_over_90
            stats["cores_over_100pct_count"] = len(cores_over_100)
            stats["cores_over_100pct"] = cores_over_100

        return stats


class BenchmarkCleanupError(RuntimeError):
    """Raised when live cleanup fails and later measurements would be contaminated."""


class BenchmarkResourceUnavailableError(RuntimeError):
    """Raised when the service rejects benchmark load before exhausting resources."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def build_stream_start_error(response, endpoint: str, stream_num: int) -> RuntimeError:
    """Build a typed benchmark error from a rejected streaming request."""
    try:
        details = response.json()
    except (json.JSONDecodeError, AttributeError, ValueError):
        details = {}
    details = details if isinstance(details, dict) else {}
    code = str(details.get("code") or "")
    message = str(details.get("message") or details.get("detail") or response.text)
    description = (
        f"{endpoint} rejected live stream {stream_num}: "
        f"HTTP {response.status_code} {code or 'APIError'}: {message}"
    )
    if response.status_code == 503 and (
        code in {"ServerBusy", "ResourceUnavailable"} or "Insufficient GPU memory" in message
    ):
        return BenchmarkResourceUnavailableError(
            description,
            status_code=response.status_code,
            code=code,
        )
    return RuntimeError(description)


class BenchmarkBase(ABC):
    """
    Base class for all RTVI performance benchmarks.

    Provides common infrastructure for:
    - Configuration parsing and validation
    - API communication with retry logic
    - GPU monitoring lifecycle management
    - Directory and file management
    - Results collection and Excel generation
    """

    # HTTP client defaults
    DEFAULT_RETRY_COUNT = 3  # Number of retry attempts
    DEFAULT_RETRY_BACKOFF_FACTOR = 1  # Exponential backoff factor
    DEFAULT_RETRY_STATUS_CODES = [429, 500, 502, 503, 504]  # Status codes to retry
    DEFAULT_POOL_CONNECTIONS = 1024  # Connection pool size
    DEFAULT_POOL_MAXSIZE = 1024  # Maximum pool size
    # Timeouts: (connect_seconds, read_seconds).
    # DELETE calls use DELETE_READ_TIMEOUT because the server must drain in-flight
    # chunks before responding; all other calls use the shorter DEFAULT_READ_TIMEOUT.
    DEFAULT_CONNECT_TIMEOUT = 10  # seconds to establish TCP connection
    DEFAULT_READ_TIMEOUT = 180  # seconds to wait for a response on normal calls
    DELETE_READ_TIMEOUT = 180  # seconds to wait for DELETE (server drains chunks)
    DEFAULT_THREAD_WAIT_TIMEOUT = 30  # Thread completion timeout
    PIPELINE_STAGE_LATENCY_FIELDS = {
        "decode_latency_ms": ("chunk_decode_latency_seconds", 0.001),
        "vlm_latency_ms": ("chunk_vlm_latency_seconds", 0.001),
        "inference_latency_ms": ("chunk_inference_latency_seconds", 0.001),
        "chunk_latency_ms": ("chunk_server_e2e_latency_seconds", 0.001),
        "queue_time_s": ("chunk_queue_latency_seconds", 1.0),
        "processing_latency_s": ("chunk_server_processing_latency_seconds", 1.0),
    }
    PIPELINE_STAGE_COUNT_FIELDS = {
        "frame_count": "chunk_frame_count",
        "input_tokens": "chunk_input_tokens",
        "output_tokens": "chunk_output_tokens",
    }

    def __init__(self, base_url: str, output_base_dir: str = "rtvi-perf-report"):
        """
        Initialize the benchmark.

        Args:
            base_url: Base URL for the RTVI API
            output_base_dir: Base directory for all benchmark outputs
        """
        self.base_url = base_url.rstrip("/")
        self.output_base_dir = output_base_dir

        # Setup HTTP session with connection pooling and retries
        self.session = requests.Session()
        self._configure_http_session()

        # GPU monitoring setup (will be configured in parse_config)
        self.gpu_monitor = None
        self.prometheus_gpu_collector = None
        self.node_exporter_collector = None
        self.gpu_monitoring_config = {}
        self.vlm_gpus = []
        self.blocking_stream_delete = False
        self.stream_delete_timeout_seconds = 300.0

        # CPU/memory monitoring via Prometheus Node Exporter (configured in parse_config)
        self.cpu_monitor: Optional[CPUMonitor] = None
        self.cpu_monitoring_config: Dict[str, Any] = {}

        # Setup logging
        self._setup_logging()

        # Cleanup tracking
        self.active_resources = []
        self.cleanup_lock = threading.Lock()

        # Current execution state
        self.current_scenario_dir = None
        self.gpu_monitoring_active = False
        self.prometheus_collector_active = False
        self.node_exporter_collector_active = False
        self.pipeline_stage_samples: Dict[str, List[float]] = {}
        self.pipeline_stage_lock = threading.Lock()

    def _setup_logging(self):
        # Configure root logger if not already configured
        # Note: If basicConfig was already called elsewhere, this will be ignored
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.StreamHandler()],
        )

        # Create logger for this class
        self.logger = logging.getLogger(self.__class__.__name__)

        # Explicitly set logger level to match root logger's effective level
        # This ensures our logger respects the global logging configuration
        root_logger = logging.getLogger()
        self.logger.setLevel(root_logger.level)

    def _configure_http_session(self, pool_maxsize=None):
        """Configure HTTP session with connection pooling and retry logic"""
        # Use provided pool_maxsize or default
        pool_size = pool_maxsize if pool_maxsize is not None else self.DEFAULT_POOL_MAXSIZE

        retry_strategy = Retry(
            total=self.DEFAULT_RETRY_COUNT,
            backoff_factor=self.DEFAULT_RETRY_BACKOFF_FACTOR,
            status_forcelist=self.DEFAULT_RETRY_STATUS_CODES,
        )

        adapter = HTTPAdapter(
            pool_connections=self.DEFAULT_POOL_CONNECTIONS,
            pool_maxsize=pool_size,
            max_retries=retry_strategy,
        )

        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

        if pool_maxsize is not None:
            self.logger.debug(f"Reconfigured HTTP session with pool_maxsize={pool_size}")

    def _reset_http_session(self, pool_maxsize=None):
        """Close and recreate the shared HTTP session."""
        try:
            self.session.close()
        except Exception as e:
            self.logger.debug(f"Failed to close HTTP session during reset: {e}")

        self.session = requests.Session()
        self._configure_http_session(pool_maxsize)

    # ================================
    # ABSTRACT METHODS - Must implement
    # ================================

    @abstractmethod
    def parse_benchmark_config(self, scenario_config: Dict, global_config: Dict) -> Any:
        """
        Parse and validate benchmark-specific configuration.

        Args:
            scenario_config: Scenario-specific configuration
            global_config: Global configuration settings

        Returns:
            Parsed benchmark configuration object

        Raises:
            ValueError: If configuration is invalid
        """
        pass

    @abstractmethod
    def execute(self, config: Dict, scenario_name: str) -> Dict[str, Any]:
        """
        Execute the benchmark and return results metadata.

        Args:
            config: Full configuration dictionary
            scenario_name: Name of the scenario being executed

        Returns:
            Dictionary containing execution results and metadata

        Raises:
            Exception: If benchmark execution fails
        """
        pass

    @abstractmethod
    def analyze_results(self, results_dir: str, output_file: str) -> None:
        """
        Generate Excel report from benchmark results.

        Args:
            results_dir: Directory containing benchmark results
            output_file: Path to output Excel file

        Raises:
            Exception: If analysis or report generation fails
        """
        pass

    # ================================
    # CONFIGURATION METHODS
    # ================================

    def _load_defaults(self) -> Dict[str, Any]:
        """Load default parameters from defaults.yaml"""
        defaults_path = os.path.join(os.path.dirname(__file__), "defaults.yaml")
        if not os.path.exists(defaults_path):
            raise FileNotFoundError(
                f"defaults.yaml not found at {defaults_path}. "
                "This file is required for benchmark configuration."
            )
        with open(defaults_path, "r") as f:
            defaults = yaml.safe_load(f)
            if not defaults:
                raise ValueError("defaults.yaml is empty or invalid")
            return defaults

    def _fetch_active_file_ids(self) -> set:
        """Return the set of file IDs currently registered on the server.

        Calls GET /v1/files?purpose=vision once and returns a set of id strings.
        Returns an empty set on any error so callers can proceed safely.
        """
        try:
            response = self.make_api_call("/files", method="GET", params={"purpose": "vision"})
            return {item["id"] for item in response.json().get("data", []) if "id" in item}
        except Exception as e:
            self.logger.warning(f"Could not fetch active file IDs: {e}")
            return set()

    def _fetch_active_stream_ids(self, strict: bool = False) -> set:
        """Return the set of stream IDs currently active on the server.

        Calls GET /v1/streams/get-stream-info once and returns a set of id strings.
        Returns an empty set on any error unless strict=True. Cleanup guards use
        strict mode so an unavailable stream-info endpoint cannot be mistaken for
        an empty server.
        """
        try:
            response = self.make_api_call("/streams/get-stream-info", method="GET")
            return {item["id"] for item in response.json() if "id" in item}
        except Exception as e:
            message = f"Could not fetch active stream IDs: {e}"
            if strict:
                raise RuntimeError(message) from e
            self.logger.warning(message)
            return set()

    def _wait_for_streams_deleted(self, stream_ids: List[str]) -> None:
        """Wait until the server no longer reports the given stream IDs.

        Batch DELETE can return before monitor threads and request bookkeeping
        fully drain. Advancing to the next scenario while the server still
        counts those live requests can trip the max-live-streams admission
        guard and invalidate the following scenario.
        """
        targets = {str(stream_id) for stream_id in stream_ids}
        if not targets:
            return

        timeout_sec = float(
            os.environ.get("RTVI_BENCHMARK_STREAM_CLEANUP_VERIFY_TIMEOUT_SEC", "300")
        )
        poll_interval = float(
            os.environ.get("RTVI_BENCHMARK_STREAM_CLEANUP_VERIFY_INTERVAL_SEC", "2")
        )
        deadline = time.time() + max(timeout_sec, 0.0)
        remaining = targets
        while True:
            active_ids = self._fetch_active_stream_ids(strict=True)
            remaining = targets & active_ids
            if not remaining:
                return
            if time.time() >= deadline:
                break
            time.sleep(max(poll_interval, 0.1))

        sample = ", ".join(sorted(remaining)[:5])
        raise RuntimeError(
            f"Timed out waiting {timeout_sec:.1f}s for {len(remaining)} live stream(s) "
            f"to disappear from server state after cleanup: {sample}"
        )

    def assert_no_active_streams(self, context: str) -> None:
        """Raise if the server still has live streams from an earlier scenario."""
        active_ids = self._fetch_active_stream_ids(strict=True)
        if not active_ids:
            return

        sample = ", ".join(sorted(active_ids)[:5])
        raise RuntimeError(
            f"Server still reports {len(active_ids)} active live stream(s) {context}: {sample}. "
            "Cleanup must complete before starting the next live-stream benchmark scenario."
        )

    def _stop_live_generation_requests(
        self,
        stream_ids: List[str],
        *,
        backend_type: str = "rtvi_vlm",
        captions_endpoint: Optional[str] = None,
    ) -> None:
        """Stop live generation requests before deleting stream assets."""
        if not stream_ids:
            return
        if self._blocking_stream_delete_enabled():
            self.logger.info(
                "Deferring live generation stop to blocking batch delete for %d stream(s)",
                len(stream_ids),
            )
            return

        if backend_type == "rtvi_embed":
            endpoint = "/generate_video_embeddings"
        else:
            endpoint = str(captions_endpoint or "/generate_captions").strip()
            if not endpoint.startswith("/"):
                endpoint = f"/{endpoint}"
        max_workers = min(
            len(stream_ids),
            max(
                1,
                int(os.environ.get("RTVI_BENCHMARK_STREAM_STOP_MAX_WORKERS", "16")),
            ),
        )

        def stop_request(stream_id: str) -> None:
            try:
                self.make_api_call(
                    f"{endpoint}/{stream_id}",
                    method="DELETE",
                    timeout=(self.DEFAULT_CONNECT_TIMEOUT, self.DEFAULT_READ_TIMEOUT),
                )
            except requests.exceptions.HTTPError as e:
                response = getattr(e, "response", None)
                if response is not None and response.status_code in (400, 404):
                    self.logger.debug(
                        "  Live generation request for stream %s was already stopped: %s",
                        stream_id,
                        e,
                    )
                    return
                self.logger.warning(
                    "  Failed to stop live generation request for stream %s: %s",
                    stream_id,
                    e,
                )
            except requests.exceptions.RequestException as e:
                self.logger.warning(
                    "  Failed to stop live generation request for stream %s: %s",
                    stream_id,
                    e,
                )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            list(executor.map(stop_request, stream_ids))

    def _blocking_stream_delete_enabled(self) -> bool:
        configured = os.environ.get("RTVI_BENCHMARK_BLOCKING_STREAM_DELETE")
        if configured is None:
            return self.blocking_stream_delete
        return configured.strip().lower() not in ("0", "false", "no", "off", "")

    def _stream_delete_timeout_sec(self) -> float:
        configured = os.environ.get(
            "RTVI_BENCHMARK_STREAM_DELETE_TIMEOUT_SEC",
            str(self.stream_delete_timeout_seconds),
        )
        try:
            timeout_sec = float(configured)
        except (TypeError, ValueError):
            timeout_sec = 0
        if not math.isfinite(timeout_sec) or timeout_sec <= 0 or timeout_sec > 3600:
            raise ValueError(
                "RTVI benchmark stream delete timeout must be a finite value in (0, 3600]"
            )
        return timeout_sec

    def _batch_delete_streams(self, stream_ids: List[str], inter_delete_delay: float = 0.0) -> None:
        """Delete live streams with the batch endpoint and clear local tracking.

        The server handles per-stream drain in parallel, which is important for
        overloaded live-stream tests where individual deletes can each wait for
        the drain timeout.
        """
        active_ids = self._fetch_active_stream_ids(strict=True)
        targets: List[str] = []
        for stream_id in stream_ids:
            stream_id = str(stream_id)
            resource = f"stream_{stream_id}"
            if stream_id not in active_ids:
                self.logger.debug(f"  Stream {stream_id} not active, skipping DELETE")
                if resource in self.active_resources:
                    self.active_resources.remove(resource)
                continue
            targets.append(stream_id)

        if not targets:
            return

        batch_max = 256  # DeleteLiveStreamsRequest.stream_ids max_length
        failed_errors: List[str] = []
        wait_candidates: set[str] = set()
        for idx in range(0, len(targets), batch_max):
            chunk = targets[idx : idx + batch_max]
            blocking_delete = self._blocking_stream_delete_enabled()
            max_attempts = max(
                1,
                int(os.environ.get("RTVI_BENCHMARK_STREAM_DELETE_RETRY_ATTEMPTS", "5")),
            )
            retry_delay = max(
                0.0,
                float(os.environ.get("RTVI_BENCHMARK_STREAM_DELETE_RETRY_DELAY_SEC", "1")),
            )
            pending = list(chunk)

            for attempt in range(1, max_attempts + 1):
                request_data = {"stream_ids": pending}
                if blocking_delete:
                    drain_timeout = self._stream_delete_timeout_sec()
                    request_data["blocking"] = True
                    request_data["drain_timeout_seconds"] = drain_timeout
                    timeout = (self.DEFAULT_CONNECT_TIMEOUT, drain_timeout + 30)
                else:
                    timeout = None
                try:
                    response = self.make_api_call(
                        "/streams/delete-batch",
                        method="DELETE",
                        data=request_data,
                        timeout=timeout,
                    )
                    body = response.json()
                except requests.exceptions.HTTPError as e:
                    response = getattr(e, "response", None)
                    if blocking_delete and response is not None and response.status_code == 422:
                        self.logger.warning(
                            "  Batch DELETE blocking fields unsupported; retrying legacy payload"
                        )
                        try:
                            response = self.make_api_call(
                                "/streams/delete-batch",
                                method="DELETE",
                                data={"stream_ids": pending},
                            )
                            body = response.json()
                        except requests.exceptions.RequestException as fallback_error:
                            self.logger.warning(f"  Batch DELETE failed: {fallback_error}")
                            failed_errors.append(f"{len(pending)} stream(s): {fallback_error}")
                            break
                    else:
                        self.logger.warning(f"  Batch DELETE failed: {e}")
                        failed_errors.append(f"{len(pending)} stream(s): {e}")
                        break
                except requests.exceptions.RequestException as e:
                    self.logger.warning(f"  Batch DELETE failed: {e}")
                    failed_errors.append(f"{len(pending)} stream(s): {e}")
                    break

                retry_ids: List[str] = []
                for sid in body.get("deleted", []):
                    sid = str(sid)
                    wait_candidates.add(sid)
                    resource = f"stream_{sid}"
                    if resource in self.active_resources:
                        self.active_resources.remove(resource)
                for err in body.get("errors", []):
                    sid = str(err.get("stream_id", "?"))
                    code = err.get("status_code")
                    error_code = err.get("error_code")
                    msg = err.get("error")
                    if code in (400, 404):
                        # Already gone server-side: dropping it is correct.
                        self.logger.warning(f"  Stream {sid} already gone (ignored)")
                        wait_candidates.add(sid)
                        resource = f"stream_{sid}"
                        if resource in self.active_resources:
                            self.active_resources.remove(resource)
                    elif code == 409 and error_code == "ResourceInUse" and attempt < max_attempts:
                        retry_ids.append(sid)
                    else:
                        # The stream is still alive on the server. Keep it in
                        # active_resources so teardown failures stay visible
                        # instead of the run silently continuing as if the GPU
                        # had been freed.
                        self.logger.error(f"  DELETE stream {sid} failed ({code}): {msg}")
                        failed_errors.append(f"{sid}: {msg}")

                if not retry_ids:
                    break
                self.logger.warning(
                    "  Batch DELETE retry %d/%d for %d stream(s) still being stopped",
                    attempt + 1,
                    max_attempts,
                    len(retry_ids),
                )
                pending = retry_ids
                if retry_delay > 0:
                    time.sleep(retry_delay)

            if inter_delete_delay > 0 and idx + batch_max < len(targets):
                time.sleep(inter_delete_delay)

        self._wait_for_streams_deleted(sorted(wait_candidates))
        if failed_errors:
            sample = "; ".join(failed_errors[:5])
            raise RuntimeError(
                f"Batch DELETE reported {len(failed_errors)} live-stream cleanup failure(s): "
                f"{sample}"
            )

    def _merge_with_defaults(self, user_params: Dict, default_params: Dict) -> Dict:
        """Merge user parameters with defaults"""
        merged = default_params.copy() if default_params else {}
        if user_params:
            merged.update(user_params)
        return merged

    def parse_global_config(self, config: Dict) -> Dict[str, Any]:
        """
        Parse and validate global configuration.

        Args:
            config: Full configuration dictionary

        Returns:
            Validated global configuration

        Raises:
            ValueError: If global configuration is invalid
        """
        if "global" not in config:
            raise ValueError("Missing 'global' section in configuration")

        global_config = config["global"]
        defaults = self._load_defaults()

        # Validate required fields
        required_fields = ["vlm_gpus"]
        for field in required_fields:
            if field not in global_config:
                raise ValueError(f"Missing required global config field: {field}")

        blocking_delete = global_config.get("blocking_stream_delete", False)
        if isinstance(blocking_delete, str):
            blocking_delete = blocking_delete.strip().lower() not in (
                "0",
                "false",
                "no",
                "off",
                "",
            )
        self.blocking_stream_delete = bool(blocking_delete)
        self.stream_delete_timeout_seconds = float(
            global_config.get("stream_delete_timeout_seconds", 300)
        )
        if (
            not math.isfinite(self.stream_delete_timeout_seconds)
            or self.stream_delete_timeout_seconds <= 0
            or self.stream_delete_timeout_seconds > 3600
        ):
            raise ValueError(
                "global.stream_delete_timeout_seconds must be a finite value in (0, 3600]"
            )

        # Merge each API parameter section with defaults
        global_config["generate_captions_params"] = self._merge_with_defaults(
            global_config.get("generate_captions_params", {}), defaults["generate_captions_params"]
        )
        global_config["chat_completions_params"] = self._merge_with_defaults(
            global_config.get("chat_completions_params", {}),
            global_config["generate_captions_params"],
        )

        # Also merge embedding parameters if present in defaults
        if "generate_video_embeddings_params" in defaults:
            global_config["generate_video_embeddings_params"] = self._merge_with_defaults(
                global_config.get("generate_video_embeddings_params", {}),
                defaults["generate_video_embeddings_params"],
            )

        # Setup GPU configuration
        self.vlm_gpus = global_config["vlm_gpus"]
        self.gpu_monitoring_config = global_config.get("gpu_monitoring", {"enabled": False})

        # Initialize GPU monitor if enabled (legacy pynvml-based)
        if self.gpu_monitoring_config.get("enabled", False) and GPUMonitor is not None:
            all_gpu_ids = list(set(self.vlm_gpus))
            if all_gpu_ids:
                self.gpu_monitor = GPUMonitor(gpu_ids=all_gpu_ids)
                self.logger.debug(f"GPU monitoring enabled for GPUs: {all_gpu_ids}")
            else:
                self.logger.warning("GPU monitoring enabled but no GPU IDs specified")
        else:
            self.logger.debug("GPU monitoring disabled")

        # Initialize Prometheus/DCGM GPU collector if enabled (runs alongside legacy)
        prometheus_config = self.gpu_monitoring_config.get("prometheus", {})
        if prometheus_config.get("enabled", False) and PrometheusGPUCollector is not None:
            dcgm_url = prometheus_config.get("dcgm_exporter_url", "http://localhost:9400/metrics")
            try:
                self.prometheus_gpu_collector = PrometheusGPUCollector(
                    dcgm_exporter_url=dcgm_url,
                    gpu_ids=list(set(self.vlm_gpus)),
                    session=self.session,
                )
                self.logger.debug(f"Prometheus/DCGM GPU collector enabled (DCGM: {dcgm_url})")
            except Exception as e:
                self.logger.warning(f"Failed to initialize Prometheus GPU collector: {e}")
        else:
            self.logger.debug("Prometheus/DCGM GPU collector disabled")

        # Initialize Node Exporter collector if enabled (CPU, memory profiling)
        if (
            prometheus_config.get("node_exporter_enabled", False)
            and NodeExporterCollector is not None
        ):
            node_exporter_url = prometheus_config.get(
                "node_exporter_url", "http://localhost:9101/metrics"
            )
            try:
                self.node_exporter_collector = NodeExporterCollector(
                    node_exporter_url=node_exporter_url,
                    session=self.session,
                )
                self.logger.debug(
                    f"Node Exporter collector enabled (CPU/memory: {node_exporter_url})"
                )
            except Exception as e:
                self.logger.warning(f"Failed to initialize Node Exporter collector: {e}")
        else:
            self.logger.debug("Node Exporter collector disabled")

        # Initialize CPU monitor if enabled
        self.cpu_monitoring_config = global_config.get("cpu_monitoring", {"enabled": False})
        if self.cpu_monitoring_config.get("enabled", False):
            prometheus_url = self.cpu_monitoring_config.get(
                "prometheus_url", "http://localhost:9090"
            )
            interval = self.cpu_monitoring_config.get("interval_seconds", 5)
            self.cpu_monitor = CPUMonitor(prometheus_url=prometheus_url, interval_seconds=interval)
            self.logger.debug(f"CPU monitoring enabled via Prometheus at {prometheus_url}")
        else:
            self.logger.debug("CPU monitoring disabled")

        return global_config

    def get_vlm_dimensions(self, scenario_config: Dict, global_config: Dict) -> Dict[str, int]:
        """
        Get VLM dimensions with scenario override support.

        Args:
            scenario_config: Scenario-specific configuration
            global_config: Global configuration

        Returns:
            Dictionary with vlm_input_width and vlm_input_height
        """
        # Get VLM dimensions with scenario override logic
        global_vlm_dimensions = global_config.get("vlm_dimensions", {})
        scenario_vlm_dimensions = scenario_config.get("vlm_dimensions", {})

        # Use scenario dimensions if available, otherwise use global defaults
        vlm_input_width = scenario_vlm_dimensions.get(
            "input_width",
            global_vlm_dimensions.get("input_width", 0),
        )
        vlm_input_height = scenario_vlm_dimensions.get(
            "input_height", global_vlm_dimensions.get("input_height", 0)
        )

        return {"vlm_input_width": vlm_input_width, "vlm_input_height": vlm_input_height}

    def load_config_file(self, config_path: str) -> Dict[str, Any]:
        """
        Load and parse YAML configuration file.

        Args:
            config_path: Path to YAML configuration file

        Returns:
            Parsed configuration dictionary

        Raises:
            FileNotFoundError: If config file doesn't exist
            ValueError: If YAML is invalid
        """
        try:
            with open(config_path, "r") as f:
                config = yaml.safe_load(f)

            # Basic structure validation
            if not isinstance(config, dict):
                raise ValueError("Configuration must be a dictionary")

            if "global" not in config:
                raise ValueError("Missing 'global' section")

            if "test_scenarios" not in config:
                raise ValueError("Missing 'test_scenarios' section")

            return config

        except FileNotFoundError:
            raise FileNotFoundError(f"Configuration file not found: {config_path}")
        except yaml.YAMLError as e:
            raise ValueError(f"Invalid YAML in configuration file: {e}")

    # ================================
    # EXECUTION INFRASTRUCTURE
    # ================================

    def setup_scenario_directory(self, scenario_name: str) -> str:
        """
        Create and setup directory for scenario execution.

        Args:
            scenario_name: Name of the scenario

        Returns:
            Path to created scenario directory
        """
        scenario_dir = os.path.join(self.output_base_dir, scenario_name)

        # Remove existing directory if it exists
        if os.path.exists(scenario_dir):
            shutil.rmtree(scenario_dir)

        # Create new directory
        os.makedirs(scenario_dir, exist_ok=True)
        self.current_scenario_dir = scenario_dir

        self.logger.debug(f"Created scenario directory: {scenario_dir}")
        return scenario_dir

    def start_gpu_monitoring(self):
        """Start GPU monitoring if configured (legacy pynvml + optional Prometheus/DCGM)"""
        # Legacy pynvml-based monitoring
        if self.gpu_monitor and not self.gpu_monitoring_active:
            try:
                interval = self.gpu_monitoring_config.get("interval_seconds", 2)
                self.gpu_monitor.start_recording_gpu_usage(interval_in_seconds=interval)
                self.gpu_monitor.start_recording_nvdec(interval_in_seconds=interval)
                self.gpu_monitoring_active = True
                self.logger.debug("GPU monitoring started (legacy)")
            except Exception as e:
                self.logger.error(f"Failed to start GPU monitoring: {e}")

        # Prometheus/DCGM exporter-based monitoring (runs alongside legacy)
        if self.prometheus_gpu_collector and not self.prometheus_collector_active:
            try:
                interval = float(
                    self.gpu_monitoring_config.get("prometheus", {}).get(
                        "scrape_interval_seconds",
                        self.gpu_monitoring_config.get("interval_seconds", 2),
                    )
                )
                self.prometheus_gpu_collector.start(interval_seconds=interval)
                self.prometheus_collector_active = True
                self.logger.debug("Prometheus/DCGM GPU collector started")
            except Exception as e:
                self.logger.warning(f"Failed to start Prometheus GPU collector: {e}")

        # Node Exporter (CPU, memory) - runs alongside when enabled
        if self.node_exporter_collector and not self.node_exporter_collector_active:
            try:
                interval = float(
                    self.gpu_monitoring_config.get("prometheus", {}).get(
                        "scrape_interval_seconds",
                        self.gpu_monitoring_config.get("interval_seconds", 2),
                    )
                )
                self.node_exporter_collector.start(interval_seconds=interval)
                self.node_exporter_collector_active = True
                self.logger.debug("Node Exporter collector started (CPU profiling)")
            except Exception as e:
                self.logger.warning(f"Failed to start Node Exporter collector: {e}")

    def reset_gpu_monitoring(self):
        """Reset GPU monitoring"""
        if self.gpu_monitor and self.gpu_monitoring_active:
            try:
                self.gpu_monitor.reset_gpu_usage()
                self.gpu_monitor.reset_nvdec_usage()
            except Exception as e:
                self.logger.error(f"Failed to reset GPU monitoring: {e}")

    def stop_recording_gpu_usage(self):
        """Stop recording GPU usage"""
        if self.gpu_monitor and self.gpu_monitoring_active:
            try:
                self.gpu_monitor.stop_recording_gpu()
                self.gpu_monitor.stop_recording_nvdec()
                self.gpu_monitoring_active = False
                self.logger.debug("GPU usage recording stopped")
            except Exception as e:
                self.logger.error(f"Failed to stop recording GPU usage: {e}")

    def stop_gpu_monitoring(self, export_dir: str = None, filename_prefix: str = "gpu_metrics"):
        """
        Stop GPU monitoring and optionally export data.
        Stops both legacy pynvml and Prometheus/DCGM collectors.

        Args:
            export_dir: Directory to export GPU data (optional)
            filename_prefix: Prefix for exported files
        """
        try:
            # Stop legacy pynvml-based monitoring
            if self.gpu_monitor and self.gpu_monitoring_active:
                self.gpu_monitor.stop_recording_gpu()
                self.gpu_monitor.stop_recording_nvdec()
                self.gpu_monitoring_active = False
                self.logger.debug("GPU monitoring stopped (legacy)")
            if export_dir and self.gpu_monitor:
                export_csv = self.gpu_monitoring_config.get("export_csv", False)
                export_plots = self.gpu_monitoring_config.get("export_plots", False)
                self.gpu_monitor.export_data(
                    output_dir=export_dir,
                    base_filename=filename_prefix,
                    export_csv=export_csv,
                    export_plots=export_plots,
                )
                self.logger.debug(f"GPU data exported to: {export_dir}")

            # Stop Prometheus/DCGM collector and export
            if self.prometheus_gpu_collector and self.prometheus_collector_active:
                try:
                    prometheus_stats = self.prometheus_gpu_collector.stop()
                    self.prometheus_collector_active = False
                    self.logger.debug("Prometheus/DCGM GPU collector stopped")
                    if export_dir and prometheus_stats:
                        prometheus_path = os.path.join(
                            export_dir,
                            f"prometheus_{filename_prefix}_stats.json",
                        )
                        self.save_json_data({"stats": prometheus_stats}, prometheus_path)
                        self.logger.debug(f"Prometheus GPU data exported to: {prometheus_path}")
                    if prometheus_stats:
                        parts = [
                            f"usage_mean={prometheus_stats.get('prometheus_vlm_gpu_usage_mean', 'N/A')}",
                            f"memory_mean={prometheus_stats.get('prometheus_vlm_gpu_memory_mean', 'N/A')}",
                            f"nvdec_mean={prometheus_stats.get('prometheus_vlm_nvdec_usage_mean', 'N/A')}",
                        ]
                        if "prometheus_vlm_power_mean_watts" in prometheus_stats:
                            parts.append(
                                f"power_mean={prometheus_stats.get('prometheus_vlm_power_mean_watts')}W"
                            )
                        if "prometheus_vlm_gpu_temp_mean_c" in prometheus_stats:
                            parts.append(
                                f"gpu_temp={prometheus_stats.get('prometheus_vlm_gpu_temp_mean_c')}C"
                            )
                        self.logger.info("Prometheus/DCGM GPU: " + ", ".join(parts))
                except Exception as e:
                    self.logger.warning(f"Failed to stop/export Prometheus GPU collector: {e}")

            # Stop Node Exporter collector and export (CPU profiling)
            if self.node_exporter_collector and self.node_exporter_collector_active:
                try:
                    node_stats = self.node_exporter_collector.stop()
                    self.node_exporter_collector_active = False
                    self.logger.debug("Node Exporter collector stopped")
                    if export_dir and node_stats:
                        node_path = os.path.join(
                            export_dir,
                            f"node_exporter_{filename_prefix}_stats.json",
                        )
                        self.save_json_data({"stats": node_stats}, node_path)
                        self.logger.debug(f"Node Exporter data exported to: {node_path}")
                    if node_stats:
                        parts = [
                            f"cpu_util_mean={node_stats.get('nodeexporter_cpu_util_mean', 'N/A')}%",
                            f"memory_used_pct={node_stats.get('nodeexporter_memory_used_pct_mean', 'N/A')}%",
                        ]
                        if "nodeexporter_load1_mean" in node_stats:
                            parts.append(f"load1={node_stats.get('nodeexporter_load1_mean')}")
                        self.logger.info("Node Exporter (CPU): " + ", ".join(parts))
                except Exception as e:
                    self.logger.warning(f"Failed to stop/export Node Exporter collector: {e}")

        except Exception as e:
            self.logger.error(f"Failed to stop GPU monitoring: {e}")

    def reset_prometheus_collectors(self):
        """Reset Prometheus/DCGM and Node Exporter collectors to discard old samples.
        The collectors keep running; only samples after this call are included in final stats.
        Use this to align stats with the last stable window instead of the full test duration."""
        if self.prometheus_gpu_collector and self.prometheus_collector_active:
            self.prometheus_gpu_collector.reset()
            self.logger.debug("Prometheus/DCGM collector samples reset")
        if self.node_exporter_collector and self.node_exporter_collector_active:
            self.node_exporter_collector.reset()
            self.logger.debug("Node Exporter collector samples reset")

    def get_current_prometheus_collector_stats(self) -> Dict[str, Any]:
        """Return current DCGM and Node Exporter stats without stopping collection."""
        stats: Dict[str, Any] = {}
        if self.prometheus_gpu_collector and self.prometheus_collector_active:
            try:
                stats.update(self.prometheus_gpu_collector.get_stats())
            except Exception as e:
                self.logger.debug("Could not snapshot Prometheus/DCGM collector stats: %s", e)
        if self.node_exporter_collector and self.node_exporter_collector_active:
            try:
                stats.update(self.node_exporter_collector.get_stats())
            except Exception as e:
                self.logger.debug("Could not snapshot Node Exporter collector stats: %s", e)
        return stats

    def start_cpu_monitoring(self):
        """Start CPU/memory monitoring via Prometheus Node Exporter."""
        if self.cpu_monitor:
            try:
                self.cpu_monitor.start()
                self.logger.debug("CPU monitoring started")
            except Exception as e:
                self.logger.error(f"Failed to start CPU monitoring: {e}")

    def reset_cpu_monitoring(self):
        """Reset CPU monitoring samples (without stopping the thread)."""
        if self.cpu_monitor:
            try:
                self.cpu_monitor.reset()
            except Exception as e:
                self.logger.error(f"Failed to reset CPU monitoring: {e}")

    def stop_cpu_monitoring(self) -> Dict[str, Any]:
        """Stop CPU monitoring and return aggregated stats."""
        if self.cpu_monitor:
            try:
                self.cpu_monitor.stop()
                stats = self.cpu_monitor.get_stats()
                self.logger.debug(f"CPU monitoring stopped. Stats: {stats}")
                return stats
            except Exception as e:
                self.logger.error(f"Failed to stop CPU monitoring: {e}")
        return {}

    def get_cpu_stats(self) -> Dict[str, Any]:
        """Get current CPU monitoring stats without stopping."""
        if self.cpu_monitor:
            try:
                return self.cpu_monitor.get_stats()
            except Exception as e:
                self.logger.error(f"Failed to get CPU stats: {e}")
        return {}

    # ================================
    # API COMMUNICATION
    # ================================

    def make_api_call(
        self,
        endpoint: str,
        method: str = "GET",
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        files: Optional[Dict] = None,
        timeout: Optional[tuple] = None,
    ) -> requests.Response:
        """
        Make an API call to the RTVI backend.

        Args:
            endpoint: API endpoint (will be appended to base_url)
            method: HTTP method
            data: JSON data for request body
            params: URL parameters
            headers: HTTP headers
            files: Files for multipart upload
            timeout: (connect_seconds, read_seconds) tuple; defaults to
                     DELETE_READ_TIMEOUT for DELETE calls, DEFAULT_READ_TIMEOUT
                     for all others.  Pass explicit value to override.

        Returns:
            Response object

        Raises:
            requests.RequestException: If API call fails
        """
        url = f"{self.base_url}/{endpoint.lstrip('/')}"

        if timeout is None:
            read_timeout = (
                self.DELETE_READ_TIMEOUT if method == "DELETE" else self.DEFAULT_READ_TIMEOUT
            )
            timeout = (self.DEFAULT_CONNECT_TIMEOUT, read_timeout)

        request_kwargs = {
            "method": method,
            "url": url,
            "params": params,
            "headers": headers,
            "timeout": timeout,
        }

        if data and method in ["POST", "PUT", "PATCH", "DELETE"]:
            request_kwargs["json"] = data

        if files:
            request_kwargs["files"] = files

        try:
            self.logger.debug(f"API call: {method} {url}")
            response = self.session.request(**request_kwargs)
            if response.status_code >= 400:
                try:
                    body = response.json()
                    self.logger.error(
                        f"API call {method} {url} returned {response.status_code}: {body}"
                    )
                except Exception:
                    self.logger.error(
                        f"API call {method} {url} returned {response.status_code}: {response.text}"
                    )
            response.raise_for_status()

            if not self.validate_api_response(response):
                raise requests.exceptions.RequestException(f"Invalid API response from {endpoint}")

            return response
        except requests.exceptions.RequestException as e:
            self.logger.error(f"API call failed: {e}")
            raise

    def validate_api_response(self, response: requests.Response) -> bool:
        """
        Validate API response for common issues.

        Args:
            response: Response to validate

        Returns:
            True if response is valid, False otherwise
        """
        # Check status code
        if response.status_code >= 400:
            try:
                error_data = response.json()
                self.logger.error(f"API error: {error_data}")
            except Exception:
                self.logger.error(f"API error: HTTP {response.status_code}")
            return False

        if not response.content and response.status_code >= 300:
            self.logger.error("Empty API response for non-2xx status")
            return False

        # Validate JSON if content-type suggests it and there's content
        if response.content and "application/json" in response.headers.get("Content-Type", ""):
            try:
                response.json()
            except json.JSONDecodeError:
                self.logger.error("Invalid JSON in API response")
                return False

        return True

    def get_available_models(self) -> str:
        """Get available model from the API"""
        response = self.make_api_call("/models")
        data = response.json()
        return data["data"][0]["id"]

    def scrape_metrics(self) -> Dict[str, Any]:
        """
        Scrape metrics from the /metrics endpoint and parse them into a dictionary.

        Returns:
            Dictionary of metrics with numeric values
        """
        try:
            response = self.make_api_call("/metrics")
            metrics = {}
            for line in response.text.split("\n"):
                if line and not line.startswith("#"):
                    try:
                        name, value = line.split(" ")
                        if any(
                            name.endswith(suffix)
                            for suffix in ["_latest", "_sum", "_count", "_seconds"]
                        ):
                            metrics[name] = float(value)
                    except ValueError:
                        continue
            return metrics
        except Exception as e:
            self.logger.error(f"Failed to scrape metrics: {e}")
            return {}

    def reset_pipeline_stage_samples(self) -> None:
        """Clear per-chunk stage latency samples accumulated from API responses."""
        with self.pipeline_stage_lock:
            self.pipeline_stage_samples = {}

    @staticmethod
    def _coerce_non_negative_float(value: Any) -> Optional[float]:
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return None
        if numeric_value < 0:
            return None
        return numeric_value

    def extract_pipeline_stage_samples(self, payload: Dict[str, Any]) -> Dict[str, List[float]]:
        """Extract per-chunk stage latency/count samples from a captions response payload."""
        samples: Dict[str, List[float]] = {}
        chunk_responses = payload.get("chunk_responses") if isinstance(payload, dict) else None
        if not isinstance(chunk_responses, list):
            return samples

        for chunk_response in chunk_responses:
            if not isinstance(chunk_response, dict):
                continue
            for response_key, (metric_key, scale) in self.PIPELINE_STAGE_LATENCY_FIELDS.items():
                value = self._coerce_non_negative_float(chunk_response.get(response_key))
                if value is not None:
                    samples.setdefault(metric_key, []).append(value * scale)
            for response_key, metric_key in self.PIPELINE_STAGE_COUNT_FIELDS.items():
                value = self._coerce_non_negative_float(chunk_response.get(response_key))
                if value is not None:
                    samples.setdefault(metric_key, []).append(value)

        return samples

    def record_pipeline_stage_samples(self, payload: Dict[str, Any]) -> None:
        """Accumulate per-chunk stage samples from one API response payload."""
        samples = self.extract_pipeline_stage_samples(payload)
        if not samples:
            return
        with self.pipeline_stage_lock:
            for metric_key, values in samples.items():
                self.pipeline_stage_samples.setdefault(metric_key, []).extend(values)

    def get_pipeline_stage_stats(self) -> Dict[str, Any]:
        """Return aggregate stats for accumulated per-chunk stage latency/count samples."""
        with self.pipeline_stage_lock:
            samples = {key: list(values) for key, values in self.pipeline_stage_samples.items()}
        return self.summarize_pipeline_stage_samples(samples)

    @staticmethod
    def summarize_pipeline_stage_samples(samples: Dict[str, List[float]]) -> Dict[str, Any]:
        """Summarize per-chunk samples as avg/min/max/p50/p75/p90/p95/p99/count fields."""
        import numpy as np

        stats: Dict[str, Any] = {}
        for metric_key, values in samples.items():
            numeric_values = [float(value) for value in values if value is not None]
            if not numeric_values:
                continue
            stats[f"{metric_key}_avg"] = float(np.mean(numeric_values))
            stats[f"{metric_key}_min"] = float(np.min(numeric_values))
            stats[f"{metric_key}_max"] = float(np.max(numeric_values))
            stats[f"{metric_key}_p50"] = float(np.percentile(numeric_values, 50))
            stats[f"{metric_key}_p75"] = float(np.percentile(numeric_values, 75))
            stats[f"{metric_key}_p90"] = float(np.percentile(numeric_values, 90))
            stats[f"{metric_key}_p95"] = float(np.percentile(numeric_values, 95))
            stats[f"{metric_key}_p99"] = float(np.percentile(numeric_values, 99))
            stats[f"{metric_key}_count"] = len(numeric_values)
        return stats

    # ================================
    # GPU STATISTICS PROCESSING
    # ================================

    def process_gpu_stats(
        self, gpu_stats_file: str, merge_prometheus: bool = True
    ) -> Dict[str, Any]:
        """
        Process GPU statistics from exported JSON file (legacy pynvml).
        Optionally merges Prometheus/DCGM metrics from companion file.

        Args:
            gpu_stats_file: Path to GPU stats JSON file
            merge_prometheus: If True, look for prometheus_*_stats.json in same
                directory and merge those metrics into the result

        Returns:
            Dictionary with processed GPU metrics (legacy + prometheus when available)
        """
        gpu_metrics = {}

        # Process legacy pynvml GPU stats
        if os.path.exists(gpu_stats_file):
            try:
                with open(gpu_stats_file, "r") as f:
                    gpu_data = json.load(f)

                # Process VLM GPU stats
                vlm_stats = []
                for gpu_id in self.vlm_gpus:
                    gpu_key = f"GPU{gpu_id}"
                    if gpu_key in gpu_data.get("gpu_stats", {}):
                        vlm_stats.append(gpu_data["gpu_stats"][gpu_key])

                if vlm_stats:
                    vlm_usage = [stats.get("usage", {}).get("mean", 0) for stats in vlm_stats]
                    vlm_memory = [stats.get("memory", {}).get("mean", 0) for stats in vlm_stats]
                    vlm_usage_p90 = [stats.get("usage", {}).get("p90", 0) for stats in vlm_stats]

                    gpu_metrics.update(
                        {
                            "vlm_gpu_usage_mean": (
                                sum(vlm_usage) / len(vlm_usage) if vlm_usage else 0
                            ),
                            "vlm_gpu_memory_mean": (
                                sum(vlm_memory) / len(vlm_memory) if vlm_memory else 0
                            ),
                            "vlm_gpu_usage_p90": (
                                sum(vlm_usage_p90) / len(vlm_usage_p90) if vlm_usage_p90 else 0
                            ),
                        }
                    )

                # Process NVDEC stats (typically on VLM GPUs)
                nvdec_stats = gpu_data.get("nvdec_stats", {})
                vlm_nvdec_stats = []
                for gpu_id in self.vlm_gpus:
                    nvdec_key = f"GPU{gpu_id}_AvgNVDEC"
                    if nvdec_key in gpu_data.get("nvdec_data", {}):
                        vlm_nvdec_stats.extend(gpu_data["nvdec_data"][nvdec_key])

                if vlm_nvdec_stats:
                    import numpy as np

                    gpu_metrics.update(
                        {
                            "vlm_nvdec_usage_mean": float(np.mean(vlm_nvdec_stats)),
                            "vlm_nvdec_usage_std": float(np.std(vlm_nvdec_stats)),
                            "vlm_nvdec_usage_p90": float(np.percentile(vlm_nvdec_stats, 90)),
                        }
                    )
                elif nvdec_stats:
                    gpu_metrics.update(
                        {
                            "vlm_nvdec_usage_mean": nvdec_stats.get("mean", 0),
                            "vlm_nvdec_usage_std": nvdec_stats.get("std", 0),
                            "vlm_nvdec_usage_p90": nvdec_stats.get("p90", 0),
                        }
                    )
            except Exception as e:
                self.logger.error(f"Error processing GPU stats: {e}")
        else:
            self.logger.warning(f"GPU stats file not found: {gpu_stats_file}")

        # Merge Prometheus/DCGM metrics if available
        if merge_prometheus and gpu_stats_file:
            prometheus_metrics = self._load_prometheus_gpu_stats(gpu_stats_file)
            if prometheus_metrics:
                gpu_metrics.update(prometheus_metrics)
            node_metrics = self._load_node_exporter_stats(gpu_stats_file)
            if node_metrics:
                gpu_metrics.update(node_metrics)

        return gpu_metrics

    def _load_prometheus_gpu_stats(self, gpu_stats_file: str) -> Dict[str, Any]:
        """
        Load Prometheus/DCGM GPU stats from companion file.
        Expected path: same dir as gpu_stats_file, with prometheus_ prefix.
        """
        try:
            dirname = os.path.dirname(gpu_stats_file)
            basename = os.path.basename(gpu_stats_file)
            # gpu_metrics_X_stats.json -> prometheus_gpu_metrics_X_stats.json
            prometheus_basename = "prometheus_" + basename
            prometheus_path = os.path.join(dirname, prometheus_basename)
            if not os.path.exists(prometheus_path):
                return {}
            with open(prometheus_path, "r") as f:
                data = json.load(f)
            stats = data.get("stats", data)
            if not isinstance(stats, dict):
                return {}
            return stats
        except Exception as e:
            self.logger.debug(f"Could not load Prometheus GPU stats: {e}")
            return {}

    def _load_node_exporter_stats(self, gpu_stats_file: str) -> Dict[str, Any]:
        """Load Node Exporter CPU/memory stats from companion file."""
        try:
            dirname = os.path.dirname(gpu_stats_file)
            basename = os.path.basename(gpu_stats_file)
            node_basename = "node_exporter_" + basename
            node_path = os.path.join(dirname, node_basename)
            if not os.path.exists(node_path):
                return {}
            with open(node_path, "r") as f:
                data = json.load(f)
            stats = data.get("stats", data)
            if not isinstance(stats, dict):
                return {}
            return stats
        except Exception as e:
            self.logger.debug("Could not load Node Exporter stats: %s", e)
            return {}

    # ================================
    # UTILITY METHODS
    # ================================

    def save_json_data(self, data: Dict, filepath: str):
        """Save dictionary data as JSON file"""
        try:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save JSON data to {filepath}: {e}")

    def save_response(self, response: requests.Response, test_dir: str, filename: str):
        """Save response data to a file"""
        filepath = os.path.join(test_dir, filename)
        try:
            with open(filepath, "w") as f:
                json.dump(response.json(), f, indent=4)
        except Exception as e:
            self.logger.error(f"Failed to save response: {str(e)}")

    def round_floats(self, obj, decimal_places: int = 2):
        """Recursively round all floating point numbers in a data structure"""
        if isinstance(obj, float):
            return round(obj, decimal_places)
        elif isinstance(obj, dict):
            return {key: self.round_floats(value, decimal_places) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self.round_floats(item, decimal_places) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self.round_floats(item, decimal_places) for item in obj)
        else:
            return obj

    def add_plots_to_excel(
        self, excel_path: str, df: pd.DataFrame, x_column: str, latency_columns: List[str]
    ):
        """
        Create latency plots using matplotlib and embed them in the Excel workbook.

        Args:
            excel_path: Path to the Excel file
            df: DataFrame with data to plot
            x_column: Column to use for x-axis
            latency_columns: List of columns to plot as y-values
        """
        try:
            import io

            import matplotlib.pyplot as plt
            from openpyxl import load_workbook
            from openpyxl.drawing.image import Image

            # Load the existing Excel workbook
            workbook = load_workbook(excel_path)

            # Calculate starting position for images (after the data)
            start_row = len(df) + 5

            # Create latency chart
            plt.figure(figsize=(12, 8))

            # Check if this is file burst mode by looking for benchmark_mode column and concurrency_level data
            is_file_burst = (
                "benchmark_mode" in df.columns
                and "concurrency_level" in df.columns
                and "file_burst" in df["benchmark_mode"].values
            )

            if is_file_burst:
                # For file burst mode: plot each test case as a separate line
                test_cases = (
                    df["test_case_id"].unique() if "test_case_id" in df.columns else [df.index[0]]
                )
                colors = ["red", "green", "blue", "orange", "purple", "brown"]

                for test_idx, test_case in enumerate(test_cases):
                    test_data = (
                        df[df["test_case_id"] == test_case] if "test_case_id" in df.columns else df
                    )

                    for i, col_name in enumerate(latency_columns):
                        if col_name in test_data.columns:
                            x_values = test_data[x_column].sort_values()
                            y_values = test_data.set_index(x_column)[col_name].reindex(x_values)

                            # Create a unique label for each test case and metric combination
                            if len(test_cases) > 1:
                                label = f"{test_case} - {col_name.replace('_', ' ').title()}"
                            else:
                                label = col_name.replace("_", " ").title()

                            plt.plot(
                                x_values,
                                y_values,
                                marker="o",
                                linewidth=2,
                                label=label,
                                color=colors[(test_idx * len(latency_columns) + i) % len(colors)],
                                markersize=6,
                            )

                plt.title("File Burst Mode: Latency vs Test Case", fontsize=14, fontweight="bold")
                plt.xlabel("Test Case ID", fontsize=12)
                plt.ylabel("Latency (seconds)", fontsize=12)
            else:
                # Original logic for single file mode: group by x_column and calculate statistics
                grouped_data = df.groupby(x_column)

                # Plot each latency column with error bars
                colors = ["red", "green", "blue", "orange"]
                for i, col_name in enumerate(latency_columns):
                    if col_name in df.columns:
                        # Calculate mean and std for each group
                        means = grouped_data[col_name].mean()
                        stds = grouped_data[col_name].std()
                        x_values = means.index

                        plt.errorbar(
                            x_values,
                            means,
                            yerr=stds,
                            marker="o",
                            linewidth=3,
                            capsize=10,
                            capthick=3,
                            label=col_name.replace("_", " ").title(),
                            color=colors[i % len(colors)],
                            elinewidth=3,
                            markersize=8,
                        )

                plt.title("Latency Trends (Mean ± Std)", fontsize=14, fontweight="bold")
                plt.xlabel(x_column.replace("_", " ").title(), fontsize=12)
                plt.ylabel("Latency (seconds)", fontsize=12)

            plt.legend(fontsize=10)
            plt.grid(True, alpha=0.3)
            plt.xticks(rotation=45)
            plt.tight_layout()

            # Save plot to bytes buffer
            img_buffer = io.BytesIO()
            plt.savefig(img_buffer, format="png", dpi=300, bbox_inches="tight")
            img_buffer.seek(0)
            plt.close()

            # Add image to Excel
            img = Image(img_buffer)
            img.width = 600  # Adjust width
            img.height = 400  # Adjust height

            # Add to Summary sheet
            if "Summary" in workbook.sheetnames:
                worksheet = workbook["Summary"]
                worksheet.add_image(img, f"A{start_row}")

            # Save the workbook with embedded images
            workbook.save(excel_path)
            self.logger.debug(f"Plots added to Excel file: {excel_path}")

        except ImportError as e:
            self.logger.warning(f"Plotting libraries not available, skipping plots: {e}")
        except Exception as e:
            self.logger.error(f"Failed to add plots to Excel: {e}")
            # Don't raise - let the Excel file be saved without plots

    def get_gpu_info_dataframe(self) -> pd.DataFrame:
        """
        Get GPU information as a pandas DataFrame for Excel reports

        Returns:
            DataFrame containing GPU specifications
        """
        gpu_info = []

        try:
            import pynvml

            pynvml.nvmlInit()
            device_count = pynvml.nvmlDeviceGetCount()

            for i in range(device_count):
                handle = pynvml.nvmlDeviceGetHandleByIndex(i)

                # Basic info
                name = pynvml.nvmlDeviceGetName(handle)

                # Memory info
                mem_info = pynvml.nvmlDeviceGetMemoryInfo(handle)
                total_mem_gb = mem_info.total / (1024**3)

                # Driver version (only need once)
                driver_version = pynvml.nvmlSystemGetDriverVersion() if i == 0 else ""

                # CUDA compute capability
                major, minor = pynvml.nvmlDeviceGetCudaComputeCapability(handle)
                compute_capability = f"{major}.{minor}"

                # Clock speeds
                try:
                    max_gpu_clock = pynvml.nvmlDeviceGetMaxClockInfo(
                        handle, pynvml.NVML_CLOCK_GRAPHICS
                    )
                    max_mem_clock = pynvml.nvmlDeviceGetMaxClockInfo(handle, pynvml.NVML_CLOCK_MEM)
                except Exception:
                    max_gpu_clock = "N/A"
                    max_mem_clock = "N/A"

                gpu_info.append(
                    {
                        "GPU Index": i,
                        "Name": name,
                        "Total Memory (GB)": f"{total_mem_gb:.2f}",
                        "Compute Capability": compute_capability,
                        "Max GPU Clock (MHz)": max_gpu_clock,
                        "Max Memory Clock (MHz)": max_mem_clock,
                        "Driver Version": driver_version,
                        "Used By": self._get_gpu_usage(i),
                    }
                )

        except Exception as e:
            self.logger.error(f"Error getting GPU info: {e}")
            gpu_info.append(
                {
                    "GPU Index": "Error",
                    "Name": f"Failed to get GPU info: {str(e)}",
                    "Total Memory (GB)": "",
                    "Compute Capability": "",
                    "Max GPU Clock (MHz)": "",
                    "Max Memory Clock (MHz)": "",
                    "Driver Version": "",
                    "Used By": "",
                }
            )

        return pd.DataFrame(gpu_info)

    def _get_gpu_usage(self, gpu_index: int) -> str:
        """Determine what the GPU is used for based on configuration"""
        usage = []
        if gpu_index in self.vlm_gpus:
            usage.append("VLM")
        return ", ".join(usage) if usage else "Not Used"

    def cleanup_resources(self):
        """Clean up any active resources"""
        with self.cleanup_lock:
            # Stop GPU monitoring
            if self.gpu_monitoring_active:
                self.stop_gpu_monitoring()

            # Clean up any tracked resources
            stream_ids = [
                resource.replace("stream_", "")
                for resource in self.active_resources
                if isinstance(resource, str) and resource.startswith("stream_")
            ]
            cleanup_errors = []
            if stream_ids:
                try:
                    self._batch_delete_streams(stream_ids)
                except Exception as e:
                    cleanup_errors.append(str(e))

                # _batch_delete_streams only drops streams the server confirmed
                # gone, so anything still tracked here is still holding GPU
                # resources. Say so loudly -- a silent teardown failure makes
                # every later scenario run against a polluted server.
                undeleted = [
                    resource
                    for resource in self.active_resources
                    if isinstance(resource, str) and resource.startswith("stream_")
                ]
                if undeleted:
                    self.logger.error(
                        f"{len(undeleted)}/{len(stream_ids)} streams could not be deleted; "
                        f"they remain active on the server and will skew subsequent "
                        f"scenarios: {[r.replace('stream_', '') for r in undeleted]}"
                    )
                    cleanup_errors.append(
                        f"{len(undeleted)}/{len(stream_ids)} live streams remain tracked"
                    )

            for resource in list(self.active_resources):
                try:
                    if isinstance(resource, str) and resource.startswith("file_"):
                        # Clean up uploaded file
                        file_id = resource.replace("file_", "")
                        self.make_api_call(f"/files/{file_id}", method="DELETE")
                except requests.exceptions.HTTPError as e:
                    # Ignore 400/404 errors - resource may already be deleted
                    if e.response is not None and e.response.status_code in [400, 404]:
                        self.logger.debug(f"Resource {resource} already deleted or not found")
                    else:
                        self.logger.error(f"Error cleaning up resource {resource}: {e}")
                        cleanup_errors.append(f"{resource}: {e}")
                except Exception as e:
                    self.logger.error(f"Error cleaning up resource {resource}: {e}")
                    cleanup_errors.append(str(e))

            if cleanup_errors:
                raise RuntimeError(
                    "Resource cleanup failed; refusing to clear local tracking: "
                    + "; ".join(cleanup_errors[:5])
                )

            self.active_resources.clear()
            self.logger.debug("Resource cleanup completed")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup_resources()

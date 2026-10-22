# SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""HTTP client for communicating with the plugin service."""

import json
import logging
import os
import time
from typing import Any

import requests
from pydantic import BaseModel, ValidationError
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src.lib.ingestion.models import IngestionContext

logger = logging.getLogger(__name__)

# Default retry configuration for transient HTTP errors
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BACKOFF_FACTOR = 1.0  # 1s, 2s, 4s
RETRY_STATUS_FORCELIST = (502, 503, 504)


class PluginClientError(Exception):
    """Raised when plugin client operations fail."""

    pass


class PluginJobStatus(BaseModel):
    """Status of an ingestion job from plugin service."""

    job_id: str
    status: str
    workload_id: str | None = None
    client_id: str | None = None
    records_received: int = 0
    records_indexed: int = 0
    records_skipped: int = 0
    next_watermark: str | None = None
    errors: list[str] = []
    plugin_metrics: dict[str, Any] = {}
    started_at: str | None = None
    completed_at: str | None = None


class IngestionPluginClient:
    """HTTP client for the plugin service with built-in retry logic."""

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 60.0,
        max_wait_timeout: float = 7200.0,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff_factor: float = DEFAULT_RETRY_BACKOFF_FACTOR,
    ):
        self.base_url = base_url or os.environ.get("PLUGIN_SERVICE_URL", "http://localhost:8001")
        self.timeout = timeout
        self.max_wait_timeout = max_wait_timeout
        self._session = requests.Session()

        # Configure retry strategy for transient failures
        retry_strategy = Retry(
            total=max_retries,
            backoff_factor=retry_backoff_factor,
            status_forcelist=RETRY_STATUS_FORCELIST,
            allowed_methods=["GET", "POST", "DELETE"],
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

    def close(self):
        """Close the HTTP session."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _url(self, path: str) -> str:
        """Build full URL from path."""
        return f"{self.base_url}{path}"

    def list_plugins(self) -> list[dict[str, str]]:
        """List available plugins from the plugin service."""
        try:
            response = self._session.get(self._url("/plugins"), timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            raise PluginClientError(f"Failed to list plugins: {e}") from e

    def execute_and_wait(
        self,
        plugin_name: str,
        context: IngestionContext,
        job_type: str,
        dataset_type: str,
    ) -> dict:
        """
        Execute a plugin, wait for completion, and return job status.

        The plugin container handles data loading into Elasticsearch directly.
        This method just starts the job and waits for completion.

        Args:
            plugin_name: Name of the plugin to execute
            context: Ingestion context with configuration
            job_type: Type of job ("llm_job" or "embedding_job")
            dataset_type: Type of data (e.g., "bug_pairs", "triplet")

        Returns:
            Dictionary with job status and metrics

        Raises:
            PluginClientError: If execution fails
        """
        logger.info(
            f"Executing plugin '{plugin_name}' for {context.workload_id}/{context.client_id} "
            f"(job_type={job_type}, dataset_type={dataset_type})"
        )

        try:
            payload = {
                "plugin_name": plugin_name,
                "context": context.model_dump(mode="json"),
                "job_type": job_type,
                "dataset_type": dataset_type,
            }
            response = self._session.post(self._url("/execute"), json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
            job_id = data["job_id"]

            # The plugin service may return an existing active job instead of
            # creating a duplicate.  Log accordingly so callers understand what
            # happened, then proceed to monitor the (new or existing) job.
            if data.get("message"):
                logger.info(
                    f"Reusing existing ingestion job {job_id} "
                    f"(status={data.get('status')}): {data['message']}"
                )
            else:
                logger.info(f"Started ingestion job: {job_id}")

        except (json.JSONDecodeError, KeyError) as e:
            raise PluginClientError(f"Invalid response from execute endpoint: {e}") from e
        except requests.exceptions.RequestException as e:
            raise PluginClientError(f"Failed to start plugin execution: {e}") from e

        try:
            job_status = self._wait_for_completion(job_id)
            return job_status
        finally:
            self._cleanup_job(job_id)

    def _wait_for_completion(self, job_id: str, poll_interval: float = 10.0) -> dict:
        """Poll job status until completion.

        Args:
            job_id: The job ID to poll.
            poll_interval: Seconds between polls (default 10s).

        Returns:
            Job status as a dictionary.
        """
        start_time = time.time()
        consecutive_errors = 0
        max_consecutive_errors = 5

        while True:
            try:
                response = self._session.get(self._url(f"/jobs/{job_id}"), timeout=self.timeout)
                response.raise_for_status()
                status = PluginJobStatus(**response.json())
                consecutive_errors = 0  # Reset on success

                if status.status == "completed":
                    logger.info(
                        f"Job {job_id} completed: {status.records_indexed} indexed, "
                        f"{status.records_skipped} skipped"
                    )
                    return status.model_dump()

                if status.status == "failed":
                    error_msg = "; ".join(status.errors) if status.errors else "Unknown error"
                    raise PluginClientError(f"Job {job_id} failed: {error_msg}")

                if status.status == "cancelled":
                    raise PluginClientError(f"Job {job_id} was cancelled")

                elapsed = time.time() - start_time
                if elapsed > self.max_wait_timeout:
                    raise PluginClientError(
                        f"Job {job_id} timed out after {self.max_wait_timeout}s"
                    )

                logger.debug(
                    f"Job {job_id} status: {status.status}, "
                    f"received: {status.records_received}, elapsed: {elapsed:.1f}s"
                )
                time.sleep(poll_interval)

            except (json.JSONDecodeError, ValidationError) as e:
                raise PluginClientError(f"Invalid response from job status endpoint: {e}") from e
            except requests.exceptions.ConnectionError as e:
                consecutive_errors += 1
                if consecutive_errors >= max_consecutive_errors:
                    raise PluginClientError(
                        f"Failed to get job status after {max_consecutive_errors} "
                        f"consecutive connection errors: {e}"
                    ) from e
                logger.warning(
                    f"Connection error polling job {job_id} "
                    f"(attempt {consecutive_errors}/{max_consecutive_errors}): {e}"
                )
                time.sleep(poll_interval)
            except requests.exceptions.RequestException as e:
                raise PluginClientError(f"Failed to get job status: {e}") from e

    def _cleanup_job(self, job_id: str) -> None:
        """Delete job from plugin service."""
        try:
            response = self._session.delete(self._url(f"/jobs/{job_id}"), timeout=self.timeout)
            response.raise_for_status()
            logger.debug(f"Cleaned up job {job_id}")
        except requests.exceptions.RequestException as e:
            logger.warning(f"Failed to cleanup job {job_id}: {e}")

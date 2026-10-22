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

"""Common data ingestion service for jobs and triggers.

Watermark state is managed by the ingestion-plugins service (server). This client
only triggers execution and returns the result; the server reads/updates
last_sync_watermark so manual and trigger-based ingestion stay in sync.
"""

import logging
from datetime import datetime

from pymongo.database import Database

from src.lib.ingestion.models import IngestionContext, IngestionResult
from src.lib.ingestion.plugin_client import IngestionPluginClient, PluginClientError

logger = logging.getLogger(__name__)


class DataIngestionService:
    """Service for running data ingestion operations."""

    def __init__(self, db: Database, plugin_client: IngestionPluginClient | None = None):
        self.db = db
        self._owns_client = plugin_client is None
        self.plugin_client = plugin_client or IngestionPluginClient()

    def close(self):
        """Close owned resources."""
        if self._owns_client:
            self.plugin_client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def run_ingestion(
        self,
        workload_id: str,
        client_id: str,
        plugin_name: str,
        config: dict,
        job_type: str,
        dataset_type: str,
        trigger_id: str | None = None,
        job_id: str | None = None,
    ) -> IngestionResult:
        """Run data ingestion via plugin service. Watermark state is managed server-side."""
        if not plugin_name:
            raise ValueError("plugin_name is required")
        if not dataset_type:
            raise ValueError("dataset_type is required")

        logger.info(f"Starting ingestion for {workload_id}/{client_id} ({plugin_name})")

        # Context: server will resolve last_sync_watermark from its own state
        context = IngestionContext(
            workload_id=workload_id,
            client_id=client_id,
            last_sync_watermark=None,
            config=config,
        )

        started_at = datetime.utcnow()
        result = IngestionResult(
            started_at=started_at, plugin_name=plugin_name, dataset_type=dataset_type
        )

        try:
            job_status = self.plugin_client.execute_and_wait(
                plugin_name=plugin_name,
                context=context,
                job_type=job_type,
                dataset_type=dataset_type,
            )

            max_timestamp = None
            if job_status.get("next_watermark"):
                max_timestamp = datetime.fromisoformat(job_status["next_watermark"])

            completed_at = datetime.utcnow()
            result = IngestionResult(
                records_fetched=job_status.get("records_received", 0),
                records_loaded=job_status.get("records_indexed", 0),
                records_skipped=job_status.get("records_skipped", 0),
                max_timestamp=max_timestamp,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=(completed_at - started_at).total_seconds(),
                errors=job_status.get("errors", []),
                plugin_name=plugin_name,
                dataset_type=dataset_type,
                metadata=job_status.get("plugin_metrics", {}),
            )

        except PluginClientError as e:
            logger.error(f"Plugin execution failed: {e}")
            result.completed_at = datetime.utcnow()
            result.duration_seconds = (result.completed_at - started_at).total_seconds()
            result.errors = [str(e)]
            return result

        return result


def run_data_ingestion(
    db: Database,
    workload_id: str,
    client_id: str,
    plugin_name: str,
    config: dict,
    job_type: str,
    dataset_type: str,
    trigger_id: str | None = None,
    job_id: str | None = None,
) -> IngestionResult:
    """Convenience function to run data ingestion."""
    with DataIngestionService(db) as service:
        return service.run_ingestion(
            workload_id=workload_id,
            client_id=client_id,
            plugin_name=plugin_name,
            config=config,
            job_type=job_type,
            dataset_type=dataset_type,
            trigger_id=trigger_id,
            job_id=job_id,
        )

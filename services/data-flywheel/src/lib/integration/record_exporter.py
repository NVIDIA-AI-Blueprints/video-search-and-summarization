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

from datetime import datetime, time

from elasticsearch import Elasticsearch, helpers

from src.config import DataSplitConfig, EmbeddingDataSplitConfig
from src.lib.integration.es_client import (
    ES_COLLECTION_NAME,
    ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME,
    get_es_client,
)
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.record_exporter")


class RecordExporter:
    """Generic record exporter that works with any Elasticsearch index.

    Fetches raw records from Elasticsearch. Data validation and transformation
    should be handled by dataset creators (e.g., DatasetCreator, DPODatasetCreator).
    """

    es_client: Elasticsearch

    def __init__(self):
        self.es_client = get_es_client()

    def decide_limit(self, limit: int | None, multiplier: int = 2) -> int:
        if limit is None:
            return float("inf")
        return limit * multiplier

    def get_records(
        self,
        client_id: str,
        workload_id: str,
        split_config: EmbeddingDataSplitConfig | DataSplitConfig,
        index_name: str = ES_COLLECTION_NAME,
        multiplier: int = 2,
    ) -> list[dict]:
        """Fetch records from Elasticsearch.

        Args:
            client_id: Client identifier
            workload_id: Workload identifier
            split_config: Configuration for data splitting
            index_name: Name of the Elasticsearch index

        Returns:
            List of raw records from Elasticsearch
        """
        logger.info(f"Pulling data from Elasticsearch for workload {workload_id}")

        # Validate date parameters - these only exist on EmbeddingDataSplitConfig
        from_date = getattr(split_config, "from_date", None)
        to_date = getattr(split_config, "to_date", None)
        if (from_date is None) != (to_date is None):
            raise ValueError(
                "Both from_date and to_date must be provided together or both must be None"
            )

        if from_date and to_date:
            # Validate date range
            if from_date > to_date:
                raise ValueError("from_date cannot be later than to_date")

            # Convert to Unix timestamps for Elasticsearch query
            from_timestamp = int(datetime.combine(from_date, time.min).timestamp())
            to_timestamp = int(datetime.combine(to_date, time.max).timestamp())

            logger.info(
                f"Filtering records from {from_date} to {to_date} (timestamps: {from_timestamp} - {to_timestamp})"
            )

        # Define the base search query
        must_clauses = [
            {"match": {"client_id": client_id}},
            {"match": {"workload_id": workload_id}},
        ]

        # Add date range filter if dates are provided
        if from_date and to_date:
            must_clauses.append(
                {"range": {"timestamp": {"gte": from_timestamp, "lte": to_timestamp}}}
            )

        search_query = {
            "query": {"bool": {"must": must_clauses}},
            "sort": [{"timestamp": {"order": "desc"}}],
        }
        max_records = self.decide_limit(split_config.limit, multiplier)
        scroll_size = min(1000, max_records)  # Process in chunks of 1000

        # Initial search with scroll
        response = self.es_client.search(
            index=index_name,
            body=search_query,
            scroll="2m",  # Keep scroll context for 2 minutes
            size=scroll_size,
        )

        records = []

        try:
            # Extract records from initial response first
            hits = response["hits"]["hits"]
            records.extend([hit["_source"] for hit in hits])

            # Check scroll_id after processing initial hits
            scroll_id = response.get("_scroll_id")
            if scroll_id is None:
                logger.warning(
                    "No scroll_id in initial response, cannot continue scrolling but collected initial batch"
                )
                return records  # Return what we have and exit early

            # Continue scrolling until we have enough records or no more data
            while len(hits) > 0 and len(records) < max_records:
                response = self.es_client.scroll(scroll_id=scroll_id, scroll="2m")
                hits = response["hits"]["hits"]

                # Process hits first, even if scroll context might be lost
                remaining_needed = max_records - len(records)
                if remaining_needed != float("inf"):
                    # Cast remaining_needed to int since it's a difference between a float('inf') and an int
                    slice_size = int(remaining_needed)
                    new_records = [hit["_source"] for hit in hits[:slice_size]]
                else:
                    # If max_records is infinite (pull all), process the entire batch
                    new_records = [hit["_source"] for hit in hits]
                records.extend(new_records)

                # Check scroll_id after processing hits
                scroll_id = response.get("_scroll_id")
                if scroll_id is None:
                    logger.warning(
                        "Scroll context lost after processing current batch, exiting scroll loop"
                    )
                    break
        except Exception as e:
            logger.error(f"Error pulling data from Elasticsearch: {e}")
            raise e
        finally:
            # Clear the scroll context - always executed even on failure
            if scroll_id:
                try:
                    self.es_client.clear_scroll(scroll_id=scroll_id)
                except Exception as e:
                    logger.warning(f"Failed to clear scroll context: {e}")

        logger.info(
            f"Found {len(records)} records for client_id {client_id} and workload_id {workload_id}"
        )
        return records

    def get_similar_records(
        self,
        client_id: str,
        workload_id: str,
        index_name: str = ES_EMBEDDINGS_SIMILAR_DATA_COLLECTION_NAME,
        chunk_size: int = 1000,
    ) -> list[dict]:
        """
        Retrieve similarity relationship records from Elasticsearch.

        Args:
            index_name: Name of the Elasticsearch index to query
            chunk_size: Number of records to retrieve per batch

        Returns:
            List of similarity relationship records
        """
        logger.info(f"Retrieving similarity records from index: {index_name}")

        all_records = []

        must_clauses = [
            {"match": {"client_id": client_id}},
            {"match": {"workload_id": workload_id}},
        ]

        try:
            # Use scan helper for efficient scrolling
            for hit in helpers.scan(
                client=self.es_client,
                query={"query": {"bool": {"must": must_clauses}}},
                index=index_name,
                size=chunk_size,
                scroll="5m",
            ):
                all_records.append(hit["_source"])

        except Exception as e:
            logger.error(f"Error retrieving similarity records: {e}", exc_info=True)
            raise

        logger.info(f"Successfully retrieved {len(all_records)} similarity records")
        return all_records

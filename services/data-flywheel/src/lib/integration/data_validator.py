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
import random
from typing import Any

from src.api.models import WorkloadClassification
from src.config import DataSplitConfig
from src.lib.integration.openai_format_validator import OpenAIFormatValidator
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.data_validator")


class DataValidator:
    """Handles validation of dataset records according to OpenAI format and quality filters."""

    def __init__(self):
        self.validation_stats = {
            "total_records": 0,
            "valid_openai_format": 0,
            "invalid_format": 0,
            "removed_quality_filters": 0,
            "deduplicated_queries": 0,
            "final_selected": 0,
        }
        self.openai_validator = OpenAIFormatValidator()

    def validate_records_count(
        self, record_length: int, min_total_records: int, eval_size: int, limit: int
    ) -> None:
        if record_length < min_total_records:
            msg = (
                "Not enough records found for the given workload. "
                + f"A minimum of {min_total_records} records is required, "
                + f"but only {record_length} were found."
            )
            logger.error(msg)
            raise ValueError(msg)

        if eval_size > record_length:
            msg = (
                "eval_size cannot be larger than the total number of records. "
                + f"eval_size is {eval_size}, but only {record_length} records were found."
            )
            logger.error(msg)
            raise ValueError(msg)

        if limit < min_total_records:
            msg = (
                "limit cannot be less than the minimum number of records. "
                + f"limit is {limit}, but the minimum number of records is {min_total_records}."
            )
            logger.error(msg)
            raise ValueError(msg)

        return

    def validate_records(
        self,
        records: list[dict[str, Any]],
        workload_type: WorkloadClassification,
        split_config: DataSplitConfig,
    ) -> list[dict[str, Any]]:
        """
        Validate and process records according to requirements.
        Flow:
        1. Validate OpenAI format
        2. Apply quality filters based on workload type
        3. Remove duplicates
        4. Select required number of records

        Args:
            records: List of records to validate
            workload_type: Type of workload (GENERIC or TOOL_CALLING)
            limit: Maximum number of records to return
            min_records: Minimum number of records to return

        Returns:
            List of validated records

        Raises:
            ValueError: If insufficient valid records after filtering
        """

        limit = split_config.limit
        min_records = split_config.min_total_records
        eval_size = split_config.eval_size

        self.validation_stats["total_records"] = len(records)
        logger.info(
            f"Starting validation of {len(records)} records with limit={limit}, \
                min_records={min_records},  \
                workload_type={workload_type}"
        )

        # Step 1: Validate record count
        self.validate_records_count(len(records), min_records, eval_size, limit)

        # Step 2: Validate OpenAI format
        valid_openai_records = []
        invalid_records = []

        for record in records:
            if self.openai_validator.validate_chat_completion_format(record):
                valid_openai_records.append(record)
            else:
                invalid_records.append(record)
                self.validation_stats["invalid_format"] += 1

        self.validation_stats["valid_openai_format"] = len(valid_openai_records)
        logger.info(
            f"Found {len(valid_openai_records)} records in valid OpenAI format, {len(invalid_records)} invalid"
        )

        # Step 3: Apply quality filters based on workload type
        if workload_type == WorkloadClassification.TOOL_CALLING:
            filtered_records = self.get_tool_calling_records(valid_openai_records, split_config)
        else:
            filtered_records = valid_openai_records

        logger.info(f"After quality filters: {len(filtered_records)} records remain")

        # Step 4: Remove duplicates
        deduplicated_records = self._deduplicate_records(filtered_records)
        logger.info(f"After deduplication: {len(deduplicated_records)} records remain")

        # Step 5: Check if we have enough records
        if len(deduplicated_records) < min_records:
            raise ValueError(
                f"Insufficient valid records. Found {len(deduplicated_records)} but need {min_records}. "
                f"Total records: {len(records)}, valid OpenAI format: {len(valid_openai_records)}, "
                f"after quality filters: {len(filtered_records)}. "
                f"Please provide more valid records."
            )

        # Step 6: Random selection
        if split_config.random_seed:
            random.seed(split_config.random_seed)

        # if limit is not set then limit=len(deduplicated_records)
        # else limit=min(limit, len(deduplicated_records)) to avoid random sampling error
        limit = (
            len(deduplicated_records) if limit is None else min(limit, len(deduplicated_records))
        )
        selected_records = random.sample(deduplicated_records, limit)

        self.validation_stats["final_selected"] = len(selected_records)
        self._log_validation_stats()

        return selected_records

    def get_tool_calling_records(
        self, records: list[dict[str, Any]], split_config: DataSplitConfig
    ) -> list[dict[str, Any]]:
        """
        Get tool calling records from a list of records.

        Steps:
        1. Validate tool calling quality
            Checks for structure:
                "response":
                    "choices": [
                        "message": {
                            "tool_calls": [
                            {
                                "function": {
                                    "arguments": "..."
                                }
                            }
                        ]
                    ]
        2. Parse function arguments to JSON objects for tool calling records
            If responses.choices.message.tool_calls.function.arguments is a string, parse it to a JSON object

        Returns:
            List of records that pass the tool calling quality filters

        Args:
            records: List of records to validate

        """
        filtered_records = []
        for record in records:
            if self.openai_validator.validate_tool_calling_quality(record):
                filtered_records.append(record)
            else:
                self.validation_stats["removed_quality_filters"] += 1

        logger.info(f"After quality filters: {len(filtered_records)} records remain")

        # Parse function arguments to JSON objects for tool calling records
        if split_config.parse_function_arguments:
            parsed_records = []
            for record in filtered_records:
                if self.openai_validator._parse_function_arguments_to_json(record):
                    # Keep records where parsing succeeded
                    parsed_records.append(record)
                else:
                    # Drop records where parsing failed
                    self.validation_stats["removed_quality_filters"] += 1

            logger.info(f"After JSON parsing: {len(parsed_records)} records remain")
            return parsed_records

        return filtered_records

    def _deduplicate_records(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Remove duplicate records based on user queries.

        This method identifies duplicates by looking at all user messages in each record.
        Records with identical user queries are considered duplicates.

        Example:
            If two records both have user messages ["What's the weather?", "In Boston"],
            only the first one is kept.

        Returns:
            List of unique records (duplicates removed)

        Note:
            - Only looks at user messages (role="user") for deduplication
            - Records without user messages are always kept
            - First occurrence of duplicate is kept, later ones are removed
        """
        seen_queries = set()
        unique_records = []

        for record in records:
            try:
                messages = record.get("request", {}).get("messages", [])

                # Check if messages is actually a list
                if not isinstance(messages, list):
                    # Keep records with malformed messages
                    unique_records.append(record)
                    continue

                user_messages = [
                    msg["content"]
                    for msg in messages
                    if isinstance(msg, dict) and msg.get("role") == "user" and msg.get("content")
                ]

                # Convert to tuple for hashability
                query_key = tuple(user_messages) if user_messages else None

                if query_key and query_key not in seen_queries:
                    seen_queries.add(query_key)
                    unique_records.append(record)
                elif query_key:
                    self.validation_stats["deduplicated_queries"] += 1
                else:
                    # Keep records without identifiable user messages
                    unique_records.append(record)
            except (KeyError, TypeError) as e:
                logger.debug(f"Error in deduplication: {e}")
                unique_records.append(record)

        return unique_records

    def _log_validation_stats(self):
        """Log validation statistics."""
        stats = self.validation_stats
        logger.info("Validation completed with the following stats:")
        logger.info("-------------------------------------------------")
        logger.info(f"  Total records:              {stats['total_records']}")
        logger.info(f"  Valid OpenAI format:        {stats['valid_openai_format']}")
        logger.info(f"  Invalid format:             {stats['invalid_format']}")
        logger.info(f"  Removed (quality filters):  {stats['removed_quality_filters']}")
        logger.info(f"  Deduplicated:               {stats['deduplicated_queries']}")
        logger.info(f"  Final selected:             {stats['final_selected']}")
        logger.info("-------------------------------------------------")

    def get_validation_stats(self) -> dict[str, int]:
        """Get validation statistics."""
        return self.validation_stats.copy()

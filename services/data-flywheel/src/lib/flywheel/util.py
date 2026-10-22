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
from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from typing import TYPE_CHECKING, Any, TypedDict

import numpy as np

if TYPE_CHECKING:
    from src.api.models import WorkloadClassification
    from src.config import DataSplitConfig, EmbeddingDataSplitConfig

from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.flywheel.util")


class Message(TypedDict):
    role: str
    content: str


class ToolCall(TypedDict):
    id: str
    type: str
    function: dict[str, Any]


class Request(TypedDict):
    messages: list[Message]


class Choice(TypedDict):
    role: str
    content: str
    tool_calls: list[ToolCall] | None
    message: Message | None


class Response(TypedDict):
    choices: list[Choice]


class Record(TypedDict):
    request: Request
    response: Response


def extract_user_query(record: Record) -> str | None:
    """Extracts the content from the 'user' role message. In case there are more than one then take the last one"""
    usr_msg = None
    for message in record.get("request", {}).get("messages", []):
        if message.get("role") == "user":
            usr_msg = message.get("content")
    return usr_msg  # Return None if no user message is found


def get_tool_name(record: Record) -> str:
    """Get the tool name from the record."""
    tool_calls = record["response"]["choices"][0]["message"].get("tool_calls")
    if tool_calls and len(tool_calls) > 0:
        return tool_calls[0]["function"]["name"]
    return "no_tool"


def identify_workload_type(records: list[Record]) -> WorkloadClassification:
    """
    Identify the type of workload from the response.
    """
    from src.api.models import WorkloadClassification

    # Check for tool calls in response messages
    for record in records:
        try:
            tool_calls = record["response"]["choices"][0]["message"].get("tool_calls")
            if tool_calls and len(tool_calls) > 0:
                return WorkloadClassification.TOOL_CALLING
        except (KeyError, IndexError):
            continue

    return WorkloadClassification.GENERIC


def format_evaluator(records: list[Record]) -> list[Record]:
    """
    Format records specifically for evaluation by converting tool call function arguments
    to JSON strings in the request section only.

    This ensures OpenAI API compatibility during evaluation while preserving the original
    data structure for other purposes.

    Also adds an ``expected_response`` field that aliases ``response``. NMP 26.03's
    ``llm-judge`` metric eval pod renders the judge prompt with both ``item.*``
    (dataset row) and ``sample.*`` (target-model output). The eval pod warns and
    overrides any dataset column that overlaps with the model-response field:

        "Dataset columns {'response'} overlap with model response keys. Model
         response values will be used."

    So ``{{ item.response.choices[0].message.tool_calls }}`` in the judge prompt
    template resolves to the *target's* prediction (same value as ``sample.*``)
    instead of the dataset's ground truth — the judge sees identical inputs on
    both sides and produces output that doesn't match the rating regex, leading
    to ``count=0, nan_count=100``. The ``expected_response`` alias has no name
    collision, so the judge prompt can reach the actual ground truth via
    ``{{ item.expected_response.choices[0].message.tool_calls }}``.

    Args:
        records: List of records to format

    Returns:
        List of formatted records with tool call arguments as JSON strings
    """
    formatted_records = []

    for record in records:
        # Create a deep copy to avoid modifying the original record
        formatted_record = deepcopy(record)

        # Only process request messages for tool call argument formatting
        if "request" in formatted_record and "messages" in formatted_record["request"]:
            for message in formatted_record["request"]["messages"]:
                if message.get("tool_calls"):
                    for tool_call in message["tool_calls"]:
                        if "function" in tool_call and "arguments" in tool_call["function"]:
                            arguments = tool_call["function"]["arguments"]
                            # Convert arguments to JSON string if they're currently an object
                            if isinstance(arguments, dict):
                                try:
                                    tool_call["function"]["arguments"] = json.dumps(arguments)
                                except (TypeError, ValueError) as e:
                                    logger.warning(
                                        f"Failed to serialize tool call arguments: {arguments}, error: {e}"
                                    )
                                    # Keep original value if serialization fails

        # Add ``expected_response`` alias so the judge prompt can reach the
        # ground truth without colliding with the eval pod's ``sample.response``.
        if "response" in formatted_record and "expected_response" not in formatted_record:
            formatted_record["expected_response"] = formatted_record["response"]

        formatted_records.append(formatted_record)

    return formatted_records


def _safe_stratified_split(data, labels, test_size, seed):
    """
    Split dataset with stratification.
    stratify the data if the number of classes is greater than 1 and the test size is greater than or equal to the number of classes.
    """

    try:
        from sklearn.model_selection import train_test_split
    except ImportError:
        raise ImportError(
            "sklearn is required for data splitting functionality. "
            "Install it with: pip install scikit-learn"
        ) from None

    num_classes = len(set(labels))
    stratify = labels if (num_classes > 1 and test_size >= num_classes) else None
    return train_test_split(data, test_size=test_size, stratify=stratify, random_state=seed)


def split_records(
    records: list[Record], split_config: DataSplitConfig
) -> tuple[list[Record], list[Record], list[Record]]:
    """Split records into eval, train, and validation sets with class-aware splitting."""
    if split_config.random_seed is not None:
        np.random.seed(split_config.random_seed)  # for sklearn

    labels = [get_tool_name(r) for r in records]
    counts = Counter(labels)

    # Create stratify_label: rare classes are grouped as "others"
    stratify_labels = []
    for label in labels:
        if counts[label] == 1:
            stratify_labels.append("others")
        else:
            stratify_labels.append(label)

    # Eval split using stratify_labels
    eval_size = min(split_config.eval_size, len(records))
    rest_records, eval = _safe_stratified_split(
        records, stratify_labels, eval_size, split_config.random_seed
    )

    # Train/val split from remaining records
    rest_labels = [get_tool_name(r) for r in rest_records]
    rest_stratify_labels = []
    rest_counts = Counter(rest_labels)
    for label in rest_labels:
        if rest_counts[label] == 1:
            rest_stratify_labels.append("others")
        else:
            rest_stratify_labels.append(label)

    train, val = _safe_stratified_split(
        rest_records, rest_stratify_labels, split_config.val_ratio, split_config.random_seed
    )

    return eval, train, val


def format_training_data(
    records: list[Record], workload_type: WorkloadClassification
) -> list[dict[str, Any]]:
    """Format training data for the model.
    Args:
        records: List of conversation records containing request and response data
    Returns:
        List of message sequences where each sequence contains the conversation
        history followed by the model's response
    Raises:
        KeyError: If required fields are missing from the record structure
        IndexError: If response choices are empty
    """
    from src.api.models import WorkloadClassification

    training_data = []

    for record in records:
        try:
            # Deep copy to avoid modifying original data
            messages = deepcopy(record["request"]["messages"])

            # Validate response structure
            if not record["response"]["choices"]:
                raise IndexError(f"No choices found in response: {record}")
            # Current customizer expects non-empty content for assistant messages
            # workaround to convert None to ""
            # TODO: remove this once customizer is updated
            # for tool-calling workloads, convert response content to ""
            rec = {}

            for message in messages:
                if message["role"] == "assistant" and message["content"] is None:
                    message["content"] = ""

            response_message = record["response"]["choices"][0]["message"]
            if workload_type == WorkloadClassification.TOOL_CALLING:
                response_message["content"] = ""
                rec["tools"] = record["request"]["tools"]

            messages.append(response_message)
            rec["messages"] = messages

            training_data.append(rec)

        except (KeyError, IndexError) as e:
            # Log error but continue processing other records
            logger.error(f"Error processing record: {e}")
            continue

    return training_data


def split_embedding_records(
    records: list, split_config: EmbeddingDataSplitConfig
) -> tuple[list, list, list]:
    """Splits records into Train, Validation, and Evaluation sets for embedding jobs."""
    try:
        from sklearn.model_selection import train_test_split
    except ImportError:
        raise ImportError(
            "sklearn is required for data splitting functionality. "
            "Install it with: pip install scikit-learn"
        ) from None

    if split_config.random_seed is not None:
        np.random.seed(split_config.random_seed)  # for sklearn

    if len(records) < split_config.min_total_records or len(records) < split_config.eval_size:
        raise ValueError(
            f"Not enough records ({len(records)}). Minimum required: {split_config.min_total_records}, "
            f"Evaluation set requires at least: {split_config.eval_size}"
        )

    # 1. Split Evaluation set (using fixed size)
    remaining_records, eval_records = train_test_split(
        records, test_size=split_config.eval_size, random_state=split_config.random_seed
    )

    # 2. Split Validation set from the remaining (using ratio)
    if not remaining_records:  # Check if remaining_records is empty
        train_records, val_records = [], []
    else:
        train_records, val_records = train_test_split(
            remaining_records,
            test_size=split_config.val_ratio,
            random_state=split_config.random_seed,
        )

    # Log/print the result
    logger.info(
        f"Split {len(records)} records: train={len(train_records)}, "
        f"val={len(val_records)}, eval={len(eval_records)}"
        f"split_config: {split_config}"
    )

    return train_records, val_records, eval_records


def format_embedding_training_data(records: list[dict]) -> list[dict]:
    """
    Format records for embedding training in triplet format.

    Args:
        records: Raw records containing query, pos_doc, and optionally neg_doc fields
        max_length: Maximum character length for text fields (default: 65500)

    Returns:
        Formatted records suitable for embedding training with triplet loss
    """
    formatted = []
    skipped_count = 0

    for idx, record in enumerate(records):
        try:
            # Validate required fields
            if "query" not in record or "pos_doc" not in record:
                logger.warning(
                    f"Record {idx} missing required fields. "
                    f"Has query: {'query' in record}, Has pos_doc: {'pos_doc' in record}"
                )
                skipped_count += 1
                continue

            # Create formatted record with all required fields
            formatted_record = {
                "query": str(record["query"]),
                "pos_doc": str(record["pos_doc"]),
                "neg_doc": record.get("neg_doc", []),
            }

            # Ensure neg_doc is always a list
            if not isinstance(formatted_record["neg_doc"], list):
                # If it's a single string, wrap it in a list
                formatted_record["neg_doc"] = [str(formatted_record["neg_doc"])]
            else:
                # If it's a list, ensure all elements are strings
                formatted_record["neg_doc"] = [str(neg) for neg in formatted_record["neg_doc"]]

            formatted.append(formatted_record)

        except Exception as e:
            logger.error(f"Error formatting record {idx}: {e}")
            skipped_count += 1
            continue

    if skipped_count > 0:
        logger.warning(f"Skipped {skipped_count} records during formatting")

    logger.info(f"Formatted {len(formatted)} records for embedding training")
    return formatted


def truncate_embedding_records(records: list[dict], max_length: int = 64000) -> list[dict]:
    """
    Truncate text fields in embedding records to prevent exceeding character limits.

    Args:
        records: List of embedding records with query, pos_doc, and neg_doc fields
        max_length: Maximum character length allowed (default: 64000)

    Returns:
        List of records with truncated text fields
    """

    def _truncate_text(text: str, max_len: int) -> tuple[str, bool]:
        """Truncate text if needed and return (text, was_truncated)."""
        if len(text) > max_len:
            return text[:max_len], True
        return text, False

    truncated_records = []
    stats = {"query": 0}

    for record in records:
        truncated_record = record.copy()

        # Truncate query and pos_doc fields
        if "query" in truncated_record and isinstance(truncated_record["query"], str):
            truncated_record["query"], was_truncated = _truncate_text(
                truncated_record["query"], max_length
            )
            if was_truncated:
                stats["query"] += 1

        truncated_records.append(truncated_record)

    # Log summary
    total = sum(stats.values())
    if total > 0:
        logger.info(f"Truncated {total} fields: query={stats['query']}")
    else:
        logger.info(f"No truncation needed (all fields <= {max_length} chars)")

    return truncated_records

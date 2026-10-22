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

import json
from collections import defaultdict
from copy import deepcopy
from typing import Any

import tiktoken

from src.api.models import WorkloadClassification
from src.backends.factory import get_backend_provider
from src.config import ICLConfig
from src.lib.flywheel.util import Message, Record
from src.lib.integration.es_client import (
    get_es_client,
    index_embeddings_to_es,
    search_similar_embeddings,
)
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.icl_selection")

DEFAULT_SYSTEM_MESSAGE = """You are a helpful assistant that can answer questions and help with tasks.
Here are some examples of how you should respond to different types of requests:"""


class ICLSelection:
    """Handles ICL example selection using various strategies."""

    def __init__(self, config: ICLConfig, workload_id: str, client_id: str):
        self.config = config
        self.workload_id = workload_id
        self.es_client = get_es_client()
        self.client_id = client_id

        if self.config.example_selection == "semantic_similarity":
            embedding_config = self.config.similarity_config.embedding_nim_config
            endpoint_url = embedding_config.get_endpoint_url()
            self.embedding_client = get_backend_provider().embedding(
                endpoint_url=endpoint_url,
                model_name=embedding_config.model_name,
                api_key=embedding_config.api_key if embedding_config.is_remote else None,
            )

    def estimate_tokens(self, text: str, buffer_percent: int = 20) -> int:
        """Estimate tokens in text with a safety buffer."""
        if not text:
            return 0

        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            token_count = len(encoding.encode(text))
        except Exception:
            # Fallback: Count words as a simple approximation of tokens
            token_count = 1.5 * len(text.split())

        # Add buffer percentage
        buffer_tokens = (token_count * buffer_percent) // 100
        return token_count + buffer_tokens

    def format_example(self, record: Record) -> tuple[str, int]:
        """Format a record into an example string and estimate its token count."""
        request_messages = "".join(
            [f"{msg['role']}: {msg['content']}\n\n" for msg in record["request"]["messages"]]
        )
        resp = record["response"]["choices"][0]["message"]
        response_content = f"{resp.get('role', 'unknown')}: {resp.get('content', 'unknown')}"

        # Add tool calls if present
        if resp.get("tool_calls"):
            tool_calls_str = json.dumps(resp["tool_calls"], indent=2)
            response_content += f"\nTool calls:\n{tool_calls_str}"

        example_str = f"For example, if the conversation looks like this:\n{request_messages}\nThen you'll respond with:\n{response_content}"
        token_count = self.estimate_tokens(example_str)

        return example_str, token_count

    def format_dpo_example(self, example: dict, idx: int = 1) -> tuple[str, int]:
        """Format a DPO record into an example string and estimate its token count.

        Args:
            example: DPO record with prompt, chosen_response, and rejected_response
            idx: Example number for display (default 1)
        """
        user_prompt = ""
        for msg in example["prompt"]:
            if msg["role"] == "user":
                user_prompt = msg["content"]
                break

        example_str = (
            f"Example {idx}:\n"
            f"Prompt: {user_prompt}\n"
            f"✓ PREFERRED Response: {example['chosen_response']}\n"
            f"✗ LESS PREFERRED Response: {example['rejected_response']}\n"
        )

        token_count = self.estimate_tokens(example_str)
        return example_str, token_count

    def uniform_bins(self, max_records: int, num_tools: int) -> list[int]:
        """Calculate uniform distribution of records across tools."""
        base = max_records // num_tools
        remainder = max_records % num_tools
        return [base + 1 if i < remainder else base for i in range(num_tools)]

    def get_tool_name(self, record: Record) -> str:
        """Get the tool name from the record.

        For DPO records, always returns "no_tool" as they don't have tool calls.
        """
        # DPO records don't have response field
        if "response" not in record:
            return "no_tool"

        tool_calls = record["response"]["choices"][0]["message"].get("tool_calls")
        if tool_calls and len(tool_calls) > 0:
            return tool_calls[0]["function"]["name"]
        return "no_tool"

    def extract_user_query(self, record: Record) -> str | None:
        """Extracts the content from the 'user' role message. In case there are more than one then take the last one"""
        usr_msg = None
        for message in record.get("request", {}).get("messages", []):
            if message.get("role") == "user":
                usr_msg = message.get("content")
        return usr_msg  # Return None if no user message is found

    def extract_user_query_dpo(self, record: dict) -> str | None:
        """Extract user query from DPO records."""
        usr_msg = None
        # DPO train format has 'prompt', eval format has 'messages'
        messages = record.get("prompt", record.get("messages", []))
        for message in messages:
            if message.get("role") == "user":
                usr_msg = message.get("content")
        return usr_msg

    def fit_examples_for_record(
        self,
        tool_groups: dict[str, list[tuple[Record, str, int]]],
        available_tokens: int,
    ) -> list[tuple[Record, str, int]]:
        """
        Fit examples for a single record using round-robin selection with token checking.
        Selected examples:
            1. Grouped by tool name,
            2. Each group is already sorted by token count

        For Generic workload: `no_tool` is the only group.
        For Tool Calling workload:
                follow round-robin approach to pick smallest examples from each tool group.
                we try to fit as many examples as possible for each record, but we don't want to exceed the max context length.
        For Generic workload:
                we try to fit as many examples as possible for each record, but we don't want to exceed the max examples.
        """
        if not tool_groups or available_tokens <= 0:
            return []

        tool_names = list(tool_groups.keys())

        # Round-robin selection with token checking
        selected_examples: list[tuple[Record, str, int]] = []
        tool_indices = dict.fromkeys(tool_names, 0)
        total_tokens = 0

        # Calculate maximum possible iterations based on available examples
        max_examples_per_tool = (
            max(len(examples) for examples in tool_groups.values()) if tool_groups else 0
        )

        for _ in range(max_examples_per_tool):
            for tool_name in tool_names:
                # Check if this tool has more examples available
                if tool_indices[tool_name] < len(tool_groups[tool_name]):
                    example = tool_groups[tool_name][tool_indices[tool_name]]

                    # Check if adding this example exceeds token limit
                    if total_tokens + example[2] <= available_tokens:
                        selected_examples.append(example)
                        total_tokens += example[2]
                        tool_indices[tool_name] += 1
                    else:
                        # Stop if tokens run out
                        return selected_examples

        return selected_examples

    def _apply_relevance_coverage_selection(
        self, candidates: list[tuple[float, str, dict]], max_examples: int
    ) -> dict[str, list[tuple[dict, str, int]]]:
        """
        Optimized relevance-coverage selection logic:
        1. Take relevance_ratio% examples from top results (pure semantic relevance)
        2. If all tools already covered, take next best candidates
        3. Otherwise, fill uncovered tools first, then remaining slots with best scores
        4. Format examples and sort by token count for fit_examples_for_record()
        """
        if not candidates:
            return {}

        relevance_size = int(max_examples * self.config.similarity_config.relevance_ratio)
        coverage_size = max_examples - relevance_size
        selected_records = defaultdict(list)

        # Phase 1: Relevance selection (pure semantic similarity)
        for _, tool_name, record in candidates[:relevance_size]:
            selected_records[tool_name].append(record)

        # Phase 2: Coverage selection
        if coverage_size > 0:
            remaining_candidates = candidates[relevance_size:]
            covered_tools = set(selected_records.keys())
            uncovered_tools = (
                set(tool_name for _, tool_name, _ in remaining_candidates) - covered_tools  # noqa: C401
            )

            if not uncovered_tools:
                # All tools already covered, just take next best candidates
                for _, tool_name, record in remaining_candidates[:coverage_size]:
                    selected_records[tool_name].append(record)
            else:
                # Some tools not covered, use coverage-aware selection
                coverage_selected = 0
                used_indices = set()

                # First pass: Fill uncovered tools with their best examples
                for i, (_, tool_name, record) in enumerate(remaining_candidates):
                    if coverage_selected >= coverage_size:
                        break
                    if tool_name in uncovered_tools:
                        selected_records[tool_name].append(record)
                        uncovered_tools.remove(tool_name)
                        used_indices.add(i)
                        coverage_selected += 1

                # Second pass: Fill remaining slots with best available scores (skip already used)
                for i, (_, tool_name, record) in enumerate(remaining_candidates):
                    if coverage_selected >= coverage_size:
                        break
                    if i not in used_indices:
                        selected_records[tool_name].append(record)
                        coverage_selected += 1

        # Format examples and group by tool for fit_examples_for_record()
        for tool_name, records in selected_records.items():
            for idx, record in enumerate(records):
                example_str, token_count = self.format_example(record)
                if example_str:  # Only include if formatting succeeds
                    selected_records[tool_name][idx] = (record, example_str, token_count)

        # Log selection statistics
        total_candidates = sum(len(examples) for examples in selected_records.values())
        tool_count = len(selected_records)
        logger.info(
            f"Relevance-coverage selection: {total_candidates} candidates prepared for token fitting, "
            f"{tool_count} tools covered (ratio: {self.config.similarity_config.relevance_ratio})"
        )

        return selected_records

    def uniform_binning_selection(
        self, source_records: list[Record], config: ICLConfig, workload_type: WorkloadClassification
    ) -> dict[str, list[tuple[Record, str, int]]]:
        """
        Select and organize ICL examples by tool groups with uniform binning for tool_calling records,
        or simple max_records selection for normal records.
        Returns binned tool groups for later round-robin fitting per record.
        """
        if not source_records:
            return {}

        # Step 1: Group records by tools and format examples
        tool_groups: dict[str, list[tuple[Record, str, int]]] = {}

        for idx, record in enumerate(source_records, 1):
            # Use appropriate formatter based on workload type
            if workload_type == WorkloadClassification.DPO:
                example_str, token_count = self.format_dpo_example(record, idx)
            else:
                example_str, token_count = self.format_example(record)
            if not example_str:
                continue

            tool_name = self.get_tool_name(record)

            if tool_name not in tool_groups:
                tool_groups[tool_name] = []
            tool_groups[tool_name].append((record, example_str, token_count))

        # Step 2: Sort each tool group by token count (shortest first)
        for examples in tool_groups.values():
            examples.sort(key=lambda x: x[2])

        # Step 3: Apply different selection logic based on workflow type
        if workload_type == WorkloadClassification.TOOL_CALLING:
            # Tool calling workflow: Apply uniform binning to limit examples per tool
            if tool_groups:
                num_tools = len(tool_groups)
                bins = self.uniform_bins(config.max_examples, num_tools)

                # Limit each tool group to its allocated bin size
                tool_names = list(tool_groups.keys())
                for i, tool_name in enumerate(tool_names):
                    allocated_size = bins[i]
                    tool_groups[tool_name] = tool_groups[tool_name][:allocated_size]
        else:
            # Normal workflow: Simple max_records selection after sorting
            if tool_groups:
                # Combine all examples from all groups and sort by token count
                all_examples = []
                for examples in tool_groups.values():
                    all_examples.extend(examples)
                all_examples.sort(key=lambda x: x[2])
                selected_examples = all_examples[: config.max_examples]
                tool_groups = {"generic_examples": selected_examples}

        return tool_groups

    def _inject_icl_examples(
        self,
        record: Record,
        selected_examples: dict[str, list[tuple[Record, str, int]]] | None = None,
    ) -> tuple[Record, tuple[int, int, int]]:
        """Inject ICL examples into eval records."""
        # Calculate available tokens for this specific eval record
        remaining_cnts = (0, 0, 0)
        if not record or not selected_examples:
            return record, remaining_cnts

        record_tokens = self.estimate_tokens(json.dumps(record))
        available_tokens = (
            self.config.max_context_length - self.config.reserved_tokens - record_tokens
        )
        if available_tokens <= 0:
            return record, remaining_cnts  # Skip if there are NOT enough tokens

        # Fit examples for this record using round-robin with token checking
        fitted_examples = self.fit_examples_for_record(selected_examples, available_tokens)
        if not fitted_examples:
            return record, remaining_cnts  # No examples fit

        example_tokens = sum(ex[2] for ex in fitted_examples)  # all examples tokens
        remaining_cnts = (
            example_tokens,
            len(fitted_examples),
            available_tokens - example_tokens,
        )
        # Create system message with fitted examples
        example_strings = [ex[1] for ex in fitted_examples]
        concatenated_string = "\n\n".join(example_strings)

        if example_tokens <= available_tokens:
            self._inject_system_message(record, concatenated_string)
        return record, remaining_cnts

    def _inject_system_message(self, record: Record, concatenated_string: str):
        """Inject ICL examples into the system message of a record."""
        if "messages" not in record["request"]:
            record["request"]["messages"] = []

        messages = record["request"]["messages"]
        system_msg_index = next(
            (i for i, msg in enumerate(messages) if msg.get("role") == "system"), -1
        )

        if system_msg_index != -1:
            messages[system_msg_index]["content"] = (
                f"{DEFAULT_SYSTEM_MESSAGE.strip()}\n\n"
                f"{concatenated_string}\n\n"
                f"{messages[system_msg_index]['content']}"
            )
        else:
            system_message: Message = {
                "role": "system",
                "content": f"{DEFAULT_SYSTEM_MESSAGE.strip()}\n\n{concatenated_string}",
            }
            messages.insert(0, system_message)

    def _inject_dpo_icl_examples(
        self, eval_record: dict, icl_examples: dict[str, list[tuple[dict, str, int]]]
    ) -> tuple[dict, tuple[int, int, int]]:
        """Inject DPO ICL examples into an evaluation record via system prompt.

        ICL examples are formatted in the system prompt to show preference pairs,
        then the actual user prompt follows in the messages.

        Args:
            eval_record: DPO evaluation record with messages, chosen_response, rejected_response
            icl_examples: Dictionary of tool groups with formatted examples

        Returns:
            Tuple of (updated record, (example_tokens, num_examples, remaining_tokens))
        """
        messages = eval_record["messages"].copy()
        remaining_cnts = (0, 0, 0)

        record_tokens = self.estimate_tokens(json.dumps(eval_record))
        available_tokens = (
            self.config.max_context_length - self.config.reserved_tokens - record_tokens
        )
        if available_tokens <= 0:
            return eval_record, remaining_cnts  # Skip if there are NOT enough tokens

        # Fit examples for this record using round-robin with token checking
        fitted_examples = self.fit_examples_for_record(icl_examples, available_tokens)
        if not fitted_examples:
            return eval_record, remaining_cnts  # No examples fit

        example_tokens = sum(ex[2] for ex in fitted_examples)  # all examples tokens
        remaining_cnts = (
            example_tokens,
            len(fitted_examples),
            available_tokens - example_tokens,
        )

        # Create system message with fitted examples
        example_strings = [ex[1] for ex in fitted_examples]
        concatenated_string = "\n\n".join(example_strings)

        # Create system message with ICL examples
        system_content = (
            "You will be given examples showing preferred vs less preferred responses. "
            "Learn from these examples to understand what makes a high-quality response.\n\n"
            + concatenated_string
        )

        # Prepend system message to the evaluation messages
        icl_messages = [{"role": "system", "content": system_content}, *messages]

        return {
            "messages": icl_messages,
            "chosen_response": eval_record["chosen_response"],
            "rejected_response": eval_record["rejected_response"],
        }, remaining_cnts

    def _log_injection_stats(self, result: list[Record], remaining_cnts: list):
        """Log statistics about ICL injection."""
        logger.info("ICL Injection Done")
        logger.info("-------------------------------------------------")
        logger.info(f"Total ICL Eval Dataset Size: {len(result)}.")
        logger.info(f"Total Max Context Length: {self.config.max_context_length}.")
        logger.info(f"Total Reserved Tokens: {self.config.reserved_tokens}.")
        logger.info(f"Tried to fit max_examples={self.config.max_examples} examples per record")
        if len(remaining_cnts) > 0:
            logger.info(
                f"On Average Injected {sum(cnt[1] for cnt in remaining_cnts) / len(remaining_cnts)} examples per record"
            )
            logger.info(
                f"On Average Used {sum(cnt[0] for cnt in remaining_cnts) / len(remaining_cnts)} tokens per record"
            )
            logger.info(
                f"On Average Remaining {sum(cnt[2] for cnt in remaining_cnts) / len(remaining_cnts)} tokens per record"
            )
        else:
            logger.info("No examples were injected")

        logger.info("-------------------------------------------------")

    def _calculate_tool_limits(
        self, workload_type: WorkloadClassification, binned_data: dict[str, list[tuple[Any, Any]]]
    ) -> dict[str, int]:
        """Calculate tool limits based on workload type."""
        tool_names = list(binned_data.keys())
        if workload_type == WorkloadClassification.TOOL_CALLING:
            if tool_names:
                num_tools = len(tool_names)
                bin_sizes = self.uniform_bins(self.config.max_examples, num_tools)
                tool_limits = {tool_name: bin_sizes[i] for i, tool_name in enumerate(tool_names)}
            else:
                tool_limits = {}
        else:
            tool_limits = dict.fromkeys(tool_names, self.config.max_examples)
        return tool_limits

    def _generate_train_embeddings(
        self,
        records: list[dict[str, Any]],
        workload_type: WorkloadClassification = WorkloadClassification.GENERIC,
    ) -> dict[str, list[tuple[list[float], dict[str, Any]]]]:
        """
        Build embeddings for records grouped by tool.

        This method processes source records to create embeddings that can be indexed
        for similarity search. It:
        1. Extracts user queries from each record
        2. Generates embeddings for all queries in batch
        3. Groups embeddings by tool name for efficient retrieval

        Args:
            records: Source records to process

        Returns:
            Dictionary mapping tool names to lists of (embedding_vector, record) tuples for Source Records
        """
        binned_data = defaultdict(list)

        # Step 1: Extract user queries from all records
        queries = []
        for record in records:
            # Use DPO-specific extraction for DPO workloads
            if workload_type == WorkloadClassification.DPO:
                query = self.extract_user_query_dpo(record)
            else:
                query = self.extract_user_query(record)
            queries.append(query if query else "")

        if not any(queries):
            logger.warning("No valid queries found for embedding generation")
            return binned_data

        embeddings = self.embedding_client.get_embeddings_batch(queries, input_type="query")

        # Step 2: Generate embeddings for all queries in batch of 32
        for idx, record in enumerate(records):
            # if the embedding is None, skip the records
            # only keep the records with embeddings generated successfully
            if embeddings[idx]:
                tool_name = self.get_tool_name(record)
                binned_data[tool_name].append((embeddings[idx], record))

        return binned_data

    def _embedding_similarity_selection(
        self,
        source_records: list[dict[str, Any]],
        eval_records: list[dict[str, Any]],
        workload_type: WorkloadClassification,
    ) -> tuple[str | None, list[Record]]:
        """
        Embedding similarity selection strategy that finds best examples per eval record.
        Enhanced with relevance-coverage selection for tool calling workloads.

        This method implements the embedding-based ICL selection approach that:
        1. Builds and indexes embeddings from source records grouped by tool
        2. For each evaluation record, finds the most similar examples using vector similarity
        3. Injects the most similar examples into the eval records

        Args:
            source_records: Historical records to select examples from
            eval_records: Records to find similar examples for
            workload_type: Type of workload (GENERIC or TOOL_CALLING)

        Returns:
            Tuple of (index_name, processed_eval_records)
        """

        # Step 1: Build embeddings index from source records
        binned_data = self._generate_train_embeddings(source_records, workload_type)

        # Step 2: Index embeddings to Elasticsearch for efficient similarity search
        index_name = index_embeddings_to_es(
            self.es_client, binned_data, self.workload_id, self.client_id
        )

        # Step 3: Generate embeddings for eval records
        queries = []
        for record in eval_records:
            # Use DPO-specific extraction for DPO workloads
            if workload_type == WorkloadClassification.DPO:
                query = self.extract_user_query_dpo(record)
            else:
                query = self.extract_user_query(record)
            queries.append(query if query else "")

        eval_embeddings = self.embedding_client.get_embeddings_batch(queries, input_type="query")

        # Step 4: Apply selection strategy
        remaining_cnts = []
        result = deepcopy(eval_records)
        for idx, record in enumerate(result):
            if not eval_embeddings[idx]:
                remaining_cnts.append((0, 0, 0))
                continue

            # Get more candidates for tool calling workloads
            max_candidates = (
                self.config.max_examples * 5
                if workload_type == WorkloadClassification.TOOL_CALLING
                else self.config.max_examples
            )
            candidates = search_similar_embeddings(
                self.es_client,
                eval_embeddings[idx],
                index_name=index_name,
                max_candidates=max_candidates,  # Get k*5 candidates
            )

            if workload_type == WorkloadClassification.TOOL_CALLING:
                # Apply relevance-coverage selection if relevance_ratio < 1.0
                similar_examples = self._apply_relevance_coverage_selection(
                    candidates, self.config.max_examples
                )
            else:
                # For generic workloads: just pick top k examples (no tool coverage needed)
                similar_examples = {"generic_examples": []}
                for example_idx, (_, _, candidate_record) in enumerate(candidates, 1):
                    if workload_type == WorkloadClassification.DPO:
                        example_str, token_count = self.format_dpo_example(
                            candidate_record, example_idx
                        )
                    else:
                        example_str, token_count = self.format_example(candidate_record)
                    if example_str:
                        similar_examples["generic_examples"].append(
                            (candidate_record, example_str, token_count)
                        )
            if workload_type == WorkloadClassification.DPO:
                result[idx], cnts = self._inject_dpo_icl_examples(record, similar_examples)
            else:
                result[idx], cnts = self._inject_icl_examples(record, similar_examples)
            remaining_cnts.append(cnts)
        # log the injection stats
        self._log_injection_stats(result, remaining_cnts)
        return index_name, result

    def generate_icl_records(
        self,
        source_records: list[dict[str, Any]],
        workload_type: WorkloadClassification,
        eval_records: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, list[Record]]:
        """
        Select ICL examples based on the configured strategy.

        Args:
            source_records: Records to select examples from
            workload_type: Type of workload (GENERIC or TOOL_CALLING)
            eval_records: Records to find examples for (needed for embedding similarity)

        Returns:
            Dictionary mapping tool names to lists of (record, example_str, token_count) tuples
        """
        if self.config.example_selection == "semantic_similarity":
            return self._embedding_similarity_selection(source_records, eval_records, workload_type)
        else:
            # For random selection, use uniform tool distribution
            selected_examples = self.uniform_binning_selection(
                source_records, self.config, workload_type
            )
            result = deepcopy(eval_records)
            remaining_cnts = []
            for idx, record in enumerate(result):
                if workload_type == WorkloadClassification.DPO:
                    result[idx], cnts = self._inject_dpo_icl_examples(record, selected_examples)
                else:
                    result[idx], cnts = self._inject_icl_examples(record, selected_examples)
                remaining_cnts.append(cnts)

            self._log_injection_stats(result, remaining_cnts)
            return None, result  # return None for the index_name

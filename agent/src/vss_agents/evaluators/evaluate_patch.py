# SPDX-FileCopyrightText: Copyright (c) 2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Multi-turn conversation and latency logging support for evaluation.

This module provides:
1. Auto-detection of multi-turn items (by "conversation" field)
2. A patch to NAT's EvaluationRun to handle multi-turn conversations
3. A patch to NAT's publish_output to write latency_summary.json alongside other output files
4. A patch to NAT's write_tabular_output to print average latency
5. Support for filtering dataset by evaluation_method using the DATASET_FILTER environment variable

Multi-turn items are automatically detected and run with the same conversation_id
across all turns, allowing the agent to maintain context.

The patch is auto-applied when this module is imported.

Dataset Format
--------------
To create a multi-turn evaluation item, add a "conversation" field:

    {
        "id": "my_multi_turn_001",
        "query": "[multi-turn]",  # placeholder for NAT loading
        "conversation": [
            {
                "turn_id": "turn_1",
                "query": "What videos are available?",
                "ground_truth": "...",
                "trajectory_ground_truth": ["..."]
                "evaluation_method": ["trajectory"]
            },
            {
                "turn_id": "turn_2",
                "query": "Show me the first one",
                "ground_truth": "...",
                "trajectory_ground_truth": ["..."]
                "evaluation_method": ["qa", "trajectory"]
            }
        ]
    }

"""

import asyncio
import enum
import io
import json
import logging
import os
from typing import Any
from uuid import uuid4

from nat.eval.evaluator.evaluator_model import EvalInputItem
from tqdm import tqdm

from vss_agents.evaluators.utils import compute_item_latency
from vss_agents.evaluators.utils import strip_agent_think_tags

logger = logging.getLogger(__name__)


class DatasetFilter(enum.StrEnum):
    ALL = "all"
    QA = "qa"
    TRAJECTORY = "trajectory"
    REPORT = "report"


def _get_conversation(dataset_entry: dict) -> list:
    """
    Get the conversation list from a dataset entry, defaulting to [] if not set or invalid.

    NAT may use pandas to load JSON datasets, which fills missing fields with NaN.
    This ensures we always return a list.
    """
    conversation = dataset_entry.get("conversation")
    return conversation if isinstance(conversation, list) else []


def is_multi_turn_item(dataset_entry: dict) -> bool:
    """Check if a dataset entry is a multi-turn conversation."""
    return len(_get_conversation(dataset_entry)) > 0


# NAT Patch: expand multi-turn items before evaluation

_patched = False


def _expand_multi_turn_items(eval_input_items: list) -> list:
    """
    Expand multi-turn conversation items into individual turn items.

    Each multi-turn dataset entry (containing a "conversation" list) is split into
    separate EvalInputItems, one per turn. All turns from the same conversation share
    a unique _multi_turn_conversation_id so the patch can group and run them sequentially.

    Single-turn items are passed through unchanged.
    """
    expanded = []

    for item in eval_input_items:
        if item.full_dataset_entry and is_multi_turn_item(item.full_dataset_entry):
            # Expand multi-turn into individual turns
            conversation = _get_conversation(item.full_dataset_entry)
            conv_id = f"multi_turn_{item.id}_{uuid4().hex[:8]}"

            logger.info(f"Expanding multi-turn item {item.id} into {len(conversation)} turns")

            for turn_idx, turn in enumerate(conversation):
                turn_id = turn.get("turn_id", f"turn_{turn_idx + 1}")

                # Create a new item for this turn
                turn_item = EvalInputItem(
                    id=f"{item.id}_{turn_id}",
                    input_obj=turn.get("query", ""),
                    output_obj=None,
                    expected_output_obj=turn.get("ground_truth"),
                    trajectory=[],
                    full_dataset_entry={
                        **turn,
                        "_multi_turn_conversation_id": conv_id,  # Marker for the patch
                    },
                )
                expanded.append(turn_item)
        else:
            expanded.append(item)

    return expanded


def _filter_by_dataset_filter(items: list, dataset_filter: list[str]) -> list:
    """
    Filter expanded items to only include those whose evaluation_method overlaps with dataset_filter.

    For multi-turn conversations, the entire conversation is kept if any turn matches,
    since turns depend on prior conversation context.

    Each kept item's evaluation_method is narrowed to only the methods in the filter,
    so evaluators not in the filter won't run on it.
    """
    if not dataset_filter:
        return items

    conv_items: dict[str, list] = {}
    single_items: list = []

    for item in items:
        conv_id = item.full_dataset_entry.get("_multi_turn_conversation_id")
        if conv_id:
            conv_items.setdefault(conv_id, []).append(item)
        else:
            single_items.append(item)

    filtered = []

    for item in single_items:
        eval_methods = item.full_dataset_entry.get("evaluation_method", [])
        if isinstance(eval_methods, list) and any(m in dataset_filter for m in eval_methods):
            item.full_dataset_entry["evaluation_method"] = [m for m in eval_methods if m in dataset_filter]
            filtered.append(item)

    for _, turns in conv_items.items():
        if any(
            isinstance(t.full_dataset_entry.get("evaluation_method", []), list)
            and any(m in dataset_filter for m in t.full_dataset_entry["evaluation_method"])
            for t in turns
        ):
            for turn in turns:
                methods = turn.full_dataset_entry.get("evaluation_method", [])
                if isinstance(methods, list):
                    turn.full_dataset_entry["evaluation_method"] = [m for m in methods if m in dataset_filter]
            filtered.extend(turns)

    skipped = len(items) - len(filtered)
    if skipped > 0:
        logger.info(
            f"[DATASET_FILTER] Filtered to {len(filtered)} items (skipped {skipped}) for filter={dataset_filter}"
        )

    return filtered


_last_avg_latency: float | None = None


def _write_latency_summary(evaluation_run: Any, items: list[Any]) -> float | None:
    """Write latency_summary.json with per-item and average latency to the results directory."""
    try:
        output_dir = evaluation_run.eval_config.general.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)

        item_latencies = []
        for item in items:
            latency = compute_item_latency(item)
            item_latencies.append({"id": item.id, "query": item.input_obj, "latency_seconds": latency})

        valid_latencies = [entry["latency_seconds"] for entry in item_latencies if entry["latency_seconds"] is not None]
        avg_latency = float(round(sum(valid_latencies) / len(valid_latencies), 3)) if valid_latencies else None

        summary = {
            "average_latency_seconds": avg_latency,
            "items": item_latencies,
        }

        summary_file = output_dir / "latency_summary.json"
        with open(summary_file, "w") as f:
            json.dump(summary, f, indent=2)
        logger.info(f"Latency summary written to {summary_file}")

        return avg_latency

    except Exception:
        logger.exception("Failed to write latency_summary.json")
        return None


def apply_patch() -> None:
    """
    Apply patch to NAT's EvaluationRun.

    1. Expand multi-turn items into individual turns
    2. Run turns within a conversation sequentially
    3. Set conversation_id on ContextState before each turn so the agent
       reuses the same LangGraph thread and retains memory across turns
    4. Write latency_summary.json with per-item and average latency to the results directory
    5. Output the average scoring to the console
    """
    global _patched
    if _patched:
        return

    from nat.eval.evaluate import EvaluationRun

    _original_run_workflow_local = EvaluationRun.run_workflow_local

    async def patched_run_workflow_local(self: Any, session_manager: Any) -> None:
        """Expand multi-turn items, then run turns sequentially within each conversation."""
        from nat.builder.context import ContextState

        # Expand multi-turn items and optionally filter by DATASET_FILTER
        original_items = self.eval_input.eval_input_items
        expanded_items = _expand_multi_turn_items(original_items)

        valid_filters = {f.value for f in DatasetFilter}
        dataset_filter_env = os.environ.get("DATASET_FILTER", DatasetFilter.ALL.value).strip().lower()
        dataset_filter = [s.strip() for s in dataset_filter_env.split(",") if s.strip()]

        invalid = set(dataset_filter) - valid_filters
        if invalid:
            raise ValueError(
                f"Invalid DATASET_FILTER values: {invalid}. Must be one of: {[f.value for f in DatasetFilter]}"
            )
        if DatasetFilter.ALL.value in dataset_filter and len(dataset_filter) > 1:
            raise ValueError("DATASET_FILTER='all' cannot be combined with other values")

        if DatasetFilter.ALL.value not in dataset_filter:
            expanded_items = _filter_by_dataset_filter(expanded_items, dataset_filter)

        # Group items by conversation_id for sequential execution
        conv_groups: dict[str, list[Any]] = {}
        non_multi_turn_items: list[Any] = []

        for item in expanded_items:
            conv_id = item.full_dataset_entry.get("_multi_turn_conversation_id")
            if conv_id:
                if conv_id not in conv_groups:
                    conv_groups[conv_id] = []
                conv_groups[conv_id].append(item)
            else:
                non_multi_turn_items.append(item)

        total_items = sum(len(items) for items in conv_groups.values()) + len(non_multi_turn_items)
        pbar = tqdm(total=total_items, desc="Running workflow")

        # Since we call _original_run_workflow_local per turn, each call creates its own progress bar.
        # We redirect NAT's tqdm to StringIO to silence them and use a single progress bar above instead.
        # NAT uses `from tqdm import tqdm` so the name is bound in its module
        # namespace at import time. We must patch it there directly.
        import nat.eval.evaluate as _nat_eval_module

        _original_nat_tqdm = _nat_eval_module.tqdm

        async def run_conversation(conv_id: str, items: list[Any]) -> None:
            """Run turns within a single conversation sequentially."""
            # Set conversation_id once for this task. asyncio.gather creates
            # a task per coroutine, each with its own ContextVar copy.
            ContextState.get().conversation_id.set(conv_id)
            logger.info(f"[Multi-turn] Running conversation {conv_id} with {len(items)} turns sequentially")
            conversation_history: list[dict[str, str]] = []

            for item in items:
                # Add previous turns so evaluators have conversation context
                if conversation_history:
                    item.full_dataset_entry["_conversation_history"] = list(conversation_history)

                # Re-set conversation_id before each turn
                ContextState.get().conversation_id.set(conv_id)
                logger.info(f"[Multi-turn] Set conversation_id={conv_id} for {item.id}")

                self.eval_input.eval_input_items = [item]
                await _original_run_workflow_local(self, session_manager)
                pbar.update(1)

                conversation_history.append(
                    {
                        "turn_id": item.full_dataset_entry.get("turn_id", f"turn_{len(conversation_history) + 1}"),
                        "query": item.input_obj,
                        "answer": strip_agent_think_tags(item.output_obj),
                    }
                )

        async def run_non_multi_turn() -> None:
            """Run non-multi-turn items."""
            self.eval_input.eval_input_items = non_multi_turn_items
            await _original_run_workflow_local(self, session_manager)
            pbar.update(len(non_multi_turn_items))

        try:
            _nat_eval_module.tqdm = lambda *args, **kwargs: _original_nat_tqdm(
                *args, **{**kwargs, "file": io.StringIO()}
            )

            # Run all conversations in parallel; turns within each are sequential.
            # Non-multi-turn items also run in parallel alongside conversations.
            tasks = [run_conversation(conv_id, items) for conv_id, items in conv_groups.items()]
            if non_multi_turn_items:
                tasks.append(run_non_multi_turn())

            await asyncio.gather(*tasks)

        finally:
            _nat_eval_module.tqdm = _original_nat_tqdm
            pbar.close()
            # Restore all items for result collection
            self.eval_input.eval_input_items = expanded_items

    # Patch publish_output to also write latency_summary.json alongside other output files
    _original_publish_output = EvaluationRun.publish_output

    def patched_publish_output(self: Any, *args: Any, **kwargs: Any) -> None:
        global _last_avg_latency
        _original_publish_output(self, *args, **kwargs)
        _last_avg_latency = _write_latency_summary(self, self.eval_input.eval_input_items)

    # Patch write_tabular_output to print average latency
    import nat.cli.commands.evaluate as _nat_cli_eval

    _original_write_tabular_output = _nat_cli_eval.write_tabular_output

    def patched_write_tabular_output(eval_run_output: Any) -> None:
        import click

        _original_write_tabular_output(eval_run_output)
        if _last_avg_latency is not None:
            click.echo(f"Average Latency: {_last_avg_latency:.2f}s")

    EvaluationRun.run_workflow_local = patched_run_workflow_local
    EvaluationRun.publish_output = patched_publish_output
    _nat_cli_eval.write_tabular_output = patched_write_tabular_output
    _patched = True
    logger.info("Evaluation patch applied")


# Auto-apply patch on import
apply_patch()

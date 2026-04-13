# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for evaluators/evaluate_patch module."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from nat.eval.evaluator.evaluator_model import EvalInputItem
import pytest

from vss_agents.evaluators.evaluate_patch import DatasetFilter
from vss_agents.evaluators.evaluate_patch import _expand_multi_turn_items
from vss_agents.evaluators.evaluate_patch import _filter_by_dataset_filter
from vss_agents.evaluators.evaluate_patch import _get_conversation
from vss_agents.evaluators.evaluate_patch import _write_latency_summary
from vss_agents.evaluators.evaluate_patch import is_multi_turn_item

# --- Helpers ---


def _make_item(item_id: str, query: str = "q", full_dataset_entry: dict | None = None) -> EvalInputItem:
    return EvalInputItem(
        id=item_id,
        input_obj=query,
        output_obj=None,
        expected_output_obj=None,
        full_dataset_entry=full_dataset_entry or {},
    )


def _make_multi_turn_entry(turns: list[dict]) -> dict:
    return {
        "id": "mt_001",
        "query": "[multi-turn]",
        "conversation": turns,
    }


# --- _get_conversation ---


class TestGetConversation:
    def test_returns_list_when_present(self):
        entry = {"conversation": [{"turn_id": "turn_1", "query": "hello"}]}
        assert _get_conversation(entry) == [{"turn_id": "turn_1", "query": "hello"}]

    def test_returns_empty_for_missing_key(self):
        assert _get_conversation({}) == []

    @pytest.mark.parametrize("value", [float("nan"), None, "not a list"])
    def test_returns_empty_for_non_list(self, value):
        assert _get_conversation({"conversation": value}) == []

    def test_returns_empty_list_as_is(self):
        assert _get_conversation({"conversation": []}) == []


# --- is_multi_turn_item ---


class TestIsMultiTurnItem:
    def test_true_with_conversation(self):
        entry = {"conversation": [{"turn_id": "turn_1", "query": "hi"}]}
        assert is_multi_turn_item(entry) is True

    def test_false_without_conversation(self):
        assert is_multi_turn_item({"query": "single"}) is False

    def test_false_with_empty_conversation(self):
        assert is_multi_turn_item({"conversation": []}) is False


# --- _expand_multi_turn_items ---


class TestExpandMultiTurnItems:
    def test_single_turn_passes_through(self):
        item = _make_item("st_001", full_dataset_entry={"query": "hello"})
        result = _expand_multi_turn_items([item])
        assert len(result) == 1
        assert result[0] is item

    def test_multi_turn_expanded(self):
        entry = _make_multi_turn_entry(
            [
                {"turn_id": "turn_1", "query": "q1", "ground_truth": "a1"},
                {"turn_id": "turn_2", "query": "q2", "ground_truth": "a2"},
                {"turn_id": "turn_3", "query": "q3"},
            ]
        )
        item = _make_item("mt_001", full_dataset_entry=entry)
        result = _expand_multi_turn_items([item])

        assert len(result) == 3
        assert result[0].id == "mt_001_turn_1"
        assert result[0].input_obj == "q1"
        assert result[0].expected_output_obj == "a1"
        assert result[1].id == "mt_001_turn_2"
        assert result[2].id == "mt_001_turn_3"
        assert result[2].expected_output_obj is None

    def test_expanded_items_share_conversation_id(self):
        entry = _make_multi_turn_entry(
            [
                {"turn_id": "turn_1", "query": "q1"},
                {"turn_id": "turn_2", "query": "q2"},
            ]
        )
        item = _make_item("mt_001", full_dataset_entry=entry)
        result = _expand_multi_turn_items([item])

        conv_id_1 = result[0].full_dataset_entry["_multi_turn_conversation_id"]
        conv_id_2 = result[1].full_dataset_entry["_multi_turn_conversation_id"]
        assert conv_id_1 == conv_id_2
        assert conv_id_1.startswith("multi_turn_mt_001_")

    def test_default_turn_id(self):
        entry = _make_multi_turn_entry([{"query": "q1"}, {"query": "q2"}])
        item = _make_item("mt_001", full_dataset_entry=entry)
        result = _expand_multi_turn_items([item])

        assert result[0].id == "mt_001_turn_1"
        assert result[1].id == "mt_001_turn_2"

    def test_mixed_single_and_multi(self):
        single = _make_item("st_001", full_dataset_entry={"query": "hello"})
        multi_entry = _make_multi_turn_entry(
            [
                {"turn_id": "turn_1", "query": "q1"},
                {"turn_id": "turn_2", "query": "q2"},
            ]
        )
        multi = _make_item("mt_001", full_dataset_entry=multi_entry)

        result = _expand_multi_turn_items([single, multi])
        assert len(result) == 3
        assert result[0].id == "st_001"
        assert result[1].id == "mt_001_turn_1"
        assert result[2].id == "mt_001_turn_2"

    def test_preserves_turn_fields(self):
        entry = _make_multi_turn_entry(
            [
                {"turn_id": "turn_1", "query": "q1", "evaluation_method": ["qa"], "extra_field": "value"},
            ]
        )
        item = _make_item("mt_001", full_dataset_entry=entry)
        result = _expand_multi_turn_items([item])

        assert result[0].full_dataset_entry["evaluation_method"] == ["qa"]
        assert result[0].full_dataset_entry["extra_field"] == "value"


# --- _filter_by_dataset_filter ---


class TestFilterByDatasetFilter:
    def test_empty_filter_returns_all(self):
        items = [_make_item("a", full_dataset_entry={"evaluation_method": ["qa"]})]
        assert _filter_by_dataset_filter(items, []) == items

    def test_single_turn_matching(self):
        item_qa = _make_item("qa_001", full_dataset_entry={"evaluation_method": ["qa"]})
        item_traj = _make_item("traj_001", full_dataset_entry={"evaluation_method": ["trajectory"]})

        result = _filter_by_dataset_filter([item_qa, item_traj], ["trajectory"])
        assert len(result) == 1
        assert result[0].id == "traj_001"

    def test_single_turn_no_match(self):
        item = _make_item("qa_001", full_dataset_entry={"evaluation_method": ["qa"]})
        result = _filter_by_dataset_filter([item], ["trajectory"])
        assert len(result) == 0

    def test_narrows_evaluation_method(self):
        item = _make_item("item_001", full_dataset_entry={"evaluation_method": ["qa", "trajectory"]})
        _filter_by_dataset_filter([item], ["trajectory"])
        assert item.full_dataset_entry["evaluation_method"] == ["trajectory"]

    def test_narrows_multi_method_to_multiple(self):
        item = _make_item("item_001", full_dataset_entry={"evaluation_method": ["qa", "trajectory", "report"]})
        _filter_by_dataset_filter([item], ["qa", "trajectory"])
        assert item.full_dataset_entry["evaluation_method"] == ["qa", "trajectory"]

    def test_multi_turn_keeps_whole_conversation_if_any_turn_matches(self):
        conv_id = "conv_001"
        turn1 = _make_item(
            "t1",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["qa"],
            },
        )
        turn2 = _make_item(
            "t2",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["trajectory"],
            },
        )

        result = _filter_by_dataset_filter([turn1, turn2], ["trajectory"])
        assert len(result) == 2

    def test_multi_turn_narrows_evaluation_methods(self):
        conv_id = "conv_001"
        turn1 = _make_item(
            "t1",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["qa", "trajectory"],
            },
        )
        turn2 = _make_item(
            "t2",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["qa"],
            },
        )

        _filter_by_dataset_filter([turn1, turn2], ["trajectory"])
        assert turn1.full_dataset_entry["evaluation_method"] == ["trajectory"]
        assert turn2.full_dataset_entry["evaluation_method"] == []

    def test_multi_turn_filters_out_entire_conversation_if_no_turn_matches(self):
        conv_id = "conv_001"
        turn1 = _make_item(
            "t1",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["qa"],
            },
        )
        turn2 = _make_item(
            "t2",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["qa"],
            },
        )

        result = _filter_by_dataset_filter([turn1, turn2], ["trajectory"])
        assert len(result) == 0

    def test_mixed_single_and_multi_turn(self):
        single_qa = _make_item("sq", full_dataset_entry={"evaluation_method": ["qa"]})
        single_traj = _make_item("st", full_dataset_entry={"evaluation_method": ["trajectory"]})

        conv_id = "conv_001"
        mt_turn1 = _make_item(
            "mt1",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["trajectory"],
            },
        )
        mt_turn2 = _make_item(
            "mt2",
            full_dataset_entry={
                "_multi_turn_conversation_id": conv_id,
                "evaluation_method": ["qa"],
            },
        )

        result = _filter_by_dataset_filter([single_qa, single_traj, mt_turn1, mt_turn2], ["trajectory"])
        ids = [item.id for item in result]
        assert "sq" not in ids
        assert "st" in ids
        assert "mt1" in ids
        assert "mt2" in ids

    def test_non_list_evaluation_method_skipped(self):
        item = _make_item("bad", full_dataset_entry={"evaluation_method": "qa"})
        result = _filter_by_dataset_filter([item], ["qa"])
        assert len(result) == 0

    def test_missing_evaluation_method_skipped(self):
        item = _make_item("no_method", full_dataset_entry={})
        result = _filter_by_dataset_filter([item], ["qa"])
        assert len(result) == 0


# --- _write_latency_summary ---


class TestWriteLatencySummary:
    def test_writes_json_file(self, tmp_path):
        mock_run = MagicMock()
        mock_run.eval_config.general.output_dir = tmp_path

        item1 = _make_item("item_1", query="q1")
        item1.trajectory = [MagicMock(event_timestamp=10.0), MagicMock(event_timestamp=15.0)]

        item2 = _make_item("item_2", query="q2")
        item2.trajectory = [MagicMock(event_timestamp=20.0), MagicMock(event_timestamp=22.0)]

        avg = _write_latency_summary(mock_run, [item1, item2])

        summary_file = tmp_path / "latency_summary.json"
        assert summary_file.exists()

        data = json.loads(summary_file.read_text())
        assert data["average_latency_seconds"] == pytest.approx(3.5, abs=0.01)
        assert len(data["items"]) == 2
        assert data["items"][0]["id"] == "item_1"
        assert data["items"][0]["latency_seconds"] == pytest.approx(5.0)
        assert data["items"][1]["latency_seconds"] == pytest.approx(2.0)
        assert avg == pytest.approx(3.5, abs=0.01)

    def test_returns_none_for_no_trajectory(self, tmp_path):
        mock_run = MagicMock()
        mock_run.eval_config.general.output_dir = tmp_path

        item = _make_item("item_1", query="q1")
        item.trajectory = []

        avg = _write_latency_summary(mock_run, [item])

        data = json.loads((tmp_path / "latency_summary.json").read_text())
        assert data["average_latency_seconds"] is None
        assert data["items"][0]["latency_seconds"] is None
        assert avg is None

    def test_returns_none_on_error(self):
        mock_run = MagicMock()
        mock_run.eval_config.general.output_dir = Path("/nonexistent/deeply/nested/path")

        result = _write_latency_summary(mock_run, [])
        assert result is None


# --- DATASET_FILTER env var validation (tested via the patch internals) ---


class TestDatasetFilterValidation:
    """Test the validation logic that runs inside patched_run_workflow_local.

    We extract the validation logic and test it directly since the actual patch
    requires a full EvaluationRun setup.
    """

    @staticmethod
    def _validate_dataset_filter(env_value: str) -> list[str]:
        """Reproduce the validation logic from patched_run_workflow_local."""
        valid_filters = {f.value for f in DatasetFilter}
        dataset_filter_env = env_value.strip().lower()
        dataset_filter = [s.strip() for s in dataset_filter_env.split(",") if s.strip()]

        invalid = set(dataset_filter) - valid_filters
        if invalid:
            raise ValueError(
                f"Invalid DATASET_FILTER values: {invalid}. Must be one of: {[f.value for f in DatasetFilter]}"
            )
        if DatasetFilter.ALL.value in dataset_filter and len(dataset_filter) > 1:
            raise ValueError("DATASET_FILTER='all' cannot be combined with other values")

        return dataset_filter

    def test_all_is_valid(self):
        assert self._validate_dataset_filter("all") == ["all"]

    def test_single_filter(self):
        assert self._validate_dataset_filter("qa") == ["qa"]
        assert self._validate_dataset_filter("trajectory") == ["trajectory"]
        assert self._validate_dataset_filter("report") == ["report"]

    def test_multiple_filters(self):
        result = self._validate_dataset_filter("qa,trajectory")
        assert set(result) == {"qa", "trajectory"}

    def test_whitespace_handling(self):
        result = self._validate_dataset_filter(" qa , trajectory ")
        assert set(result) == {"qa", "trajectory"}

    def test_case_insensitive(self):
        assert self._validate_dataset_filter("QA") == ["qa"]
        assert self._validate_dataset_filter("Trajectory") == ["trajectory"]

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError, match="Invalid DATASET_FILTER"):
            self._validate_dataset_filter("invalid")

    def test_all_combined_with_others_raises(self):
        with pytest.raises(ValueError, match="cannot be combined"):
            self._validate_dataset_filter("all,qa")

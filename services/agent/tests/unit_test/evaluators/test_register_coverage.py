# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Additional unit tests for evaluator register modules to improve coverage."""

from pydantic import ValidationError
import pytest

from vss_agents.evaluators.customized_qa_evaluator.register import CustomizedQAEvaluatorConfig
from vss_agents.evaluators.customized_trajectory_evaluator.register import CustomizedTrajectoryEvaluatorConfig


class TestCustomizedQAEvaluatorConfig:
    """Test CustomizedQAEvaluatorConfig model."""

    def test_required_fields(self):
        config = CustomizedQAEvaluatorConfig(llm_name="gpt-4o")
        assert config.llm_name == "gpt-4o"
        assert config.evaluation_method_id == "qa"
        assert config.custom_prompt_template is None
        assert config.max_retries == 2
        assert config.llm_judge_reasoning is True

    def test_custom_values(self):
        config = CustomizedQAEvaluatorConfig(
            llm_name="custom-llm",
            evaluation_method_id="custom_qa",
            custom_prompt_template="Custom template {question} {answer} {reference}",
            max_retries=5,
            llm_judge_reasoning=False,
        )
        assert config.evaluation_method_id == "custom_qa"
        assert config.custom_prompt_template is not None
        assert config.max_retries == 5
        assert config.llm_judge_reasoning is False

    def test_missing_llm_name_raises(self):
        with pytest.raises(ValidationError):
            CustomizedQAEvaluatorConfig()


class TestCustomizedTrajectoryEvaluatorConfig:
    """Test CustomizedTrajectoryEvaluatorConfig model."""

    def test_required_fields(self):
        config = CustomizedTrajectoryEvaluatorConfig(llm_name="gpt-4o")
        assert config.llm_name == "gpt-4o"
        assert config.evaluation_method_id == "trajectory"
        assert config.track_agent_selected_tools_only is True
        assert config.custom_prompt_template_with_reference is None
        assert config.custom_prompt_template_without_reference is None
        assert config.max_retries == 2
        assert config.llm_judge_reasoning is True

    def test_custom_values(self):
        config = CustomizedTrajectoryEvaluatorConfig(
            llm_name="custom-llm",
            evaluation_method_id="custom_traj",
            track_agent_selected_tools_only=False,
            custom_prompt_template_with_reference="Template {question} {agent_trajectory} {answer} {reference}",
            custom_prompt_template_without_reference="Template {question} {agent_trajectory} {answer} {tool_schemas} {conversation_history}",
            max_retries=3,
            llm_judge_reasoning=False,
        )
        assert config.track_agent_selected_tools_only is False
        assert config.custom_prompt_template_with_reference is not None
        assert config.custom_prompt_template_without_reference is not None
        assert config.max_retries == 3

    def test_missing_llm_name_raises(self):
        with pytest.raises(ValidationError):
            CustomizedTrajectoryEvaluatorConfig()

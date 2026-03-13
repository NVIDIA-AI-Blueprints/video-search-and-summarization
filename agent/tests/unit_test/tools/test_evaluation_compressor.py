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
"""Unit tests for evaluation_compressor module."""

from pydantic import ValidationError
import pytest
from vss_agents.tools.evaluation_compressor import EvaluationCompressorConfig
from vss_agents.tools.evaluation_compressor import EvaluationCompressorInput
from vss_agents.tools.evaluation_compressor import remove_caption_details
from vss_agents.tools.evaluation_compressor import split_text_by_sections


class TestEvaluationCompressorConfig:
    """Test EvaluationCompressorConfig model."""

    def test_required_fields(self):
        config = EvaluationCompressorConfig(
            llm_name="openai_llm",
            token_limit=4000,
        )
        assert config.llm_name == "openai_llm"
        assert config.token_limit == 4000
        assert config.remove_caption_details is True

    def test_custom_remove_caption_details(self):
        config = EvaluationCompressorConfig(
            llm_name="openai_llm",
            token_limit=8000,
            remove_caption_details=False,
        )
        assert config.remove_caption_details is False

    def test_missing_llm_name_fails(self):
        with pytest.raises(ValidationError):
            EvaluationCompressorConfig(token_limit=4000)

    def test_missing_token_limit_fails(self):
        with pytest.raises(ValidationError):
            EvaluationCompressorConfig(llm_name="openai_llm")


class TestEvaluationCompressorInput:
    """Test EvaluationCompressorInput model."""

    def test_basic_input(self):
        input_data = EvaluationCompressorInput(input_text="This is some text to compress.")
        assert input_data.input_text == "This is some text to compress."

    def test_empty_string(self):
        input_data = EvaluationCompressorInput(input_text="")
        assert input_data.input_text == ""

    def test_long_text(self):
        long_text = "A" * 10000
        input_data = EvaluationCompressorInput(input_text=long_text)
        assert len(input_data.input_text) == 10000


class TestRemoveCaptionDetails:
    """Test remove_caption_details function."""

    def test_removes_timestamp_lines(self):
        text = """[0.0] Person walking
[1.5] Vehicle passing
Regular text here"""
        result = remove_caption_details(text)
        assert "[0.0]" not in result
        assert "[1.5]" not in result
        assert "Regular text here" in result

    def test_preserves_non_timestamp_lines(self):
        text = """This is regular text.
Another line without timestamps.
Final line."""
        result = remove_caption_details(text)
        assert "This is regular text." in result
        assert "Another line without timestamps." in result
        assert "Final line." in result

    def test_empty_input(self):
        result = remove_caption_details("")
        assert result == ""

    def test_mixed_content(self):
        text = """Introduction paragraph.

[0.0] Caption 1
[1.0] Caption 2

Middle paragraph.

[2.0] Caption 3

Conclusion paragraph."""
        result = remove_caption_details(text)
        assert "Introduction paragraph." in result
        assert "Middle paragraph." in result
        assert "Conclusion paragraph." in result
        assert "[0.0]" not in result
        assert "[1.0]" not in result
        assert "[2.0]" not in result

    def test_timestamp_with_spaces(self):
        text = """  [0.5] Indented caption
[1.0] Normal caption"""
        result = remove_caption_details(text)
        assert "[0.5]" not in result
        assert "[1.0]" not in result


class TestSplitTextBySections:
    """Test split_text_by_sections function."""

    def test_single_section(self):
        text = "Paragraph 1"
        sections = split_text_by_sections(text, 1)
        assert len(sections) == 1
        assert sections[0] == "Paragraph 1"

    def test_two_sections(self):
        text = """Paragraph 1

Paragraph 2"""
        sections = split_text_by_sections(text, 2)
        assert len(sections) == 2
        assert "Paragraph 1" in sections[0]
        assert "Paragraph 2" in sections[1]

    def test_more_sections_than_paragraphs(self):
        text = "Single paragraph"
        sections = split_text_by_sections(text, 3)
        # Should return at least as many sections as paragraphs
        assert len(sections) >= 1

    def test_equal_sections(self):
        text = """P1

P2

P3

P4"""
        sections = split_text_by_sections(text, 2)
        assert len(sections) == 2

    def test_invalid_num_sections(self):
        with pytest.raises(ValueError):
            split_text_by_sections("test", 0)

    def test_negative_num_sections(self):
        with pytest.raises(ValueError):
            split_text_by_sections("test", -1)

    def test_many_paragraphs(self):
        paragraphs = [f"Paragraph {i}" for i in range(10)]
        text = "\n\n".join(paragraphs)
        sections = split_text_by_sections(text, 3)
        assert len(sections) == 3

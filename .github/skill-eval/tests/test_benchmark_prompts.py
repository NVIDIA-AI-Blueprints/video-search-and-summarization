# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import json

from benchmark.domain import (
    BenchmarkCase,
    BenchmarkGroup,
    Choice,
    ChoiceAnswer,
    GroupType,
    MultipleChoiceTask,
    VideoReference,
)
from benchmark.prompts import CHOICE_ANSWER_SCHEMA, render_question_prompt


def test_question_prompt_uses_canonical_choice_answer_schema() -> None:
    case = BenchmarkCase(
        task=MultipleChoiceTask(
            case_id="video-1-1",
            question="Which option?",
            choices=(Choice("A", "First"), Choice("B", "Second")),
        ),
        ground_truth=ChoiceAnswer(label="A"),
    )
    group = BenchmarkGroup(
        group_id="video-1",
        video=VideoReference("video-1", "https://example.invalid/video"),
        group_type=GroupType.RELEVANCE,
        group_structure="[1,2,3,4]",
        cases=(case,),
    )

    prompt = render_question_prompt(group, case, first=True)

    assert json.loads(CHOICE_ANSWER_SCHEMA) == ChoiceAnswer.model_json_schema()
    assert CHOICE_ANSWER_SCHEMA in prompt
    assert "Do not include explanations, Markdown, or additional text." in prompt

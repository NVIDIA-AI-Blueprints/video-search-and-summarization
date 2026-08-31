# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Question prompts that do not coach the skill's routing policy."""

from __future__ import annotations

import json

from benchmark.domain import BenchmarkCase, BenchmarkGroup, ChoiceAnswer


CHOICE_ANSWER_SCHEMA = json.dumps(
    ChoiceAnswer.model_json_schema(),
    separators=(",", ":"),
)


def render_question_prompt(group: BenchmarkGroup, case: BenchmarkCase, *, first: bool) -> str:
    question = case.task.question
    if first:
        question = f"Regarding video {group.video.video_id} in memory, {question}"
    options = "\n".join(f"{choice.label}. {choice.text}" for choice in case.task.choices)
    return (
        "Use the vss-ask-video skill to answer this question.\n\n"
        f"Question:\n{question}\n\nOptions:\n{options}\n\n"
        "Return only one JSON object matching this schema:\n"
        f"{CHOICE_ANSWER_SCHEMA}\n\n"
        "Do not include explanations, Markdown, or additional text."
    )

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
import logging

from vss_agents.evaluators.utils import ScoreOutputParser
from vss_agents.evaluators.utils import invoke_llm_with_retry
from vss_agents.evaluators.utils import should_evaluate
from vss_agents.evaluators.utils import strip_agent_think_tags

from langchain_core.language_models import BaseChatModel
from langchain_core.prompts import PromptTemplate
from nat.eval.evaluator.base_evaluator import BaseEvaluator
from nat.eval.evaluator.evaluator_model import EvalInputItem
from nat.eval.evaluator.evaluator_model import EvalOutputItem

logger = logging.getLogger(__name__)


# Default QA evaluation prompt for QA tasks
DEFAULT_QA_EVAL_PROMPT = PromptTemplate(
    input_variables=["question", "answer", "reference"],
    template="""You are an expert evaluator assessing a Question Answering (QA) system's response accuracy.

Question Asked: {question}

Agent's Answer: {answer}

Ground Truth Answer: {reference}

EVALUATION TASK:
Compare the agent's answer against the ground truth and determine if they are semantically equivalent with a nuanced score between 0.0 and 1.0.

EVALUATION CRITERIA:

1. **Factual Correctness**: Does the agent's answer convey the same factual information as the ground truth?
    - For Yes/No questions: The boolean value must match exactly.
    - For counting questions: The number must exactly match the ground truth.
    - For temporal questions: Allow ±5 seconds tolerance for timestamps.
    - For descriptive questions: Key facts and details must align.

2. **Completeness**: Does the agent's answer include all key information from the ground truth?
    - Partial answers should receive partial credit.
    - Additional correct details beyond ground truth are acceptable.

3. **Semantic Equivalence**: Different phrasings of the same answer are acceptable.
    - "Yes" and "Yes, a worker dropped one box" are equivalent for a Yes/No question.
    - "60 seconds" and "at the 1 minute mark" are equivalent.
    - "No" and "The worker is not wearing a safety vest" are equivalent.

SCORING GUIDELINES:
- 1.0: Perfect match - answer is factually correct and complete
- 0.8-0.9: Essentially correct with minor omissions or slight imprecision
- 0.6-0.7: Partially correct - captures main point but missing some details
- 0.4-0.5: Mixed - some correct elements but significant errors or omissions
- 0.2-0.3: Mostly incorrect but shows some understanding
- 0.0-0.1: Completely wrong or irrelevant answer

IMPORTANT NOTES:
- Focus on SEMANTIC correctness, not exact text matching.

OUTPUT:
Think through your evaluation step by step, then output ONLY a single decimal number
(your score from 0.0 to 1.0) on the final line.
""",
)


class CustomizedQAEvaluator(BaseEvaluator):
    """
    QA Evaluator that uses an LLM judge to compare agent answers against ground truth.

    This evaluator is designed for QA tasks where:
    - Questions are asked about video content
    - Ground truth answers are provided
    - Semantic equivalence is more important than exact text matching
    """

    def __init__(
        self,
        llm: BaseChatModel,
        max_concurrency: int = 8,
        custom_prompt: PromptTemplate | None = None,
        max_retries: int = 2,
        evaluation_method_id: str = "qa",
        llm_judge_reasoning: bool = True,
    ):
        """
        Initialize the QA Evaluator.

        Args:
            llm: The LLM to use as a judge
            max_concurrency: Maximum concurrent evaluations
            custom_prompt: Optional custom prompt template (must include: question, answer, reference)
            max_retries: Maximum retry attempts for failed evaluations
            evaluation_method_id: The method ID to match against dataset's evaluation_method field
            llm_judge_reasoning: Whether to enable LLM judge reasoning mode
        """
        super().__init__(max_concurrency=max_concurrency, tqdm_desc="Evaluating QA")
        self.llm = llm
        self.max_retries = max_retries
        self.evaluation_method_id = evaluation_method_id
        self.llm_judge_reasoning = llm_judge_reasoning

        self.eval_prompt = custom_prompt if custom_prompt is not None else DEFAULT_QA_EVAL_PROMPT
        self.output_parser = ScoreOutputParser()

        logger.info(f"Using {'custom' if custom_prompt is not None else 'default'} QA evaluation prompt")
        logger.info(f"Evaluation method ID: {self.evaluation_method_id}")
        logger.info(f"LLM judge reasoning: {self.llm_judge_reasoning}")
        logger.debug("QA evaluator initialized.")

    async def evaluate_item(self, item: EvalInputItem) -> EvalOutputItem:
        """
        Evaluate a single QA item by comparing agent's answer to ground truth.

        Args:
            item: The evaluation input containing question, answer, and reference

        Returns:
            EvalOutputItem with score and reasoning
        """
        if not should_evaluate(item, self.evaluation_method_id):
            logger.info(
                f"Skipping evaluation for item {item.id} - '{self.evaluation_method_id}' not in evaluation_method"
            )
            return EvalOutputItem(
                id=item.id, score=None, reasoning=f"Skipped: not marked for {self.evaluation_method_id} evaluation"
            )

        question = item.input_obj
        # Strip out <agent-think> tags from generated answer
        generated_answer = strip_agent_think_tags(item.output_obj)
        reference = (
            item.expected_output_obj if hasattr(item, "expected_output_obj") and item.expected_output_obj else ""
        )

        if not reference:
            logger.warning(f"Item {item.id} marked for QA evaluation but has no ground_truth")
            return EvalOutputItem(
                id=item.id, score=0.0, reasoning="Error: marked for QA evaluation but no ground_truth provided"
            )

        # Format the evaluation prompt
        prompt_text = self.eval_prompt.format(
            question=question,
            answer=generated_answer,
            reference=reference,
        )

        # Build reasoning closure to capture local variables
        def build_reasoning(eval_result: dict) -> dict:
            return {
                "reasoning": eval_result["reasoning"],
                "question": question,
                "generated_answer": generated_answer,
                "ground_truth": reference,
            }

        return await invoke_llm_with_retry(
            llm=self.llm,
            prompt_text=prompt_text,
            output_parser=self.output_parser,
            item_id=item.id,
            max_retries=self.max_retries,
            evaluator_name="QA Evaluator",
            question_preview=question[:50] + "...",
            build_reasoning=build_reasoning,
            llm_judge_reasoning=self.llm_judge_reasoning,
        )

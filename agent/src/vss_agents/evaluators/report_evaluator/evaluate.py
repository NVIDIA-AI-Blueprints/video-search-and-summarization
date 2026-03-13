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

import asyncio
import json
import logging
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING
from typing import Any
from typing import cast

from nat.data_models.component_ref import ObjectStoreRef
from nat.data_models.evaluator import EvaluatorBaseConfig
from nat.eval.evaluator.base_evaluator import BaseEvaluator
from nat.eval.evaluator.evaluator_model import EvalInputItem
from nat.eval.evaluator.evaluator_model import EvalOutput
from nat.eval.evaluator.evaluator_model import EvalOutputItem
from pydantic import Field
import yaml

from vss_agents.evaluators.utils import should_evaluate
from vss_agents.utils.markdown_parser import parse_markdown_to_json

from .data_models import EvaluationScore
from .eval_config_models import EvalMetricsConfig
from .eval_config_models import FieldConfig
from .field_evaluators import EvaluationMetric

if TYPE_CHECKING:
    from .field_evaluators.llm_judge import LLMJudgeMetric

logger = logging.getLogger(__name__)


class ExtendedEvalOutputItem(EvalOutputItem):
    """Extended EvalOutputItem that includes vlm_field_score."""

    vlm_field_score: float | None = Field(None, description="VLM field score for this report")


class ExtendedEvalOutput(EvalOutput):
    """Extended EvalOutput that includes average_vlm_field_score."""

    average_score: float | None = None
    average_vlm_field_score: float | None = None
    eval_output_items: list[ExtendedEvalOutputItem] = Field(default_factory=list)


class ReportEvaluatorConfig(EvaluatorBaseConfig, name="report_evaluator"):
    """Configuration for the report evaluator."""

    eval_metrics_config_path: str = Field(..., description="Path to the YAML evaluation metrics configuration file.")

    reference_base_dir: str = Field(..., description="Path to the reference reports directory.")

    object_store: ObjectStoreRef = Field(description="Reference to the object store.")

    evaluation_method_id: str = Field(
        default="report",
        description="The evaluation method ID that this evaluator corresponds to. "
        "Items in the dataset must have this ID in their 'evaluation_method' field to be evaluated.",
    )

    metric_configs: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        description="Configuration for each metric type.",
    )

    report_url_pattern: str = Field(
        ...,
        description="Regex pattern to match the report URL in the agent response. First capture group should be the filename.",
    )

    include_vlm_output: bool = Field(
        default=True,
        description="Whether to include VLM field score in the evaluation output.",
    )

    vlm_related_fields: list[str] | None = Field(
        default=None,
        description="List of section names that are related to VLM output.",
    )


def _load_eval_metrics_yaml(config_path: str) -> EvalMetricsConfig:
    """
    Load and validate evaluation metrics from YAML file.

    Returns:
        Validated EvalMetricsConfig
    """
    path = Path(config_path)
    if not path.is_absolute():
        path = Path.cwd() / path

    if not path.exists():
        raise FileNotFoundError(f"Evaluation metrics config not found: {path}")

    with open(path) as f:
        raw_config = yaml.safe_load(f)
        if not raw_config:
            raise ValueError(f"Evaluation metrics config at {path} is empty")

    try:
        validated_config = EvalMetricsConfig.from_dict(raw_config)
    except Exception as e:
        raise ValueError(f"Invalid evaluation metrics config at {path}: {e}") from e

    logger.info(f"Loaded and validated evaluation metrics from {path}")
    return validated_config


async def _fetch_and_parse_report(
    object_store_client: Any, response: str, url_pattern: str, camera_id: str | None = None
) -> tuple[dict[str, Any], str]:
    """
    Fetch report from object store and parse to JSON.

    Args:
        object_store_client: Object store client
        response: Generated response containing report URL
        url_pattern: Regex pattern to extract report URL
        camera_id: Optional camera ID to construct full path (e.g., "camera_001")

    Returns:
        Tuple of (parsed_report, report_url)
    """
    # Extract URL and filename from response
    url_match = re.search(url_pattern, response)
    if not url_match:
        raise ValueError(f"No report URL found in response matching pattern: {url_pattern}")

    report_url = url_match.group(0)  # Full URL for logging
    filename = (
        url_match.group(1) if url_match.lastindex and url_match.lastindex >= 1 else report_url.split("/")[-1]
    )  # Extract filename from capture group or URL

    # Construct object store path with camera_id prefix to avoid conflicts
    object_path = f"{camera_id}/{filename}" if camera_id else filename

    # Fetch from object store
    obj = await object_store_client.get_object(object_path)
    if not obj or not obj.data:
        raise ValueError(f"Report not found in object store: {object_path}")

    content = obj.data.decode("utf-8") if isinstance(obj.data, bytes) else obj.data

    return parse_markdown_to_json(content), report_url


class ReportEvaluator(BaseEvaluator):
    """
    Hierarchical report evaluator with two-stage evaluation:
    1. Field-level scoring (explicit metrics + dynamic discovery for unspecified fields)
    2. Section-level scoring (section treated as a field with dict value)
    """

    def __init__(
        self,
        config: EvalMetricsConfig,
        metric_instances: dict[str, EvaluationMetric],
        object_store_client: Any,
        report_url_pattern: str,
        reference_base_dir: str = "",
        include_vlm_output: bool = True,
        vlm_related_fields: list[str] | None = None,
        max_concurrency: int = 4,
        evaluation_method_id: str = "report",
    ) -> None:
        """
        Initialize the report evaluator.

        Args:
            config: Validated EvalMetricsConfig
            metric_instances: Initialized metric instances
            object_store_client: Object store for fetching reports
            report_url_pattern: Regex pattern to extract report URL
            reference_base_dir: Base directory for reference report files (optional; uses cwd if empty)
            include_vlm_output: Whether to include VLM field score in output
            vlm_related_fields: List of section names related to VLM output
            max_concurrency: Max concurrent evaluations
            evaluation_method_id: The method ID to match against dataset's evaluation_method field
        """
        super().__init__(max_concurrency, tqdm_desc="Evaluating agent generated reports")
        self.config = config
        self.metric_instances = metric_instances
        self.object_store_client = object_store_client
        self.report_url_pattern = report_url_pattern
        self.reference_base_dir = reference_base_dir
        self.include_vlm_output = include_vlm_output
        self.vlm_related_fields = vlm_related_fields
        self.evaluation_method_id = evaluation_method_id
        logger.info(f"Report evaluator initialized with evaluation_method_id: {self.evaluation_method_id}")

    async def evaluate(self, eval_input_items: list[EvalInputItem]) -> ExtendedEvalOutput:
        """
        Override evaluate to add custom aggregation for VLM field scores.

        Args:
            eval_input_items: List of evaluation input items

        Returns:
            ExtendedEvalOutput with average_score and average_vlm_field_score
        """
        # Call base evaluate method to get standard output
        result = await super().evaluate(eval_input_items)

        # Calculate average VLM field score
        average_vlm_field_score = None
        if self.include_vlm_output:
            vlm_field_scores = []
            for item in result.eval_output_items:
                if hasattr(item, "vlm_field_score"):
                    vlm_field_scores.append(item.vlm_field_score if item.vlm_field_score is not None else 0.0)

            average_vlm_field_score = sum(vlm_field_scores) / len(vlm_field_scores) if vlm_field_scores else None

            # Log the results
            vlm_score_str = f"{average_vlm_field_score:.4f}" if average_vlm_field_score is not None else "N/A"
            avg_score_str = f"{result.average_score:.4f}" if result.average_score is not None else "N/A"
            logger.info(f"Evaluation complete: average_score={avg_score_str}, average_vlm_field_score={vlm_score_str}")
        else:
            avg_score_str = f"{result.average_score:.4f}" if result.average_score is not None else "N/A"
            logger.info(f"Evaluation complete: average_score={avg_score_str} (VLM field score disabled)")

        extended_output = ExtendedEvalOutput(
            average_score=result.average_score,
            eval_output_items=result.eval_output_items,
            average_vlm_field_score=average_vlm_field_score,
        )

        return extended_output

    async def evaluate_item(self, item: EvalInputItem) -> ExtendedEvalOutputItem:
        """Evaluate an item from the evaluation dataset."""
        if not should_evaluate(item, self.evaluation_method_id):
            logger.info(
                f"Skipping evaluation for item {item.id} - '{self.evaluation_method_id}' not in evaluation_method"
            )
            return ExtendedEvalOutputItem(
                id=item.id,
                score=None,
                vlm_field_score=None,
                reasoning=f"Skipped: not marked for {self.evaluation_method_id} evaluation",
            )

        try:
            answer = item.expected_output_obj  # Reference file path
            generated_answer = item.output_obj  # Generated report reference

            # Load reference
            base_dir = Path(self.reference_base_dir) if self.reference_base_dir else Path.cwd()
            reference_path = base_dir / answer

            with open(reference_path) as f:
                reference = json.load(f)

            # Extract camera_id from reference path
            camera_id = None
            camera_match = re.search(r"camera_\d+", str(reference_path))
            if camera_match:
                camera_id = camera_match.group(0)
                logger.debug(f"Extracted camera_id: {camera_id} from reference path: {reference_path}")

            # Fetch and parse generated report
            try:
                generated, actual_filename = await _fetch_and_parse_report(
                    self.object_store_client, generated_answer, self.report_url_pattern, camera_id
                )
            except ValueError as e:
                logger.warning(f"Failed to fetch or parse report: {e}. Assigning score 0.")
                return ExtendedEvalOutputItem(
                    id=item.id,
                    score=0.0,
                    vlm_field_score=0.0 if self.include_vlm_output else None,
                    reasoning={"error": str(e)},
                )

            # Evaluate the report
            logger.info(
                f"Evaluating report {item.id} with reference {reference_path} and generated report {actual_filename}..."
            )
            result = await self.evaluate_tree(reference, generated, self.config.root, path=[self.config.root_key])

            # Top level report overall score
            if result.section_score is None:
                logger.warning(f"Item {item.id} top-level score is None. Some error occurred during evaluation.")
            else:
                logger.info(f"Item {item.id} top-level score: {result.section_score:.3f}")

            # Calculate VLM field score
            vlm_field_score = None
            if self.include_vlm_output and self.vlm_related_fields:
                vlm_scores = []
                for section_name in self.vlm_related_fields:
                    if section_name in result.field_scores and result.field_scores[section_name] is not None:
                        section_eval = result.field_scores[section_name]
                        section_score = section_eval.section_score if section_eval else None
                        if section_score is not None:
                            vlm_scores.append(section_score)
                            logger.info(f"VLM section '{section_name}' score: {section_score:.3f}")
                        else:
                            logger.warning(f"VLM section '{section_name}' has None score, treating as 0.0")
                            vlm_scores.append(0.0)
                    else:
                        logger.warning(f"VLM section '{section_name}' not found in evaluation results")

                vlm_field_score = sum(vlm_scores) / len(vlm_scores) if vlm_scores else None

                if vlm_field_score is not None:
                    logger.info(f"Item {item.id} VLM field score: {vlm_field_score:.3f}")
                else:
                    logger.warning(f"Item {item.id} VLM field score could not be calculated")

            return ExtendedEvalOutputItem(
                id=item.id,
                score=result.section_score,
                vlm_field_score=vlm_field_score,
                reasoning={
                    "sections": result.field_scores,
                    "metadata": {"reference_file": str(reference_path), "actual_file": actual_filename},
                },
            )

        except Exception as e:
            logger.error(f"Evaluation failed for item {item.id}: {e}", exc_info=True)
            return ExtendedEvalOutputItem(id=item.id, score=None, vlm_field_score=None, reasoning={"error": str(e)})

    async def evaluate_tree(self, reference: Any, actual: Any, config: FieldConfig, path: list[str]) -> EvaluationScore:
        """
        Recursively evaluate a node (field or section) in the report.

        Args:
            reference: Reference data at this node
            actual: Actual generated data at this node
            config: FieldConfig for this node
            path: Current path in the tree (for logging)

        Returns:
            EvaluationScore for the node
        """
        # Default to llm_judge if no method specified
        if (method := config.method) is None:
            method = "llm_judge"
            logger.debug(f"No method specified for '{'.'.join(path)}', defaulting to llm_judge")
        explicit_fields = config.fields or {}
        allow_dynamic_discovery = config.allow_dynamic_field_discovery

        is_section = bool(explicit_fields or allow_dynamic_discovery)

        # Evaluate fields within the section
        field_scores: dict[str, EvaluationScore | None] = {}

        if is_section:
            if not isinstance(reference, dict):
                logger.warning(
                    f"Section '{'.'.join(path)}' expects dict reference but got {type(reference).__name__}. "
                    f"This may indicate a mismatch between config and reference data."
                )
                return EvaluationScore.from_error(
                    error_message=f"Reference at '{'.'.join(path)}' is {type(reference).__name__}, expected dict for section",
                    method=method,
                    actual_value=actual,
                    reference_value=reference,
                )
            actual_dict = actual if isinstance(actual, dict) else {}

            # 1. Evaluate explicit fields from config
            if explicit_fields:
                tasks = [
                    self.evaluate_tree(
                        reference.get(field_name),
                        actual_dict.get(field_name),
                        explicit_fields[field_name],
                        [*path, field_name],
                    )
                    for field_name in explicit_fields
                ]
                results = await asyncio.gather(*tasks)
                field_scores.update(zip(explicit_fields.keys(), results, strict=True))

            # 2. Evaluate dynamic fields using llm_judge batch evaluation
            if allow_dynamic_discovery:
                # See if there are fields in the actual report that do not have an explicit evaluation metric
                actual_unspecified = set(actual_dict.keys()) - set(explicit_fields)
                if actual_unspecified:
                    llm_judge = cast("LLMJudgeMetric", self.metric_instances["llm_judge"])
                    eval_results = await llm_judge.evaluate_with_field_discovery(
                        reference_section=reference,
                        actual_section=actual_dict,
                        unspecified_fields=list(actual_unspecified),
                    )

                    for field_name, result in eval_results.items():
                        if result is None:
                            field_scores[field_name] = EvaluationScore.from_error(
                                error_message="LLM failed to score this field during discovery",
                                method="llm_judge_with_field_discovery",
                                actual_value=actual_dict.get(field_name),
                                reference_value=None,
                            )
                        else:
                            # Extract score and reference_field from LLM result
                            score = result.get("score")
                            reference_field = result.get("reference_field")

                            # Look up reference_value from the reference section
                            if reference_field and reference_field in reference:
                                reference_value = reference[reference_field]
                            elif reference_field:
                                # Reference field specified but not found in reference section
                                reference_value = f"[no matching reference field: {reference_field}]"
                                logger.warning(
                                    f"Field '{field_name}': LLM identified reference field '{reference_field}' "
                                    f"but it does not exist in the reference section"
                                )
                            else:
                                # No reference field match found in LLM response
                                reference_value = "[no matching reference field found in LLM response]"
                                logger.debug(f"Field '{field_name}': No matching reference field found response")

                            field_scores[field_name] = EvaluationScore(
                                section_score=score,
                                method="llm_judge_with_field_discovery",
                                actual_value=actual_dict.get(field_name),
                                reference_value=reference_value,
                            )

                            # Log the dynamic field evaluation result
                            ref_info = f" is matched to {reference_field}" if reference_field else ""
                            logger.info(f"'{'.'.join([*path, field_name])}'{ref_info} and scored {score:.2f}")

        # Compute score for this node
        try:
            if method == "average":
                if not field_scores:
                    logger.warning(f"'{'.'.join(path)}' uses 'average' method but has no field scores")
                    score = 0.0
                else:
                    # Aggregate field scores, treating None as 0.0
                    scores = [fs.section_score or 0.0 for fs in field_scores.values() if fs is not None]
                    score = sum(scores) / len(scores)
                    logger.info(f"'{'.'.join(path)}' averaged {len(scores)} field scored: {score:.2f}")
            else:
                score = await self._score_value(reference, actual, method, path)
                if score is None:
                    logger.error(f"Evaluation failed for '{'.'.join(path)}': metric returned None")
                    return EvaluationScore.from_error(
                        error_message="Evaluation failed: metric returned None",
                        method=method,
                        actual_value=actual,
                        reference_value=reference,
                        field_scores=field_scores,
                    )
                logger.info(f"'{'.'.join(path)}' scored {score:.2f}")
        except Exception as e:
            logger.exception(f"Error evaluating '{'.'.join(path)}': {e}")
            return EvaluationScore.from_error(
                error_message=str(e),
                method=method,
                actual_value=actual,
                reference_value=reference,
                field_scores=field_scores,
            )

        return EvaluationScore(
            section_score=score,
            method=method,
            actual_value=actual,
            reference_value=reference,
            field_scores=field_scores,
        )

    async def _score_value(self, reference: Any, actual: Any, method: str, path: list[str]) -> float | None:
        """Score any value using configured metric."""
        field_name = path[-1] if path else ""
        metric = self.metric_instances[method.lower()]

        # For non-dict values, convert to strings and replace env vars
        if not isinstance(reference, dict) and not isinstance(actual, dict):
            reference_str = os.path.expandvars(str(reference) if reference is not None else "")
            actual_str = str(actual) if actual is not None else ""
            return await metric.evaluate(actual_str, reference_str, field_name)

        # For dict values (sections), use metric directly
        return await metric.evaluate(actual, reference, field_name)

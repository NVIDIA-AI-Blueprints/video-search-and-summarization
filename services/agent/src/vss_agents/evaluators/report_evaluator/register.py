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

from collections.abc import AsyncGenerator
import logging

from nat.builder.builder import EvalBuilder
from nat.builder.evaluator import EvaluatorInfo
from nat.builder.framework_enum import LLMFrameworkEnum
from nat.cli.register_workflow import register_evaluator

from .evaluate import ReportEvaluator
from .evaluate import ReportEvaluatorConfig
from .evaluate import _load_eval_metrics_yaml
from .field_evaluators import METRIC_REGISTRY

logger = logging.getLogger(__name__)


@register_evaluator(config_type=ReportEvaluatorConfig)
async def register_report_evaluator(
    config: ReportEvaluatorConfig, builder: EvalBuilder
) -> AsyncGenerator[EvaluatorInfo]:
    """Register the report evaluator with NAT."""
    object_store_client = await builder.get_object_store_client(config.object_store)
    eval_metrics_config = _load_eval_metrics_yaml(config.eval_metrics_config_path)

    # Collect all unique methods from validated config
    unique_methods = eval_metrics_config.methods
    logger.info(f"Collected unique methods: {unique_methods}")

    # Validate and initialize each metric
    metric_instances = {}
    for method_name in unique_methods:
        # Validate method exists in registry
        if method_name not in METRIC_REGISTRY:
            available_metrics = ", ".join(sorted(METRIC_REGISTRY.keys()))
            raise ValueError(f"Unknown metric '{method_name}' found in config. Available metrics: {available_metrics}")

        metric_class = METRIC_REGISTRY[method_name]
        metric_config = config.metric_configs.get(method_name, {}).copy()

        # If llm_name is present, load the LLM using NAT builder
        if "llm_name" in metric_config:
            llm = await builder.get_llm(metric_config["llm_name"], wrapper_type=LLMFrameworkEnum.LANGCHAIN)
            metric_config["llm"] = llm
            logger.info(f"Loaded LLM '{metric_config['llm_name']}' for metric '{method_name}'")
            del metric_config["llm_name"]

        metric_instances[method_name] = metric_class(**metric_config)
        logger.info(f"Initialized metric: {method_name}")

    report_evaluator = ReportEvaluator(
        config=eval_metrics_config,
        metric_instances=metric_instances,
        object_store_client=object_store_client,
        report_url_pattern=config.report_url_pattern,
        reference_base_dir=config.reference_base_dir,
        include_vlm_output=config.include_vlm_output,
        vlm_related_fields=config.vlm_related_fields,
        max_concurrency=builder.get_max_concurrency(),
        evaluation_method_id=config.evaluation_method_id,
    )

    yield EvaluatorInfo(config=config, evaluate_fn=report_evaluator.evaluate, description="Report Evaluator")

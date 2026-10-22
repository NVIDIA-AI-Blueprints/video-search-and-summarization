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
from time import sleep, time
from typing import Any

from nemo_platform import NeMoPlatform

from src.api.models import ToolEvalType, WorkloadClassification
from src.backends.nmp.workspace import ensure_secret
from src.config import settings
from src.lib.flywheel.cancellation import check_cancellation
from src.log_utils import setup_logging

logger = setup_logging("data_flywheel.nemo.evaluator")

JUDGE_PROMPT = """
TASK / QUESTION
<<<
{{item.request.messages}}
>>>

REFERENCE ANSWER
<<<
{{item.expected_response.choices[0].message.content}}
>>>

CANDIDATE ANSWER
<<<
{{sample.output_text}}
>>>
"""

JUDGE_SYSTEM_PROMPT = """You are an expert evaluator.
Read the task, reference answer, and candidate answer.
Score similarity on a scale of 1 to 10 using this rubric:

10 - Nearly identical meaning; wording differences only.
8-9 - Same key info, maybe one minor detail off.
6-7 - Rough overlap but missing or altering several points.
4-5 - Some relation but wrong on most core content.
2-3 - Largely irrelevant or incorrect.
1 - Completely irrelevant or empty.

Return ONLY the integer (1-10). No other text."""

TOOL_CALLING_JUDGE_SYSTEM_PROMPT = """
You are an expert evaluator. Your task is to compare two tool call objects: a ground truth and a prediction. Each object contains metadata about a function call, including the function name and its arguments. Your goal is to determine whether the predicted tool call is correct according to the following strict criteria:

Evaluation Rules:

1. If the prediction is null, empty, or structurally invalid, return a rating of 0.
2. The prediction must include a valid `function.name` field that exactly matches the `function.name` in the ground truth.
3. The prediction must include a `function.arguments` field containing a valid JSON object, or a string that can be parsed into one.
4. Each argument key in the ground truth must also appear in the prediction and satisfy one of the following matching criteria:
   - **Strict-match arguments**: These are typically short identifiers (e.g., user_id, product_id). They must match the ground truth exactly.
   - **Semantic-match arguments**: These are typically longer, natural-language strings (e.g., queries, messages, questions). They should be semantically similar to the ground truth but may differ in wording.
5. If any required argument is missing or fails to meet its matching criteria, the rating must be 0.
6. Only return a rating of 1 if:
   - The function name matches exactly, AND
   - All required arguments are present and correctly matched (strict or semantic as appropriate).

Assume you're familiar with typical tool function usage patterns and can infer which argument types require exact matching versus semantic similarity.

Return your result in the following format:

RATING: 0 or 1
EXPLANATION: Explanation of the rating.
"""

TOOL_CALLING_JUDGE_PROMPT = """
*Ground Truth*: {{item.expected_response.choices[0].message.tool_calls}}
*Prediction*: {{sample.response.choices[0].message.tool_calls}}
"""

DPO_JUDGE_PROMPT = """You are an expert evaluator for language model responses. You will be given:
1. A prompt
2. The model's response
3. A preferred (chosen) response
4. A less preferred (rejected) response

Your task is to evaluate how well the model's response aligns with the preferred response on a scale of 1-10:
- Score 10: Model response is excellent, matches or exceeds the quality of the preferred response
- Score 7-9: Model response is good, aligns well with the preferred response
- Score 4-6: Model response is acceptable, has some alignment with the preferred response
- Score 1-3: Model response is poor, more similar to the rejected response

PROMPT:
{{item.messages}}

MODEL RESPONSE:
{{sample.output_text}}

PREFERRED RESPONSE:
{{item.chosen_response}}

LESS PREFERRED RESPONSE:
{{item.rejected_response}}

Provide your evaluation ONLY as an integer (1-10). No other text.
"""

# NMP 26.03 PlatformJobStatus → legacy DFW vocabulary used by DFW callers.
_LEGACY_STATUS_MAP = {
    "active": "running",
    "error": "failed",
}


def _to_legacy_status(status: str | None) -> str:
    if not status:
        return ""
    return _LEGACY_STATUS_MAP.get(status, status)


class Evaluator:
    """NMP 26.03 evaluation client.

    DFW historically used ``client.evaluation.jobs`` which accepted a free-form
    ``config`` dict containing tasks/metrics, plus a separate ``target`` model. In
    26.03 evaluation jobs are split into ``metric_jobs``, ``benchmark_jobs``, and
    ``metric_jobs.results`` is a sub-resource. Each metric_job runs **one** metric
    against a dataset (and optionally a model for online evaluations).

    Mapping:
      - LLM-as-judge / DPO / tool-calling-judge → ``metric_jobs.create`` with a
        ``MetricOnlineJobParam`` (the model is invoked at evaluation time).
      - Retriever / BEIR  → ``metric_jobs.create`` with a ``MetricRetrieverJobParam``.

    The result format also changed: 25.12 returned ``tasks.{name}.metrics.{name}.scores``;
    26.03 returns ``aggregate_scores`` (a list of score objects). Results helpers below
    translate the new shape back to DFW's legacy structure.
    """

    def __init__(
        self,
        judge_model_config: Any | None = None,
        include_tools: bool = False,
        include_tool_choice: bool = False,
        include_nvext: bool = False,
    ):
        self.nemo_url = settings.system_config.nmp_config.nemo_base_url
        self.nim_url = settings.system_config.nmp_config.nim_base_url
        assert self.nemo_url, "nemo_base_url must be set in config"
        self.namespace = settings.system_config.nmp_config.nmp_namespace
        self.workspace = settings.system_config.nmp_config.workspace
        # Client is created before normalizing the judge config: a remote judge
        # carrying a raw key needs a workspace secret materialised (see
        # _build_model_param).
        self.nemo_client = NeMoPlatform(
            base_url=self.nemo_url,
            inference_base_url=self.nim_url,
            workspace=self.workspace,
        )
        # Normalize the judge config into a 26.03 ``ModelParam`` (the API rejects
        # the legacy DFW ``api_endpoint`` shape with ``extra_forbidden``).
        # ``judge_model_config`` is either a plain string (local NIM model name)
        # or the legacy remote dict ``{api_endpoint: {...}}``.
        self.judge_model_config: dict[str, Any] | str | None = (
            self._build_model_param(judge_model_config) if judge_model_config else None
        )
        self.include_tools = include_tools
        self.include_tool_choice = include_tool_choice
        self.include_nvext = include_nvext

    @staticmethod
    def _to_nmp_model_name(model_name: str) -> str:
        """Apply the same character substitutions as ``NIMConfig.nmp_model_name``."""
        return model_name.replace("/", "-").replace("@", "-").replace(".", "-")

    def _inference_gateway_url(self) -> str:
        """Cluster-internal URL for the platform OpenAI-proxy inference gateway.

        The evaluator pod runs inside the cluster, so the externally-resolved
        ingress host (``http://nim.test``) is not reachable. We point at the
        platform API service directly: ``nemo-nemo-platform-api:8080``. The
        gateway resolves models referenced as ``{workspace}/{nmp-name}``.
        """
        # TODO(nmp-26.03): expose the in-cluster API service via NMPConfig so
        # downstream consumers do not depend on the default install name.
        return (
            "http://nemo-nemo-platform-api.default.svc.cluster.local:8080"
            f"/apis/inference-gateway/v2/workspaces/{self.workspace}/openai/-/v1/chat/completions"
        )

    def _build_model_param(self, target_model: str | dict[str, Any]) -> dict[str, Any] | str:
        """Translate the DFW target_model shape into a 26.03 ``ModelParam``.

        Accepts either a plain string ``model_name`` (local NIM deployed in the
        workspace) or a remote dict carrying ``api_endpoint`` (legacy DFW shape).
        Returns a ``ModelParam`` with required ``name`` + ``url`` fields.
        """
        if isinstance(target_model, str):
            # Local model — route via the in-cluster inference gateway.
            # If the caller already passes a workspace-qualified path (e.g.
            # ``dfwbp/cp1`` from a customized-model entity), keep it as-is;
            # otherwise normalize via ``_to_nmp_model_name`` and prefix with
            # the current workspace. Without this the merged-LoRA output gets
            # double-prefixed (``dfwbp/dfwbp-cp1``) and the IG returns 404.
            if "/" in target_model and target_model.split("/", 1)[0] == self.workspace:
                name = target_model
            else:
                name = f"{self.workspace}/{self._to_nmp_model_name(target_model)}"
            return {
                "name": name,
                "url": self._inference_gateway_url(),
                "format": "openai",
            }
        # Already a structured config. Accept either a 26.03-native ModelParam
        # ({name,url,...}) or the legacy DFW shape ({api_endpoint:{url,model_id,api_key}})
        # and normalize the latter into a ModelParam so the request is valid.
        if "api_endpoint" in target_model:
            api = target_model["api_endpoint"]
            param: dict[str, Any] = {
                "name": api.get("model_id") or api.get("name") or "",
                "url": api["url"],
                "format": "openai",
            }
            # NMP 26.03 ``ModelParam`` authenticates remote models only via
            # ``api_key_secret`` (the name of a workspace SecretRef); a raw
            # ``api_key`` is not a valid field. Prefer an explicit
            # ``api_key_secret`` from config; otherwise take the raw ``api_key``
            # (the same key ``validate_llm_judge`` uses directly), materialise it
            # into a workspace secret named after the model, and reference that.
            if api.get("api_key_secret"):
                param["api_key_secret"] = api["api_key_secret"]
            elif api.get("api_key"):
                secret_name = (
                    f"dfw-judge-{self._to_nmp_model_name(api.get('model_id') or 'remote-model')}"
                )
                param["api_key_secret"] = ensure_secret(
                    self.nemo_client, self.workspace, secret_name, api["api_key"]
                )
            return param
        return target_model

    # ------------------------------------------------------------------
    # Metric definitions (LLMJudgeMetricParam)
    # ------------------------------------------------------------------

    def _llm_judge_similarity_metric(self) -> dict[str, Any]:
        """LLM-as-judge metric scoring 1-10 similarity (used by LLM eval).

        NMP 26.03's ``llm-judge`` metric pod renders ``prompt_template`` as a
        chat-completions body. The supported shape is ``{"messages": [...]}``
        with role-tagged entries. A bare string ``prompt_template`` ends up
        sent to ``/v1/chat/completions`` as a legacy ``prompt`` field, which
        the NIM ignores → empty response → all rows score NaN.
        """
        return {
            "type": "llm-judge",
            "model": self.judge_model_config,
            "prompt_template": {
                "messages": [
                    {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": JUDGE_PROMPT},
                ]
            },
            "scores": [
                {
                    "name": "similarity",
                    "minimum": 1,
                    "maximum": 10,
                    "parser": {"type": "regex", "pattern": r"(\d+)"},
                }
            ],
        }

    def _llm_judge_tool_calling_metric(self) -> dict[str, Any]:
        """LLM-as-judge metric scoring 0/1 tool-call correctness.

        See ``_llm_judge_similarity_metric`` for the rationale behind the
        ``{"messages": [...]}`` prompt-template shape.
        """
        return {
            "type": "llm-judge",
            "model": self.judge_model_config,
            "prompt_template": {
                "messages": [
                    {"role": "system", "content": TOOL_CALLING_JUDGE_SYSTEM_PROMPT},
                    {"role": "user", "content": TOOL_CALLING_JUDGE_PROMPT},
                ]
            },
            "scores": [
                {
                    "name": "rating",
                    "minimum": 0,
                    "maximum": 1,
                    "parser": {"type": "regex", "pattern": r"RATING:\s*(\d+)"},
                }
            ],
        }

    def _llm_judge_dpo_metric(self) -> dict[str, Any]:
        """LLM-as-judge metric for DPO preference alignment (1-10).

        See ``_llm_judge_similarity_metric`` for the rationale behind the
        ``{"messages": [...]}`` prompt-template shape.
        """
        return {
            "type": "llm-judge",
            "model": self.judge_model_config,
            "prompt_template": {
                "messages": [
                    {"role": "user", "content": DPO_JUDGE_PROMPT},
                ]
            },
            "scores": [
                {
                    "name": "preference_score",
                    "minimum": 1,
                    "maximum": 10,
                    "parser": {"type": "regex", "pattern": r"(\d+)"},
                }
            ],
        }

    def _tool_calling_accuracy_metric(self) -> dict[str, Any]:
        """ToolCallingMetric (system metric) — exact tool-call match accuracy."""
        return {
            "type": "tool-calling",
            "reference": "{{ item.response.choices[0].message.tool_calls | tojson }}",
        }

    # ------------------------------------------------------------------
    # Dataset / template helpers
    # ------------------------------------------------------------------

    def _dataset_param(
        self, dataset_name: str, test_file: str | None = None, limit: int | None = None
    ) -> str:
        """Return a FilesetRef (``workspace/fileset-name``) pointing at the workspace fileset.

        NMP 26.03 ``metric_jobs`` accepts FilesetRef as a bare string; the old DFW
        25.12 envelope (``type: fileset`` + ``fileset`` + ``files`` + ``limit``) is
        rejected as ``extra_forbidden``. Sample subsetting / file selection is now
        expressed via the offline-job dataset or ``params.limit_samples``.
        """
        return f"{self.workspace}/{dataset_name}"

    def _online_template(self, tool_call: bool = False, dpo: bool = False) -> dict[str, Any]:
        """Return the prompt_template object for online metric jobs.

        DPO eval datasets ship items with ``messages`` at the top level (plus
        ``chosen_response`` / ``rejected_response``); LLM tool-calling / similarity
        datasets nest the OpenAI-style payload under ``request`` (``item.request.
        messages``, ``item.request.tools``). Pick the right path so the
        nemo-evaluator generation step doesn't hit
        ``UndefinedError: 'dict object' has no attribute 'request'``.
        """
        messages_path = "item.messages" if dpo else "item.request.messages"
        template: dict[str, Any] = {
            "messages": f"{{{{ {messages_path} | tojson }}}}",
        }
        if tool_call:
            template["tools"] = "{{ item.request.tools | tojson }}"
            # Use ``tool_choice: required`` so the eval-pod hits vllm's
            # ``_accept_all_tools`` / explicit tool path (which works) instead
            # of ``auto_tool_use`` (which triggers an outlines guided-decoding
            # bug — ``outlines.fsm.json_schema.to_regex`` hits
            # ``TypeError: argument of type 'NoneType' is not iterable`` while
            # walking even well-formed tool schemas, returning 500 for every
            # row and producing ``count=0, nan_count=100`` aggregate scores).
            # Direct ``curl`` with the same payload + ``tool_choice=required``
            # succeeds, confirming the issue is the ``auto`` code path.
            template["tool_choice"] = "required"
        else:
            if self.include_tools:
                template["tools"] = "{{ item.tools | tojson }}"
            if self.include_tool_choice:
                template["tool_choice"] = "{{ item.tool_choice }}"
            if self.include_nvext:
                template["nvext"] = "{{ item.nvext | tojson }}"
        return template

    # ------------------------------------------------------------------
    # Job lifecycle (status / results / cancel)
    # ------------------------------------------------------------------

    def get_job_uri(self, job_id: str) -> str:
        """Workspace-scoped metric_job URI (26.03)."""
        return (
            f"{self.nemo_url}/apis/evaluation/v2/workspaces/{self.workspace}/metric-jobs/{job_id}"
        )

    def _get_job_status(self, job_id: str) -> dict[str, Any]:
        """Fetch metric_job status and translate to legacy DFW dict shape.

        Legacy keys: ``status``, ``status_details.progress``.
        """
        try:
            status_response = self.nemo_client.evaluation.metric_jobs.get_status(
                name=job_id, workspace=self.workspace
            )
            status_dict = (
                status_response.model_dump()
                if hasattr(status_response, "model_dump")
                else dict(status_response)
            )
        except Exception as e:
            logger.error(f"Failed to get job status: {e}")
            raise

        # Derive a percentage from steps if present.
        percentage: float | None = None
        for step in status_dict.get("steps") or []:
            for task in step.get("tasks") or []:
                td = task.get("status_details") or {}
                if td.get("progress_pct") is not None:
                    try:
                        percentage = max(percentage or 0.0, float(td["progress_pct"]))
                    except (TypeError, ValueError):
                        pass

        details = dict(status_dict.get("status_details") or {})
        if percentage is not None and "progress" not in details:
            details["progress"] = percentage

        return {
            **status_dict,
            "status": _to_legacy_status(status_dict.get("status")),
            "status_details": details,
        }

    def wait_for_evaluation(
        self,
        job_id: str,
        flywheel_run_id: str,
        polling_interval: int = 10,
        timeout: int = 6000,
        progress_callback=None,
    ) -> dict[str, Any]:
        """Poll a metric_job until it terminates."""
        start_time = time()
        last_progress = 0.0

        while True:
            try:
                check_cancellation(flywheel_run_id)
            except Exception as e:
                logger.info(f"Evaluation wait cancelled: {e}")
                raise e

            current_time = time()
            if current_time - start_time > timeout:
                error_message = f"Evaluation stalled for more than {timeout} seconds"
                logger.error(error_message)
                if progress_callback:
                    progress_callback({"progress": 0.0, "error": error_message})
                raise TimeoutError(error_message)

            job_data = self._get_job_status(job_id)
            status = job_data["status"]

            if status == "running":  # translated from "active"
                progress_value = job_data.get("status_details", {}).get("progress")
                progress = float(progress_value) if progress_value is not None else 0.0
                logger.info(f"Job status: {status} Progress: {progress}%")

                if progress > last_progress:
                    start_time = current_time
                    last_progress = progress

                if progress_callback:
                    progress_callback({"progress": progress})

            elif status == "completed":
                logger.info(f"Job status: {status} Progress: 100%")
                if progress_callback:
                    progress_callback({"progress": 100.0})
                return job_data

            elif status in ("created", "pending"):
                logger.info(f"Job status: {status} - Waiting for job to start...")
                if progress_callback:
                    progress_callback({"progress": 0.0})

            else:
                error_message = f"Job status: {status} / {job_data.get('status_details', {})}"
                if progress_callback:
                    progress_callback({"progress": 0.0, "error": error_message})
                logger.warning(error_message)
                raise Exception(error_message)

            sleep(polling_interval)

    def get_evaluation_status(self, job_id: str) -> dict[str, Any]:
        """Return the legacy-shaped status dict for ``job_id``."""
        job_data = self._get_job_status(job_id)
        status = job_data["status"]

        if status == "running":
            progress_value = job_data.get("status_details", {}).get("progress")
            progress = float(progress_value) if progress_value is not None else 0.0
            logger.info(f"Job status: {status} Progress: {progress}%")
        elif status == "completed":
            logger.info("Job completed")
        return job_data

    def get_evaluation_results(self, job_id: str) -> dict[str, Any]:
        """Fetch evaluation results and translate into the legacy DFW shape.

        Legacy structure expected by callers: ``{tasks: {task: {metrics: {metric: {scores: {name: {value}}}}}}}``.
        26.03 returns ``aggregate_scores.scores`` (list of score objects). We synthesize
        a single task ("llm-as-judge") since each metric_job runs exactly one metric.
        """
        try:
            agg = self.nemo_client.evaluation.metric_jobs.results.aggregate_scores.download(
                job=job_id, workspace=self.workspace
            )
            agg_dict = agg.model_dump() if hasattr(agg, "model_dump") else dict(agg)
        except Exception as e:
            logger.error(f"Failed to get evaluation results: {e}")
            raise

        scores: dict[str, Any] = {}
        for score in agg_dict.get("scores") or []:
            name = score.get("name") or score.get("metric_name") or "score"
            value = score.get("value")
            if value is None and "mean" in score:
                value = score["mean"]
            scores[name] = {
                "value": value,
                "stats": score.get("stats", {}),
            }

        # Synthesize the legacy task/metric tree DFW callers expect.
        return {
            "id": job_id,
            "tasks": {
                "llm-as-judge": {
                    "type": "metric-job",
                    "metrics": {name: {"scores": {name: data}} for name, data in scores.items()},
                }
            },
            "raw_aggregate": agg_dict,
        }

    def run_evaluation(
        self,
        dataset_name: str,
        workload_type: WorkloadClassification,
        target_model: str | dict[str, Any],
        test_file: str,
        tool_eval_type: ToolEvalType | None = None,
        limit: int | None = None,
    ) -> str:
        """Create a metric_job and return its name (the legacy ``job_id``)."""
        # Resolve metric + template based on workload type.
        if workload_type == WorkloadClassification.TOOL_CALLING:
            if tool_eval_type is None:
                raise ValueError("tool_eval_type must be provided for tool calling workload")
            tool_call = True
            if tool_eval_type == ToolEvalType.TOOL_CALLING_METRIC:
                metric = self._tool_calling_accuracy_metric()
            else:  # TOOL_CALLING_JUDGE
                metric = self._llm_judge_tool_calling_metric()
        elif workload_type == WorkloadClassification.DPO:
            tool_call = False
            metric = self._llm_judge_dpo_metric()
        else:
            tool_call = False
            metric = self._llm_judge_similarity_metric()

        # Build target model param. Local models are routed via the cluster-internal
        # inference gateway (the evaluator pod cannot resolve the external ingress).
        model_param = self._build_model_param(target_model)

        spec: dict[str, Any] = {
            "dataset": self._dataset_param(dataset_name, test_file, limit),
            "metric": metric,
            "model": model_param,
            "prompt_template": self._online_template(
                tool_call=tool_call,
                dpo=workload_type == WorkloadClassification.DPO,
            ),
        }
        # Tolerate single-row inference failures so a transient NIM 500 doesn't
        # nuke the entire metric_job. The eval pod's own error hint names
        # ``ignore_request_failure``; some SDK builds use ``ignore_inference_failure``.
        # The deployed platform validates ``params`` as a closed-world schema.
        spec["params"] = {"ignore_request_failure": True}
        if limit is not None:
            spec["params"]["limit_samples"] = limit

        try:
            eval_job = self.nemo_client.evaluation.metric_jobs.create(
                workspace=self.workspace,
                spec=spec,
            )
            logger.info(f"Created metric_job: {eval_job.name}")
            return eval_job.name
        except Exception as e:
            logger.error(f"Failed to launch evaluation job: {e}")
            raise

    def delete_evaluation_job(self, job_id: str) -> None:
        """Delete a metric_job."""
        try:
            self.nemo_client.evaluation.metric_jobs.delete(name=job_id, workspace=self.workspace)
            logger.info(f"Successfully deleted evaluation job {job_id}")
        except Exception as e:
            logger.error(f"Failed to delete evaluation job {job_id}: {e}")
            raise

    def run_retriever_evaluation(
        self,
        model_name: str,
        namespace: str,
        dataset_name: str | None = None,
        test_file: str | None = None,
        benchmark: str = "scidocs",
        metrics: list[str] | None = None,
        top_k: int = 10,
        limit: int | None = None,
    ) -> str:
        """Run a retriever metric_job (BEIR / SciDocs) — one metric per system metric requested."""
        if metrics is None:
            metrics = ["recall_5", "recall_10", "ndcg_cut_5", "ndcg_cut_10"]

        # NMP 26.03 renamed system retriever metrics from snake_case to
        # hyphen-prefixed ``retriever-<metric>`` (e.g. ``recall_5`` →
        # ``retriever-recall-5``, ``ndcg_cut_5`` → ``retriever-ndcg-cut-5``).
        # Normalize so callers can keep passing the legacy names.
        def _to_nmp_metric(m: str) -> str:
            if m.startswith("retriever-") or m.startswith("retriever_"):
                return m.replace("_", "-")
            return f"retriever-{m.replace('_', '-')}"

        metrics = [_to_nmp_metric(m) for m in metrics]

        if dataset_name:
            dataset: str | dict[str, Any] = self._dataset_param(dataset_name, test_file, limit)
        else:
            # Built-in BEIR/SciDocs dataset reference.
            dataset = {"type": "built-in", "name": benchmark}

        # NMP 26.03 retriever pipelines take embedding models as ``ModelParam``.
        # The retriever-eval pod always instantiates haystack's
        # ``NvidiaDocumentEmbedder`` for the embeddings_model, regardless of
        # the ``format`` we declare. Two haystack-side gotchas to navigate:
        #
        # 1. ``NvidiaDocumentEmbedder.url_validation`` only accepts URLs of the
        #    form ``http://host:port`` and rejects anything with a longer path
        #    (``ValueError: Base URL path is not recognized``). The platform
        #    inference-gateway URL (``.../apis/inference-gateway/v2/
        #    workspaces/<ws>/openai/-/v1/embeddings``) has a deep path → eval
        #    pod fails before any inference call. We bypass the gateway and
        #    hit the deployed embedding NIM's k8s Service directly at
        #    ``http://md-<ws>-<nmp_model_name>.default.svc.cluster.local:8000``.
        #
        # 2. The class defaults ``api_key`` to ``EnvVarSecret('NVIDIA_API_KEY',
        #    strict=True)``; the eval pod has no ``NVIDIA_API_KEY`` env, so
        #    haystack fails component deserialization before any call. We
        #    reference a workspace secret named ``nvidia-api-key`` (its value
        #    isn't validated against the in-cluster NIM — direct k8s Service
        #    URLs accept unauthenticated requests). The cluster-level fix is
        #    to also set ``NVIDIA_API_KEY`` env on platform-job-step pods
        #    (see fix-readme.md "embedder ``api_key_secret`` for the retriever
        #    eval"); without that, haystack's strict-env check still fails.
        nmp_name = self._to_nmp_model_name(model_name)
        # NMP's retriever pipeline appends ``/embeddings`` (no ``/v1/``)
        # to our base URL regardless of ``format``. Haystack's
        # ``NvidiaDocumentEmbedder.url_validation`` requires the rendered URL
        # to end with ``v1/embeddings``, so pre-append ``/v1`` to the base
        # URL — final rendered URL becomes
        # ``http://md-<ws>-<nmp_name>.default.svc.cluster.local:8000/v1/embeddings``
        # which haystack accepts.
        embedding_url = f"http://md-{self.workspace}-{nmp_name}.default.svc.cluster.local:8000/v1"
        # The direct NIM service responds with the model's **native** name
        # (e.g. ``nvidia/llama-3.2-nv-embedqa-1b-v2``) — not the workspace-
        # qualified hyphen-normalized form (``dfwbp/nvidia-llama-3-2-nv-
        # embedqa-1b-v2``). Hitting it with the latter returns 404
        # ``Unknown model``. Pass the original DFW ``model_name`` as-is.
        embedding_model_param = {
            "name": model_name,
            "url": embedding_url,
            "format": "openai",
            "api_key_secret": "nvidia-api-key",
        }
        # NMP 26.03 ``RetrieverPipelineParam`` requires a single ``embeddings_model``
        # field (older SDKs accepted query/index split). ``top_k`` is no longer
        # part of the pipeline params either; it now lives on
        # ``SystemMetricParam`` as a metric_params override when needed.
        retriever_pipeline = {
            "embeddings_model": embedding_model_param,
        }

        # MetricRetrieverJobParam takes a single metric. For retriever evals
        # each metric is run as its own job and results are merged downstream.
        # The deployed NMP rejects inline ``SystemMetricParam`` for retriever
        # jobs ("Unsupported job with custom system metric — use metric
        # reference instead 'system/<metric-name>'"), so we pass the bare
        # ``MetricRef`` string.
        primary_metric = metrics[0]
        metric_ref = f"system/{primary_metric}"
        spec: dict[str, Any] = {
            "dataset": dataset,
            "metric": metric_ref,
            "retriever_pipeline": retriever_pipeline,
        }
        # Tolerate single-row inference failures so a transient NIM 500 doesn't
        # nuke the entire metric_job. The eval pod's own error hint names
        # ``ignore_request_failure``; some SDK builds use ``ignore_inference_failure``.
        # The deployed platform validates ``params`` as a closed-world schema.
        spec["params"] = {"ignore_request_failure": True}
        if limit is not None:
            spec["params"]["limit_samples"] = limit

        try:
            eval_job = self.nemo_client.evaluation.metric_jobs.create(
                workspace=self.workspace,
                spec=spec,
            )
            logger.info(f"Started retriever metric_job: {eval_job.name}")
            return eval_job.name
        except Exception as e:
            logger.error(f"Failed to start retriever evaluation: {e}")
            raise

    def parse_retriever_evaluation_results(self, results: dict) -> dict[str, float]:
        """Parse retriever results (legacy-shaped from ``get_evaluation_results``)."""
        scores: dict[str, float] = {}

        try:
            tasks = results.get("tasks", {})
            for _task_name, task_data in tasks.items():
                for metric_key, metric_data in task_data.get("metrics", {}).items():
                    metric_name = metric_key.split(".")[-1] if "." in metric_key else metric_key
                    metric_scores = metric_data.get("scores", {})
                    if metric_name in metric_scores:
                        score_data = metric_scores[metric_name]
                        if isinstance(score_data, dict) and "value" in score_data:
                            scores[metric_name] = score_data["value"]
                        else:
                            scores[metric_name] = score_data

            # Also surface raw aggregate scores if present (26.03 list shape).
            if not scores and "raw_aggregate" in results:
                for s in results["raw_aggregate"].get("scores") or []:
                    name = s.get("name") or s.get("metric_name") or ""
                    if name and s.get("value") is not None:
                        scores[name] = s["value"]

            logger.info(f"Parsed retriever evaluation scores: {scores}")
            return scores
        except Exception as e:
            logger.error(f"Error parsing retriever evaluation results: {e}")
            return {}

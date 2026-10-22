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

"""Tests for trigger API schemas."""

import math

import pytest
from pydantic import ValidationError

from src.api.triggers.schemas import (
    NIMExecutionResultResponse,
    TriggerCreateRequest,
    TriggerUpdateRequest,
)
from src.job_types import DEFT_JOB_TYPES, JobType


class TestTriggerCreateRequest:
    """Tests for TriggerCreateRequest schema."""

    def test_valid_data_threshold_request(self, sample_data_threshold_trigger):
        """Test valid data threshold trigger creation request."""
        request = TriggerCreateRequest(**sample_data_threshold_trigger)

        assert request.name == "test-data-threshold"
        assert request.trigger_type == "data_threshold"
        assert request.min_new_records == 100
        assert request.enable_data_ingestion is True

    def test_valid_metric_degradation_request(self, sample_metric_degradation_trigger):
        """Test valid metric degradation trigger creation request."""
        request = TriggerCreateRequest(**sample_metric_degradation_trigger)

        assert request.name == "test-metric-degradation"
        assert request.trigger_type == "metric_degradation"
        assert request.metric_thresholds == {"accuracy": 0.85, "f1_score": 0.80}

    def test_data_threshold_requires_min_new_records(self, sample_data_threshold_trigger):
        """Test data_threshold validation."""
        sample_data_threshold_trigger["min_new_records"] = None

        with pytest.raises(ValidationError) as exc_info:
            TriggerCreateRequest(**sample_data_threshold_trigger)

        assert "min_new_records is required" in str(exc_info.value).lower()

    def test_metric_degradation_requires_thresholds(self, sample_metric_degradation_trigger):
        """Test metric_degradation validation."""
        sample_metric_degradation_trigger["metric_thresholds"] = None

        with pytest.raises(ValidationError) as exc_info:
            TriggerCreateRequest(**sample_metric_degradation_trigger)

        assert "metric_thresholds is required" in str(exc_info.value).lower()

    def test_empty_metric_thresholds_rejected(self, sample_metric_degradation_trigger):
        """Test empty metric_thresholds is rejected."""
        sample_metric_degradation_trigger["metric_thresholds"] = {}

        with pytest.raises(ValidationError) as exc_info:
            TriggerCreateRequest(**sample_metric_degradation_trigger)

        assert "cannot be empty" in str(exc_info.value)

    def test_nan_threshold_value_rejected(self, sample_metric_degradation_trigger):
        """Test NaN values in metric_thresholds are rejected."""
        sample_metric_degradation_trigger["metric_thresholds"] = {"accuracy": math.nan}

        with pytest.raises(ValidationError) as exc_info:
            TriggerCreateRequest(**sample_metric_degradation_trigger)

        assert "cannot be NaN" in str(exc_info.value)

    def test_inf_threshold_value_rejected(self, sample_metric_degradation_trigger):
        """Test Inf values in metric_thresholds are rejected."""
        sample_metric_degradation_trigger["metric_thresholds"] = {"accuracy": math.inf}

        with pytest.raises(ValidationError) as exc_info:
            TriggerCreateRequest(**sample_metric_degradation_trigger)

        assert "cannot be" in str(exc_info.value) and "Inf" in str(exc_info.value)

    def test_invalid_cron_expression(self, sample_data_threshold_trigger):
        """Test invalid cron expression validation."""
        # Too few fields
        sample_data_threshold_trigger["eval_schedule"] = "0 0 *"

        with pytest.raises(ValidationError) as exc_info:
            TriggerCreateRequest(**sample_data_threshold_trigger)

        assert "5 fields" in str(exc_info.value)

    def test_name_validation(self, sample_data_threshold_trigger):
        """Test trigger name validation."""
        # Too short
        sample_data_threshold_trigger["name"] = ""

        with pytest.raises(ValidationError):
            TriggerCreateRequest(**sample_data_threshold_trigger)

        # Too long
        sample_data_threshold_trigger["name"] = "a" * 101

        with pytest.raises(ValidationError):
            TriggerCreateRequest(**sample_data_threshold_trigger)

    def test_model_source_validation(self, sample_data_threshold_trigger):
        """Test model_source enum validation."""
        sample_data_threshold_trigger["model_source"] = "invalid"

        with pytest.raises(ValidationError):
            TriggerCreateRequest(**sample_data_threshold_trigger)

    def test_job_type_validation(self, sample_data_threshold_trigger):
        """Test job_type enum validation."""
        sample_data_threshold_trigger["job_type"] = "invalid"

        with pytest.raises(ValidationError):
            TriggerCreateRequest(**sample_data_threshold_trigger)


class TestTriggerUpdateRequest:
    """Tests for TriggerUpdateRequest schema."""

    def test_partial_update_schedule(self):
        """Test partial update with schedule change."""
        request = TriggerUpdateRequest(eval_schedule="0 */12 * * *")

        assert request.eval_schedule == "0 */12 * * *"
        assert request.metric_thresholds is None
        assert request.enabled is None

    def test_partial_update_thresholds(self):
        """Test partial update with threshold changes."""
        request = TriggerUpdateRequest(metric_thresholds={"accuracy": 0.90, "f1_score": 0.85})

        assert request.metric_thresholds == {"accuracy": 0.90, "f1_score": 0.85}

    def test_partial_update_enabled(self):
        """Test partial update to enable/disable trigger."""
        request = TriggerUpdateRequest(enabled=False)

        assert request.enabled is False

    def test_invalid_cron_in_update(self):
        """Test invalid cron expression in update."""
        with pytest.raises(ValidationError):
            TriggerUpdateRequest(eval_schedule="invalid cron")

    def test_empty_thresholds_in_update(self):
        """Test empty thresholds rejected in update."""
        with pytest.raises(ValidationError):
            TriggerUpdateRequest(metric_thresholds={})


class TestNIMExecutionResultResponse:
    """Tests for NIMExecutionResultResponse schema."""

    def test_nim_result_response_creation(self, sample_nim_result):
        """Test creating NIM execution result response."""
        response = NIMExecutionResultResponse(
            model_name=sample_nim_result.model_name,
            model_used=sample_nim_result.model_used,
            metrics=sample_nim_result.metrics,
            thresholds_checked=sample_nim_result.thresholds_checked,
            passed_thresholds=sample_nim_result.passed_thresholds,
            needs_retraining=sample_nim_result.needs_retraining,
            training_triggered=sample_nim_result.training_triggered,
            customization_id=sample_nim_result.customization_id,
            fine_tuned_model=sample_nim_result.fine_tuned_model,
            post_training_metrics=sample_nim_result.post_training_metrics,
            incomplete_evaluation=sample_nim_result.incomplete_evaluation,
            eval_error=sample_nim_result.eval_error,
            customization_error=sample_nim_result.customization_error,
            post_eval_error=sample_nim_result.post_eval_error,
            skip_reason=None,
        )

        assert response.model_name == "meta/llama-3.2-1b-instruct"
        assert response.incomplete_evaluation is False

    def test_nim_result_with_error_fields(self, sample_nim_result_with_error):
        """Test NIM result response with error fields."""
        response = NIMExecutionResultResponse(
            model_name=sample_nim_result_with_error.model_name,
            model_used=sample_nim_result_with_error.model_used,
            metrics=sample_nim_result_with_error.metrics,
            thresholds_checked=sample_nim_result_with_error.thresholds_checked,
            passed_thresholds=sample_nim_result_with_error.passed_thresholds,
            incomplete_evaluation=sample_nim_result_with_error.incomplete_evaluation,
            eval_error=sample_nim_result_with_error.eval_error,
            needs_retraining=False,
            training_triggered=False,
            customization_id=None,
            fine_tuned_model=None,
            post_training_metrics={},
            customization_error=None,
            post_eval_error=None,
            skip_reason=None,
        )

        assert response.eval_error == "Evaluation job failed: timeout"
        assert response.incomplete_evaluation is True


class TestTriggerCreateRequestJobTypeContract:
    """The ``job_type`` Literal and the ``json_schema_extra`` oneOf must agree.

    The oneOf is documentation-only — pydantic does not enforce it — so a
    mistake here breaks generated clients and contract tests while the server
    keeps accepting the request. That is the hardest failure mode to debug, and
    grep showed the contract was entirely unpinned before this.

    Deliberately dependency-free (no ``jsonschema``): the branch semantics that
    matter are "which branch does this payload select" and "does that branch
    require config_name", both of which are checkable directly.
    """

    @staticmethod
    def _branches() -> list[dict]:
        return TriggerCreateRequest.model_json_schema()["oneOf"]

    @staticmethod
    def _matching_branches(branches: list[dict], payload: dict) -> list[int]:
        """Indices of the branches a payload satisfies (the oneOf semantics)."""
        matched = []
        for i, branch in enumerate(branches):
            allowed = branch["properties"]["job_type"]["enum"]
            if payload["job_type"] not in allowed:
                continue
            required = branch.get("required", [])
            if any(not payload.get(field) for field in required):
                continue
            matched.append(i)
        return matched

    def test_literal_admits_the_deft_family(self):
        enum = TriggerCreateRequest.model_json_schema()["properties"]["job_type"]["enum"]
        assert set(enum) == {"embedding_job", "llm_job", "aoi_job", "its_job"}
        # Derived, because the literal above is add-BLIND: a fifth ``JobType``
        # member would leave this Literal un-widened and /jobs would accept a
        # job type /triggers 422s. The two registries are hand-written and must
        # agree; this is the only thing making them.
        assert set(enum) == {job_type.value for job_type in JobType}

    def test_branch_enums_are_disjoint(self):
        """``oneOf`` means EXACTLY one.

        Adding "its_job" to both branches — the intuitive "widen everything"
        move — makes every its_job request that supplies a config_name match
        both branches and therefore validate as FALSE.
        """
        first, second = self._branches()
        a = set(first["properties"]["job_type"]["enum"])
        b = set(second["properties"]["job_type"]["enum"])
        assert a & b == set()
        # The config_name-optional branch is exactly the DEFT family — derived,
        # so a third DEFT job type cannot land in ``DEFT_JOB_TYPES`` (and thus
        # skip the workload_id requirement at /jobs) while still being forced to
        # supply a config_name at /triggers.
        assert a == {job_type.value for job_type in DEFT_JOB_TYPES}
        assert a == {"aoi_job", "its_job"}
        assert b == {"embedding_job", "llm_job"}
        assert a | b == {job_type.value for job_type in JobType}
        # The DEFT branch must NOT require config_name; the NMP branch must.
        assert "config_name" not in first.get("required", [])
        assert second["required"] == ["config_name"]

    @pytest.mark.parametrize(
        "payload",
        [
            {"job_type": "its_job"},
            {"job_type": "its_job", "config_name": None},
            {"job_type": "its_job", "config_name": "x"},
            {"job_type": "aoi_job"},
            {"job_type": "aoi_job", "config_name": "x"},
            {"job_type": "llm_job", "config_name": "x"},
            {"job_type": "embedding_job", "config_name": "x"},
        ],
    )
    def test_exactly_one_branch_matches_valid_payloads(self, payload):
        assert len(self._matching_branches(self._branches(), payload)) == 1

    @pytest.mark.parametrize(
        "payload",
        [
            {"job_type": "llm_job"},
            {"job_type": "embedding_job", "config_name": None},
        ],
    )
    def test_no_branch_matches_nmp_without_config_name(self, payload):
        assert self._matching_branches(self._branches(), payload) == []

    @pytest.mark.parametrize("config_name", [None, "cfg"])
    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job", "llm_job", "embedding_job"])
    def test_the_server_and_the_published_schema_agree(self, job_type, config_name):
        """A body the SERVER accepts must validate against the schema we PUBLISH.

        This is the whole point of pinning the oneOf. ``json_schema_extra`` is
        documentation-only — pydantic never enforces it — so a wrong branch enum
        leaves the server happily accepting requests that every generated client
        and contract test rejects, with nothing red on our side.

        Run against the real Draft 2020-12 validator so ``oneOf``'s exactly-one
        semantics are the spec's, not this file's reading of them.

        The ``config_name="cfg"`` half of the matrix is the load-bearing one:
        supplying a config_name is legal for EVERY job type, and listing
        "its_job" in BOTH branches (the intuitive widening) makes exactly that
        body match two branches — which ``oneOf`` scores as INVALID. Omitting it
        would leave that mistake invisible here, because a null config_name
        fails the NMP branch's string type and so matches only one branch either
        way.
        """
        jsonschema = pytest.importorskip("jsonschema")
        validator = jsonschema.Draft202012Validator(TriggerCreateRequest.model_json_schema())

        if config_name is None and job_type in ("llm_job", "embedding_job"):
            pytest.skip("NMP job types cannot be constructed without a config_name")
        kwargs = {"config_name": config_name} if config_name else {}
        accepted = TriggerCreateRequest(
            name="t",
            workload_id="w1",
            client_id="c1",
            job_type=job_type,
            trigger_type="schedule",
            eval_schedule="0 0 * * 0",
            **kwargs,
        ).model_dump(mode="json")

        assert validator.is_valid(accepted), [e.message for e in validator.iter_errors(accepted)]

    def test_the_published_schema_still_rejects_nmp_without_config_name(self):
        """The contract must stay CLOSED where the runtime is closed.

        The DEFT exemption is expressed by branch membership, so a botched
        widening (one enum holding all four types) would open config_name for
        llm_job/embedding_job too — invisible to the server, which keeps
        rejecting them, and therefore only ever caught here.
        """
        jsonschema = pytest.importorskip("jsonschema")
        validator = jsonschema.Draft202012Validator(TriggerCreateRequest.model_json_schema())

        body = {
            "name": "t",
            "workload_id": "w1",
            "client_id": "c1",
            "job_type": "llm_job",
            "trigger_type": "schedule",
            "eval_schedule": "0 0 * * 0",
        }
        assert not validator.is_valid(body)
        assert not validator.is_valid({**body, "job_type": "embedding_job", "config_name": None})


class TestTriggerCreateRequestDeftFamilyValidation:
    """``validate_trigger_type_requirements`` — the DEFT-family exemptions."""

    @staticmethod
    def _body(job_type: str, **overrides) -> dict:
        body = {
            "name": "t",
            "workload_id": "w1",
            "client_id": "c1",
            "job_type": job_type,
            "trigger_type": "data_threshold",
            "eval_schedule": "0 0 * * 0",
            "min_new_records": 100,
        }
        body.update(overrides)
        return body

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_config_name_is_optional(self, job_type):
        assert TriggerCreateRequest(**self._body(job_type)).config_name is None

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_data_threshold_does_not_require_ingestion(self, job_type):
        # The DEFT data plane is the Storage Backend catalog, so the
        # enable_data_ingestion requirement must NOT apply. (Negated polarity —
        # a mechanical "widen every == aoi_job" sweep gets this one backwards.)
        req = TriggerCreateRequest(**self._body(job_type, enable_data_ingestion=False))
        assert req.enable_data_ingestion is False

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_metric_degradation_is_rejected_and_names_the_job_type(self, job_type):
        with pytest.raises(ValidationError, match=f"not supported for {job_type}"):
            TriggerCreateRequest(
                **self._body(
                    job_type,
                    trigger_type="metric_degradation",
                    metric_thresholds={"recall": 0.8},
                )
            )

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    def test_ingestion_config_is_rejected(self, job_type):
        with pytest.raises(ValidationError, match="ingestion_config is not supported"):
            TriggerCreateRequest(
                **self._body(
                    job_type,
                    ingestion_config={"plugin_name": "bugnemo", "dataset_type": "bug_pairs"},
                )
            )

    @pytest.mark.parametrize("job_type", ["llm_job", "embedding_job"])
    def test_nmp_job_types_keep_their_requirements(self, job_type):
        # config_name still mandatory...
        with pytest.raises(ValidationError, match="config_name is required"):
            TriggerCreateRequest(**self._body(job_type))
        # ...and so is enable_data_ingestion for data_threshold.
        with pytest.raises(ValidationError, match="enable_data_ingestion must be True"):
            TriggerCreateRequest(
                **self._body(job_type, config_name="cfg", enable_data_ingestion=False)
            )

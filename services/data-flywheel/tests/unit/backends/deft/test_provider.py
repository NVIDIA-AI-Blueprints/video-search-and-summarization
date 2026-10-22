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
"""DEFT backend + DEFT pipeline-family wiring.

Verifies the seam is wired (provider capability, factory selection, wrapper
types, pipeline selection) — not the (stubbed) behavior. Stub methods are
expected to raise ``NotImplementedError``.

Since the AOI/ITS pipeline split, "pipeline selection" is per job type rather
than one class serving the family: see ``TestDeftPipelineSplit`` (each class
claims exactly one type, and both build the identical DAG) and
``TestDispatcherRoutesEachDeftJobTypeToItsOwnPipeline`` (the dispatcher's real
branch, patched at ``_DEFT_PIPELINES``).
"""

import logging
from unittest.mock import MagicMock, patch

import pytest
from bson import ObjectId

from src.backends.base.interfaces import (
    DatastoreBackend,
    DeploymentClient,
    EmbeddingBackend,
    EvalBackend,
    TrainingBackend,
)
from src.backends.deft.eval import DeftAOIEvaluator, get_evaluator
from src.backends.deft.provider import DeftBackendProvider
from src.backends.deft.wrappers import DeftTraining
from src.backends.factory import _build
from src.config import build_job_config
from src.config.deft import KpiConstraint
from src.job_types import JobType
from src.lib.eval_harness import BaseEvaluator, OfflineEval
from src.tasks.pipelines import AOIPipeline, DeftPipeline, ITSPipeline


class TestDeftProviderCapability:
    def test_kind_and_supported_job_types(self):
        p = DeftBackendProvider()
        assert p.kind == "deft"
        # The whole DEFT family, not just AOI: its_job runs the same pipeline
        # off the same config schema, so the backend must declare it or every
        # its_job POST 400s at the shared assert_supported_type gate (/jobs,
        # /configs, /triggers) and at the dispatcher's re-check.
        assert p.supported_job_types == frozenset({JobType.AOI, JobType.ITS})
        assert p.supports_job_type("aoi_job")
        assert p.supports_job_type("its_job")
        assert not p.supports_job_type("llm_job")
        assert not p.supports_job_type("embedding_job")

    def test_factory_builds_deft_provider(self):
        assert isinstance(_build("deft"), DeftBackendProvider)

    def test_factory_methods_return_neutral_backends(self):
        p = DeftBackendProvider()
        assert isinstance(p.training(), TrainingBackend)
        assert isinstance(p.datastore("aoi"), DatastoreBackend)
        assert isinstance(p.deployment_client(nim=None), DeploymentClient)
        assert isinstance(p.embedding(), EmbeddingBackend)

    def test_provider_resolves_config_from_settings(self):
        # A deployment's config.deft.yaml deft_config block must reach the
        # clients — the provider resolves it from settings when not injected.
        from src.config import DEFTConfig

        deft_cfg = DEFTConfig(deft_base_url="http://deployed-deft:9999")
        with patch("src.config.settings") as settings:
            settings.system_config.deft_config = deft_cfg
            p = DeftBackendProvider()
        assert p._config.deft_base_url == "http://deployed-deft:9999"

    def test_provider_falls_back_to_defaults_without_settings_block(self):
        with patch("src.config.settings") as settings:
            settings.system_config = object()  # no deft_config attr (nmp build)
            p = DeftBackendProvider()
        assert p._config.deft_base_url == "http://deft:8000"


class TestEvaluatorResultsSourceWiring:
    def test_evaluator_gets_http_source_when_configured(self):
        from src.config import DEFTConfig

        p = DeftBackendProvider(DEFTConfig(aoi_results_url="http://aoi:9000/v1/results"))
        ev = p.evaluator(use_case="aoi")
        assert ev._results_source is not None

    def test_evaluator_defaults_to_the_eval_set_flow(self):
        # No aoi_results_url -> the Storage Backend eval-set flow (design §4.3)
        # is the default sample source; the evaluator is never source-less.
        from src.config import DEFTConfig

        ev = DeftBackendProvider(DEFTConfig()).evaluator(use_case="aoi")
        assert ev._results_source is not None

    def test_http_results_source_parses_results_envelope(self):
        from src.backends.deft.provider import _http_results_source

        resp = MagicMock()
        resp.json.return_value = {"results": [{"target_id": "R0"}]}
        resp.raise_for_status.return_value = None
        with patch("requests.get", return_value=resp) as get:
            samples = _http_results_source("http://aoi/v1/results")(None, "shadow")
        assert samples == [{"target_id": "R0"}]
        assert get.call_args.kwargs["params"]["role"] == "shadow"

    def test_http_results_source_forwards_window_dict_as_params(self):
        # The beat passes {model, version} (Storage Backend GET /v1/results
        # contract) so results are filtered to THIS candidate.
        from src.backends.deft.provider import _http_results_source

        resp = MagicMock()
        resp.json.return_value = []
        resp.raise_for_status.return_value = None
        with patch("requests.get", return_value=resp) as get:
            _http_results_source("http://aoi/v1/results")(
                {"model": "changenet-aoi", "version": "run-7"}, "canary"
            )
        params = get.call_args.kwargs["params"]
        assert params == {"role": "canary", "model": "changenet-aoi", "version": "run-7"}

    def test_http_results_source_fails_safe_to_empty(self):
        # A dead AOI results endpoint must yield [] (verdict holds), not raise.
        from src.backends.deft.provider import _http_results_source

        with patch("requests.get", side_effect=ConnectionError("down")):
            assert _http_results_source("http://aoi/v1/results")(None, "canary") == []


class TestEvalSetResultsSource:
    """Storage Backend contract v2: id-only create → poll detail → manifests."""

    def _source(self, client):
        from src.backends.deft.provider import _eval_set_results_source

        return _eval_set_results_source(client)

    @staticmethod
    def _manifest_resp(records):
        import hashlib
        import json as _json

        payload = ("\n".join(_json.dumps(r) for r in records) + "\n").encode()
        resp = MagicMock()
        resp.content = payload
        resp.raise_for_status.return_value = None
        return resp, hashlib.sha256(payload).hexdigest()

    def _detail(self, status, uri="http://storage/v1/eval/eval-1/manifest0.jsonl", sha=None):
        manifest = {"format": "jsonl", "uri": uri, "size_bytes": 1}
        if sha is not None:
            manifest["sha256"] = sha
        return {"eval_id": "eval-1", "status": status, "manifests": [manifest]}

    def test_create_poll_then_retrieve_via_manifest(self):
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}  # ID-only create
        resp, sha = self._manifest_resp([{"target_id": "R0"}, {"target_id": "R1"}])
        client.get_eval_stats.side_effect = [
            self._detail("creating"),
            self._detail("ready", sha=sha),
        ]
        with (
            patch("requests.get", return_value=resp) as get,
            patch("time.sleep"),
        ):
            samples = self._source(client)({"model": "m", "version": "v2"}, "shadow")
        assert samples == [{"target_id": "R0"}, {"target_id": "R1"}]
        # The provider hands the client the BARE selection; the CLIENT owns
        # the CreateEvaluationSetRequest envelope (asserted in test_clients).
        sel = client.create_eval_set.call_args.args[0]
        assert sel == {"role": "shadow", "model": "m", "version": "v2"}
        assert get.call_args.args[0] == "http://storage/v1/eval/eval-1/manifest0.jsonl"

    def test_sha256_mismatch_discards_samples(self):
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        resp, _ = self._manifest_resp([{"target_id": "R0"}])
        client.get_eval_stats.return_value = self._detail("ready", sha="f" * 64)
        with patch("requests.get", return_value=resp):
            assert (
                self._source(client)({"model": "m", "version": "v"}, "shadow") == []
            )  # corrupt -> hold

    def test_never_ready_yields_no_samples(self):
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        client.get_eval_stats.return_value = self._detail("creating")
        with patch("time.sleep"):
            assert self._source(client)({"model": "m", "version": "v"}, "shadow") == []

    def test_failed_status_exits_early_with_no_samples(self):
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        client.get_eval_stats.return_value = self._detail("failed")
        with patch("time.sleep"):
            assert self._source(client)({"model": "m", "version": "v"}, "shadow") == []
        client.get_eval_stats.assert_called_once()  # terminal on first poll, no budget burn

    def test_status_casing_is_normalized(self):
        # "Ready" must not burn the whole poll budget and spuriously hold.
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        resp, sha = self._manifest_resp([{"target_id": "R0"}])
        client.get_eval_stats.return_value = self._detail("Ready", sha=sha)
        with patch("requests.get", return_value=resp):
            assert self._source(client)({"model": "m", "version": "v"}, "shadow") == [
                {"target_id": "R0"}
            ]

    def test_unexpected_window_keys_are_not_forwarded(self):
        # additionalProperties: false — an extra window key must never reach
        # the request body (it would 422 the whole create).
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        client.get_eval_stats.return_value = {
            "eval_id": "eval-1",
            "status": "ready",
            "manifests": [],
        }
        self._source(client)(
            {"model": "m", "version": "v", "surprise": "nope", "role": "hijack"}, "shadow"
        )
        sel = client.create_eval_set.call_args.args[0]
        assert sel == {"role": "shadow", "model": "m", "version": "v"}

    def test_window_without_model_version_holds_without_calling_backend(self):
        # The contract REQUIRES selection.model/version — a doomed request is
        # never sent; the evaluator fail-safes to hold.
        client = MagicMock()
        assert self._source(client)(None, "canary") == []
        client.create_eval_set.assert_not_called()

    def test_s3_manifest_is_presigned_then_fetched(self):
        # The Storage Backend returns s3:// manifest URIs (contract default) and
        # allow-lists the evals/ prefix so they can be presigned. DFW must resolve
        # the locator and read it — WITHOUT carrying object-store credentials.
        s3_uri = "s3://aoi-samples/evals/eval-1/manifest0.jsonl"
        presigned = (
            "http://storage:8333/aoi-samples/evals/eval-1/manifest0.jsonl?Signature=abc&Expires=1"
        )
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        resp, sha = self._manifest_resp([{"target_id": "R0"}, {"target_id": "R1"}])
        client.get_eval_stats.return_value = self._detail("ready", uri=s3_uri, sha=sha)
        client.get_presigned_urls.return_value = {
            "items": [
                {
                    "input": s3_uri,
                    "key": "evals/eval-1/manifest0.jsonl",
                    "presigned_url": presigned,
                }
            ],
            "failed": [],
        }
        with patch("requests.get", return_value=resp) as get:
            samples = self._source(client)({"model": "m", "version": "v"}, "canary")
        assert samples == [{"target_id": "R0"}, {"target_id": "R1"}]
        # Presigned via the backend (no boto3, no credentials), then fetched.
        assert client.get_presigned_urls.call_args.args[0] == [s3_uri]
        assert get.call_args.args[0] == presigned

    def test_s3_manifest_sha256_is_still_verified(self):
        # The integrity check must apply to the PRESIGNED fetch too.
        s3_uri = "s3://aoi-samples/evals/eval-1/manifest0.jsonl"
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        resp, _ = self._manifest_resp([{"target_id": "R0"}])
        client.get_eval_stats.return_value = self._detail("ready", uri=s3_uri, sha="f" * 64)
        client.get_presigned_urls.return_value = {
            "items": [{"presigned_url": "http://storage:8333/x?Signature=abc"}],
            "failed": [],
        }
        with patch("requests.get", return_value=resp):
            assert self._source(client)({"model": "m", "version": "v"}, "canary") == []

    def test_s3_manifest_holds_when_presign_errors(self):
        # Degrades to the previous behaviour (hold); never raises into the beat.
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        client.get_eval_stats.return_value = self._detail(
            "ready", uri="s3://aoi-samples/evals/eval-1/manifest0.jsonl"
        )
        client.get_presigned_urls.side_effect = ConnectionError("storage down")
        with patch("requests.get") as get:
            assert self._source(client)({"model": "m", "version": "v"}, "canary") == []
        get.assert_not_called()

    def test_s3_manifest_holds_when_presign_returns_no_items(self):
        # e.g. the key falls outside the backend's presign allow-list.
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        client.get_eval_stats.return_value = self._detail(
            "ready", uri="s3://other-bucket/nope/manifest0.jsonl"
        )
        client.get_presigned_urls.return_value = {
            "items": [],
            "failed": [{"input": "s3://other-bucket/nope/manifest0.jsonl", "error": "forbidden"}],
        }
        with patch("requests.get") as get:
            assert self._source(client)({"model": "m", "version": "v"}, "canary") == []
        get.assert_not_called()

    def test_presigned_url_query_is_never_logged(self, caplog):
        # Presigned URLs carry signature material in their query string; only the
        # stable s3:// locator may appear in logs.
        s3_uri = "s3://aoi-samples/evals/eval-1/manifest0.jsonl"
        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "eval-1"}
        client.get_eval_stats.return_value = self._detail("ready", uri=s3_uri, sha="f" * 64)
        client.get_presigned_urls.return_value = {
            "items": [{"presigned_url": "http://storage:8333/x?Signature=SECRETSIG&Expires=1"}],
            "failed": [],
        }
        resp, _ = self._manifest_resp([{"target_id": "R0"}])
        with patch("requests.get", return_value=resp), caplog.at_level(logging.WARNING):
            self._source(client)({"model": "m", "version": "v"}, "canary")
        assert "SECRETSIG" not in caplog.text
        assert s3_uri in caplog.text  # the stable locator IS logged

    def test_fails_safe_to_empty_on_backend_error(self):
        client = MagicMock()
        client.create_eval_set.side_effect = ConnectionError("down")
        assert self._source(client)(None, "shadow") == []


class TestDeftTrainingStartRun:
    """start_run builds a contract-valid FineTuneRequest and calls the client."""

    def test_start_run_builds_request_and_returns_handle(self):
        # Inject the snapshot + model + baseline-checkpoint refs; assert the DEFT
        # client receives a well-formed FineTuneRequest (ExperimentConfig requires
        # model+data_pool; ModelConfig requires name+checkpoint per the contract).
        client = MagicMock()
        client.create_fine_tune.return_value = {"id": "run-123", "state": "pending"}
        training = DeftTraining(client)

        handle = training.start_run(
            snapshot_uri="s3://aoi-artifacts/snapshots/snap_1/",
            model_name="changenet-aoi",
            deft_params={"config": {"training": {"epochs": 3}}},
            baseline_checkpoint="s3://aoi-models/base/model.pt",
        )

        assert handle == {"id": "run-123", "state": "pending"}
        client.create_fine_tune.assert_called_once()
        (request,), _ = client.create_fine_tune.call_args
        assert request["workflow"] == "aoi_active_learning"
        assert request["config"]["data_pool"]["path"] == "s3://aoi-artifacts/snapshots/snap_1/"
        assert request["config"]["model"]["name"] == "changenet-aoi"
        assert request["config"]["model"]["checkpoint"] == "s3://aoi-models/base/model.pt"
        assert request["config"]["training"]["epochs"] == 3

    def test_resolved_baseline_overrides_explicit_config_checkpoint(self):
        # Iterative fine-tuning: a passed baseline_checkpoint is the RESOLVED
        # baseline (the promoted @production model) and supersedes the
        # config's day-0 seed — the handler owns the policy of when to pass it.
        client = MagicMock()
        client.create_fine_tune.return_value = {"id": "run-1", "state": "pending"}
        DeftTraining(client).start_run(
            snapshot_uri="s3://snap/",
            model_name="m",
            deft_params={"config": {"model": {"checkpoint": "s3://explicit/model.pt"}}},
            baseline_checkpoint="s3://resolved/model.pt",
        )
        (request,), _ = client.create_fine_tune.call_args
        assert request["config"]["model"]["checkpoint"] == "s3://resolved/model.pt"

    def test_explicit_config_checkpoint_used_when_no_baseline_resolved(self):
        # First rollout / registry unreachable: baseline_checkpoint=None defers
        # to the config's day-0 seed.
        client = MagicMock()
        client.create_fine_tune.return_value = {"id": "run-1", "state": "pending"}
        DeftTraining(client).start_run(
            snapshot_uri="s3://snap/",
            model_name="m",
            deft_params={"config": {"model": {"checkpoint": "s3://explicit/model.pt"}}},
            baseline_checkpoint=None,
        )
        (request,), _ = client.create_fine_tune.call_args
        assert request["config"]["model"]["checkpoint"] == "s3://explicit/model.pt"

    def test_kpi_set_uri_injected_into_config_kpi_path(self):
        client = MagicMock()
        client.create_fine_tune.return_value = {"id": "run-1", "state": "pending"}
        DeftTraining(client).start_run(
            snapshot_uri="s3://snap/",
            model_name="m",
            deft_params={"config": {"model": {"checkpoint": "s3://c/m.pt"}}},
            kpi_set_uri="kpi/testing_set.csv",
        )
        request = client.create_fine_tune.call_args.args[0]
        assert request["config"]["kpi"]["path"] == "kpi/testing_set.csv"

    def test_explicit_kpi_path_wins_over_injected(self):
        client = MagicMock()
        client.create_fine_tune.return_value = {"id": "run-1", "state": "pending"}
        DeftTraining(client).start_run(
            snapshot_uri="s3://snap/",
            model_name="m",
            deft_params={
                "config": {
                    "model": {"checkpoint": "s3://c/m.pt"},
                    "kpi": {"path": "kpi/custom.csv"},
                }
            },
            kpi_set_uri="kpi/testing_set.csv",
        )
        request = client.create_fine_tune.call_args.args[0]
        assert request["config"]["kpi"]["path"] == "kpi/custom.csv"

    def test_missing_checkpoint_fails_loud_not_422(self):
        # Without a checkpoint the real service would return an opaque 422 —
        # the builder must fail loud with guidance instead.
        with pytest.raises(ValueError, match=r"model\.checkpoint"):
            DeftTraining(MagicMock()).start_run(
                snapshot_uri="s3://snap/", model_name="m", deft_params={}
            )

    def test_profile_mode_injects_via_overrides(self):
        # Partials ride on `overrides` in profile mode — a partial `config`
        # would fail ExperimentConfig validation on the real service.
        client = MagicMock()
        client.create_fine_tune.return_value = {"id": "run-1", "state": "pending"}
        DeftTraining(client).start_run(
            snapshot_uri="s3://snap/",
            model_name="m",
            deft_params={"profile": "aoi-default"},
            baseline_checkpoint="s3://base/model.pt",
        )
        (request,), _ = client.create_fine_tune.call_args
        assert request["profile"] == "aoi-default"
        assert "workflow" not in request
        assert "config" not in request
        assert request["overrides"]["data_pool"]["path"] == "s3://snap/"
        assert request["overrides"]["model"]["name"] == "m"
        assert request["overrides"]["model"]["checkpoint"] == "s3://base/model.pt"


class TestDeftStubsRaise:
    """Not-applicable-for-AOI methods must fail loudly, not silently no-op."""

    def test_embedding_not_supported(self):
        with pytest.raises(NotImplementedError):
            DeftBackendProvider().embedding().embed()

    def test_datastore_put_not_applicable(self):
        # AOI never uploads datasets; it requests snapshots via register().
        with pytest.raises(NotImplementedError):
            DeftBackendProvider().datastore("changenet-aoi").put()

    def test_deployment_client_deploy_not_applicable(self):
        # AOI deploys via the Promotion Controller / DeploymentBackend.
        with pytest.raises(NotImplementedError):
            DeftBackendProvider().deployment_client(nim=None).deploy()


class TestDeftEvaluator:
    """The DEFT backend owns its eval logic; the harness is handed the evaluator."""

    def test_evaluator_selected_by_use_case(self):
        # provider.evaluator(use_case) returns the concrete backend evaluator, which
        # satisfies BOTH the neutral EvalBackend seam and the harness BaseEvaluator.
        ev = DeftBackendProvider().evaluator(use_case="aoi")
        assert isinstance(ev, DeftAOIEvaluator)
        assert isinstance(ev, EvalBackend)
        assert isinstance(ev, BaseEvaluator)

    def test_evaluator_satisfies_evalbackend_seam(self):
        # In-process eval → service verbs are trivial shims (no remote job).
        ev = DeftBackendProvider().evaluator(use_case="aoi")
        assert ev.get_status("job-1") == {"status": "completed"}
        assert ev.cancel("job-1") is None

    def test_evaluator_none_for_cleanup_path(self):
        # No use_case (cleanup/cancel): DEFT has no service-style eval to tear down.
        assert DeftBackendProvider().evaluator() is None

    def test_unknown_use_case_raises(self):
        with pytest.raises(ValueError, match="use_case"):
            DeftBackendProvider().evaluator(use_case="does-not-exist")

    def test_harness_uses_backend_evaluator(self):
        # Generic harness orchestration runs the backend-owned evaluator.
        ev = DeftBackendProvider().evaluator(use_case="aoi")
        result = OfflineEval(ev).run(baseline=None, candidate=None, kpi_set=None)
        assert set(result) >= {"metrics", "passed"}


def _samples(n: int, *, defect_frac: float = 0.5, candidate_correct: bool = True) -> list[dict]:
    """Build ``n`` collected AOI samples with operator-confirmed labels.

    Half (by ``defect_frac``) are true defects. When ``candidate_correct`` the
    candidate's ``ai_classification`` matches ``confirmed_label`` (and baseline)
    everywhere; otherwise defects are misclassified ``Good`` (false accepts).
    """
    out: list[dict] = []
    n_defects = round(n * defect_frac)
    for i in range(n):
        actual_defect = i < n_defects
        confirmed = "NotGood" if actual_defect else "Good"
        # When incorrect, the candidate calls everything "Good" so true defects
        # become false accepts (missed defects).
        ai = confirmed if candidate_correct else "Good"
        out.append(
            {
                "target_id": f"R{i}",
                "ai_classification": ai,
                "ai_confidence": 0.95,
                "confirmed_label": confirmed,
                "baseline_classification": confirmed,
            }
        )
    return out


class TestGetEvaluator:
    def test_returns_aoi_evaluator(self):
        assert isinstance(get_evaluator("aoi"), DeftAOIEvaluator)

    def test_unknown_use_case_raises(self):
        with pytest.raises(ValueError, match="use_case"):
            get_evaluator("does-not-exist")

    def test_no_arg_construction(self):
        # Provider seam constructs with no args — must not raise.
        ev = DeftAOIEvaluator()
        assert ev.min_sample_size > 0
        # No criteria by default: the online gate is fail-closed, so a
        # no-arg evaluator holds until a policy is supplied.
        assert ev.online_target == []
        assert ev.evaluate(None, None, [])["verdict"] == "hold"


class TestDeftAOIEvaluatorOnline:
    """Online eval gates on FAR (false ALARM rate) and nothing else.

    These tests were rewritten when the online gate moved from a four-metric
    verdict to FAR-only. The previous suite encoded FAR as the *miss rate* on
    true defects ("over the labeled subset FAR == 1 - recall"), which is the
    opposite of DEFT's KPI definition — see ``test_far_is_the_false_alarm_rate``.
    """

    @staticmethod
    def _ev(*, threshold=5.0, min_sample_size=10):
        ev = DeftAOIEvaluator(min_sample_size=min_sample_size)
        ev.online_target = [KpiConstraint(metric="FAR_pct", op="<", threshold=threshold)]
        return ev

    def test_insufficient_data_holds(self):
        ev = self._ev(min_sample_size=100)
        out = ev.evaluate(None, None, _samples(10))
        assert out["verdict"] == "hold"
        assert out["metrics"]["n"] == 10

    def test_far_is_the_false_alarm_rate(self):
        """Of the GOOD units, the share the candidate flagged — not the miss rate."""
        ev = self._ev()
        # 100 good units, 5 wrongly flagged; every defect missed.
        samples = _samples(200, defect_frac=0.5, candidate_correct=False)
        for s in samples[:5]:
            if s["confirmed_label"] == "Good":
                s["ai_classification"] = "NotGood"
        out = ev.evaluate(None, None, samples)
        far = out["metrics"]["FAR_pct"]
        assert far == pytest.approx(
            100.0
            * sum(
                1
                for s in samples
                if s["confirmed_label"] == "Good" and s["ai_classification"] == "NotGood"
            )
            / 100
        )
        # Missing every defect tanks recall but does NOT raise FAR.
        assert out["metrics"]["recall_pct"] == pytest.approx(0.0)

    def test_clean_candidate_advances(self):
        ev = self._ev()
        out = ev.evaluate(None, None, _samples(50, candidate_correct=True))
        assert out["verdict"] == "advance"
        assert out["metrics"]["FAR_pct"] == pytest.approx(0.0)

    def test_false_alarm_regression_rolls_back(self):
        ev = self._ev(threshold=5.0)
        samples = _samples(200, defect_frac=0.5, candidate_correct=True)
        # Flag 20 of the 100 good units -> FAR 20% > 5%.
        for s in [x for x in samples if x["confirmed_label"] == "Good"][:20]:
            s["ai_classification"] = "NotGood"
        out = ev.evaluate(None, None, samples)
        assert out["metrics"]["FAR_pct"] == pytest.approx(20.0)
        assert out["verdict"] == "rollback"

    def test_missing_defects_alone_does_not_roll_back(self):
        """Recall is reported, not gated — DEFT validates FAR only."""
        ev = self._ev()
        out = ev.evaluate(None, None, _samples(50, candidate_correct=False))
        assert out["metrics"]["recall_pct"] == pytest.approx(0.0)
        assert out["metrics"]["FAR_pct"] == pytest.approx(0.0)
        assert out["verdict"] == "advance"

    def test_no_good_units_holds(self):
        """FAR is undefined without a PASS population — hold, never advance."""
        ev = self._ev()
        out = ev.evaluate(None, None, _samples(50, defect_frac=1.0, candidate_correct=True))
        assert "FAR_pct" not in out["metrics"]
        assert out["verdict"] == "hold"

    def test_too_few_labeled_samples_holds(self):
        ev = self._ev(min_sample_size=100)
        samples = _samples(120, candidate_correct=True)
        for s in samples[5:]:
            s["confirmed_label"] = None
        out = ev.evaluate(None, None, samples)
        assert out["verdict"] == "hold"

    def test_missing_criteria_holds(self):
        ev = DeftAOIEvaluator(min_sample_size=10)
        ev.online_target = []
        ev.thresholds = {}
        assert ev.evaluate(None, None, _samples(50))["verdict"] == "hold"

    def test_canary_single_model_samples_are_not_disagreements(self):
        # In CANARY each request routes to exactly ONE model (serving/deployment.py:
        # Normal => stable only; Canary => exactly one; Shadow => both). A
        # stable-routed sample therefore carries a baseline call and NO candidate
        # call. That is not a disagreement — agreement is UNDEFINED over it.
        # Counting it (the old behaviour) drove agreement to 0 on a healthy model.
        ev = DeftAOIEvaluator(min_sample_size=10)
        samples = _samples(50, candidate_correct=True)
        for s in samples:
            s["ai_classification"] = None  # candidate never ran for this target
        out = ev.evaluate(None, None, samples)
        assert out["metrics"]["paired_n"] == 0
        assert "agreement" not in out["metrics"]
        # A target the candidate never scored is not a candidate "Good" call, so
        # it must not become a false accept either. Before the fix this window
        # scored FAR=1.0 and ROLLED BACK a healthy candidate; now there is simply
        # nothing to judge, so it holds.
        assert out["metrics"]["labeled_n"] == 0
        assert "false_accept_rate" not in out["metrics"]
        assert out["verdict"] == "hold"

    def test_agreement_ignores_single_model_canary_samples(self):
        # Mixed window: 30 samples scored by both models, 10 routed to stable
        # only. Agreement must be computed over the 30 comparable samples, not
        # diluted to 0.75 by the 10 that the candidate never saw.
        ev = self._ev(min_sample_size=10)
        samples = _samples(40, candidate_correct=True)
        # Null the candidate call on 10 PASS samples. Those targets leave BOTH
        # the agreement pairing and the FAR denominator — a target the candidate
        # never scored says nothing about either.
        for s in samples[-10:]:
            assert s["confirmed_label"] == "Good"
            s["ai_classification"] = None
        out = ev.evaluate(None, None, samples)
        assert out["metrics"]["paired_n"] == 30
        assert out["metrics"]["agreement"] == 1.0
        # The 10 unscored PASS units are excluded from FAR rather than counted
        # as silently-passed goods, so FAR stays 0 over the 10 it did score.
        assert out["metrics"]["n_pass"] == 10
        assert out["metrics"]["FAR_pct"] == 0.0
        assert out["verdict"] == "advance"

    def test_collect_uses_injected_source(self):
        samples = _samples(5)
        ev = DeftAOIEvaluator(results_source=lambda window, role: samples)
        assert ev.collect("w", "shadow") == samples

    def test_collect_default_empty(self):
        assert DeftAOIEvaluator().collect("w", "shadow") == []


class TestDeftAOIEvaluatorKpi:
    def test_kpi_eval_extracts_candidate_metrics(self):
        ev = DeftAOIEvaluator()
        candidate = {"metrics": {"mAP50": 0.95, "far": 0.0005, "recall": 1.0}}
        out = ev.kpi_eval(None, candidate, None)
        assert out["metrics"] == {"mAP50": 0.95, "far": 0.0005, "recall": 1.0}
        assert out["passed"] is True  # no constraints configured -> nothing unmet

    def test_kpi_eval_ignores_baseline(self):
        # The DEFT contract returns candidate-only metrics (no baseline in
        # ResultsResponse; DEFT's own workflow runs the baseline_eval step), so
        # the gate decision must not depend on the baseline argument at all.
        ev = DeftAOIEvaluator()
        baseline = {"metrics": {"mAP50": 0.99, "far": 0.0001, "recall": 1.0}}
        candidate = {"metrics": {"mAP50": 0.80, "far": 0.05, "recall": 0.90}}
        out = ev.kpi_eval(baseline, candidate, None)
        assert out["passed"] is True  # no constraints -> passes regardless of baseline

    def test_kpi_eval_handles_bare_metrics_dict_and_none(self):
        ev = DeftAOIEvaluator()
        out = ev.kpi_eval(None, {"mAP50": 0.9}, None)
        assert out["metrics"] == {"mAP50": 0.9}
        out_none = ev.kpi_eval(None, None, None)
        assert out_none["metrics"] == {}

    # --- structured KPI constraints (the backend-owned gate decision) ---------
    def test_structured_constraints_met_passes(self):
        from src.config import EvalHarnessConfig

        ev = DeftAOIEvaluator()
        cfg = EvalHarnessConfig(
            kpi_target=[
                {"metric": "far", "op": "<", "threshold": 0.001},
                {"metric": "recall", "op": ">=", "threshold": 1.0},
            ]
        )
        out = ev.kpi_eval(None, {"metrics": {"far": 0.0005, "recall": 1.0}}, cfg)
        assert out["passed"] is True
        assert "unmet" not in out

    def test_structured_constraint_unmet_fails(self):
        from src.config import EvalHarnessConfig

        ev = DeftAOIEvaluator()
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "far", "op": "<", "threshold": 0.001}])
        out = ev.kpi_eval(None, {"metrics": {"far": 0.02}}, cfg)
        assert out["passed"] is False
        assert out["unmet"]  # reports the offending constraint

    def test_structured_constraint_metric_name_case_insensitive(self):
        # 'FAR' in the constraint matches the evaluator's 'far' metric key.
        from src.config import EvalHarnessConfig

        ev = DeftAOIEvaluator()
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "FAR", "op": "<", "threshold": 0.001}])
        out = ev.kpi_eval(None, {"metrics": {"far": 0.0005}}, cfg)
        assert out["passed"] is True

    def test_structured_constraint_missing_metric_fails_closed(self):
        # A required KPI metric that was never produced must fail the gate.
        from src.config import EvalHarnessConfig

        ev = DeftAOIEvaluator()
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "mAP50", "op": ">=", "threshold": 0.9}])
        out = ev.kpi_eval(None, {"metrics": {"far": 0.0005}}, cfg)
        assert out["passed"] is False

    def test_offline_gate_uses_deft_metric_names_verbatim(self):
        # Regression lock: config.deft.yaml eval.kpi_target uses `FAR_pct` — the
        # SAME name/unit DEFT returns (ResultsResponse -> kpi.py; FAR_pct is a
        # percentage at recall=100%). A candidate carrying only DEFT's real keys
        # must pass on merit and fail when FAR_pct exceeds the target. (A config
        # written against `far`/`recall` — absent from DEFT — would fail-closed.)
        from src.config import EvalHarnessConfig

        ev = DeftAOIEvaluator()
        cfg = EvalHarnessConfig(kpi_target=[{"metric": "FAR_pct", "op": "<", "threshold": 0.1}])
        good = {"FAR_pct": 0.0, "FAR95_pct": 0.0, "AUC": 1.0, "best_f1": 1.0, "n_defects": 23.0}
        assert ev.kpi_eval(None, {"metrics": good}, cfg)["passed"] is True
        bad = {"FAR_pct": 0.5, "AUC": 0.7}  # 0.5% > 0.1%
        assert ev.kpi_eval(None, {"metrics": bad}, cfg)["passed"] is False

    def test_structured_constraints_via_dict_kpi_set(self):
        # kpi_set may also be a plain dict (defensive path).
        ev = DeftAOIEvaluator()
        out = ev.kpi_eval(
            None,
            {"metrics": {"recall": 1.0}},
            {"kpi_target": [{"metric": "recall", "op": ">=", "threshold": 1.0}]},
        )
        assert out["passed"] is True

    def test_unknown_dict_op_fails_closed_without_raising(self):
        # Dict-form constraints are unvalidated: a bogus operator must count as
        # unmet (fail-closed), never crash the gate path with a KeyError.
        ev = DeftAOIEvaluator()
        out = ev.kpi_eval(
            None,
            {"metrics": {"recall": 1.0}},
            {"kpi_target": [{"metric": "recall", "op": "~=", "threshold": 1.0}]},
        )
        assert out["passed"] is False
        assert out["unmet"]


#: The 8 DEFT stages, in the order the chain must execute them, spelled as the
#: CELERY WIRE NAMES rather than the Python symbols. A wire name is what a
#: queued message carries and what an operator sees in Flower, and it is exactly
#: what a module-level rename leaves stale — asserting the symbols would let a
#: wire-name change survive underneath a renamed module and go green.
_DEFT_CHAIN = [
    "tasks.aoi.initialize_aoi_workflow",
    "tasks.aoi.select_and_snapshot",
    "tasks.aoi.run_deft_training",
    "tasks.aoi.run_offline_eval_gate",
    "tasks.aoi.register_candidate",
    "tasks.aoi.start_promotion",
    "tasks.aoi.wait_for_promotion",
    "tasks.aoi.finalize_aoi_run",
]


class TestTheDeftStagesAreRegisteredForAWorker:
    """The ``aoi_tasks.py → deft_tasks.py`` rename's one SILENT failure mode.

    A worker registers tasks by importing the modules named in
    ``celery_app.conf.imports`` — a tuple of STRING literals. Renaming
    ``aoi_tasks.py`` to ``deft_tasks.py`` without updating that string breaks
    nothing at import time and nothing in a test that imports the module
    directly; it surfaces only in production, as ``NotRegistered`` the first
    time a chain is dispatched, after the FlywheelRun row already exists.

    ``build_workflow`` cannot catch it either: the chain's ``.s()`` signatures
    take their names from the task objects the pipeline imported, so the DAG
    test agrees with itself no matter what the worker would actually load.
    """

    def test_the_worker_import_list_names_the_module_defining_the_stages(self):
        """The string literal, checked against the module that must be loaded."""
        from src.celery_app import celery_app
        from src.tasks import deft_tasks

        assert deft_tasks.__name__ in celery_app.conf.imports

    def test_every_stage_registers_under_its_wire_name(self):
        """Registration driven the way a worker drives it, not by direct import.

        ``import_default_modules()`` is what the worker calls at boot: it walks
        ``conf.imports`` and imports each entry. Going through it means a stale
        entry leaves the registry short, whereas asserting after a plain
        ``import src.tasks.deft_tasks`` would pass on the strength of the test's
        own import.
        """
        from src.celery_app import celery_app

        celery_app.loader.import_default_modules()

        missing = [name for name in _DEFT_CHAIN if name not in celery_app.tasks]
        assert not missing, (
            f"DEFT stages not registered: {missing}. A worker would answer "
            f"NotRegistered for these at dispatch."
        )

    def test_every_stage_still_answers_to_its_existing_tasks_aoi_name(self):
        """The wire names are UNCHANGED by this MR: renaming them is not backward
        compatible, so ``tasks.aoi.* → tasks.deft.*`` is deferred to its own MR
        that can register aliases and roll workers before the API.
        """
        from src.celery_app import celery_app

        celery_app.loader.import_default_modules()

        assert [name for name in celery_app.tasks if name.startswith("tasks.deft.")] == []
        assert set(_DEFT_CHAIN) <= {
            name for name in celery_app.tasks if name.startswith("tasks.aoi.")
        }

    def test_the_python_symbols_match_their_wire_names(self):
        """A wire name and its Python symbol must not drift apart.

        The stage is dispatched by wire name but read, imported and grepped by
        symbol. ``tasks.aoi.initialize_aoi_workflow`` bound to a function called
        ``initialize_deft_workflow`` would satisfy every other test here and
        still send the next reader to the wrong place.
        """
        from src.tasks import deft_tasks

        for wire_name in _DEFT_CHAIN:
            symbol = wire_name.removeprefix("tasks.aoi.")
            assert hasattr(
                deft_tasks, symbol
            ), f"{wire_name} has no matching symbol deft_tasks.{symbol}"
            assert symbol in deft_tasks.__all__


class TestEverySourceModuleIsTrackedByGit:
    """The split's one failure mode that NO import-level test can see.

    ``deft_pipeline.py`` and ``its_pipeline.py`` arrived as new files, and
    ``src/tasks/pipelines/__init__.py`` imports both. While they were untracked,
    every test in this file passed — tests read the working tree — but a commit
    that stages only tracked changes (``git commit -a``, ``git add -u``) ships a
    tree where ``import src.tasks.pipelines`` raises ``ModuleNotFoundError``.
    ``src/tasks/tasks.py`` imports that package and ``src/celery_app.py`` loads
    ``src.tasks.tasks`` at worker boot, so the blast radius is the API and BOTH
    celery workers, for llm_job and embedding_job as much as for aoi_job/its_job.
    """

    def test_no_python_file_under_src_is_untracked(self):
        import shutil
        import subprocess
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[4]
        if shutil.which("git") is None or not (repo_root / ".git").exists():
            pytest.skip("not a git checkout (sdist / container build context)")

        proc = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-z", "--", "src"],
            capture_output=True,
            text=True,
            check=True,
        )
        tracked = {p for p in proc.stdout.split("\0") if p}

        on_disk = {
            str(p.relative_to(repo_root))
            for p in (repo_root / "src").rglob("*.py")
            if "__pycache__" not in p.parts
        }

        untracked = sorted(on_disk - tracked)
        assert untracked == [], (
            f"Python modules under src/ that git does not track: {untracked}. "
            f"A tracked-only commit would ship a tree that fails to import."
        )


def _concrete_deft_pipelines():
    """Every ``DeftPipeline`` leaf currently loaded, found by walking subclasses.

    Recursive so a leaf added under an intermediate class is still seen.
    """

    def walk(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from walk(sub)

    return list(walk(DeftPipeline))


class TestDeftPipelineSplit:
    """AOI and ITS are separate classes building the IDENTICAL DAG.

    The split is routing only. These tests pin both halves of that sentence:
    each class claims exactly one job type (so no two pipelines claim one type
    and dispatcher ordering decides the winner), and both produce the same
    8-stage chain (so the split did not quietly fork the DAG).
    """

    @pytest.mark.parametrize(
        ("pipeline_cls", "job_type"),
        [(AOIPipeline, "aoi_job"), (ITSPipeline, "its_job")],
    )
    def test_each_pipeline_claims_exactly_its_own_job_type(self, pipeline_cls, job_type):
        # Exactly one, not a superset: while AOIPipeline also claimed its_job,
        # two pipelines answered for one job type and which DAG an ITS run got
        # depended on the order of the dispatcher's branches.
        assert pipeline_cls(MagicMock()).get_supported_job_types() == [job_type]

    def test_no_two_pipelines_claim_the_same_job_type(self):
        """DISCOVERED, not hardcoded — a future leaf must be covered too.

        Iterating a literal ``(AOIPipeline, ITSPipeline)`` tuple could only ever
        compare the two classes that already exist: a third leaf claiming
        ``aoi_job`` would leave this green while two pipelines answered for one
        job type and dispatcher ordering silently picked the winner — the exact
        bug the split was made to remove.
        """
        import src.tasks.pipelines  # noqa: F401  (imports every leaf module)

        leaves = [cls for cls in _concrete_deft_pipelines() if cls is not DeftPipeline]
        assert {AOIPipeline, ITSPipeline} <= set(
            leaves
        ), f"discovery missed a known leaf: found {sorted(c.__name__ for c in leaves)}"

        claimed = [jt for cls in leaves for jt in cls(MagicMock()).get_supported_job_types()]
        assert sorted(claimed) == sorted(set(claimed))

    def test_the_base_pipeline_is_not_routable(self):
        """``DeftPipeline`` is the shared DAG, not a job type. It must not answer.

        If the base ever acquires a ``job_type``, it becomes a silent third
        claimant on whichever type it names.
        """
        with pytest.raises(AttributeError):
            DeftPipeline(MagicMock()).get_supported_job_types()

    @staticmethod
    def _job_config(job_type="aoi_job", use_case="aoi"):
        # A REAL DeftJobConfig, not a MagicMock: build_workflow calls
        # model_dump() on it and seeds the chain's first task with the result,
        # so a double would let a schema mismatch through unnoticed.
        return build_job_config(
            job_type,
            {
                "use_case": use_case,
                "model_name": f"changenet-{use_case}",
                "selection": {"filters": {"created_after": "1970-01-01T00:00:00Z"}},
            },
        )

    @pytest.mark.parametrize(
        ("pipeline_cls", "job_type", "use_case"),
        [(AOIPipeline, "aoi_job", "aoi"), (ITSPipeline, "its_job", "its")],
    )
    def test_build_workflow_chains_all_eight_stages_in_order(
        self, pipeline_cls, job_type, use_case
    ):
        """The DAG a real submission would run, stage by stage.

        Not "a chain came back": an ITS run that skipped ``start_promotion``, or
        ran the eval gate before training, would still return a chain object.
        And because the names asserted are the wire names, this is the test that
        catches a stage left pointing at a task that no longer registers under
        that name — a failure mode that surfaces in production as
        ``NotRegistered`` at dispatch rather than as an ImportError at boot.
        """
        wf = pipeline_cls(MagicMock()).build_workflow(
            workload_id="w1",
            flywheel_run_id=str(ObjectId()),
            client_id="c1",
            job_type=job_type,
            job_config=self._job_config(job_type, use_case),
        )
        assert [t.name for t in wf.tasks] == _DEFT_CHAIN

    def test_aoi_and_its_build_the_identical_dag(self):
        """The split bought no behaviour, and this test says so out loud.

        Its job is to FAIL the day someone gives one leaf its own chain body
        without deciding to — at which point delete this test deliberately,
        rather than discovering the fork from a production run.
        """
        kwargs = {"workload_id": "w1", "flywheel_run_id": str(ObjectId()), "client_id": "c1"}
        aoi = AOIPipeline(MagicMock()).build_workflow(
            job_type="aoi_job", job_config=self._job_config("aoi_job", "aoi"), **kwargs
        )
        its = ITSPipeline(MagicMock()).build_workflow(
            job_type="its_job", job_config=self._job_config("its_job", "its"), **kwargs
        )
        assert [t.name for t in aoi.tasks] == [t.name for t in its.tasks]

    @pytest.mark.parametrize("pipeline_cls", [AOIPipeline, ITSPipeline])
    @pytest.mark.parametrize("job_type", ["llm_job", "embedding_job"])
    def test_build_workflow_rejects_non_deft_job_types(self, pipeline_cls, job_type):
        """The gate still CLOSES. An NMP job that reached either pipeline would
        run the snapshot/DEFT-training DAG against a config that has no snapshot
        in it.
        """
        with pytest.raises(ValueError, match=f"Job type '{job_type}' is not supported"):
            pipeline_cls(MagicMock()).build_workflow(
                workload_id="w1",
                flywheel_run_id=str(ObjectId()),
                client_id="c1",
                job_type=job_type,
                job_config=self._job_config(),
            )

    @pytest.mark.parametrize(
        ("pipeline_cls", "wrong_job_type"),
        [(AOIPipeline, "its_job"), (ITSPipeline, "aoi_job")],
    )
    def test_neither_pipeline_accepts_its_sibling_job_type(self, pipeline_cls, wrong_job_type):
        """The point of the split, stated as a refusal.

        Before it, both of these passed — one class served both types. If they
        pass again, the split has been undone and ``get_supported_job_types``
        has silently widened back to the family.
        """
        with pytest.raises(ValueError, match=f"Job type '{wrong_job_type}' is not supported"):
            pipeline_cls(MagicMock()).build_workflow(
                workload_id="w1",
                flywheel_run_id=str(ObjectId()),
                client_id="c1",
                job_type=wrong_job_type,
                job_config=self._job_config(),
            )


class TestDispatcherRoutesEachDeftJobTypeToItsOwnPipeline:
    """Routing, per job type, through the dispatcher's real branch.

    Patched at ``_DEFT_PIPELINES`` rather than at the module-level class names:
    the dispatcher looks the class up out of that dict, so ``@patch(
    "src.tasks.tasks.AOIPipeline")`` no longer intercepts anything and would
    build a REAL Celery chain.

    ``its_job`` is the case that regresses silently. The dispatcher's
    else-branch raise sits OUTSIDE every mark_flywheel_run_error guard, so a
    DEFT type that fell through leaves the FlywheelRun PENDING forever instead
    of failing it — which is why the branch test stays ``is_deft_job_type`` and
    a missing map entry is raised explicitly.
    """

    @staticmethod
    def _fake_pipelines():
        """A stand-in class per DEFT job type, so routing is observable.

        Both are patched in together on every case: asserting only that the
        expected one was built would pass just as happily if BOTH were, which
        is exactly the "two pipelines claim one job type" bug.
        """
        fakes = {"aoi_job": MagicMock(), "its_job": MagicMock()}
        for fake in fakes.values():
            workflow = MagicMock()
            workflow.apply_async.return_value.get.return_value = {"status": "completed"}
            fake.return_value.build_workflow.return_value = workflow
        return fakes

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    @patch("src.backends.factory.get_backend_provider")
    @patch("src.tasks.tasks.load_job_configuration")
    @patch("src.tasks.tasks.db_manager")
    def test_each_deft_job_type_reaches_only_its_own_pipeline(
        self, mock_db_manager, mock_load_cfg, mock_provider, job_type
    ):
        from src.api.schemas import FlywheelRunStatus
        from src.tasks.tasks import run_nim_workflow_dag

        flywheel_run_id = str(ObjectId())
        mock_db_manager.get_flywheel_run.return_value = {
            "status": FlywheelRunStatus.PENDING,
            "_id": ObjectId(flywheel_run_id),
        }
        job_cfg = MagicMock()
        job_cfg.ingestion_config = None
        mock_load_cfg.return_value = job_cfg
        mock_provider.return_value = DeftBackendProvider()  # supports the DEFT family

        fakes = self._fake_pipelines()
        other = "its_job" if job_type == "aoi_job" else "aoi_job"

        with patch.dict("src.tasks.tasks._DEFT_PIPELINES", fakes, clear=True):
            result = run_nim_workflow_dag(
                workload_id="w1",
                flywheel_run_id=flywheel_run_id,
                client_id="c1",
                job_type=job_type,
            )

        assert result["status"] == "completed"
        fakes[job_type].assert_called_once()
        fakes[job_type].return_value.build_workflow.assert_called_once()
        assert fakes[job_type].return_value.build_workflow.call_args.kwargs["job_type"] == job_type
        # The sibling pipeline was never constructed.
        fakes[other].assert_not_called()

    def test_the_map_covers_the_whole_deft_family(self):
        """Parity with ``DEFT_JOB_TYPES``, in both directions.

        A new family member added to ``DEFT_JOB_TYPES`` but not to the map
        passes ``is_deft_job_type`` and then fails at dispatch — every
        submission of that type dies after the run row exists. An extra map
        entry is unreachable routing.
        """
        from src.job_types import DEFT_JOB_TYPES
        from src.tasks.tasks import _DEFT_PIPELINES

        assert set(_DEFT_PIPELINES) == {jt.value for jt in DEFT_JOB_TYPES}
        # And each entry is really the pipeline that serves that type.
        for job_type, pipeline_cls in _DEFT_PIPELINES.items():
            assert issubclass(pipeline_cls, DeftPipeline)
            assert pipeline_cls(MagicMock()).get_supported_job_types() == [job_type]

    @patch("src.backends.factory.get_backend_provider")
    @patch("src.tasks.tasks.load_job_configuration")
    @patch("src.tasks.tasks.db_manager")
    def test_a_family_member_missing_from_the_map_fails_the_run_loudly(
        self, mock_db_manager, mock_load_cfg, mock_provider
    ):
        """The failure the ``is_deft_job_type`` branch test exists to produce.

        Simulated by emptying the map for one DEFT type: the run must be MARKED
        FAILED, not fall through to the else-branch raise, which happens outside
        every error-marking guard and strands the run in PENDING forever.
        """
        from src.api.schemas import FlywheelRunStatus
        from src.tasks.tasks import run_nim_workflow_dag

        flywheel_run_id = str(ObjectId())
        mock_db_manager.get_flywheel_run.return_value = {
            "status": FlywheelRunStatus.PENDING,
            "_id": ObjectId(flywheel_run_id),
        }
        job_cfg = MagicMock()
        job_cfg.ingestion_config = None
        mock_load_cfg.return_value = job_cfg
        mock_provider.return_value = DeftBackendProvider()

        with patch.dict("src.tasks.tasks._DEFT_PIPELINES", {"aoi_job": MagicMock()}, clear=True):
            with pytest.raises(ValueError, match="No pipeline registered for DEFT job_type"):
                run_nim_workflow_dag(
                    workload_id="w1",
                    flywheel_run_id=flywheel_run_id,
                    client_id="c1",
                    job_type="its_job",
                )

        mock_db_manager.mark_flywheel_run_error.assert_called_once()
        assert "its_job" in mock_db_manager.mark_flywheel_run_error.call_args.args[1]

    @pytest.mark.parametrize("job_type", ["aoi_job", "its_job"])
    @patch("src.tasks.tasks._apply_runtime_overrides")
    @patch("src.backends.factory.get_backend_provider")
    @patch("src.tasks.tasks.load_job_configuration")
    @patch("src.tasks.tasks.db_manager")
    def test_runtime_overrides_are_skipped_for_deft_job_types(
        self,
        mock_db_manager,
        mock_load_cfg,
        mock_provider,
        mock_apply_overrides,
        job_type,
    ):
        """_apply_runtime_overrides is NMP-shaped and must never see a DEFT job.

        Called directly rather than through TriggerExecutor on purpose: the
        executor's DEFT branch omits the overrides kwarg, so routing this
        through it would pass for the wrong reason. Reached with a truthy
        overrides dict, _apply_runtime_overrides dies on ``job_config.nims``
        (AttributeError) outside every error-marking guard.
        """
        from src.api.schemas import FlywheelRunStatus
        from src.tasks.tasks import run_nim_workflow_dag

        flywheel_run_id = str(ObjectId())
        mock_db_manager.get_flywheel_run.return_value = {
            "status": FlywheelRunStatus.PENDING,
            "_id": ObjectId(flywheel_run_id),
        }
        job_cfg = MagicMock()
        job_cfg.ingestion_config = None
        mock_load_cfg.return_value = job_cfg
        mock_provider.return_value = DeftBackendProvider()

        with patch.dict("src.tasks.tasks._DEFT_PIPELINES", self._fake_pipelines(), clear=True):
            result = run_nim_workflow_dag(
                workload_id="w1",
                flywheel_run_id=flywheel_run_id,
                client_id="c1",
                job_type=job_type,
                runtime_overrides={"training_enabled": True},
            )

        assert result["status"] == "completed"
        mock_apply_overrides.assert_not_called()

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
"""The DEFT provider resolves the Storage Backend BY USE CASE.

One DFW process serves both AOI and its ITS sibling, and they do not share a
store. ``DEFTConfig.for_use_case`` already carries the per-use-case endpoint
overrides; these tests pin that EVERY storage client the provider builds is
resolved through it. The provider has no ``_storage_client`` helper — both call
sites construct ``StorageBackendClient(config.for_use_case(use_case))`` inline —
so each claim below is asserted through the public seam that builds one
(``datastore()``, and ``evaluator()`` via its eval-set source's closure).

The bug being prevented is not a crash. ``evaluator()`` builds a storage client
too (for the eval-set / online-results flow), so fixing only ``datastore()``
leaves an ITS rollout graded on AOI board records — a wrong number that gates a
promotion, silently.
"""

from __future__ import annotations

import logging

import pytest

from src.backends.deft.provider import DeftBackendProvider
from src.backends.deft.storage_backend_client import StorageBackendClient
from src.config import DEFTConfig
from src.config.deft import UseCaseEndpoints

AOI_URL = "http://aoi-storage:8080"
ITS_URL = "http://sc-backend:8080"


def _config(**kwargs) -> DEFTConfig:
    """A two-use-case deployment: AOI on the top-level endpoints, ITS overriding."""
    return DEFTConfig(
        storage_backend_url=AOI_URL,
        object_store_uri="s3://aoi-artifacts",
        storage_api_key_env="AOI_KEY",
        use_case_endpoints={
            # Declared with no overrides: the deliberate "inherits everything
            # above" statement. Required, not optional — a non-empty map is
            # exhaustive, so omitting `aoi` here would refuse every AOI run.
            "aoi": UseCaseEndpoints(),
            "its": UseCaseEndpoints(
                storage_backend_url=ITS_URL,
                object_store_uri="s3://sc-artifacts",
                storage_api_key_env="ITS_KEY",
            ),
        },
        **kwargs,
    )


class TestStorageClientResolution:
    """Observed through ``datastore()``, which is where the provider builds one.

    There is no ``_storage_client`` helper to call: each call site constructs
    ``StorageBackendClient(config.for_use_case(use_case))`` inline, so the
    resolution is asserted on the client a public seam actually hands back.
    """

    @staticmethod
    def _config_for(provider: DeftBackendProvider, use_case):
        return provider.datastore("d", use_case)._client.config

    def test_storage_client_uses_the_use_case_endpoints(self):
        cfg = self._config_for(DeftBackendProvider(_config()), "its")
        assert cfg.storage_backend_url == ITS_URL
        assert cfg.object_store_uri == "s3://sc-artifacts"
        assert cfg.storage_api_key_env == "ITS_KEY"

    def test_a_declared_use_case_with_no_overrides_inherits_the_top_level(self):
        # `aoi: {}` and a caller that names no use case at all both land on the
        # process-wide endpoints. The second is the pre-use_case_endpoints path
        # and must stay reachable: old promotion records carry use_case=None.
        p = DeftBackendProvider(_config())
        assert self._config_for(p, "aoi").storage_backend_url == AOI_URL
        assert self._config_for(p, None).storage_backend_url == AOI_URL

    def test_an_undeclared_use_case_is_refused_rather_than_inheriting(self):
        # An AOI-family use case may legitimately share this store — but only
        # once someone SAYS so. Undeclared must fail loudly: inheriting silently
        # is how a run snapshots, signs and promotes against another use case's
        # data while every stage reports success.
        p = DeftBackendProvider(_config())
        with pytest.raises(ValueError, match="use_case_endpoints"):
            self._config_for(p, "undeclared_sibling")

    def test_no_use_case_map_hands_back_the_identical_config_object(self):
        # for_use_case returns SELF when there is nothing to override; that
        # identity is what makes the AOI path byte-identical to before.
        cfg = DEFTConfig(storage_backend_url=AOI_URL)
        p = DeftBackendProvider(cfg)
        assert self._config_for(p, "aoi") is cfg
        assert self._config_for(p, None) is cfg


class TestDatastoreByUseCase:
    def test_datastore_defaults_to_the_top_level_store(self):
        # LSP-safe: every existing caller passes dataset_name only, unchanged.
        ds = DeftBackendProvider(_config()).datastore("its-stream-detector")
        assert ds._client.config.storage_backend_url == AOI_URL

    def test_datastore_routes_to_the_use_case_store(self):
        ds = DeftBackendProvider(_config()).datastore("its-stream-detector", use_case="its")
        assert ds._client.config.storage_backend_url == ITS_URL

    def test_datastore_still_carries_the_dataset_name(self):
        ds = DeftBackendProvider(_config()).datastore("its-stream-detector", use_case="its")
        assert isinstance(ds._client, StorageBackendClient)
        assert ds._dataset_name == "its-stream-detector"

    def test_positional_use_case_also_works(self):
        ds = DeftBackendProvider(_config()).datastore("d", "its")
        assert ds._client.config.storage_backend_url == ITS_URL


class TestEvaluatorStorageIsAlsoUseCaseScoped:
    """The data-crossing bug: evaluator() builds a storage client too."""

    @staticmethod
    def _client_of(results_source):
        # The eval-set source closes over its StorageBackendClient; reach it
        # through the closure so the wiring itself is under test rather than a
        # re-derived value.
        cells = {
            name: cell.cell_contents
            for name, cell in zip(
                results_source.__code__.co_freevars, results_source.__closure__ or (), strict=False
            )
        }
        return cells["client"]

    def test_evaluator_eval_set_client_follows_the_use_case(self):
        p = DeftBackendProvider(_config())
        its_client = self._client_of(p.evaluator(use_case="its")._results_source)
        assert its_client.config.storage_backend_url == ITS_URL

    def test_evaluator_eval_set_client_for_aoi_is_unchanged(self):
        p = DeftBackendProvider(_config())
        aoi_client = self._client_of(p.evaluator(use_case="aoi")._results_source)
        assert aoi_client.config.storage_backend_url == AOI_URL

    def test_the_two_use_cases_do_not_share_a_store(self):
        # The one-line summary of the whole file.
        p = DeftBackendProvider(_config())
        aoi = self._client_of(p.evaluator(use_case="aoi")._results_source)
        its = self._client_of(p.evaluator(use_case="its")._results_source)
        assert aoi.config.storage_backend_url != its.config.storage_backend_url


class TestLegacyAoiResultsUrlOverride:
    """``aoi_results_url`` is an AOI-only legacy escape hatch."""

    LEGACY = "http://aoi:9000/v1/results"

    def test_still_honoured_for_aoi(self):
        # Byte-identical to the pre-ITS behaviour.
        p = DeftBackendProvider(_config(aoi_results_url=self.LEGACY))
        source = p.evaluator(use_case="aoi")._results_source
        assert source.__closure__ is not None
        assert self.LEGACY in [c.cell_contents for c in source.__closure__]

    def test_no_warning_for_the_aoi_evaluator_family(self, caplog):
        # A sibling registered against DeftAOIEvaluator is AOI-family: same
        # board records, same endpoint, same metric. Warning about the legacy
        # override there would train operators to ignore a warning that DOES
        # matter for a genuinely non-AOI use case.
        #
        # Registered locally rather than leaning on a shipped use case: the rule
        # is about the evaluator FAMILY, so tying it to whichever names the
        # deployment happens to ship makes the test fail when that list changes
        # for unrelated reasons.
        from src.backends.deft import eval as eval_mod

        eval_mod._EVALUATORS["aoi_family_sibling"] = eval_mod.DeftAOIEvaluator
        try:
            cfg = _config(aoi_results_url=self.LEGACY)
            # Declared alongside the shipped entries: the map is EXHAUSTIVE, so
            # an undeclared sibling would be refused before the override is even
            # consulted and this would test the guard instead of the rule.
            cfg.use_case_endpoints["aoi_family_sibling"] = UseCaseEndpoints()
            p = DeftBackendProvider(cfg)
            with caplog.at_level(logging.WARNING):
                source = p.evaluator(use_case="aoi_family_sibling")._results_source
        finally:
            eval_mod._EVALUATORS.pop("aoi_family_sibling", None)

        # inherits the legacy override (the sibling rule) ...
        assert self.LEGACY in [c.cell_contents for c in source.__closure__ or ()]
        # ... and does so silently
        assert "aoi_results_url" not in caplog.text

    def test_the_guard_follows_the_registry_not_a_name_list(self):
        # Registering another AOI-shaped use case must inherit the override with
        # no edit to the provider: the evaluator FAMILY is the criterion.
        from src.backends.deft import eval as eval_mod

        eval_mod._EVALUATORS["aoi_lookalike"] = eval_mod.DeftAOIEvaluator
        try:
            p = DeftBackendProvider(_config(aoi_results_url=self.LEGACY))
            source = p.evaluator(use_case="aoi_lookalike")._results_source
            assert self.LEGACY in [c.cell_contents for c in source.__closure__ or ()]
        finally:
            eval_mod._EVALUATORS.pop("aoi_lookalike", None)

    def test_ignored_for_a_non_aoi_use_case(self):
        # Honouring it would collect AOI board records and grade an ITS rollout
        # on them. Instead we fall through to that use case's eval-set flow.
        p = DeftBackendProvider(_config(aoi_results_url=self.LEGACY))
        source = p.evaluator(use_case="its")._results_source
        client = TestEvaluatorStorageIsAlsoUseCaseScoped._client_of(source)
        assert client.config.storage_backend_url == ITS_URL

    def test_ignoring_it_is_logged_loudly(self, caplog):
        p = DeftBackendProvider(_config(aoi_results_url=self.LEGACY))
        with caplog.at_level(logging.WARNING):
            p.evaluator(use_case="its")
        assert "aoi_results_url" in caplog.text
        assert "IGNORING" in caplog.text

    def test_no_warning_on_the_aoi_path(self, caplog):
        p = DeftBackendProvider(_config(aoi_results_url=self.LEGACY))
        with caplog.at_level(logging.WARNING):
            p.evaluator(use_case="aoi")
        assert "aoi_results_url" not in caplog.text


class TestEvalSetSelectionWhitelistUnchanged:
    """The selection whitelist is deliberate — ITS must not widen it."""

    def test_only_contract_keys_reach_the_create_call(self):
        from unittest.mock import MagicMock

        from src.backends.deft.provider import _eval_set_results_source

        client = MagicMock()
        client.create_eval_set.return_value = {"eval_id": "e1"}
        client.get_eval_stats.return_value = {"status": "ready", "manifests": []}
        _eval_set_results_source(client)(
            {"model": "its-stream-detector", "version": "v2", "cameras": ["cam-0"], "from": "t0"},
            "shadow",
        )
        # `cameras` is an ITS facet; EvaluationSetSelection is
        # additionalProperties: false, so sending it would 422 the create.
        assert client.create_eval_set.call_args.args[0] == {
            "role": "shadow",
            "model": "its-stream-detector",
            "version": "v2",
            "from": "t0",
        }


class TestUnknownUseCaseStillFailsLoudly:
    def test_unknown_use_case_raises_before_any_client_is_used(self):
        with pytest.raises(ValueError, match="use_case"):
            DeftBackendProvider(_config()).evaluator(use_case="not-a-use-case")

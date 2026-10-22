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

"""The place the per-record use case was resolved but NOT applied.

The pre-pull re-presign signed against the top-level (AOI) object store even
with ``record.use_case == "its"`` in hand. The failure is silent by
construction: the request succeeds, the promotion advances, and only the DATA is
wrong.

The AOI half of each test is as important as the ITS half: with no
``use_case_endpoints`` configured, ``for_use_case`` returns SELF and this path
is byte-identical to its pre-ITS behaviour.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api.promotion.models import PromotionRecord
from src.api.promotion.service import PromotionService
from src.config import PromotionPolicyConfig
from src.config.deft import DEFTConfig, UseCaseEndpoints
from src.tasks.promotion import controller as controller_mod

AOI_STORE = "s3://aoi-artifacts"
ITS_STORE = "s3://sc-artifacts"
AOI_STORAGE_URL = "http://aoi-storage:8080"
ITS_STORAGE_URL = "http://sc-backend:8080"


def _deft_config() -> DEFTConfig:
    """A two-use-case deployment: AOI top-level, ITS overriding its store."""
    return DEFTConfig(
        storage_backend_url=AOI_STORAGE_URL,
        object_store_uri=AOI_STORE,
        storage_api_key_env="AOI_KEY",
        use_case_endpoints={
            # Declared with no overrides: the deliberate "inherits everything
            # above" statement. Required, not optional — a non-empty map is
            # exhaustive, so omitting `aoi` here would refuse every AOI pull.
            "aoi": UseCaseEndpoints(),
            "its": UseCaseEndpoints(
                storage_backend_url=ITS_STORAGE_URL,
                object_store_uri=ITS_STORE,
                storage_api_key_env="ITS_KEY",
            ),
        },
    )


def _settings_with(deft_config) -> MagicMock:
    fake = MagicMock()
    fake.system_config.deft_config = deft_config
    return fake


@pytest.fixture
def controller(fake_db):
    """A controller on the fake DB with a mock backend/mlflow.

    ``_apply_record_policy`` is neutralized so these tests exercise the pull gate
    with the policy set below instead of the deployment's default AOI job config
    — policy resolution has its own suite in test_controller.py and is not what
    is under test here.
    """
    backend = MagicMock()
    mlflow = MagicMock()
    mlflow.get_version_by_alias.return_value = None
    with (
        patch.object(controller_mod, "_get_db", return_value=fake_db),
        patch.object(controller_mod.PromotionController, "_build_backend", return_value=backend),
        patch.object(controller_mod.PromotionController, "_build_mlflow", return_value=mlflow),
    ):
        ctrl = controller_mod.PromotionController()
    ctrl.policy = PromotionPolicyConfig(poll_interval_s=0.0, poll_max_attempts=1)
    with patch.object(controller_mod.PromotionController, "_apply_record_policy", lambda *_: None):
        yield ctrl


@pytest.fixture(autouse=True)
def _patch_enqueue():
    """Keep the Celery broker out of these tests."""
    with (
        patch("src.tasks.promotion.tasks.advance_promotion", MagicMock()),
        patch("src.tasks.promotion.tasks.rollback_promotion", MagicMock()),
    ):
        yield


def _record(ctrl, **overrides) -> PromotionRecord:
    fields = {
        "model_name": "m",
        "candidate_version": "2",
        "baseline_version": "1",
        "use_case": "aoi",
        "state": "promoting",
        "pending_gate": "pull",
        "artifact_uri": "https://store/stale?X-Amz-Signature=old",
        "artifact_locator": "runs/2/weights.onnx",
        "model_format": "onnx",
        "sha256": "abc",
    }
    fields.update(overrides)
    record = PromotionRecord(**fields)
    PromotionService(ctrl.db).create(record)
    return record


class TestPresignIsUseCaseScoped:
    """``_presign_for_pull`` had ``record.use_case`` in hand and signed against
    the top-level config anyway.

    The damage is not a 500. A signature minted against the AOI store either
    404s the ITS key (a pull failure blamed on the registry) or — where a
    same-named key exists in both buckets — succeeds and stages the WRONG
    WEIGHTS into an ITS serving stack.
    """

    @staticmethod
    def _run(ctrl, record):
        """Advance the pull gate, capturing the config handed to the presigner."""
        with (
            patch.object(controller_mod, "settings", _settings_with(_deft_config())),
            patch(
                "src.backends.deft.presign.presign_pull_locators",
                return_value={"artifact": "https://fresh", "spec": None},
            ) as presign,
            patch("src.backends.deft.presign.needs_signing", return_value=True),
        ):
            ctrl.advance(record.promotion_id)
        return presign.call_args.args[0]

    def test_an_its_pull_is_signed_against_the_its_store(self, controller):
        record = _record(controller, use_case="its")
        config = self._run(controller, record)
        assert config.object_store_uri == ITS_STORE
        assert config.storage_backend_url == ITS_STORAGE_URL
        # The credentials follow the store: signing an ITS key with the AOI key
        # is a 403 at download time, i.e. a pull failure with no cause on it.
        assert config.storage_api_key_env == "ITS_KEY"

    def test_an_aoi_pull_still_uses_the_top_level_store(self, controller):
        record = _record(controller, use_case="aoi")
        config = self._run(controller, record)
        assert config.object_store_uri == AOI_STORE
        assert config.storage_backend_url == AOI_STORAGE_URL

    def test_the_two_use_cases_never_share_a_signing_config(self, controller):
        # The one-line summary: whichever record is being pulled decides.
        its = self._run(controller, _record(controller, use_case="its"))
        aoi = self._run(controller, _record(controller, use_case="aoi"))
        assert its.object_store_uri != aoi.object_store_uri

    def test_a_deployment_with_no_overrides_signs_with_the_identical_object(self, controller):
        # for_use_case returns SELF when nothing overrides — the non-regression
        # claim for every AOI deployment that exists today.
        config = DEFTConfig(object_store_uri=AOI_STORE)
        record = _record(controller, use_case="aoi")
        with (
            patch.object(controller_mod, "settings", _settings_with(config)),
            patch(
                "src.backends.deft.presign.presign_pull_locators",
                return_value={"artifact": "https://fresh", "spec": None},
            ) as presign,
            patch("src.backends.deft.presign.needs_signing", return_value=True),
        ):
            controller.advance(record.promotion_id)
        assert presign.call_args.args[0] is config

    def test_an_undeclared_use_case_falls_back_instead_of_failing_the_pull(
        self, controller, caplog
    ):
        """The fail-closed resolution must not escape this method's own handler.

        ``for_use_case`` raises on a use case absent from a non-empty map. Resolved
        ABOVE the presign ``try`` that raise escaped ``_presign_for_pull`` entirely
        and hard-failed the pull — for an AOI-family use case the deployment had
        not declared, whose promotions used to fall back to the stored URL and
        pull fine. The documented contract is that
        NO presign problem fails a pull; the snapshot stage is the one that refuses.

        Driven through the real ``needs_signing`` with a raw ``s3://`` locator on
        purpose: with a pass-through locator the early return short-circuits before
        the resolution and the test would prove nothing.
        """
        import logging

        record = _record(
            controller,
            use_case="aoi_family_sibling",
            artifact_locator="s3://bucket/model.pt",
        )
        with (
            patch.object(controller_mod, "settings", _settings_with(_deft_config())),
            patch("src.backends.deft.presign.presign_pull_locators") as presign,
            caplog.at_level(logging.WARNING, logger="data_flywheel.promotion.controller"),
        ):
            artifact_uri, spec_uri = controller._presign_for_pull(record)

        # The stored URLs come back untouched, and nothing was signed against the
        # store we could not legitimately resolve.
        assert artifact_uri == record.artifact_uri
        assert spec_uri == record.spec_uri
        presign.assert_not_called()

        # Diagnosable: the log must say "undeclared use case", not just "presign
        # failed", or the operator goes looking at the object store.
        warning = "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)
        assert "use_case_endpoints" in warning
        assert "aoi_family_sibling" in warning

    def test_a_config_without_for_use_case_is_still_usable(self, controller):
        # Defensive: a deployment/test double whose deft_config predates the
        # seam must degrade to the old behaviour, never raise inside the pull.
        legacy = MagicMock(spec=[])  # no attributes at all, so no for_use_case
        record = _record(controller, use_case="its")
        with (
            patch.object(controller_mod, "settings", _settings_with(legacy)),
            patch(
                "src.backends.deft.presign.presign_pull_locators",
                return_value={"artifact": "https://fresh", "spec": None},
            ) as presign,
            patch("src.backends.deft.presign.needs_signing", return_value=True),
        ):
            controller.advance(record.promotion_id)
        assert presign.call_args.args[0] is legacy

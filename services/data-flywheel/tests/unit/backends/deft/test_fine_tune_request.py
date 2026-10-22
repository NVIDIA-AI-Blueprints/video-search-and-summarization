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
"""What ``FineTuneRequest`` assembly DROPS, and whether it says so.

``profile`` and ``config`` are the two ALTERNATIVE bodies of a DEFT
``FineTuneRequest``, not layers. In profile mode the complete
``ExperimentConfig`` is assembled server-side, so anything the caller puts in
``deft.config`` has nowhere to go and is discarded.

That is the correct behaviour and a dangerous silence. The values an operator
writes into ``deft.config`` are precisely the ones whose absence is invisible
downstream — a day-0 ``model.checkpoint``, an epoch count — and DEFT does not
fail when they are missing: it trains a perfectly valid run off the profile's own
defaults. Nothing later in the pipeline can tell the two runs apart. The ITS
config is profile-mode, and the design's §8.3 block writes ``profile`` AND a
``config`` tree, so an operator reconciling toward it verbatim walks straight
into this.

These tests pin the drop (it is intended) TOGETHER WITH the warning (which is
what makes it survivable), and pin that workflow mode is unaffected.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from src.backends.deft.wrappers import DeftTraining

#: A §8.3-verbatim profile-mode block: the named profile PLUS a config tree
#: carrying the two things an operator most plausibly wants to survive.
_PROFILE_PLUS_CONFIG: dict[str, Any] = {
    "profile": "smart-city-object-detection",
    "config": {
        "model": {"checkpoint": "s3://sc-models/base/1.0.0/model.pt"},
        "training": {"epochs": 40},
    },
}


def _build(**params: Any) -> dict[str, Any]:
    return DeftTraining._build_fine_tune_request(
        snapshot_uri="s3://sc-artifacts/snapshots/snap-1/",
        model_name="gdino-its",
        deft_params=params,
    )


def test_profile_mode_drops_a_config_tree_but_warns_by_name(caplog):
    """The drop is intended; the SILENCE was the defect.

    Sibling key ``deft.workflow`` has always warned when a profile overrides it.
    ``deft.config`` — a far costlier omission, because it can carry the baseline
    checkpoint the run fine-tunes FROM — was discarded with no error, no log and
    no trace in the assembled request. The two are now symmetric.

    The warning names the dropped SECTIONS rather than dumping the tree: an
    operator needs "your ``model`` and ``training`` blocks did not go", and a
    checkpoint uri in a log line is noise at best.
    """
    with caplog.at_level(logging.WARNING, logger="data_flywheel.backends.deft.wrappers"):
        request = _build(**_PROFILE_PLUS_CONFIG)

    # The drop itself, pinned: a partial `config` would fail ExperimentConfig
    # validation server-side, so profile mode must not emit one.
    assert "config" not in request
    assert request["profile"] == "smart-city-object-detection"

    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "deft.config" in m for m in warnings
    ), f"a dropped deft.config must warn, exactly as deft.workflow does; got {warnings}"
    dropped = next(m for m in warnings if "deft.config" in m)
    # The sections that went missing, and where to put them instead.
    assert "model" in dropped and "training" in dropped
    assert "deft.overrides" in dropped


def test_a_profile_with_no_config_tree_warns_about_nothing(caplog):
    """The shipped ITS block is profile-only; it must stay quiet.

    A warning that fires on the normal path is a warning operators learn to
    ignore, which would cost exactly the signal the test above installs.
    """
    with caplog.at_level(logging.WARNING, logger="data_flywheel.backends.deft.wrappers"):
        request = _build(profile="smart-city-object-detection")

    assert request["profile"] == "smart-city-object-detection"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_an_empty_config_tree_is_not_worth_a_warning(caplog):
    """``config: {}`` drops nothing, so it says nothing."""
    with caplog.at_level(logging.WARNING, logger="data_flywheel.backends.deft.wrappers"):
        _build(profile="p", config={})

    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_workflow_mode_still_honours_the_config_tree(caplog):
    """The fix must not turn a HONOURED config into a warned-about one.

    Without a profile there is no server-side ExperimentConfig, so ``config`` is
    the request body — merged with the injected snapshot/model refs and validated
    against the contract's required fields. Nothing is dropped, so nothing warns.
    """
    with caplog.at_level(logging.WARNING, logger="data_flywheel.backends.deft.wrappers"):
        request = _build(
            workflow="aoi_finetune",
            config={
                "model": {"checkpoint": "s3://sc-models/base/1.0.0/model.pt"},
                "training": {"epochs": 40},
            },
        )

    assert request["config"]["model"]["checkpoint"] == "s3://sc-models/base/1.0.0/model.pt"
    assert request["config"]["training"] == {"epochs": 40}
    # The injected refs merged in rather than replacing the operator's tree.
    assert request["config"]["data_pool"]["path"] == "s3://sc-artifacts/snapshots/snap-1/"
    assert request["config"]["model"]["name"] == "gdino-its"
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_the_shipped_its_deft_block_never_trips_the_warning():
    """Whatever ``its_job_config.deft`` ships must be honourable as written.

    This is the reconciliation the warning exists to force: if a later edit
    restores §8.3's ``config`` tree beside the profile, the deployment default
    starts emitting a warning on every single ITS run — which is the correct
    signal, and this test is where it is noticed before an operator sees it.
    """
    import os
    from pathlib import Path
    from unittest import mock

    from src.config.base import Settings

    repo_root = Path(__file__).resolve().parents[4]
    # AOI_SELECTION_TAG has no `:-` default in the shipped file, so the renderer
    # refuses the load without it; every other value falls back to the file.
    with mock.patch.dict(os.environ, {"AOI_SELECTION_TAG": "aoi1"}):
        settings = Settings.from_yaml(repo_root / "config" / "config.deft.yaml")
    deft_block = settings.job_configs["its_job"].deft

    assert "profile" in deft_block
    assert not deft_block.get("config"), (
        "its_job_config.deft ships a `config` tree beside a `profile`; profile mode "
        "DISCARDS it server-side, so those values would never reach DEFT. Move them to "
        "deft.overrides or drop the profile."
    )


@pytest.mark.parametrize("ignored_key", ["workflow", "config"])
def test_every_profile_mode_drop_is_logged(ignored_key, caplog):
    """No key may be dropped in profile mode without a WARNING naming it.

    Parameterized so that adding a third silently-ignored key is a deliberate
    act: the list here is the list of things profile mode is allowed to swallow,
    and each entry must earn its silence by being loud.
    """
    payload: dict[str, Any] = {"profile": "p", ignored_key: {"training": {"epochs": 40}}}
    if ignored_key == "workflow":
        payload[ignored_key] = "aoi_finetune"

    with caplog.at_level(logging.WARNING, logger="data_flywheel.backends.deft.wrappers"):
        request = _build(**payload)

    assert ignored_key not in request
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        f"deft.{ignored_key}" in m for m in warnings
    ), f"deft.{ignored_key} was dropped without a warning naming it; got {warnings}"

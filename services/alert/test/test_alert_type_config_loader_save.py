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

"""Tests for AlertTypeConfigLoader.save_to_redis seeding semantics.

Specifically guards the deep-merge path that protects API-managed
``vlm_params`` from clobbering file defaults across container restarts.
"""

import os
import sys

import fakeredis
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handlers.alert_config import AlertConfigStore
from handlers.prompt_handler.alert_type_config_loader import (
    AlertTypeConfig,
    AlertTypeConfigLoader,
    Prompts,
    VlmParams,
)


def _loader() -> AlertTypeConfigLoader:
    """Build a loader without exercising the real config-file lookup."""
    instance = AlertTypeConfigLoader.__new__(AlertTypeConfigLoader)
    import logging
    instance.logger = logging.getLogger("AlertTypeConfigLoaderTest")
    return instance


def _store() -> AlertConfigStore:
    return AlertConfigStore(fakeredis.FakeRedis(decode_responses=True))


def _config(vlm_params: VlmParams = None) -> AlertTypeConfig:
    return AlertTypeConfig(
        alert_type="collision",
        prompts=Prompts(user="user", system="system"),
        vlm_params=vlm_params,
    )


class TestSeedingMerge:

    def test_seed_writes_file_data_when_redis_empty(self):
        loader, store = _loader(), _store()
        loader.save_to_redis(
            "collision",
            _config(VlmParams(max_tokens=256, num_frames=18)),
            store,
        )
        record = store.get("collision")
        assert record["vlm_params"] == {"max_tokens": 256, "num_frames": 18}

    def test_seed_preserves_api_managed_top_level_fields(self):
        loader, store = _loader(), _store()
        store.set("collision", {
            "prompt": "API user prompt",
            "system_prompt": "API system",
            "vlm_params": None,
            "output_category": "API category",
            "alert_type": "collision",
        })
        loader.save_to_redis(
            "collision",
            _config(),
            store,
        )
        record = store.get("collision")
        assert record["prompt"] == "API user prompt"
        assert record["output_category"] == "API category"

    def test_partial_api_vlm_params_merged_with_file_defaults(self):
        loader, store = _loader(), _store()
        # User PUT only sent ``temperature``; file defaults provide the rest.
        store.set("collision", {
            "prompt": "user",
            "system_prompt": "system",
            "vlm_params": {"temperature": 0.4},
            "output_category": None,
            "alert_type": "collision",
        })
        loader.save_to_redis(
            "collision",
            _config(VlmParams(max_tokens=256, num_frames=18, temperature=0.6)),
            store,
        )
        merged = store.get("collision")["vlm_params"]
        # API value wins for temperature; file defaults seed missing keys.
        assert merged["temperature"] == 0.4
        assert merged["max_tokens"] == 256
        assert merged["num_frames"] == 18

    def test_existing_vlm_params_keeps_file_keys_after_restart(self):
        """Regression guard for the shallow-merge bug: API-set partial
        vlm_params must not drop the file's other defaults on restart."""
        loader, store = _loader(), _store()
        store.set("collision", {
            "prompt": "user",
            "system_prompt": "system",
            "vlm_params": {"max_tokens": 1024},  # API override only
            "output_category": None,
            "alert_type": "collision",
        })
        loader.save_to_redis(
            "collision",
            _config(VlmParams(max_tokens=256, num_frames=18, temperature=0.6)),
            store,
        )
        merged = store.get("collision")["vlm_params"]
        assert merged["max_tokens"] == 1024  # API kept
        assert merged["num_frames"] == 18    # restored from file
        assert merged["temperature"] == 0.6  # restored from file

    def test_no_file_vlm_params_keeps_existing_api_value(self):
        loader, store = _loader(), _store()
        store.set("collision", {
            "prompt": "user",
            "system_prompt": "system",
            "vlm_params": {"max_tokens": 1024},
            "output_category": None,
            "alert_type": "collision",
        })
        loader.save_to_redis("collision", _config(), store)
        assert store.get("collision")["vlm_params"] == {"max_tokens": 1024}

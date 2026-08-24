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

"""Unit tests for AnomalyEnhancer._get_merged_vlm_config (the alert-config REST API).

The full enhancer is heavy to construct (it pulls Kafka, Redis, VLM,
etc.), so the tests invoke ``_get_merged_vlm_config`` against a
``SimpleNamespace`` that mimics the minimal attributes the method reads.
This exercises the precedence rules without spinning up the whole
pipeline.
"""

import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import fakeredis
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from handlers.alert_config import AlertConfigStore  # noqa: E402

# Import the AnomalyEnhancer class only to grab the unbound function — we
# never instantiate it.
import importlib.util  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_spec = importlib.util.spec_from_file_location(
    "_enh_under_test", os.path.join(_REPO_ROOT, "enhance_alert_with_vlm.py")
)


@pytest.fixture(scope="module")
def merge_fn():
    enh = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(enh)
    return enh.AnomalyEnhancer._get_merged_vlm_config


def _make_self(global_cfg, file_params=None, redis_store=None):
    """Build the smallest object that satisfies attribute access."""
    loader = SimpleNamespace(
        get_vlm_params_for_alert_type=lambda alert_type: file_params,
    ) if file_params is not None else None
    if file_params is None:
        loader = SimpleNamespace(
            get_vlm_params_for_alert_type=lambda alert_type: None,
        )
    prompt_manager = SimpleNamespace(alert_config_loader=loader)
    return SimpleNamespace(
        _global_vlm_config=dict(global_cfg),
        prompt_manager=prompt_manager,
        _alert_config_store=redis_store,
    )


def _file_params(**fields):
    """Return a fake VlmParams-like object with model_dump."""
    return SimpleNamespace(model_dump=lambda exclude_none=False: dict(fields))


# ── Precedence: global only ────────────────────────────────────────────────

class TestGlobalOnly:

    def test_returns_global_when_no_loader(self, merge_fn):
        self_obj = SimpleNamespace(
            _global_vlm_config={"max_tokens": 256, "num_frames": 18},
            prompt_manager=SimpleNamespace(alert_config_loader=None),
            _alert_config_store=None,
        )
        result = merge_fn(self_obj, "anything")
        assert result == {"max_tokens": 256, "num_frames": 18}

    def test_unknown_category_falls_back_to_global(self, merge_fn):
        self_obj = _make_self({"max_tokens": 256})
        assert merge_fn(self_obj, "unknown_cat") == {"max_tokens": 256}


# ── Precedence: file overrides global ──────────────────────────────────────

class TestFileOverridesGlobal:

    def test_file_params_override_global_keys(self, merge_fn):
        self_obj = _make_self(
            global_cfg={"max_tokens": 256, "temperature": 0.7},
            file_params=_file_params(max_tokens=2048),
        )
        result = merge_fn(self_obj, "collision")
        assert result["max_tokens"] == 2048   # file wins
        assert result["temperature"] == 0.7   # global preserved


# ── Precedence: Redis overrides file overrides global ─────────────────────

class TestRedisOverridesFile:

    def _store_with(self, alert_type, vlm_params):
        store = AlertConfigStore(fakeredis.FakeRedis(decode_responses=True))
        store.set(alert_type, {"vlm_params": vlm_params})
        return store

    def test_redis_wins_over_file_and_global(self, merge_fn):
        store = self._store_with("collision", {"max_tokens": 999})
        self_obj = _make_self(
            global_cfg={"max_tokens": 256, "num_frames": 18},
            file_params=_file_params(max_tokens=2048, num_frames=10),
            redis_store=store,
        )
        result = merge_fn(self_obj, "collision")
        assert result["max_tokens"] == 999      # redis wins
        assert result["num_frames"] == 10       # from file (not in redis)

    def test_redis_partial_keeps_other_layers(self, merge_fn):
        store = self._store_with("x", {"temperature": 0.1})
        self_obj = _make_self(
            global_cfg={"max_tokens": 256, "temperature": 0.7},
            file_params=_file_params(max_tokens=1024),
            redis_store=store,
        )
        result = merge_fn(self_obj, "x")
        assert result["max_tokens"] == 1024    # file
        assert result["temperature"] == 0.1    # redis

    def test_redis_none_values_ignored(self, merge_fn):
        store = self._store_with("x", {"max_tokens": None, "num_frames": 5})
        self_obj = _make_self(
            global_cfg={"max_tokens": 256, "num_frames": 18},
            redis_store=store,
        )
        result = merge_fn(self_obj, "x")
        assert result["max_tokens"] == 256   # untouched (redis None ignored)
        assert result["num_frames"] == 5

    def test_redis_missing_alert_type_falls_through(self, merge_fn):
        store = AlertConfigStore(fakeredis.FakeRedis(decode_responses=True))
        # Redis empty
        self_obj = _make_self(
            global_cfg={"max_tokens": 256},
            redis_store=store,
        )
        assert merge_fn(self_obj, "no_such") == {"max_tokens": 256}

    def test_redis_get_exception_swallowed(self, merge_fn):
        broken_store = MagicMock()
        broken_store.get = MagicMock(side_effect=RuntimeError("boom"))
        self_obj = _make_self(
            global_cfg={"max_tokens": 256},
            redis_store=broken_store,
        )
        # Must not raise — falls back to global
        assert merge_fn(self_obj, "x") == {"max_tokens": 256}

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""Unit tests for the EVS session sampling-param conversion helpers.

These cover the vendored vLLM patch
``docker/rtvi_vlm/patches/evs_vllm_files/entrypoints/openai/engine/protocol.py``:

* ``VideoSessionSamplingParams.to_sampling_params_config`` — request model ->
  serializable SamplingParams-config dict (``exclude_none`` + ``max_tokens``
  default), the config a session persists.
* ``build_session_sampling_params`` — persisted config -> vLLM ``SamplingParams``
  at generate time (the inverse).

The installed vLLM in the container still carries the *pre-patch* protocol module
until the image is rebuilt, so the vendored file is loaded directly by path (it
only imports public vLLM APIs, which are available). This tests the patch source,
not whatever is currently installed.
"""

import importlib.util
import os
import sys

import pytest

_VENDORED_PROTOCOL = os.path.abspath(
    os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "docker",
        "rtvi_vlm",
        "patches",
        "evs_vllm_files",
        "entrypoints",
        "openai",
        "engine",
        "protocol.py",
    )
)


@pytest.fixture(scope="module")
def evs_protocol():
    """Load the vendored EVS protocol module under a private name."""
    spec = importlib.util.spec_from_file_location(
        "evs_protocol_vendored_under_test", _VENDORED_PROTOCOL
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


# --- to_sampling_params_config -----------------------------------------------


def test_to_config_defaults_max_tokens_when_omitted(evs_protocol):
    cfg = evs_protocol.VideoSessionSamplingParams().to_sampling_params_config()

    assert cfg == {"max_tokens": 1024}


def test_to_config_honors_custom_default_max_tokens(evs_protocol):
    cfg = evs_protocol.VideoSessionSamplingParams().to_sampling_params_config(
        default_max_tokens=256
    )

    assert cfg["max_tokens"] == 256


def test_to_config_keeps_explicit_max_tokens_over_default(evs_protocol):
    cfg = evs_protocol.VideoSessionSamplingParams(max_tokens=100).to_sampling_params_config()

    assert cfg["max_tokens"] == 100


def test_to_config_excludes_unset_fields(evs_protocol):
    cfg = evs_protocol.VideoSessionSamplingParams(max_tokens=100).to_sampling_params_config()

    assert "temperature" not in cfg
    assert "ignore_eos" not in cfg
    assert "min_tokens" not in cfg


def test_to_config_includes_ignore_eos_and_min_tokens_when_set(evs_protocol):
    cfg = evs_protocol.VideoSessionSamplingParams(
        max_tokens=100, ignore_eos=True, min_tokens=8
    ).to_sampling_params_config()

    assert cfg["ignore_eos"] is True
    assert cfg["min_tokens"] == 8


def test_to_config_keeps_zero_temperature(evs_protocol):
    # temperature=0.0 is meaningful (greedy) and must not be dropped by exclude_none.
    cfg = evs_protocol.VideoSessionSamplingParams(
        max_tokens=100, temperature=0.0
    ).to_sampling_params_config()

    assert cfg["temperature"] == 0.0


# --- build_session_sampling_params -------------------------------------------


def test_build_from_config_sets_all_fields(evs_protocol):
    sp = evs_protocol.build_session_sampling_params(
        {"max_tokens": 100, "ignore_eos": True, "min_tokens": 8}
    )

    assert sp.max_tokens == 100
    assert sp.ignore_eos is True
    assert sp.min_tokens == 8


def test_build_from_none_uses_default_max_tokens(evs_protocol):
    sp = evs_protocol.build_session_sampling_params(None)

    assert sp.max_tokens == 1024
    assert sp.ignore_eos is False


def test_build_from_empty_config_falls_back_to_default(evs_protocol):
    # An empty dict is falsy, so the helper substitutes the bounded default.
    sp = evs_protocol.build_session_sampling_params({})

    assert sp.max_tokens == 1024


def test_build_honors_custom_default_max_tokens(evs_protocol):
    sp = evs_protocol.build_session_sampling_params(None, default_max_tokens=512)

    assert sp.max_tokens == 512


# --- round trip ---------------------------------------------------------------


def test_round_trip_preserves_ignore_eos_and_min_tokens(evs_protocol):
    params = evs_protocol.VideoSessionSamplingParams(max_tokens=100, ignore_eos=True, min_tokens=8)

    sp = evs_protocol.build_session_sampling_params(params.to_sampling_params_config())

    assert sp.max_tokens == 100
    assert sp.ignore_eos is True
    assert sp.min_tokens == 8

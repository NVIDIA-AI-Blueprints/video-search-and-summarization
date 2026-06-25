# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import socket
import tempfile
from importlib import reload

import prometheus_client as prom
import pytest
import torch

from tests.tests_common import TempEnv


@pytest.fixture(autouse=True)
def use_temp_env():
    with TempEnv({"SKIP_PIPELINE_WARMUP": "1"}):
        yield


@pytest.fixture(autouse=True)
def reset_sse_appstatus_event():
    from sse_starlette.sse import AppStatus

    AppStatus.should_exit_event = None


@pytest.fixture(autouse=True, scope="function")
def torch_cache_clean():
    torch.cuda.empty_cache()


@pytest.fixture(autouse=True, scope="function")
def reload_trt():
    yield
    try:
        import tensorrt
        import tensorrt_libs
        import tensorrt_llm

        reload(tensorrt_llm)
        reload(tensorrt_libs)
        reload(tensorrt)

        torch.cuda.empty_cache()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def cleanup_prom_registry():
    for collector in list(prom.REGISTRY._names_to_collectors.values()):
        try:
            prom.REGISTRY.unregister(collector)
        except KeyError:
            # Handle the case where a collector is already unregistered
            pass


@pytest.fixture
def temp_asset_dir():
    with tempfile.TemporaryDirectory() as td:
        yield td


@pytest.fixture(scope="session", autouse=True)
def cleanup_rtsp_streams():
    """Cleanup all RTSP streams after test session"""
    yield
    try:
        from tests.rtsp_stream_helper import cleanup_all_streams

        cleanup_all_streams()
    except ImportError:
        pass

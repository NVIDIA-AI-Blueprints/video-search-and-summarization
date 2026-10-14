######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
######################################################################################################
"""Regression coverage for concurrent vLLM SHM multimodal serialization."""

import pickle
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import torch


def _max_concurrent_calls(call, workers: int = 8) -> int:
    active = 0
    max_active = 0
    guard = threading.Lock()

    def tracked_call():
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.01)
        with guard:
            active -= 1

    with ThreadPoolExecutor(max_workers=workers) as executor:
        list(executor.map(lambda _: call(tracked_call), range(workers)))

    return max_active


@pytest.mark.no_gpu
def test_shm_msgpack_serde_serializes_concurrent_encoder_access(monkeypatch):
    from vllm.distributed.device_communicators.shm_object_storage import MsgpackSerde

    serde = MsgpackSerde()

    def serialize(tracked_call):
        monkeypatch.setattr(
            serde.encoder,
            "encode",
            lambda _: (tracked_call(), [b"payload"])[1],
        )
        serde.serialize(torch.zeros(1))

    assert _max_concurrent_calls(serialize) == 1


@pytest.mark.no_gpu
def test_shm_msgpack_serde_serializes_concurrent_decoder_access(monkeypatch):
    from vllm.distributed.device_communicators.shm_object_storage import MsgpackSerde

    serde = MsgpackSerde()
    metadata = pickle.dumps((torch.Tensor.__name__, 1, [1]))
    payload = memoryview(metadata + b"x")

    def deserialize(tracked_call):
        monkeypatch.setattr(
            serde.tensor_decoder,
            "decode",
            lambda _: tracked_call(),
        )
        serde.deserialize(payload)

    assert _max_concurrent_calls(deserialize) == 1

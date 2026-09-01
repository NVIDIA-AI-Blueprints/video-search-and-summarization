######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
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

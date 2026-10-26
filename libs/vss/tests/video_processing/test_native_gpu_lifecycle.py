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

"""Opt-in native session settlement under close, cancellation and read failure."""

from __future__ import annotations

import asyncio
import gc
import json
import os
import threading
import weakref
from contextlib import aclosing
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_VSS_GPU_TESTS") != "1",
    reason="opt-in real PyNv session lifecycle stress",
)


@pytest.mark.asyncio
async def test_native_gpu_sessions_settle_and_permit_is_reusable(tmp_path, monkeypatch):
    from benchmarks import run as benchmark_run
    from benchmarks.gpu_resources import GPUSampler
    from benchmarks.media import file_hash, generated
    from benchmarks.processing import preflight
    from benchmarks.resources import ResourceSampler
    from benchmarks.run import software_hash
    from vss.observability import collect_vss_metrics
    from vss.video_processing import (
        DecodeError,
        DecodeOptions,
        DecodePlan,
        decode_frames,
    )
    from vss.video_processing.decoders.pynv import PyNvSession
    from vss.video_processing.execution import _limiter

    tools, _ = preflight()
    source = generated(tmp_path, "tiny", tools["ffmpeg"])
    original_read, original_close = PyNvSession.read_frames, PyNvSession.close
    sessions, payloads = [], []
    signal = threading.Event()
    release = threading.Event()
    mode = "normal"

    def read(session, count):
        if not session.initializing and mode in {"cancel", "failed_read"}:
            # Create a genuine native output before the injected read error;
            # cancellation waits for this trusted native call to settle.
            frames = original_read(session, count)
            payloads.extend(weakref.ref(frame.tensor) for frame in frames)
            signal.set()
            if mode == "failed_read":
                raise DecodeError("injected failure after native output")
            if not release.wait(2):
                raise DecodeError("native test release deadline")
            return frames
        return original_read(session, count)

    def close(session):
        original_close(session)
        if session not in sessions:
            sessions.append(session)

    monkeypatch.setattr(PyNvSession, "read_frames", read)
    monkeypatch.setattr(PyNvSession, "close", close)
    options = DecodeOptions(decoder="pynv", batch_size=1, max_concurrency=1)
    permit = _limiter(asyncio.get_running_loop(), 1)
    counts = dict.fromkeys(("normal", "early_close", "cancel", "failed_read"), 0)
    with (
        collect_vss_metrics(max_events=30000) as metrics,
        GPUSampler() as gpu,
        ResourceSampler(0.05) as resources,
    ):
        for iteration in range(32):
            selected_mode = tuple(counts)[iteration % 4]
            mode = "normal"
            signal.clear()
            stream = decode_frames(source, DecodePlan(), options=options)
            async with aclosing(stream):
                first = await asyncio.wait_for(anext(stream), 5)
                assert first.device == "gpu" and first.decoder == "pynv"
                payloads.append(weakref.ref(first.tensor))
                del first
                mode = selected_mode
                signal.clear()
                release.clear()
                if mode == "normal":
                    count = 1
                    async for frame in stream:
                        assert frame.device == "gpu" and frame.decoder == "pynv"
                        payloads.append(weakref.ref(frame.tensor))
                        count += 1
                    del frame
                    assert count == 10
                elif mode == "failed_read":
                    with pytest.raises(DecodeError, match="injected failure"):
                        await asyncio.wait_for(anext(stream), 5)
                elif mode == "cancel":
                    pending = asyncio.create_task(anext(stream))
                    assert await asyncio.wait_for(asyncio.to_thread(signal.wait, 2), 3)
                    pending.cancel()
                    release.set()
                    with pytest.raises(asyncio.CancelledError):
                        await asyncio.wait_for(pending, 5)
                    del pending
            counts[selected_mode] += 1
            assert permit._value == 1 and not permit._waiters
            # A subsequent real GPU session must acquire the same single permit.
            mode = "normal"
            async with aclosing(
                decode_frames(source, DecodePlan(), options=options)
            ) as reuse:
                first = await asyncio.wait_for(anext(reuse), 5)
                assert first.decoder == "pynv" and first.index == 0
                del first
            assert permit._value == 1 and not permit._waiters
            assert all(
                s.closed and s.decoder is None and s.demuxer is None and not s.pending
                for s in sessions
            )
            gc.collect()
            assert all(p() is None for p in payloads)
    assert metrics.complete and len(sessions) == 64
    assert gpu.trace.complete and resources.trace.complete
    output = os.getenv("M8_GPU_LIFECYCLE_OUTPUT")
    if output:
        data = dict(
            scope="short_native_session_lifecycle_stress",
            soak_30m=False,
            iterations=32,
            reuse_sessions=32,
            modes=counts,
            closed_sessions=len(sessions),
            retained_payloads=sum(p() is not None for p in payloads),
            gpu=gpu.identity.model_dump(),
            gpu_trace=gpu.trace.model_dump(),
            resources=resources.trace.model_dump(),
            media_sha256=file_hash(source),
            software_sha256=software_hash(
                Path(benchmark_run.__file__).resolve().parents[1]
            ),
            observations=len(metrics.observations),
            collection_complete=metrics.complete,
        )
        with Path(output).open("x") as target:
            json.dump(data, target, indent=2)
            target.write("\n")

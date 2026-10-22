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

"""Native resources must leave failing worker calls without relying on cyclic GC."""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest


@pytest.mark.parametrize("stage", ["factory", "open", "probe", "seek", "read", "close"])
@pytest.mark.parametrize("chained", [False, True])
async def test_failed_native_calls_release_payload_on_worker_without_gc(
    processing: Any,
    video_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    chained: bool,
) -> None:
    from vss.video_processing.decoders import registry

    references: list[weakref.ReferenceType[Any]] = []
    lifecycle: list[tuple[str, int]] = []
    failing = True

    class Payload:
        def __init__(self) -> None:
            references.append(weakref.ref(self))
            lifecycle.append(("allocate", threading.get_ident()))

        def __del__(self) -> None:
            lifecycle.append(("destroy", threading.get_ident()))

    def fail(where: str, native: Payload) -> None:
        if failing and where == stage:
            if chained:
                try:
                    raise RuntimeError("private native diagnostic", native)
                except RuntimeError as cause:
                    raise processing.DecodeError(f"failure during {where}") from cause
            raise processing.DecodeError(f"failure during {where}")

    class Session:
        def __init__(self, native: Payload) -> None:
            self.native: Payload | None = native
            self.read = False

        def probe(self) -> Any:
            native = self.native
            assert native is not None
            fail("probe", native)
            return processing.MediaFacts(
                source_id="test",
                width=2,
                height=2,
                duration_s=1,
                fps=1,
                timestamp_origin_ns=0,
            )

        def seek(self, timestamp: int) -> None:
            native = self.native
            assert native is not None
            fail("seek", native)

        def read_frames(self, batch_size: int) -> list[Any]:
            native = self.native
            assert native is not None
            fail("read", native)
            if self.read:
                return []
            self.read = True
            return [
                SimpleNamespace(
                    tensor=np.zeros((2, 2, 3), np.uint8),
                    timestamp_ns=0,
                    index=0,
                    timestamp_quality="pts",
                )
            ]

        def close(self) -> None:
            native = self.native
            self.native = None
            assert native is not None
            fail("close", native)

    class Backend:
        def capability_map(self) -> dict[str, object]:
            return {"device": "gpu"}

        def open(self, path: Path, options: Any) -> Session:
            native = Payload()
            fail("open", native)
            return Session(native)

    def factory() -> Backend:
        if stage == "factory":
            native = Payload()
            fail("factory", native)
        return Backend()

    monkeypatch.setattr(registry, "_DECODER_REGISTRY", {"memory": factory})

    async def attempt() -> None:
        try:
            async for _ in processing.decode_frames(
                video_path,
                processing.DecodePlan(),
                options=processing.DecodeOptions(decoder="memory", max_concurrency=1),
            ):
                pass
        except processing.DecodeError:
            pass
        else:
            pytest.fail("the selected native stage did not fail")

    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for _ in range(20):
            await attempt()
            assert references and all(ref() is None for ref in references)
        allocated = [tid for op, tid in lifecycle if op == "allocate"]
        destroyed = [tid for op, tid in lifecycle if op == "destroy"]
        assert allocated == destroyed
        assert threading.get_ident() not in destroyed
        failing = False
        async with asyncio.timeout(3):
            frames = [
                f
                async for f in processing.decode_frames(
                    video_path,
                    processing.DecodePlan(),
                    options=processing.DecodeOptions(
                        decoder="memory", max_concurrency=1
                    ),
                )
            ]
        assert len(frames) == 1
    finally:
        if enabled:
            gc.enable()
        gc.collect()


async def test_cancelled_open_failure_does_not_fall_back(
    processing: Any,
    video_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vss.video_processing.decoders import registry

    from .fakes import MemoryBackend

    entered, release = threading.Event(), threading.Event()
    cpu = MemoryBackend(processing, name="opencv")

    class GPU:
        def open(self, path: Path, options: Any) -> Any:
            entered.set()
            assert release.wait(3)
            raise processing.DecoderUnavailableError("device failed after cancellation")

    monkeypatch.setattr(
        registry, "_DECODER_REGISTRY", {"pynv": GPU, "opencv": lambda: cpu}
    )
    task = asyncio.create_task(processing.extract_frames(video_path, [0]))
    try:
        assert await asyncio.to_thread(entered.wait, 3)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not cpu.open_paths


@pytest.mark.parametrize("broken_format", [False, True])
async def test_worker_diagnostics_cannot_transport_native_string_payload(
    processing: Any, broken_format: bool
) -> None:
    from vss.video_processing.execution import _Failure, _settle, _Worker

    refs = []
    allocated, destroyed = [], []

    class Payload:
        def __init__(self) -> None:
            refs.append(weakref.ref(self))
            allocated.append(threading.get_ident())

        def __del__(self) -> None:
            destroyed.append(threading.get_ident())

    class Diagnostic(str):
        pass

    class NativeError(processing.DecodeError):
        def __str__(self) -> str:
            native = self.args[0]
            if broken_format:
                raise RuntimeError("cannot format diagnostic", native)
            message = Diagnostic("typed diagnostic")
            message.native = native
            return message

    def fail() -> None:
        native = Payload()
        raise NativeError(native)

    worker = _Worker()
    enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        for _ in range(20):
            result, cancelled = await _settle(worker.submit(fail))
            assert isinstance(result, _Failure) and not cancelled
            assert type(result.category) is str and type(result.message) is str
            assert result.category == "DecodeError"
            assert all(ref() is None for ref in refs)
        assert allocated == destroyed
        assert threading.get_ident() not in destroyed
    finally:
        await worker.close_executor()
        if enabled:
            gc.enable()
        gc.collect()

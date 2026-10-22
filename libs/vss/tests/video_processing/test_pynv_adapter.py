# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Real PyNv adapter against the documented external PyNvVideoCodec API.

The NVIDIA 2.2.3 source archive exposes DecodedFrame.timestamp in the video's
AVStream.time_base ticks, CreateDemuxer timebase getters, and CPU DLPack for
host RGB output. Only that external module and platform are replaced here;
public execution and the built-in PyNv backend perform all clock conversion,
selection, ownership, and fallback. This does not establish real GPU support.
"""

from __future__ import annotations

import asyncio
import gc
import platform
import sys
import threading
import time
import weakref
from collections.abc import Iterator
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import numpy as np
import pytest

from .fakes import MemoryBackend


def test_cuda_ipc_decoder_arguments_select_device_nv12(processing: Any) -> None:
    from vss.video_processing import DecodeOptions
    from vss.video_processing.decoders.pynv import _decoder_arguments

    nvc = SimpleNamespace(
        OutputColorType=SimpleNamespace(RGB="rgb", NV12="nv12"),
        DisplayDecodeLatencyType=SimpleNamespace(NATIVE="native"),
    )

    arguments = _decoder_arguments(
        nvc,
        "h264",
        DecodeOptions(decoder="pynv", frame_transport="cuda_ipc"),
    )

    assert arguments["usedevicememory"] is True
    assert arguments["outputColorType"] == "nv12"


@pytest.fixture
def native_api(processing: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from vss.video_processing.decoders import pynv, registry
    from vss.video_processing.decoders.pynv import PyNvBackend

    state = SimpleNamespace(
        numerator=1,
        denominator=51_200,
        origin=256_000,
        offsets=(0, 2_048, 9_728, 35_840),
        timebase_available=True,
        constructors=[],
        demux_paths=[],
        decoded_frames=[],
        constructor_error=None,
        demux_error=None,
        metadata_error=None,
        first_timestamp_available=True,
        decoder_refs=[],
        lifecycle=[],
        packet_calls=[],
        raw_constructors=[],
        raw_timestamp_available=True,
        recycle=False,
        legacy_dts=True,
        native_duration=1.0,
        reported_fps=4.0,
        raw_error=None,
        codec="h264",
        color_space=5,
        color_range=0,
        decode_order=None,
        delay_until_eof=False,
        missing_packet_pts=False,
        packet_duration=None,
        track_overlap=False,
        active_calls=0,
        maximum_calls=0,
        native_operations=[],
        call_lock=threading.Lock(),
    )
    rgb = object()

    @contextmanager
    def native_call(operation: str) -> Iterator[None]:
        if not state.track_overlap:
            yield
            return
        with state.call_lock:
            state.native_operations.append(operation)
            state.active_calls += 1
            state.maximum_calls = max(state.maximum_calls, state.active_calls)
        try:
            time.sleep(0.02)
            yield
        finally:
            with state.call_lock:
                state.active_calls -= 1

    class HostFrame:
        def __init__(self, index: int) -> None:
            self.timestamp = state.origin + state.offsets[index]
            if state.legacy_dts:
                self.timestamp = state.origin + index * 2048 - 4096
            self.pixels = np.empty((2, 4, 3), dtype=np.uint8)
            self.pixels[:] = (170 + index, 60 + index, 10 + index)
            self.shape = self.pixels.shape
            state.decoded_frames.append(self)

        def __dlpack_device__(self) -> tuple[int, int]:
            return (1, 0)  # DLPack kDLCPU, not a CUDA allocation.

        def __dlpack__(self, **kwargs: Any) -> Any:
            return self.pixels.__dlpack__(**kwargs)

    class SimpleDecoder:
        def __init__(
            self,
            enc_file_path: str,
            *,
            gpu_id: int = 0,
            use_device_memory: bool = True,
            output_color_type: Any = None,
            **kwargs: Any,
        ) -> None:
            state.constructors.append(
                (enc_file_path, gpu_id, use_device_memory, output_color_type)
            )
            if state.constructor_error is not None:
                raise state.constructor_error
            state.decoder_refs.append(weakref.ref(self))
            state.lifecycle.append(("open", threading.get_ident()))
            self.frames = [HostFrame(index) for index in range(len(state.offsets))]
            self.cursor = 0

        def get_stream_metadata(self) -> Any:
            if state.metadata_error is not None:
                raise state.metadata_error
            return SimpleNamespace(
                width=4,
                height=2,
                num_frames=len(self.frames),
                average_fps=state.reported_fps,
                duration=state.native_duration,
                codec_name="h264",
            )

        def __len__(self) -> int:
            return len(self.frames)

        def __getitem__(self, index: int) -> HostFrame:
            self.cursor = index + 1
            if not state.first_timestamp_available:
                self.frames[index].timestamp = None
            return self.frames[index]

        def seek_to_index(self, index: int) -> None:
            if not 0 <= index < len(self.frames):
                raise IndexError(index)
            self.cursor = index

        def get_index_from_time_in_seconds(self, seconds: float) -> int:
            # The old convenience seek is deliberately untrusted on VFR:
            # it maps seconds using the average rate rather than observed PTS.
            if state.reported_fps is None:
                raise ValueError("no reported FPS")
            return min(int(seconds * state.reported_fps), len(self.frames) - 1)

        def get_batch_frames(self, batch_size: int) -> list[HostFrame]:
            result = self.frames[self.cursor : self.cursor + batch_size]
            self.cursor += len(result)
            return result

        def get_batch_frames_by_index(self, indices: list[int]) -> list[HostFrame]:
            return [self.frames[index] for index in sorted(set(indices))]

        def __del__(self) -> None:
            state.lifecycle.append(("destroy", threading.get_ident()))

    class RawDecoder:
        def __init__(self, **kwargs: Any) -> None:
            with native_call("create"):
                state.raw_constructors.append(kwargs)
                state.constructors.append(
                    (
                        "raw",
                        kwargs["gpuid"],
                        kwargs["usedevicememory"],
                        kwargs["outputColorType"],
                    )
                )
                if state.constructor_error is not None:
                    raise state.constructor_error
                state.decoder_refs.append(weakref.ref(self))
                state.lifecycle.append(("open", threading.get_ident()))
                self.pending: dict[int, HostFrame] = {}
                self.index = 0
                self.previous: list[HostFrame] = []

        def Decode(self, packet: Any) -> list[HostFrame]:
            with native_call("decode"):
                return self._decode(packet)

        def _decode(self, packet: Any) -> list[HostFrame]:
            state.packet_calls.append((packet.index, packet.pts, packet.bsl))
            if state.raw_error is not None:
                raise state.raw_error
            if state.recycle:
                for frame in self.previous:
                    frame.pixels.fill(0)
            if packet.bsl:
                frame = HostFrame(packet.index)
                frame.timestamp = (
                    packet.pts if state.raw_timestamp_available else -(2**63)
                )
                if not state.first_timestamp_available and packet.index == 0:
                    frame.timestamp = None
                self.pending[packet.index] = frame
            frames = []
            while self.index in self.pending and (
                not state.delay_until_eof or packet.bsl == 0
            ):
                frames.append(self.pending.pop(self.index))
                self.index += 1
            self.previous = frames
            return frames

        def __del__(self) -> None:
            with native_call("release"):
                state.lifecycle.append(("destroy", threading.get_ident()))

    class Demuxer:
        def GetTimebaseNum(self) -> int:
            return state.numerator

        def GetTimebaseDen(self) -> int:
            return state.denominator

        def GetNvCodecId(self) -> str:
            return state.codec

        def ColorSpace(self) -> int:
            return state.color_space

        def ColorRange(self) -> int:
            return state.color_range

        def Width(self) -> int:
            if state.metadata_error is not None:
                raise state.metadata_error
            return 4

        def Height(self) -> int:
            return 2

        def FrameRate(self) -> float | None:
            return state.reported_fps

        def AvgFrameRateNum(self) -> int:
            return int(state.reported_fps or 0)

        def AvgFrameRateDen(self) -> int:
            return 1

        def __iter__(self) -> Any:
            packet = SimpleNamespace()
            order = state.decode_order or (
                [0, 2, 1, 3]
                if len(state.offsets) == 4
                else list(range(len(state.offsets)))
            )
            for index in order:
                packet.index = index
                packet.pts = (
                    -(2**63)
                    if state.missing_packet_pts
                    else state.origin + state.offsets[index]
                )
                packet.dts = state.origin + index * 2048 - 4096
                packet.bsl = 1
                if state.packet_duration is not None:
                    packet.duration = state.packet_duration
                yield packet
            packet.index, packet.pts, packet.dts, packet.bsl = -1, 0, 0, 0
            yield packet

    def create_demuxer(filename: str) -> Any:
        state.demux_paths.append(filename)
        if state.demux_error is not None:
            raise state.demux_error
        if not state.timebase_available:
            return SimpleNamespace()
        return Demuxer()

    module = ModuleType("PyNvVideoCodec")
    module.PyNvVCExceptionUnsupported = type(
        "PyNvVCExceptionUnsupported", (RuntimeError,), {}
    )
    module.__version__ = "2.2.3"
    module.ColorSpace = SimpleNamespace(BT_709=1)
    module.ColorRange = SimpleNamespace(MPEG=0, JPEG=1)
    module.SimpleDecoder = SimpleDecoder
    module.CreateDecoder = RawDecoder
    module.DisplayDecodeLatencyType = SimpleNamespace(NATIVE="display-order")
    module.CreateDemuxer = create_demuxer
    module.OutputColorType = SimpleNamespace(RGB=rgb)
    monkeypatch.setitem(sys.modules, "PyNvVideoCodec", module)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(pynv, "_check_cuda_device", lambda device: None, raising=False)
    cpu = MemoryBackend(processing, name="opencv", device="cpu")
    monkeypatch.setattr(
        registry,
        "_DECODER_REGISTRY",
        {"pynv": PyNvBackend, "opencv": lambda: cpu},
    )
    state.cpu = cpu
    state.rgb = rgb
    state.module = module
    return state


@pytest.mark.parametrize("phase", ["demux", "constructor", "metadata"])
async def test_gpu_initialization_corruption_does_not_retry_cpu(
    processing: Any, native_api: Any, video_path: Path, phase: str
) -> None:
    setattr(native_api, f"{phase}_error", RuntimeError("Invalid data found in input"))
    with pytest.raises(processing.DecodeError):
        await processing.extract_frames(video_path, [0])
    assert not native_api.cpu.open_paths


@pytest.mark.parametrize(
    "message", ["CUDA_ERROR_NO_DEVICE", "GPU ordinal out of range"]
)
async def test_known_gpu_driver_unavailability_allows_cpu_fallback(
    processing: Any, native_api: Any, video_path: Path, message: str
) -> None:
    native_api.constructor_error = RuntimeError(message)
    frames = await processing.extract_frames(video_path, [0])
    assert frames[0].decoder == "opencv"
    assert frames[0].fallback_reason


async def test_native_unsupported_gpu_format_allows_cpu_fallback(
    processing: Any, native_api: Any, video_path: Path
) -> None:
    native_api.constructor_error = native_api.module.PyNvVCExceptionUnsupported(
        "No supported output format found"
    )
    frames = await processing.extract_frames(video_path, [0])
    assert frames[0].decoder == "opencv"


@pytest.mark.parametrize("phase", ["first_timestamp", "metadata"])
async def test_failed_gpu_initialization_releases_resources_on_opening_worker(
    processing: Any, native_api: Any, video_path: Path, phase: str
) -> None:
    gc.collect()
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        if phase == "first_timestamp":
            native_api.first_timestamp_available = False
            with pytest.raises(processing.TimestampUnavailableError):
                await processing.extract_frames(video_path, [0])
            assert not native_api.cpu.open_paths
        else:
            native_api.metadata_error = RuntimeError("Invalid stream metadata")
            with pytest.raises(processing.DecodeError):
                await processing.extract_frames(video_path, [0])
        assert native_api.decoder_refs
        assert all(ref() is None for ref in native_api.decoder_refs)
        opening_thread = next(
            thread for operation, thread in native_api.lifecycle if operation == "open"
        )
        assert opening_thread != threading.get_ident()
        assert ("destroy", opening_thread) in native_api.lifecycle
    finally:
        if was_enabled:
            gc.enable()


@pytest.mark.parametrize("decoder", ["auto", "pynv"])
async def test_failed_cuda_preflight_never_constructs_native_decoder(
    processing: Any,
    native_api: Any,
    video_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    decoder: str,
) -> None:
    from vss.video_processing.decoders import pynv

    def unavailable(device: int) -> None:
        raise processing.DecoderUnavailableError("CUDA driver unavailable")

    monkeypatch.setattr(pynv, "_check_cuda_device", unavailable)
    if decoder == "auto":
        frames = await processing.extract_frames(video_path, [0])
        assert frames[0].decoder == "opencv"
    else:
        with pytest.raises(processing.DecoderUnavailableError, match="CUDA"):
            await processing.extract_frames(
                video_path, [0], options=processing.DecodeOptions(decoder=decoder)
            )
        assert not native_api.cpu.open_paths
    assert not native_api.constructors


@pytest.mark.parametrize(
    "numerator,denominator,origin,offsets,expected_ns",
    [
        (
            1,
            51_200,
            256_000,
            (0, 2_048, 9_728, 35_840),
            (0, 40_000_000, 190_000_000, 700_000_000),
        ),
        (3, 1_000, -1_000, (0, 1, 3, 10), (0, 3_000_000, 9_000_000, 30_000_000)),
    ],
)
async def test_builtin_pynv_rescales_raw_ticks_and_returns_owned_host_rgb(
    processing: Any,
    native_api: Any,
    video_path: Path,
    numerator: int,
    denominator: int,
    origin: int,
    offsets: tuple[int, ...],
    expected_ns: tuple[int, ...],
) -> None:
    native_api.numerator, native_api.denominator = numerator, denominator
    native_api.origin, native_api.offsets = origin, offsets
    frames = [
        frame
        async for frame in processing.decode_frames(
            video_path,
            processing.DecodePlan(),
            source_id="original-video",
            options=processing.DecodeOptions(decoder="pynv", device_id=2, batch_size=2),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == list(expected_ns)
    assert all(frame.timestamp_quality == "pts" for frame in frames)
    assert all(frame.source_id == "original-video" for frame in frames)
    assert all(frame.decoder == "pynv" and frame.device == "gpu" for frame in frames)
    assert native_api.constructors
    for path, device, use_device_memory, color in native_api.constructors:
        assert path == "raw"
        assert device == 2
        assert use_device_memory is False
        assert color is native_api.rgb
    assert native_api.demux_paths
    assert all(Path(path) == video_path.resolve() for path in native_api.demux_paths)
    # Destroy native host buffers after the session has closed. Returned frames
    # must retain channel order, ownership and read-only public storage.
    for raw_frame in native_api.decoded_frames:
        raw_frame.pixels.fill(0)
    for index, frame in enumerate(frames):
        assert frame.tensor.shape == (2, 4, 3)
        assert frame.tensor.dtype == np.uint8
        assert frame.tensor.flags.c_contiguous
        assert not frame.tensor.flags.writeable
        np.testing.assert_array_equal(
            frame.tensor[0, 0], (170 + index, 60 + index, 10 + index)
        )
    assert native_api.cpu.sessions == []


async def test_builtin_pynv_vfr_selection_uses_rescaled_pts_not_average_fps(
    processing: Any, native_api: Any, video_path: Path
) -> None:
    frames = [
        frame
        async for frame in processing.decode_frames(
            video_path,
            processing.DecodePlan(sample_timestamps_ns=(50_000_000, 200_000_000)),
            options=processing.DecodeOptions(decoder="pynv"),
        )
    ]
    assert [frame.timestamp_ns for frame in frames] == [190_000_000, 700_000_000]
    assert [frame.requested_timestamp_ns for frame in frames] == [
        50_000_000,
        200_000_000,
    ]
    assert all(frame.timestamp_quality == "pts" for frame in frames)
    # Fixture sanity: this is five seconds of nonzero origin in raw ticks.
    assert (
        Fraction(native_api.origin * native_api.numerator, native_api.denominator) == 5
    )


async def test_missing_pynv_timebase_uses_typed_availability_fallback(
    processing: Any, native_api: Any, video_path: Path
) -> None:
    native_api.timebase_available = False
    frames = [
        frame
        async for frame in processing.decode_frames(video_path, processing.DecodePlan())
    ]
    assert frames
    assert all(frame.decoder == "opencv" and frame.device == "cpu" for frame in frames)
    assert all(frame.fallback_reason for frame in frames)
    assert native_api.cpu.sessions
    assert native_api.cpu.active_sessions == 0


async def test_explicit_pynv_without_timebase_fails_instead_of_guessing_units(
    processing: Any, native_api: Any, video_path: Path
) -> None:
    native_api.timebase_available = False
    with pytest.raises(processing.DecoderUnavailableError):
        _ = [
            frame
            async for frame in processing.decode_frames(
                video_path,
                processing.DecodePlan(),
                options=processing.DecodeOptions(decoder="pynv"),
            )
        ]
    assert not native_api.cpu.open_paths


def test_builtin_pynv_probe_does_not_claim_container_from_filename(
    processing: Any, native_api: Any, video_path: Path
) -> None:
    from vss.video_processing.decoders.pynv import PyNvBackend

    misleading_path = video_path.with_suffix(".mkv")
    misleading_path.write_bytes(video_path.read_bytes())
    session = PyNvBackend().open(
        misleading_path, processing.DecodeOptions(decoder="pynv")
    )
    try:
        facts = session.probe()
        assert facts.codec == "h264"
        assert facts.container is None
        assert facts.audio is None
        assert any("audio/container" in warning for warning in facts.warnings)
    finally:
        session.close()


async def test_native_metadata_field_names_are_public_facts(
    processing, native_api, video_path
):
    from vss.video_processing.decoders.pynv import PyNvBackend

    session = PyNvBackend().open(video_path, processing.DecodeOptions(decoder="pynv"))
    try:
        facts = session.probe()
        assert facts.fps == 4.0
        assert facts.codec == "h264"
    finally:
        session.close()


async def test_pynv_probe_does_not_scan_packet_timeline(
    processing, native_api, video_path
):
    from vss.video_processing.decoders.pynv import PyNvBackend

    native_api.packet_duration = 2_048
    session = PyNvBackend().open(video_path, processing.DecodeOptions(decoder="pynv"))
    try:
        demux_count = len(native_api.demux_paths)
        first = session.probe()
        second = session.probe()
        assert first.duration_s is None
        assert second.duration_quality == "unknown"
        assert len(native_api.demux_paths) == demux_count
    finally:
        session.close()


async def test_packet_pts_are_not_legacy_dts(processing, native_api, video_path):
    native_api.legacy_dts = True
    frames = [
        frame
        async for frame in processing.decode_frames(
            video_path,
            processing.DecodePlan(),
            options=processing.DecodeOptions(decoder="pynv", batch_size=2),
        )
    ]
    assert [f.timestamp_ns for f in frames] == [0, 40_000_000, 190_000_000, 700_000_000]
    assert [f.index for f in frames] == [0, 1, 2, 3]
    assert native_api.raw_constructors


@pytest.mark.parametrize("window", [False, True])
async def test_unverified_duration_does_not_drop_final_frame(
    processing, native_api, video_path, window
):
    native_api.native_duration = 0.7
    plan = (
        processing.DecodePlan(window=processing.TimeWindow(end_ns=800_000_000))
        if window
        else processing.DecodePlan()
    )
    frames = [
        f
        async for f in processing.decode_frames(
            video_path, plan, options=processing.DecodeOptions(decoder="pynv")
        )
    ]
    assert [f.index for f in frames] == [0, 1, 2, 3]
    last = await processing.extract_frames(
        video_path, [0.7], options=processing.DecodeOptions(decoder="pynv")
    )
    assert last[0].timestamp_ns == 700_000_000 and last[0].index == 3


async def test_raw_decode_does_not_require_fps_and_owns_recycled_batches(
    processing, native_api, video_path
):
    native_api.reported_fps = None
    native_api.recycle = True
    frames = [
        f
        async for f in processing.decode_frames(
            video_path,
            processing.DecodePlan(),
            options=processing.DecodeOptions(decoder="pynv", batch_size=3),
        )
    ]
    assert len(frames) == 4
    for index, frame in enumerate(frames):
        np.testing.assert_array_equal(
            frame.tensor[0, 0], (170 + index, 60 + index, 10 + index)
        )
    assert native_api.packet_calls[-1][-1] == 0
    assert sum(size == 0 for _, _, size in native_api.packet_calls) == 1


async def test_raw_missing_pts_is_not_fabricated(processing, native_api, video_path):
    native_api.raw_timestamp_available = False
    with pytest.raises(processing.TimestampUnavailableError):
        await processing.extract_frames(
            video_path,
            [0],
            options=processing.DecodeOptions(decoder="pynv", timestamp_mode="strict"),
        )


@pytest.mark.parametrize("decoder", ["auto", "pynv"])
async def test_unsupported_first_packet_is_availability(
    processing, native_api, video_path, decoder
):
    native_api.raw_error = native_api.module.PyNvVCExceptionUnsupported(
        "unsupported source format"
    )
    if decoder == "auto":
        frames = await processing.extract_frames(video_path, [0])
        assert frames[0].decoder == "opencv" and frames[0].fallback_reason
    else:
        with pytest.raises(processing.DecoderUnavailableError):
            await processing.extract_frames(
                video_path, [0], options=processing.DecodeOptions(decoder=decoder)
            )
        assert not native_api.cpu.open_paths


async def test_unsupported_after_output_is_decode_failure(
    processing, native_api, video_path
):
    stream = processing.decode_frames(
        video_path,
        processing.DecodePlan(),
        options=processing.DecodeOptions(batch_size=1),
    )
    first = await anext(stream)
    assert first.decoder == "pynv"
    native_api.raw_error = native_api.module.PyNvVCExceptionUnsupported(
        "unsupported source format"
    )
    with pytest.raises(processing.DecodeError):
        await anext(stream)
    assert not native_api.cpu.open_paths


async def test_corrupt_first_packet_is_not_availability(
    processing, native_api, video_path
):
    native_api.raw_error = RuntimeError("corrupt packet")
    with pytest.raises(processing.DecodeError):
        await processing.extract_frames(video_path, [0])
    assert not native_api.cpu.open_paths


async def test_delayed_frames_are_flushed_once_at_eof(
    processing, native_api, video_path
):
    native_api.delay_until_eof = True
    frames = [
        f
        async for f in processing.decode_frames(
            video_path,
            processing.DecodePlan(),
            options=processing.DecodeOptions(decoder="pynv", batch_size=2),
        )
    ]
    assert [f.index for f in frames] == [0, 1, 2, 3]
    assert [f.timestamp_ns for f in frames] == [0, 40_000_000, 190_000_000, 700_000_000]
    assert sum(size == 0 for _, _, size in native_api.packet_calls) == 1


async def test_missing_packet_pts_rejected_before_native_decode(
    processing, native_api, video_path
):
    native_api.missing_packet_pts = True
    with pytest.raises(processing.TimestampUnavailableError):
        await processing.extract_frames(
            video_path, [0], options=processing.DecodeOptions(decoder="pynv")
        )
    assert native_api.packet_calls == []


@pytest.mark.parametrize("decoder", ["auto", "pynv"])
async def test_known_native_vp9_color_path_is_unavailable(
    processing, native_api, video_path, decoder
):
    native_api.codec, native_api.color_space, native_api.color_range = "VP9", 1, 0
    if decoder == "auto":
        frames = await processing.extract_frames(video_path, [0])
        assert frames[0].decoder == "opencv" and "color" in frames[0].fallback_reason
    else:
        with pytest.raises(processing.DecoderUnavailableError, match="color"):
            await processing.extract_frames(
                video_path, [0], options=processing.DecodeOptions(decoder="pynv")
            )
    assert not native_api.raw_constructors


@pytest.mark.parametrize("decoder", ["auto", "pynv"])
@pytest.mark.parametrize("api_state", ["absent", "noncallable"])
async def test_missing_demuxer_api_is_availability_failure(
    processing, native_api, video_path, monkeypatch, decoder, api_state
):
    if api_state == "absent":
        monkeypatch.delattr(native_api.module, "CreateDemuxer")
    else:
        monkeypatch.setattr(native_api.module, "CreateDemuxer", None)
    if decoder == "auto":
        frames = await processing.extract_frames(video_path, [0])
        assert frames[0].decoder == "opencv" and frames[0].fallback_reason
    else:
        with pytest.raises(processing.DecoderUnavailableError):
            await processing.extract_frames(
                video_path, [0], options=processing.DecodeOptions(decoder=decoder)
            )
        assert not native_api.cpu.open_paths
    assert not native_api.raw_constructors


@pytest.mark.parametrize("decoder", ["auto", "pynv"])
@pytest.mark.parametrize("api_state", ["absent", "none", "noncallable"])
async def test_missing_decoder_api_is_availability_failure(
    processing, native_api, video_path, monkeypatch, decoder, api_state
):
    if api_state == "absent":
        monkeypatch.delattr(native_api.module, "CreateDecoder")
    else:
        monkeypatch.setattr(
            native_api.module, "CreateDecoder", None if api_state == "none" else 0
        )
    message = "PyNvVideoCodec packet decoder API is unavailable"
    if decoder == "auto":
        frames = await processing.extract_frames(video_path, [0])
        assert frames[0].decoder == "opencv"
        assert message in frames[0].fallback_reason
    else:
        with pytest.raises(processing.DecoderUnavailableError, match=message):
            await processing.extract_frames(
                video_path, [0], options=processing.DecodeOptions(decoder=decoder)
            )
        assert not native_api.cpu.open_paths
    assert not native_api.raw_constructors
    assert not native_api.decoder_refs


@pytest.mark.parametrize(
    "version,codec,matrix,range_id",
    [
        ("2.2.4", "VP9", 1, 0),
        ("2.2.3", "h264", 1, 0),
        ("2.2.3", "hevc", 1, 0),
        ("2.2.3", "VP9", 5, 0),
        ("2.2.3", "VP9", 1, 1),
    ],
)
async def test_vp9_color_guard_does_not_match_other_native_paths(
    processing, native_api, video_path, version, codec, matrix, range_id
):
    native_api.module.__version__ = version
    native_api.codec, native_api.color_space, native_api.color_range = (
        codec,
        matrix,
        range_id,
    )
    frames = await processing.extract_frames(
        video_path, [0], options=processing.DecodeOptions(decoder="pynv")
    )
    assert frames[0].decoder == "pynv"


@pytest.mark.parametrize("system,machine", [("Windows", "AMD64"), ("Linux", "aarch64")])
async def test_vp9_color_guard_does_not_match_other_platforms(
    processing, native_api, video_path, monkeypatch, system, machine
):
    native_api.codec, native_api.color_space, native_api.color_range = "VP9", 1, 0
    monkeypatch.setattr(platform, "system", lambda: system)
    monkeypatch.setattr(platform, "machine", lambda: machine)
    frames = await processing.extract_frames(
        video_path, [0], options=processing.DecodeOptions(decoder="pynv")
    )
    assert frames[0].decoder == "pynv"
    assert not native_api.cpu.open_paths


async def test_native_operations_are_serialized_across_concurrent_sessions(
    processing, native_api, video_path
):
    native_api.track_overlap = True

    async def consume():
        return [
            f
            async for f in processing.decode_frames(
                video_path,
                processing.DecodePlan(),
                options=processing.DecodeOptions(decoder="pynv", max_concurrency=2),
            )
        ]

    left, right = await asyncio.gather(consume(), consume())
    assert len(left) == len(right) == 4
    assert native_api.maximum_calls == 1
    assert set(native_api.native_operations) == {"create", "decode", "release"}


async def test_native_reset_and_close_serialize_concurrent_sessions(
    processing, native_api, video_path
):
    from vss.video_processing.decoders.pynv import PyNvBackend
    from vss.video_processing.execution import _native, _Worker

    workers = [_Worker(), _Worker()]
    sessions = []
    try:
        for worker in workers:
            sessions.append(
                await _native(
                    worker,
                    PyNvBackend().open,
                    video_path,
                    processing.DecodeOptions(decoder="pynv"),
                )
            )
        # First positioning retains primed output; subsequent positioning
        # releases/recreates native objects on the same owning workers.
        for worker, session in zip(workers, sessions, strict=True):
            await _native(worker, session.seek, 0)
        native_api.track_overlap = True
        await asyncio.gather(
            *(
                _native(worker, session.seek, 0)
                for worker, session in zip(workers, sessions, strict=True)
            )
        )
        assert native_api.maximum_calls == 1
        assert set(native_api.native_operations) == {"create", "release"}
        await asyncio.gather(
            *(
                _native(worker, session.close)
                for worker, session in zip(workers, sessions, strict=True)
            )
        )
        assert native_api.maximum_calls == 1
        assert all(ref() is None for ref in native_api.decoder_refs)
    finally:
        for worker, session in zip(workers, sessions, strict=False):
            await _native(worker, session.close)
        for worker in workers:
            await worker.close_executor()

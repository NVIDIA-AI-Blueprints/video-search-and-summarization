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

import sys
import threading
import types
from types import SimpleNamespace

import pytest
import torch

from common.chunk_info import ChunkInfo
from vlm_pipeline import vlm_pipeline as vlm_pipeline_module
from vlm_pipeline.cuda_frame_ring import CudaFrameRing
from vlm_pipeline.vlm_pipeline import DecoderProcess


class ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        return fn(*args, **kwargs)


class CaptureQueue:
    def __init__(self):
        self.items = []

    def put(self, item):
        self.items.append(item)


class FakeDefaultFrameSelector:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class FlakyFrameGetter:
    def __init__(self):
        self.calls = 0
        self.destroyed = 0
        self.flushed = 0

    def get_frames(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return [], [], [], "qtdemux not-linked"
        return ["frame"], [1.234], [], None

    def destroy_pipeline(self):
        self.destroyed += 1

    def flush_pipeline(self):
        self.flushed += 1


class CleanFrameGetter(FlakyFrameGetter):
    def get_frames(self, *args, **kwargs):
        self.calls += 1
        return ["frame"], [1.234], [], None


class FakeLiveFrameGetter:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.stream_kwargs = None
        self.destroyed = 0

    def stream(self, **kwargs):
        self.stream_kwargs = kwargs

    def destroy_pipeline(self):
        self.destroyed += 1


class OomFrameGetter(FlakyFrameGetter):
    def get_frames(self, *args, **kwargs):
        self.calls += 1
        raise torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 20.00 MiB.")


class EmptyFrameGetter(FlakyFrameGetter):
    def get_frames(self, *args, **kwargs):
        self.calls += 1
        return [], [], [], None


class UnderfilledFixedFrameGetter(FlakyFrameGetter):
    def get_frames(self, *args, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return ["frame"], [1.234], [], None
        return ["frame"] * 30, [float(i) for i in range(30)], [], None


def _install_fake_frame_selector(monkeypatch):
    fake_frame_getter_module = types.ModuleType("vlm_pipeline.video_file_frame_getter")
    fake_frame_getter_module.DefaultFrameSelector = FakeDefaultFrameSelector
    monkeypatch.setitem(
        sys.modules,
        "vlm_pipeline.video_file_frame_getter",
        fake_frame_getter_module,
    )


def _make_decoder():
    decoder = DecoderProcess.__new__(DecoderProcess)
    decoder._nfrms = 1
    decoder._use_fps_for_chunking = False
    decoder._minframes = 1
    decoder._width = 0
    decoder._height = 0
    decoder._enable_jpeg_tensors = False
    decoder._file_thread_pool = ImmediateExecutor()
    decoder._fgetters = []
    decoder._fgetter_handoff_lock = threading.Lock()
    return decoder


def _make_live_decoder():
    decoder = _make_decoder()
    decoder._nfrms = 3
    decoder._use_fps_for_chunking = True
    decoder._live_stream_handle_info = {}
    decoder._live_stream_handle_info_lock = threading.Lock()
    decoder._final_output_queue = CaptureQueue()
    decoder._width = 608
    decoder._height = 320
    decoder._do_preprocess = False
    decoder._image_mean = None
    decoder._rescale_factor = None
    decoder._image_std = None
    decoder._crop_height = 0
    decoder._crop_width = 0
    decoder._shortest_edge = 0
    decoder._image_aspect_ratio = None
    decoder._enable_jpeg_tensors = False
    decoder._data_type_int8 = False
    decoder._enable_audio = False
    decoder._ipc_frame_copy = False
    decoder._ipc_socket_dir = "/run/rtvi-ipc"
    decoder._ipc_socket_template = "nvds_ipc_{camera_id}.sock"
    decoder._cuda_frame_ring = CudaFrameRing(max_bytes=1024)
    return decoder


def _make_vlm_query():
    return SimpleNamespace(
        num_frames_per_second_or_fixed_frames_chunk=None,
        use_fps_for_chunking=False,
        enable_audio=False,
        chunk_duration=10,
        chunk_overlap_duration=0,
        vlm_input_width=None,
        vlm_input_height=None,
    )


@pytest.mark.no_gpu
def test_decoder_warmup_decodes_locally_without_forwarding_frames(monkeypatch):
    class WarmupFrameGetter:
        def __init__(self):
            self.files = []

        def get_frames(self, chunk):
            self.files.append(chunk.file)
            return ["cuda-frame"], [0.0], [], None

    decoder = _make_decoder()
    decoder._fgetters = [WarmupFrameGetter(), WarmupFrameGetter()]
    decoder._output_queue = CaptureQueue()

    monkeypatch.setattr(vlm_pipeline_module.os.path, "exists", lambda path: True)

    decoder._warmup()

    expected_files = [
        "/opt/nvidia/rtvi/warmup_streams/its_264.mp4",
        "/opt/nvidia/rtvi/warmup_streams/its_265.mp4",
    ]
    assert decoder._fgetters[0].files == expected_files
    assert decoder._fgetters[1].files == expected_files
    assert decoder._output_queue.items == []


@pytest.mark.no_gpu
def test_decode_chunk_retries_frame_extraction_error_and_resets_pipeline(monkeypatch):
    _install_fake_frame_selector(monkeypatch)
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)
    monkeypatch.setenv("RTVI_DECODE_MAX_ATTEMPTS", "2")
    monkeypatch.delenv("RTVI_REUSE_FILE_DECODER_PIPELINE", raising=False)
    warnings = []

    def capture_warning(message, *args, **kwargs):
        warnings.append(message % args if args else message)

    monkeypatch.setattr(vlm_pipeline_module.logger, "warning", capture_warning)

    decoder = _make_decoder()
    fgetter = FlakyFrameGetter()
    chunk = ChunkInfo(file="video.mp4", end_pts=1000000000)
    vlm_query = _make_vlm_query()

    result = decoder._decode_chunk(
        fgetter,
        chunk,
        vlm_query,
        video_codec="HEVC",
        request_id="test-request",
    )

    assert result["frames"] == ["frame"]
    assert result["error"] is None
    assert result["decode_retry_count"] == 1
    assert any("Retrying decode for chunk" in warning for warning in warnings)
    assert fgetter.calls == 2
    # First-attempt failure destroys the broken pipeline once; the retry then
    # rebuilds it with a fresh cached decoder. On retry success the fresh
    # decoder is preserved for the next chunk; CUDA decoder context creation is
    # expensive and reuse is the goal.
    assert fgetter.destroyed == 1
    assert fgetter.flushed == 0
    assert decoder._fgetters == [fgetter]


@pytest.mark.no_gpu
def test_decode_chunk_retries_and_fails_empty_frame_extraction(monkeypatch):
    _install_fake_frame_selector(monkeypatch)
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)
    monkeypatch.setenv("RTVI_DECODE_MAX_ATTEMPTS", "2")
    monkeypatch.delenv("RTVI_REUSE_FILE_DECODER_PIPELINE", raising=False)
    warnings = []

    def capture_warning(message, *args, **kwargs):
        warnings.append(message % args if args else message)

    monkeypatch.setattr(vlm_pipeline_module.logger, "warning", capture_warning)

    decoder = _make_decoder()
    fgetter = EmptyFrameGetter()
    chunk = ChunkInfo(file="video.mp4", end_pts=1000000000)

    result = decoder._decode_chunk(
        fgetter,
        chunk,
        _make_vlm_query(),
        video_codec="HEVC",
        request_id="test-request",
    )

    assert result["chunk"] is chunk
    assert result["error"] == "Decode error: decoded 0 frame(s), required at least 1"
    assert result["decode_retry_count"] == 1
    assert any("decoded 0 frame(s)" in warning for warning in warnings)
    assert fgetter.calls == 2
    assert fgetter.destroyed == 2
    assert fgetter.flushed == 0
    assert decoder._fgetters == [fgetter]


@pytest.mark.no_gpu
def test_decode_chunk_retries_underfilled_strict_fixed_frame_chunk(monkeypatch):
    _install_fake_frame_selector(monkeypatch)
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)
    monkeypatch.setenv("RTVI_DECODE_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("RTVI_STRICT_FIXED_FRAME_CHUNK_DECODE", "true")

    decoder = _make_decoder()
    fgetter = UnderfilledFixedFrameGetter()
    chunk = ChunkInfo(file="video.mp4", end_pts=1000000000)
    vlm_query = _make_vlm_query()
    vlm_query.num_frames_per_second_or_fixed_frames_chunk = 30

    result = decoder._decode_chunk(
        fgetter,
        chunk,
        vlm_query,
        video_codec="HEVC",
        request_id="test-request",
    )

    assert len(result["frames"]) == 30
    assert result["error"] is None
    assert result["decode_retry_count"] == 1
    assert fgetter.calls == 2
    assert fgetter.destroyed == 1
    assert decoder._fgetters == [fgetter]


@pytest.mark.no_gpu
def test_decode_chunk_returns_cuda_oom_without_retry(monkeypatch):
    _install_fake_frame_selector(monkeypatch)
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)
    monkeypatch.setenv("RTVI_DECODE_MAX_ATTEMPTS", "2")

    decoder = _make_decoder()
    fgetter = OomFrameGetter()

    result = decoder._decode_chunk(
        fgetter,
        ChunkInfo(file="video.mp4", end_pts=1000000000),
        _make_vlm_query(),
        video_codec="HEVC",
        request_id="test-request",
    )

    assert "CUDA out of memory while extracting decoded chunk frames" in result["error"]
    assert result["error_status_code"] == 503
    assert result["decode_retry_count"] == 0
    assert fgetter.calls == 1
    assert fgetter.destroyed == 1
    assert fgetter.flushed == 0
    assert decoder._fgetters == [fgetter]


@pytest.mark.no_gpu
def test_should_issue_initial_seek_skips_only_for_fresh_chunk_zero(monkeypatch):
    """The seek-skip optimisation must not apply to a pipeline that has
    already streamed: it may be parked at EOS from the previous decode and
    must be rewound when a new chunk-0 request arrives, even though
    start_pts == 0."""
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import _should_issue_initial_seek

    # Images never seek.
    assert not _should_issue_initial_seek(is_image=True, start_pts=0, pipeline_has_streamed=False)
    assert not _should_issue_initial_seek(
        is_image=True, start_pts=10**9, pipeline_has_streamed=True
    )
    # Non-zero start always seeks (positions the pipeline to the chunk).
    assert _should_issue_initial_seek(is_image=False, start_pts=10**9, pipeline_has_streamed=False)
    assert _should_issue_initial_seek(is_image=False, start_pts=10**9, pipeline_has_streamed=True)
    # The skip case: start_pts == 0 AND the pipeline has not yet streamed.
    assert not _should_issue_initial_seek(is_image=False, start_pts=0, pipeline_has_streamed=False)
    # Critical correctness case: start_pts == 0 on a streamed pipeline
    # must still seek — otherwise the pipeline plays from wherever the
    # last decode left it (often EOS) and returns no frames or wrong frames.
    assert _should_issue_initial_seek(is_image=False, start_pts=0, pipeline_has_streamed=True)


@pytest.mark.no_gpu
def test_failed_seek_playthrough_only_when_pipeline_is_before_target(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import _can_play_through_after_seek_failure

    assert not _can_play_through_after_seek_failure(
        seek_position=0,
        pipeline_has_streamed=True,
        current_position=None,
    )
    assert not _can_play_through_after_seek_failure(
        seek_position=30_000_000_000,
        pipeline_has_streamed=True,
        current_position=60_000_000_000,
    )
    assert _can_play_through_after_seek_failure(
        seek_position=60_000_000_000,
        pipeline_has_streamed=True,
        current_position=30_000_000_000,
    )
    assert _can_play_through_after_seek_failure(
        seek_position=30_000_000_000,
        pipeline_has_streamed=False,
        current_position=None,
    )


@pytest.mark.no_gpu
@pytest.mark.parametrize(
    ("error", "expected", "frames", "timestamps", "audio", "accepted"),
    [
        ("qtdemux: streaming stopped, reason not-linked (-1)", 20, 20, 20, False, True),
        ("qtdemux: streaming stopped, reason not-linked (-1)", 20, 19, 20, False, False),
        ("qtdemux: streaming stopped, reason not-linked (-1)", 20, 20, 19, False, False),
        ("qtdemux: streaming stopped, reason not-linked (-1)", 20, 20, 20, True, False),
        ("decoder failed", 20, 20, 20, False, False),
    ],
)
def test_completed_frames_suppress_only_late_qtdemux_not_linked(
    monkeypatch, error, expected, frames, timestamps, audio, accepted
):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import (
        _can_use_completed_frames_after_qtdemux_not_linked,
    )

    assert (
        _can_use_completed_frames_after_qtdemux_not_linked(
            error,
            expected_frames=expected,
            actual_frames=frames,
            actual_timestamps=timestamps,
            audio_enabled=audio,
        )
        is accepted
    )


@pytest.mark.no_gpu
def test_completed_frames_use_original_count_after_selector_consumption(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import (
        DefaultFrameSelector,
        _can_use_completed_frames_after_qtdemux_not_linked,
    )

    selector = DefaultFrameSelector(20, use_fps_for_chunking=False)
    selector.set_chunk(ChunkInfo(file="video.mp4", start_pts=0, end_pts=20_000_000_000))
    expected_frame_count = selector._num_frames
    error = "qtdemux: streaming stopped, reason not-linked (-1)"

    for pts in range(0, 10_000_000_000, 1_000_000_000):
        assert selector.choose_frame(None, pts)
    assert len(selector._selected_pts_array) == 10
    assert not _can_use_completed_frames_after_qtdemux_not_linked(
        error,
        expected_frames=expected_frame_count,
        actual_frames=10,
        actual_timestamps=10,
        audio_enabled=False,
    )

    for pts in range(10_000_000_000, 20_000_000_000, 1_000_000_000):
        assert selector.choose_frame(None, pts)
    assert not selector._selected_pts_array
    assert _can_use_completed_frames_after_qtdemux_not_linked(
        error,
        expected_frames=expected_frame_count,
        actual_frames=20,
        actual_timestamps=20,
        audio_enabled=False,
    )


@pytest.mark.no_gpu
def test_gst_property_setter_skips_properties_missing_on_jetson(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import _set_gst_property_if_supported

    class FakeElement:
        def __init__(self):
            self.values = {}

        def find_property(self, name):
            return object() if name == "extract-sei-type5-data" else None

        def set_property(self, name, value):
            self.values[name] = value

    element = FakeElement()

    assert not _set_gst_property_if_supported(element, "gpu-id", 0)
    assert _set_gst_property_if_supported(element, "extract-sei-type5-data", True)
    assert element.values == {"extract-sei-type5-data": True}


@pytest.mark.no_gpu
def test_late_file_frame_after_cache_handoff_is_dropped(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    fgetter._file_frame_cache_lock = threading.Lock()
    fgetter._cached_frames = None
    fgetter._cached_frames_pts = None

    assert not fgetter._append_file_frame_to_cache("late-frame", 1.23)
    assert fgetter._cached_frames is None
    assert fgetter._cached_frames_pts is None

    fgetter._cached_frames = []
    fgetter._cached_frames_pts = []

    assert fgetter._append_file_frame_to_cache("frame", 2.34)
    assert fgetter._cached_frames == ["frame"]
    assert fgetter._cached_frames_pts == [2.34]


@pytest.mark.no_gpu
def test_pipeline_replacement_removes_bus_watch_and_callbacks(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    events = []

    class FakePad:
        def remove_probe(self, probe_id):
            events.append(("probe", probe_id))

    class FakeSignalObject:
        def disconnect(self, handler_id):
            events.append(("handler", handler_id))

    class FakeBus:
        def remove_signal_watch(self):
            events.append(("bus-watch", None))

    class FakePipeline:
        def remove(self, element):
            events.append(("decoder", element))

    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    old_pipeline = FakePipeline()
    cached_decoder = object()
    old_bus = FakeBus()
    fgetter._pipeline = old_pipeline
    fgetter._vdecodebin = cached_decoder
    fgetter._vdecodebin_cache = {("h264", 320, 320): cached_decoder}
    old_parser_pad = FakePad()
    fgetter._gst_pad_probe_ids = [(old_parser_pad, 11)]
    fgetter._gst_signal_handler_ids = [(FakeSignalObject(), 22)]
    fgetter._vdecodebin_cache_signal_keys = {("h264", 320, 320)}
    fgetter._bus = old_bus
    fgetter._bus_signal_watch_added = True

    detached = fgetter._detach_pipeline_for_replacement()

    assert detached is old_pipeline
    assert events == [
        ("decoder", cached_decoder),
        ("probe", 11),
        ("handler", 22),
        ("bus-watch", None),
    ]
    assert fgetter._pipeline is None
    assert fgetter._vdecodebin is None
    assert fgetter._bus is None
    assert fgetter._gst_pad_probe_ids == []
    assert fgetter._gst_signal_handler_ids == []
    assert fgetter._vdecodebin_cache_signal_keys == set()
    assert not fgetter._bus_signal_watch_added


@pytest.mark.no_gpu
def test_cached_decoder_restores_existing_parser_probe(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline import video_file_frame_getter as frame_getter_module
    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    class FakeFactory:
        def get_name(self):
            return "h264parse"

    class FakePad:
        def __init__(self):
            self.probes = []

        def add_probe(self, probe_type, callback, *args):
            self.probes.append((probe_type, callback, args))
            return len(self.probes)

    class FakeParser:
        def __init__(self):
            self.src_pad = FakePad()

        def get_factory(self):
            return FakeFactory()

        def get_static_pad(self, name):
            assert name == "src"
            return self.src_pad

    class FakeIterator:
        def __init__(self, elem):
            self.elem = elem

        def next(self):
            if self.elem is not None:
                elem, self.elem = self.elem, None
                return frame_getter_module.Gst.IteratorResult.OK, elem
            return frame_getter_module.Gst.IteratorResult.DONE, None

        def resync(self):
            raise AssertionError("unexpected iterator resync")

    parser = FakeParser()
    cached_decoder = SimpleNamespace(iterate_recurse=lambda: FakeIterator(parser))
    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    fgetter._gop_decode_opt_enabled = True
    fgetter._gst_pad_probe_ids = []

    fgetter._restore_cached_decoder_parser_probes(cached_decoder)
    fgetter._restore_cached_decoder_parser_probes(cached_decoder)

    assert len(parser.src_pad.probes) == 1
    assert fgetter._gst_pad_probe_ids == [(parser.src_pad, 1)]


@pytest.mark.no_gpu
def test_handle_cuda_oom_records_err_msg_and_drops_cached_frames(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())
    monkeypatch.delenv("RTVI_ENABLE_CUDA_OOM_RECOVERY", raising=False)

    from vlm_pipeline import video_file_frame_getter as frame_getter_module
    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    fgetter._err_msg = None
    fgetter._err_msg_lock = threading.Lock()
    fgetter._file_frame_cache_lock = threading.Lock()
    fgetter._cached_frames = ["frame-a", "frame-b"]
    fgetter._cached_frames_pts = [0.0, 1.0]
    fgetter._live_stream_frame_selectors_lock = threading.Lock()
    fgetter._live_stream_frame_selectors = {}

    loop_quits = []
    fgetter._loop = SimpleNamespace(
        is_running=lambda: True,
        quit=lambda: loop_quits.append(True),
    )

    monkeypatch.setattr(frame_getter_module.torch.cuda, "empty_cache", lambda: None)

    exc = torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 56.00 MiB.")
    msg = fgetter._handle_cuda_oom(exc, "preprocessing decoded chunk frames")

    assert "CUDA out of memory while preprocessing decoded chunk frames" in msg
    assert "Reduce frame sampling rate" in msg
    assert fgetter._err_msg == msg
    assert fgetter._cached_frames == []
    assert fgetter._cached_frames_pts == []
    assert loop_quits == [True]

    later_msg = fgetter._handle_cuda_oom(
        torch.OutOfMemoryError("CUDA out of memory. Tried to allocate 12.00 MiB."),
        "copying decoded frame to CUDA cache",
    )
    assert later_msg != msg
    assert fgetter._err_msg == msg


@pytest.mark.no_gpu
def test_handle_cuda_oom_preserves_live_selectors_when_requested(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())
    monkeypatch.delenv("RTVI_ENABLE_CUDA_OOM_RECOVERY", raising=False)

    from vlm_pipeline import video_file_frame_getter as frame_getter_module
    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    fgetter._err_msg = None
    fgetter._err_msg_lock = threading.Lock()
    fgetter._file_frame_cache_lock = threading.Lock()
    fgetter._cached_frames = None
    fgetter._cached_frames_pts = None
    fgetter._live_stream_frame_selectors_lock = threading.Lock()

    other_selector_data = SimpleNamespace(cached_frames=["other-frame"])
    fgetter._live_stream_frame_selectors = {"other-fs": other_selector_data}

    fgetter._loop = SimpleNamespace(is_running=lambda: False, quit=lambda: None)
    monkeypatch.setattr(frame_getter_module.torch.cuda, "empty_cache", lambda: None)

    fgetter._handle_cuda_oom(
        torch.OutOfMemoryError("CUDA out of memory."),
        "preprocessing live stream chunk frames",
        clear_live_selectors=False,
    )

    assert other_selector_data.cached_frames == ["other-frame"]


@pytest.mark.no_gpu
def test_handle_cuda_oom_recovery_can_be_disabled(monkeypatch):
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())
    monkeypatch.setenv("RTVI_ENABLE_CUDA_OOM_RECOVERY", "false")

    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    fgetter._err_msg = None
    fgetter._err_msg_lock = threading.Lock()
    fgetter._file_frame_cache_lock = threading.Lock()
    fgetter._cached_frames = ["frame-a"]
    fgetter._cached_frames_pts = [0.0]
    fgetter._live_stream_frame_selectors_lock = threading.Lock()
    fgetter._live_stream_frame_selectors = {}

    loop_quits = []
    fgetter._loop = SimpleNamespace(
        is_running=lambda: True,
        quit=lambda: loop_quits.append(True),
    )

    exc = torch.OutOfMemoryError("CUDA out of memory.")
    with pytest.raises(torch.OutOfMemoryError, match="CUDA out of memory"):
        fgetter._handle_cuda_oom(exc, "preprocessing decoded chunk frames")

    assert fgetter._err_msg is None
    assert fgetter._cached_frames == ["frame-a"]
    assert fgetter._cached_frames_pts == [0.0]
    assert loop_quits == []


@pytest.mark.no_gpu
def test_bcd_file_transition_rebuild_preserves_current_decode_cache(monkeypatch):
    """BCD 3.2 e2e runs reuse decoder workers across the 10s and 10min
    local files. Rebuilding the old 10s pipeline must not leave the current
    10min chunk cache disabled, or the first decode attempt drops every frame
    and retries with "No frames found".
    """
    monkeypatch.setitem(sys.modules, "pyds", types.SimpleNamespace())

    from vlm_pipeline import video_file_frame_getter as frame_getter_module
    from vlm_pipeline.video_file_frame_getter import VideoFileFrameGetter

    fgetter = VideoFileFrameGetter.__new__(VideoFileFrameGetter)
    fgetter._file_frame_cache_lock = threading.Lock()
    fgetter._cached_frames = []
    fgetter._cached_frames_pts = []
    fgetter._cached_audio_frames = []
    fgetter._cached_transcripts = []
    fgetter._live_stream_frame_selectors = {}
    fgetter._live_stream_frame_selectors_lock = threading.Lock()
    fgetter._copy_stream = None
    fgetter._gdino = None

    # Reference BCD 3.2 transition: after a 10s clip, the next e2e test
    # switches the same reusable decoder worker to the 10min local file.
    fgetter._last_stream_id = "/opt/nvidia/rtvi/streams/perf/FPS10_Res1080p_Dur10sec_1.mp4"
    next_file = "/opt/nvidia/rtvi/streams/perf/warehouse_gopro_10m_10fps.mp4"
    assert fgetter._last_stream_id != next_file

    def fake_pipeline_teardown(*args, **kwargs):
        # Model a late callback from the old pipeline while it is being
        # destroyed. It must be dropped instead of polluting the new chunk.
        fgetter._append_file_frame_to_cache("late-old-frame", 1.0)

    monkeypatch.setattr(fgetter, "_set_pipeline_null_and_clear_refs", fake_pipeline_teardown)
    monkeypatch.setattr(frame_getter_module.gc, "collect", lambda: None)
    monkeypatch.setattr(frame_getter_module.torch.cuda, "empty_cache", lambda: None)

    fgetter._destroy_pipeline_before_current_decode()

    assert fgetter._cached_frames == []
    assert fgetter._cached_frames_pts == []
    assert fgetter._append_file_frame_to_cache("new-file-frame", 10.0)
    assert fgetter._cached_frames == ["new-file-frame"]
    assert fgetter._cached_frames_pts == [10.0]


@pytest.mark.no_gpu
def test_decode_chunk_reuses_decoder_after_clean_success_by_default(monkeypatch):
    _install_fake_frame_selector(monkeypatch)
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)
    monkeypatch.delenv("RTVI_REUSE_FILE_DECODER_PIPELINE", raising=False)

    decoder = _make_decoder()
    fgetter = CleanFrameGetter()

    result = decoder._decode_chunk(
        fgetter,
        ChunkInfo(file="video.mp4", end_pts=1000000000),
        _make_vlm_query(),
        video_codec="HEVC",
        request_id="test-request",
    )

    assert result["frames"] == ["frame"]
    assert result["decode_retry_count"] == 0
    assert fgetter.calls == 1
    assert fgetter.destroyed == 0
    assert fgetter.flushed == 0
    assert decoder._fgetters == [fgetter]


@pytest.mark.no_gpu
@pytest.mark.parametrize("requested_frames, expected", [(2, [0, 2]), (8, [0, 1, 2, 3])])
def test_persistent_ring_decodes_file_once_and_reuses_pts_frames(
    monkeypatch, tmp_path, requested_frames, expected
):
    class DenseFrameGetter(CleanFrameGetter):
        def get_frames(self, chunk, frame_selector, *args, **kwargs):
            self.calls += 1
            frame_selector.set_chunk(chunk)
            assert frame_selector.selects_all_frames
            return torch.arange(4).reshape(4, 1), [0.0, 1.0, 2.0, 3.0], [], None

    class UnexpectedDecodeFrameGetter(CleanFrameGetter):
        def get_frames(self, *args, **kwargs):
            raise AssertionError("ring hit must not invoke GStreamer decode")

    monkeypatch.setenv("RTVI_PERSISTENT_CUDA_FRAME_RING", "true")
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)

    decoder = _make_decoder()
    decoder._cuda_frame_ring = CudaFrameRing(max_bytes=1024)
    query = _make_vlm_query()
    query.num_frames_per_second_or_fixed_frames_chunk = requested_frames
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"content-version-1")
    chunk = ChunkInfo(file=str(video), start_pts=0, end_pts=4_000_000_000)

    first_getter = DenseFrameGetter()
    first = decoder._decode_chunk(
        first_getter,
        chunk,
        query,
        video_codec="HEVC",
        request_id="first-request",
    )
    decoder._fgetters.clear()
    second = decoder._decode_chunk(
        UnexpectedDecodeFrameGetter(),
        chunk,
        query,
        video_codec="HEVC",
        request_id="second-request",
    )

    assert first_getter.calls == 1
    assert first["frames"].flatten().tolist() == expected
    assert second["frames"].flatten().tolist() == expected
    assert first["frame_times"] == second["frame_times"] == [float(i) for i in expected]


@pytest.mark.no_gpu
@pytest.mark.parametrize("as_tensor", [False, True])
@pytest.mark.parametrize("mode", ["pts", "indices", "all"])
@pytest.mark.parametrize(
    "pts_ns, end_ns",
    [
        ([0, 100_000_000], 200_000_000),
        ([70_000_000, 100_000_000, 130_000_000, 170_000_000], 200_000_000),
    ],
)
def test_dense_and_ring_sampling_match_sparse_selector(
    monkeypatch, as_tensor, mode, pts_ns, end_ns
):
    from vlm_pipeline.video_file_frame_getter import DefaultFrameSelector

    monkeypatch.setenv("RTVI_QWEN_REFERENCE_RESIZE", "false")
    selector = DefaultFrameSelector(-1 if mode == "all" else 8)
    chunk = ChunkInfo(file="fixture.mp4", start_pts=0, end_pts=end_ns)
    query = vlm_pipeline_module._selector_ring_query(selector, chunk)
    if mode == "indices":
        query["target_indices"] = [round(i * (len(pts_ns) - 1) / 7) for i in range(8)]
        selector._select_by_frame_index = True
        selector._selected_frame_indices_array.extend(query["target_indices"])
    expected_indices = [i for i, pts in enumerate(pts_ns) if selector.choose_frame(None, pts)]
    expected_pts = [pts_ns[i] for i in expected_indices]
    frames = torch.arange(len(pts_ns)).reshape(-1, 1) if as_tensor else list(range(len(pts_ns)))

    selected, selected_times = vlm_pipeline_module._select_dense_frames(
        frames, [pts / 1e9 for pts in pts_ns], query
    )

    assert (selected.flatten().tolist() if as_tensor else selected) == expected_indices
    assert selected_times == [pts / 1e9 for pts in expected_pts]
    ring = CudaFrameRing(max_bytes=1024)
    ring.publish("source", "epoch", 0, end_ns, pts_ns, list(range(len(pts_ns))))
    assert ring.acquire("source", "epoch", **query) == (expected_indices, expected_pts)


@pytest.mark.no_gpu
def test_dense_sampling_with_unavailable_targets_never_returns_full_batch():
    query = dict(
        start_ns=0,
        end_ns=300_000_000,
        target_pts_ns=[0, 250_000_000],
        target_indices=None,
        select_all=False,
    )
    assert vlm_pipeline_module._select_dense_frames([0, 1, 2], [0.0, 0.1, 0.2], query) == (
        [0],
        [0.0],
    )


@pytest.mark.no_gpu
@pytest.mark.parametrize("packed", [False, True])
def test_underfilled_ring_hit_preserves_strict_frame_count_check(monkeypatch, tmp_path, packed):
    from vlm_pipeline.video_file_frame_getter import DefaultFrameSelector

    monkeypatch.setenv("RTVI_PERSISTENT_CUDA_FRAME_RING", "true")
    monkeypatch.setenv("RTVI_STRICT_FIXED_FRAME_CHUNK_DECODE", "true")
    monkeypatch.setenv("RTVI_QWEN_REFERENCE_RESIZE", "false")
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *a, **kw: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *a, **kw: None)
    decoder = _make_decoder()
    decoder._cuda_frame_ring = CudaFrameRing(max_bytes=1024)
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"content-version-1")
    chunk = ChunkInfo(file=str(video), start_pts=0, end_pts=4_000_000_000)
    key = vlm_pipeline_module._file_ring_identity(chunk, 0, 0, "HEVC")
    decoder._cuda_frame_ring.publish(*key, 0, 4_000_000_000, [0], [torch.tensor([0])])
    if packed:
        selection_key = vlm_pipeline_module._ring_selection_key(
            vlm_pipeline_module._selector_ring_query(DefaultFrameSelector(2), chunk)
        )
        decoder._cuda_frame_ring.publish_selection(*key, selection_key, torch.tensor([[0]]), [0])
    query = _make_vlm_query()
    query.num_frames_per_second_or_fixed_frames_chunk = 2
    getter = UnderfilledFixedFrameGetter()

    result = decoder._decode_chunk(getter, chunk, query, video_codec="HEVC", request_id="strict")

    assert getter.calls == 2
    assert result["error"] is None
    assert result["decode_retry_count"] == 1
    assert len(result["frames"]) == 30


@pytest.mark.no_gpu
def test_persistent_ring_aborts_single_flight_when_decode_raises(monkeypatch, tmp_path):
    class ExplodingFrameGetter(CleanFrameGetter):
        def get_frames(self, *args, **kwargs):
            raise RuntimeError("decoder failed")

    monkeypatch.setenv("RTVI_PERSISTENT_CUDA_FRAME_RING", "true")
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())

    decoder = _make_decoder()
    decoder._cuda_frame_ring = CudaFrameRing(max_bytes=1024)
    video = tmp_path / "fixture.mp4"
    video.write_bytes(b"content-version-1")
    chunk = ChunkInfo(file=str(video), start_pts=0, end_pts=4_000_000_000)
    ring_key = vlm_pipeline_module._file_ring_identity(chunk, 0, 0, "HEVC")

    with pytest.raises(RuntimeError, match="decoder failed"):
        decoder._decode_chunk(
            ExplodingFrameGetter(),
            chunk,
            _make_vlm_query(),
            video_codec="HEVC",
            request_id="failed-request",
        )

    assert decoder._cuda_frame_ring.claim_fill(*ring_key)


@pytest.mark.no_gpu
def test_persistent_ring_does_not_claim_static_images(tmp_path):
    image = tmp_path / "fixture.jpg"
    image.write_bytes(b"image")
    chunk = ChunkInfo(file=str(image), start_pts=0, end_pts=1_000_000_000)

    assert vlm_pipeline_module._file_ring_identity(chunk, 608, 320, "JPEG") is None


@pytest.mark.no_gpu
def test_fast_image_decode_rejects_underfilled_fixed_frame_chunk(monkeypatch):
    _install_fake_frame_selector(monkeypatch)
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        vlm_pipeline_module,
        "_try_decode_image_asset_chunk",
        lambda *args, **kwargs: (torch.zeros((2, 2, 2, 3)), [0.0, 0.0], [], None),
    )
    monkeypatch.setenv("RTVI_STRICT_FIXED_FRAME_CHUNK_DECODE", "true")

    decoder = _make_decoder()
    fgetter = CleanFrameGetter()
    query = _make_vlm_query()
    query.num_frames_per_second_or_fixed_frames_chunk = 3
    chunk = ChunkInfo(file="first.jpg;second.jpg", end_pts=1_000_000_000)

    result = decoder._decode_chunk(
        fgetter,
        chunk,
        query,
        video_codec=None,
        request_id="test-request",
    )

    assert result["error"] == "Decode error: decoded 2 frame(s), required at least 3"
    assert fgetter.calls == 0
    assert decoder._fgetters == [fgetter]


@pytest.mark.no_gpu
def test_vlm_queue_maxsize_env_override(monkeypatch):
    monkeypatch.setenv("RTVI_VLM_QUEUE_MAXSIZE", "2")
    assert vlm_pipeline_module._vlm_queue_maxsize(num_gpus=8) == 2


@pytest.mark.no_gpu
def test_vlm_queue_maxsize_invalid_env_uses_default(monkeypatch):
    monkeypatch.setenv("RTVI_VLM_QUEUE_MAXSIZE", "not-an-int")
    assert vlm_pipeline_module._vlm_queue_maxsize(num_gpus=2) == 256


@pytest.mark.no_gpu
def test_decode_chunk_passes_all_frames_sentinel_to_frame_selector(monkeypatch):
    created_selectors = []

    class CapturingFrameSelector(FakeDefaultFrameSelector):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created_selectors.append(self)

    fake_frame_getter_module = types.ModuleType("vlm_pipeline.video_file_frame_getter")
    fake_frame_getter_module.DefaultFrameSelector = CapturingFrameSelector
    monkeypatch.setitem(
        sys.modules,
        "vlm_pipeline.video_file_frame_getter",
        fake_frame_getter_module,
    )
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "start_range", lambda *args, **kwargs: object())
    monkeypatch.setattr(vlm_pipeline_module.nvtx, "end_range", lambda *args, **kwargs: None)

    decoder = _make_decoder()
    fgetter = CleanFrameGetter()
    chunk = ChunkInfo(file="video.mp4", end_pts=1000000000)
    vlm_query = _make_vlm_query()
    vlm_query.num_frames_per_second_or_fixed_frames_chunk = -1

    result = decoder._decode_chunk(
        fgetter,
        chunk,
        vlm_query,
        video_codec="HEVC",
        request_id="test-request",
    )

    assert result["frames"] == ["frame"]
    assert created_selectors[0].args == (-1,)
    assert created_selectors[0].kwargs["use_fps_for_chunking"] is False


@pytest.mark.no_gpu
def test_live_stream_fallback_frame_selector_honors_server_fps_default(monkeypatch):
    created_selectors = []
    created_getters = []

    class CapturingFrameSelector(FakeDefaultFrameSelector):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created_selectors.append(self)

    class CapturingLiveFrameGetter(FakeLiveFrameGetter):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            created_getters.append(self)

    fake_frame_getter_module = types.ModuleType("vlm_pipeline.video_file_frame_getter")
    fake_frame_getter_module.DefaultFrameSelector = CapturingFrameSelector
    fake_frame_getter_module.VideoFileFrameGetter = CapturingLiveFrameGetter
    monkeypatch.setitem(
        sys.modules,
        "vlm_pipeline.video_file_frame_getter",
        fake_frame_getter_module,
    )

    decoder = _make_live_decoder()
    asset = SimpleNamespace(
        asset_id="live-stream-id",
        camera_id="camera-id",
        sensor_name="sensor-name",
        path="rtsp://example.test/stream.mp4",
        username="",
        password="",
    )

    query = _make_vlm_query()
    query.chunk_overlap_duration = 2
    decoder._live_stream(
        asset,
        query,
        request_id="test-request",
        request_params=object(),
    )

    assert created_selectors[0].args == (3,)
    assert created_selectors[0].kwargs["use_fps_for_chunking"] is True
    assert created_getters[0].destroyed == 1
    assert created_getters[0].kwargs["cuda_frame_ring"] is decoder._cuda_frame_ring
    assert created_getters[0].stream_kwargs["chunk_overlap_duration"] == 2
    assert decoder._final_output_queue.items[-1]["live_stream_ended"] is True

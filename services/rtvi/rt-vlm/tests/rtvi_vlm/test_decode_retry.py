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
    decoder._file_thread_pool = ImmediateExecutor()
    decoder._fgetters = []
    decoder._fgetter_handoff_lock = threading.Lock()
    return decoder


def _make_live_decoder():
    decoder = _make_decoder()
    decoder._nfrms = 3
    decoder._use_fps_for_chunking = True
    decoder._live_stream_handle_info = {}
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
    return decoder


def _make_vlm_query():
    return SimpleNamespace(
        num_frames_per_second_or_fixed_frames_chunk=None,
        use_fps_for_chunking=False,
        enable_audio=False,
        chunk_duration=10,
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

    def fake_pipeline_teardown():
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
        path="rtsp://example.test/stream.mp4",
        username="",
        password="",
    )

    decoder._live_stream(
        asset,
        _make_vlm_query(),
        request_id="test-request",
        request_params=object(),
    )

    assert created_selectors[0].args == (3,)
    assert created_selectors[0].kwargs["use_fps_for_chunking"] is True
    assert created_getters[0].destroyed == 1
    assert decoder._final_output_queue.items[-1]["live_stream_ended"] is True

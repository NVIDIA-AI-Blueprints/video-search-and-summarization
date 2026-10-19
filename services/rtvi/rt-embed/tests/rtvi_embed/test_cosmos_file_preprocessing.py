# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CPU contract checks for file sampling, without importing GPU/GStreamer."""

import ast
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def sample():
    source = Path(__file__).parents[2] / "src/vlm_pipeline/video_file_frame_getter.py"
    tree = ast.parse(source.read_text())
    function = next(
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef) and n.name == "select_file_indices"
    )
    namespace = {"np": np}
    exec(  # noqa: S102
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    return namespace["select_file_indices"]


def test_file_sampling_includes_endpoints_and_preserves_vfr_alignment(sample):
    frames = list(range(10))
    timestamps = [0, 0.01, 0.03, 0.09, 0.5, 0.6, 0.9, 1, 1.5, 3]
    selected, times, indices = sample(frames, timestamps, 4)
    assert indices == [0, 3, 6, 9]
    assert selected == [0, 3, 6, 9]
    assert times == [0, 0.09, 0.9, 3]


def test_short_clips_repeat_indices_in_place(sample):
    frames = [object(), object()]
    selected, times, indices = sample(frames, [0, 0.2], 8)
    assert indices == [0, 0, 0, 0, 1, 1, 1, 1]
    assert selected[0] is frames[0] and selected[-1] is frames[-1]
    assert times == [0] * 4 + [0.2] * 4


@pytest.mark.parametrize(
    "frames,times,count", [([], [], 8), ([1], [], 8), ([1], [0], 0)]
)
def test_invalid_sampling_fails(frames, times, count, sample):
    with pytest.raises(ValueError):
        sample(frames, times, count)


def test_native_resize_layout_and_antialias(monkeypatch):
    import sys
    from types import SimpleNamespace

    calls = []

    class Tensor:
        dtype = "uint8"

        def __init__(self, shape):
            self.shape = shape
            self.ndim = len(shape)

        def permute(self, *axes):
            return Tensor(tuple(self.shape[i] for i in axes))

        def unbind(self, axis):
            assert axis == 0
            return [Tensor(self.shape[1:]) for _ in range(self.shape[0])]

    class Resize:
        def __init__(self, size, *, interpolation, antialias):
            calls.append((size, interpolation, antialias))
            self.size = size

        def __call__(self, tensor):
            calls.append(tensor.shape)
            return Tensor((*tensor.shape[:-2], *self.size))

    monkeypatch.setitem(
        sys.modules,
        "torchvision.transforms",
        SimpleNamespace(
            Resize=Resize, InterpolationMode=SimpleNamespace(BILINEAR="bilinear")
        ),
    )
    torch = SimpleNamespace(
        uint8="uint8", stack=lambda frames: Tensor((len(frames), *frames[0].shape))
    )
    source = Path(__file__).parents[2] / "src/vlm_pipeline/video_file_frame_getter.py"
    function = next(
        n
        for n in ast.parse(source.read_text()).body
        if isinstance(n, ast.FunctionDef) and n.name == "resize_file_frames"
    )
    namespace = {"torch": torch}
    exec(  # noqa: S102
        compile(ast.Module(body=[function], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    frames = namespace["resize_file_frames"](
        [Tensor((100, 200, 3)), Tensor((100, 200, 3))], (48, 48)
    )
    assert calls == [((48, 48), "bilinear", True), (2, 3, 100, 200)]
    assert [frame.shape for frame in frames] == [(48, 48, 3), (48, 48, 3)]


@pytest.mark.parametrize("live", [False, True])
def test_retained_frame_budget_fails_without_partial_success_and_preserves_streams(
    live,
):
    from threading import Lock
    from types import SimpleNamespace
    from unittest.mock import Mock

    source = Path(__file__).parents[2] / "src/vlm_pipeline/video_file_frame_getter.py"
    cls = next(
        n
        for n in ast.parse(source.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "VideoFileFrameGetter"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_append_file_frame_to_cache"
    )
    namespace = {"logger": Mock()}
    exec(  # noqa: S102
        compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    target = SimpleNamespace(
        _file_frame_cache_lock=Lock(),
        _err_msg_lock=Lock(),
        _cached_frames=["first"],
        _cached_frames_pts=[0],
        _cosmos_file_index_sampling=True,
        _is_live=live,
        _cosmos_file_max_retained_frames=1,
        _err_msg=None,
        _quit_loop_after_error=Mock(),
    )
    result = namespace["_append_file_frame_to_cache"](target, "second", 0.1)
    assert result is live
    if live:
        assert target._cached_frames == ["first", "second"]
        assert target._err_msg is None
        target._quit_loop_after_error.assert_not_called()
    else:
        assert target._cached_frames == ["first"]
        assert "budget exceeded" in target._err_msg
        target._quit_loop_after_error.assert_called_once()


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("index_sampling", [False, True])
def test_native_retention_respects_bytes_before_frame_count(live, index_sampling):
    from threading import Lock
    from types import SimpleNamespace
    from unittest.mock import Mock

    source = Path(__file__).parents[2] / "src/vlm_pipeline/video_file_frame_getter.py"
    cls = next(
        n
        for n in ast.parse(source.read_text()).body
        if isinstance(n, ast.ClassDef) and n.name == "VideoFileFrameGetter"
    )
    method = next(
        n
        for n in cls.body
        if isinstance(n, ast.FunctionDef) and n.name == "_append_file_frame_to_cache"
    )
    namespace = {"logger": Mock()}
    exec(  # noqa: S102
        compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"),
        namespace,
    )
    storage = SimpleNamespace(nbytes=lambda: 10)
    frame = SimpleNamespace(untyped_storage=lambda: storage)
    target = SimpleNamespace(
        _file_frame_cache_lock=Lock(),
        _err_msg_lock=Lock(),
        _cached_frames=[],
        _cached_frames_pts=[],
        _cached_frame_bytes=500,
        _cosmos_file_index_sampling=index_sampling,
        _file_resize_active=True,
        _is_live=live,
        _cosmos_file_max_retained_frames=100,
        _cosmos_file_max_retained_bytes=20,
        _err_msg=None,
        _quit_loop_after_error=Mock(),
    )
    append = namespace["_append_file_frame_to_cache"]
    assert append(target, frame, 0)
    assert append(target, frame, 0.1)
    assert append(target, frame, 0.2) is live
    if not live:
        assert target._cached_frame_bytes == 20
        assert len(target._cached_frames) == 2
        assert "byte budget exceeded" in target._err_msg
        target._quit_loop_after_error.assert_called_once()
        # A later fresh cache must not inherit the previous chunk's byte count.
        target._cached_frames.clear()
        target._cached_frames_pts.clear()
        assert append(target, frame, 0)
        assert target._cached_frame_bytes == 10
    else:
        target._quit_loop_after_error.assert_not_called()

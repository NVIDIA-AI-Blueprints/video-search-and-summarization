# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

import ast
import importlib.util
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytestmark = [pytest.mark.no_gpu, pytest.mark.test_in_ci]
SOURCE = Path(__file__).parents[1] / "src" / "vlm_pipeline"


@pytest.fixture
def gc_module():
    spec = importlib.util.spec_from_file_location("file_decoder_gc", SOURCE / "file_decoder_gc.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_cadence_is_shared_across_threads(gc_module, monkeypatch):
    monkeypatch.delenv("RTVI_FILE_DECODER_GC_INTERVAL", raising=False)
    collect = Mock()
    monkeypatch.setattr(gc_module.gc, "collect", collect)
    collector = gc_module.FileDecoderGarbageCollector()
    with ThreadPoolExecutor(max_workers=5) as pool:
        for count in range(1, 4):
            list(pool.map(lambda _: collector.collect(), range(9)))
            assert collect.call_count == count - 1
            pool.submit(collector.collect).result()
            assert collect.call_count == count
    assert gc_module.gc.isenabled()


def test_force_collects_immediately_and_restarts_cadence(gc_module, monkeypatch):
    monkeypatch.setenv("RTVI_FILE_DECODER_GC_INTERVAL", "3")
    collect = Mock()
    monkeypatch.setattr(gc_module.gc, "collect", collect)
    collector = gc_module.FileDecoderGarbageCollector()
    collector.collect()
    collector.collect(force=True)
    assert collect.call_count == 1
    collector.collect()
    collector.collect()
    assert collect.call_count == 1
    collector.collect()
    assert collect.call_count == 2


def test_interval_one_restores_collection_after_every_cleanup(gc_module, monkeypatch):
    monkeypatch.setenv("RTVI_FILE_DECODER_GC_INTERVAL", "1")
    collect = Mock()
    monkeypatch.setattr(gc_module.gc, "collect", collect)
    collector = gc_module.FileDecoderGarbageCollector()
    for _ in range(5):
        collector.collect()
    assert collect.call_count == 5


@pytest.mark.parametrize("interval", ["0", "-1", "oops", "1.5", ""])
def test_invalid_interval_is_rejected(gc_module, monkeypatch, interval):
    monkeypatch.setenv("RTVI_FILE_DECODER_GC_INTERVAL", interval)
    with pytest.raises(ValueError, match="RTVI_FILE_DECODER_GC_INTERVAL"):
        gc_module.FileDecoderGarbageCollector()


def test_collection_does_not_hold_lock_or_reenter(gc_module, monkeypatch):
    monkeypatch.setenv("RTVI_FILE_DECODER_GC_INTERVAL", "1")
    collector = gc_module.FileDecoderGarbageCollector()
    calls = []

    def finalize():
        calls.append("collect")
        # Object finalizers can invoke cleanup again, including from another thread.
        thread = threading.Thread(target=lambda: collector.collect(force=True), daemon=True)
        thread.start()
        thread.join(timeout=1)
        assert not thread.is_alive()
        collector.collect(force=True)

    monkeypatch.setattr(gc_module.gc, "collect", finalize)
    collector.collect()
    assert calls == ["collect"]


def test_collection_failure_does_not_disable_future_collection(gc_module, monkeypatch):
    monkeypatch.setenv("RTVI_FILE_DECODER_GC_INTERVAL", "1")
    collect = Mock(side_effect=[RuntimeError("collector failed"), 0])
    monkeypatch.setattr(gc_module.gc, "collect", collect)
    collector = gc_module.FileDecoderGarbageCollector()
    with pytest.raises(RuntimeError, match="collector failed"):
        collector.collect()
    collector.collect()
    assert collect.call_count == 2


def _getter_method(name, namespace):
    tree = ast.parse((SOURCE / "video_file_frame_getter.py").read_text())
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "VideoFileFrameGetter"
    )
    method = next(
        node for node in cls.body if isinstance(node, ast.FunctionDef) and node.name == name
    )
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(SOURCE), "exec"), namespace)
    return namespace[name]


@pytest.mark.parametrize("force_gc", [None, False, True])
def test_real_teardown_preserves_cleanup_and_collection_order(force_gc):
    events = []
    collector = SimpleNamespace(collect=lambda **kw: events.append(("gc", kw)))
    method = _getter_method(
        "destroy_pipeline",
        {
            "_FILE_DECODER_GC": collector,
            "torch": SimpleNamespace(
                cuda=SimpleNamespace(empty_cache=lambda: events.append("empty_cache"))
            ),
        },
    )
    getter = SimpleNamespace(
        _file_frame_cache_lock=threading.Lock(),
        _live_stream_frame_selectors_lock=threading.Lock(),
        _live_stream_frame_selectors={"frame": object()},
        _copy_stream=SimpleNamespace(synchronize=lambda: events.append("synchronize")),
        _gdino=object(),
        _set_pipeline_null_and_clear_refs=lambda **kw: events.append(("null", kw)),
    )
    kwargs = {} if force_gc is None else {"force_gc": force_gc}
    method(getter, preserve_decoder_cache=True, **kwargs)
    expected_force = True if force_gc is None else force_gc
    assert events == [
        "synchronize",
        ("null", {"preserve_decoder_cache": True}),
        ("gc", {"force": expected_force}),
        "empty_cache",
    ]
    assert getter._cached_frames is None and getter._cached_frames_pts is None
    assert not getter._live_stream_frame_selectors and getter._copy_stream is None
    assert getter._gdino is None


def test_normal_rebuild_uses_periodic_gc_and_preserves_current_frame_cache():
    method = _getter_method("_destroy_pipeline_before_current_decode", {})
    frames, timestamps = [], []
    getter = SimpleNamespace(
        _file_frame_cache_lock=threading.Lock(),
        _cached_frames=frames,
        _cached_frames_pts=timestamps,
        destroy_pipeline=Mock(),
    )
    method(getter, preserve_decoder_cache=True)
    getter.destroy_pipeline.assert_called_once_with(preserve_decoder_cache=True, force_gc=False)
    assert getter._cached_frames is frames and getter._cached_frames_pts is timestamps


@pytest.mark.parametrize("error", [None, "decode failed"])
def test_file_worker_forces_gc_only_on_failure(error):
    tree = ast.parse((SOURCE / "vlm_pipeline.py").read_text())
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DecoderProcess"
    )
    decode = next(
        node
        for node in cls.body
        if isinstance(node, ast.FunctionDef) and node.name == "_decode_chunk"
    )
    handoff = next(
        node
        for node in decode.body
        if isinstance(node, ast.FunctionDef) and node.name == "append_fgetter_delayed"
    )
    getter = SimpleNamespace(destroy_pipeline=Mock(), _file_pipeline_reusable=True)
    decoder = SimpleNamespace(_fgetter_handoff_lock=threading.Lock(), _fgetters=[])
    namespace = {
        "self": decoder,
        "fgetter": getter,
        "error": error,
        "_reuse_file_decoder_pipeline": lambda: False,
        "logger": Mock(),
    }
    exec(compile(ast.Module(body=[handoff], type_ignores=[]), str(SOURCE), "exec"), namespace)
    namespace["append_fgetter_delayed"]()
    getter.destroy_pipeline.assert_called_once_with(force_gc=bool(error))
    assert decoder._fgetters == [getter]

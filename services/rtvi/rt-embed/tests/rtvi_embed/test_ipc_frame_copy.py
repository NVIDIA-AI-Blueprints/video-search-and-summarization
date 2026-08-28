# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Tests for RTVI Embed decoded-frame IPC socket selection and wiring."""

import ctypes
import importlib.util
import types
from pathlib import Path

import pytest


IPC_HELPER_PATH = Path(__file__).parents[2] / "src" / "vlm_pipeline" / "ipc_frame_source.py"
spec = importlib.util.spec_from_file_location("ipc_frame_source", IPC_HELPER_PATH)
ipc_frame_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc_frame_source)


def test_resolve_ipc_socket_path_uses_fixed_camera_id_convention():
    assert (
        ipc_frame_source.resolve_ipc_socket_path("uniqueSensorID1")
        == "/run/rtvi-ipc/nvds_ipc_uniqueSensorID1.sock"
    )


@pytest.mark.parametrize("identity", ["", "../bad/sensor id", "cam/1", "camera id"])
def test_resolve_ipc_socket_path_rejects_unsafe_identity(identity):
    with pytest.raises(ValueError, match="IPC stream identity"):
        ipc_frame_source.resolve_ipc_socket_path(identity)


def test_resolve_ipc_socket_path_prevents_sanitization_collisions():
    assert (
        ipc_frame_source.resolve_ipc_socket_path("cam_1")
        == "/run/rtvi-ipc/nvds_ipc_cam_1.sock"
    )
    with pytest.raises(ValueError, match="IPC stream identity"):
        ipc_frame_source.resolve_ipc_socket_path("cam/1")


def test_resolve_ipc_socket_path_accepts_uuid_identity():
    assert (
        ipc_frame_source.resolve_ipc_socket_path("af5a2ec8-e779-4b6b-a3bc-85b4e51044ee")
        == "/run/rtvi-ipc/nvds_ipc_af5a2ec8-e779-4b6b-a3bc-85b4e51044ee.sock"
    )


def test_resolve_ipc_socket_path_ignores_socket_override_environment(monkeypatch):
    monkeypatch.setenv("RTVI_IPC_SOCKET_DIR", "/elsewhere")
    monkeypatch.setenv("RTVI_IPC_SOCKET_TEMPLATE", "other_{camera_id}.sock")
    assert ipc_frame_source.resolve_ipc_socket_path("camera-1") == "/run/rtvi-ipc/nvds_ipc_camera-1.sock"


def test_select_ipc_stream_identity_prefers_camera_then_sensor_then_asset():
    select = ipc_frame_source.select_ipc_stream_identity
    assert select("camera-1", "sensor-1", "asset-1") == "camera-1"
    assert select("", "sensor-1", "asset-1") == "sensor-1"
    assert select(None, "", "asset-1") == "asset-1"


def test_start_script_forwards_current_ipc_environment_names():
    script = (Path(__file__).parents[2] / "src" / "scripts" / "start_rtvi_embed.sh").read_text()
    assert "RTVI_IPC_FRAME_COPY" in script
    assert "--ipc-frame-copy" in script
    assert "RTVI_EMBED_IPC_" not in script


def test_frame_getter_accepts_ipc_stream_arguments(monkeypatch):
    frame_getter = pytest.importorskip(
        "vlm_pipeline.video_file_frame_getter",
        reason="GStreamer frame-getter dependencies are not importable",
    )
    getter = frame_getter.VideoFileFrameGetter(
        frame_selector=frame_getter.DefaultFrameSelector(1),
        frame_width=448,
        frame_height=448,
        gpu_id=0,
        enable_jpeg_output=True,
    )

    def stop_after_setup(self, *_args, **_kwargs):
        assert self._ipc_frame_copy_enabled is True
        assert self._ipc_stream_identity == "camera-1"
        assert self._ipc_socket_path == "/run/rtvi-ipc/nvds_ipc_camera-1.sock"
        self._stop_stream = True
        raise RuntimeError("stop after setup")

    monkeypatch.setattr(frame_getter.VideoFileFrameGetter, "_create_pipeline", stop_after_setup)
    with pytest.raises(RuntimeError, match="stop after setup"):
        getter.stream(
            live_stream_url="rtsp://127.0.0.1:8554/cam",
            chunk_duration=1,
            on_chunk_decoded=lambda *_args: None,
            live_stream_id="asset-1",
            live_stream_identity="camera-1",
            ipc_frame_copy_enabled=True,
        )


def test_get_buffer_sei_data_reads_standard_gstreamer_meta(monkeypatch):
    frame_getter = pytest.importorskip(
        "vlm_pipeline.video_file_frame_getter",
        reason="GStreamer frame-getter dependencies are not importable",
    )
    frame_getter._STANDARD_SEI_META_API_TYPE = None
    monkeypatch.setattr(
        frame_getter.GstVideo,
        "video_sei_user_data_unregistered_meta_api_get_type",
        lambda: "standard-sei-api",
    )
    standard_meta = types.SimpleNamespace(
        data=b'prefix {"timestamp": 2000, "sim_time": 2.5}\\x00', size=48
    )

    class Buffer:
        def get_meta(self, api_type):
            assert api_type == "standard-sei-api"
            return standard_meta

    sei_data, source = frame_getter._get_buffer_sei_data(Buffer())
    assert source == "gst_video_user_data_unregistered_meta"
    assert sei_data == {"timestamp": 2000, "sim_time": 2.5}


def test_get_buffer_sei_data_reads_generic_gst_meta_layout(monkeypatch):
    frame_getter = pytest.importorskip(
        "vlm_pipeline.video_file_frame_getter",
        reason="GStreamer frame-getter dependencies are not importable",
    )
    frame_getter._STANDARD_SEI_META_API_TYPE = None
    monkeypatch.setattr(
        frame_getter.GstVideo,
        "video_sei_user_data_unregistered_meta_api_get_type",
        lambda: "standard-sei-api",
    )
    payload = ctypes.create_string_buffer(b'{"timestamp": 3000, "sim_time": 3.5}')
    meta = frame_getter._GstVideoSEIUserDataUnregisteredMeta()
    meta.data = ctypes.cast(payload, ctypes.c_void_p).value
    meta.size = len(payload.value)

    class GenericMeta:
        def __hash__(self):
            return ctypes.addressof(meta)

    class Buffer:
        def get_meta(self, api_type):
            assert api_type == "standard-sei-api"
            return GenericMeta()

    sei_data, source = frame_getter._get_buffer_sei_data(Buffer())
    assert source == "gst_video_user_data_unregistered_meta"
    assert sei_data == {"timestamp": 3000, "sim_time": 3.5}

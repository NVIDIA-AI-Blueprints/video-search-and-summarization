# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""RTVI VLM decoded-frame IPC packaging and configuration tests."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).parents[2]
IPC_HELPER = ROOT / "src" / "vlm_pipeline" / "ipc_frame_source.py"
spec = importlib.util.spec_from_file_location("ipc_frame_source", IPC_HELPER)
ipc_frame_source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ipc_frame_source)


def test_generic_ipc_environment_selects_socket_path(monkeypatch):
    monkeypatch.setenv("RTVI_IPC_SOCKET_DIR", "/run/rtvi-ipc")
    monkeypatch.setenv("RTVI_IPC_SOCKET_TEMPLATE", "frame_{camera_id}.sock")

    assert (
        ipc_frame_source.resolve_ipc_socket_path("camera-1") == "/run/rtvi-ipc/frame_camera-1.sock"
    )


def test_vlm_image_contains_ipc_runtime_wiring():
    package_files = (ROOT / "docker" / "rtvi_vlm" / "package_file_list.txt").read_text()
    dockerfile = (ROOT / "docker" / "rtvi_vlm" / "Dockerfile").read_text()

    assert "vlm_pipeline/ipc_frame_source.py" in package_files
    assert "libgstnvunixfd.so" in dockerfile


def test_vlm_compose_mounts_socket_directory_without_hiding_tmp():
    compose = (ROOT / "docker" / "rtvi_vlm" / "deploy" / "compose.yaml").read_text()

    assert "RTVI_IPC_SOCKET_HOST_DIR" in compose
    assert "RTVI_IPC_FRAME_COPY" in compose
    assert "${RTVI_IPC_SOCKET_DIR:-/run/rtvi-ipc}:ro" in compose


def test_cv_vlm_launcher_uses_one_camera_socket_and_gpu():
    launcher = (ROOT / "docker" / "rtvi_vlm" / "deploy" / "run_rtvi_cv_vlm_ipc.sh").read_text()

    assert "socket_path=${SOCKET_DIR}/nvds_ipc_${CAMERA_ID}.sock" in launcher
    assert '--gpus "device=${GPU_ID}"' in launcher
    assert 'NVIDIA_VISIBLE_DEVICES="${GPU_ID}"' in launcher
    assert launcher.count('--data-binary "@${STREAM_PAYLOAD}"') == 2

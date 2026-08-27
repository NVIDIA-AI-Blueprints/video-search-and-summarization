# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Helpers for decoded-frame IPC socket selection."""

import os

DEFAULT_IPC_SOCKET_DIR = "/tmp"
DEFAULT_IPC_META_DESERIALIZATION_LIB = ""


def sanitize_ipc_socket_token(value: str) -> str:
    """Return a socket filename-safe stream identity token."""
    sanitized = "".join(ch if ch.isalnum() or ch in ("-", "_", ".") else "_" for ch in value)
    return sanitized.strip("._") or "stream"


def select_ipc_stream_identity(
    camera_id: str | None, sensor_name: str | None, asset_id: str
) -> str:
    """Select the stable stream identity used for IPC socket naming."""
    return camera_id or sensor_name or asset_id


def resolve_ipc_socket_path(
    stream_identity: str,
    socket_dir: str | None = None,
    socket_template: str | None = None,
) -> str:
    """Resolve the Unix socket path used by CV to publish decoded frames."""
    socket_dir = socket_dir or os.environ.get("RTVI_IPC_SOCKET_DIR") or DEFAULT_IPC_SOCKET_DIR
    socket_template = (
        socket_template or os.environ.get("RTVI_IPC_SOCKET_TEMPLATE") or "nvds_ipc_{camera_id}.sock"
    )
    safe_identity = sanitize_ipc_socket_token(stream_identity)
    socket_name = socket_template.format(
        camera_id=safe_identity, sensor_id=safe_identity, stream_id=safe_identity
    )
    return os.path.join(socket_dir, os.path.basename(socket_name))

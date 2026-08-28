# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Helpers for decoded-frame IPC socket selection."""

import os
import re

DEFAULT_IPC_SOCKET_DIR = "/tmp"
DEFAULT_IPC_META_DESERIALIZATION_LIB = ""
_SAFE_IPC_SOCKET_TOKEN = re.compile(r"[A-Za-z0-9._-]+")


def sanitize_ipc_socket_token(value: str) -> str:
    """Validate and return a collision-free IPC stream identity token."""
    if not _SAFE_IPC_SOCKET_TOKEN.fullmatch(value):
        raise ValueError(
            "IPC stream identity must be non-empty and contain only ASCII letters, digits, '.', '_', or '-'"
        )
    return value


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

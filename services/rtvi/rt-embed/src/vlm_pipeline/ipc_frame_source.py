# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Helpers for decoded-frame IPC socket selection."""

import re

DEFAULT_IPC_SOCKET_DIR = "/run/rtvi-ipc"
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


def resolve_ipc_socket_path(stream_identity: str) -> str:
    """Resolve the Unix socket path used by CV to publish decoded frames."""
    safe_identity = sanitize_ipc_socket_token(stream_identity)
    return f"{DEFAULT_IPC_SOCKET_DIR}/nvds_ipc_{safe_identity}.sock"

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES.
# SPDX-License-Identifier: Apache-2.0
"""Helpers for decoded-frame IPC socket selection."""

import os
import re
from string import Formatter

DEFAULT_IPC_SOCKET_DIR = "/tmp"
DEFAULT_IPC_META_DESERIALIZATION_LIB = ""
_SAFE_IPC_SOCKET_TOKEN = re.compile(r"[A-Za-z0-9._-]+")
_IPC_TEMPLATE_FIELDS = frozenset({"camera_id", "sensor_id", "stream_id"})


def sanitize_ipc_socket_token(value: str) -> str:
    """Validate and return a collision-free IPC stream identity token."""
    if not _SAFE_IPC_SOCKET_TOKEN.fullmatch(value):
        raise ValueError(
            "IPC stream identity must be non-empty and contain only ASCII letters, digits, '.', '_', or '-'"
        )
    return value


def validate_ipc_socket_template(value: str) -> str:
    """Validate that a socket template includes supported identity fields."""
    try:
        fields = {field_name for _, field_name, _, _ in Formatter().parse(value) if field_name is not None}
    except ValueError as exc:
        raise ValueError("IPC socket template is invalid") from exc

    unsupported_fields = fields - _IPC_TEMPLATE_FIELDS
    if unsupported_fields:
        raise ValueError(
            f"IPC socket template contains unsupported placeholder(s): {', '.join(sorted(unsupported_fields))}"
        )
    if not fields:
        raise ValueError("IPC socket template must include {camera_id}, {sensor_id}, or {stream_id}")
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
    socket_template = validate_ipc_socket_template(socket_template)
    socket_name = socket_template.format(
        camera_id=safe_identity, sensor_id=safe_identity, stream_id=safe_identity
    )
    return os.path.join(socket_dir, os.path.basename(socket_name))

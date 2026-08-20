# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Reusable VST client and request helpers."""

from .client import SensorRef
from .client import VSTClient
from .client import VSTError
from .client import add_stream
from .client import build_screenshot_url
from .client import classify_source
from .client import confirm_absent
from .client import delete_media
from .client import get_name_to_stream_id_map
from .client import get_sensor_id_from_stream_id
from .client import get_snapshot_url
from .client import get_stream_id
from .client import get_streams_info
from .client import get_timeline
from .client import get_timelines_map
from .client import get_video_clip_url
from .client import list_media
from .client import list_sensors
from .client import map_interval_to_timeline
from .client import map_timestamp_to_timeline
from .client import normalise_media_url
from .client import recorded_span
from .client import resolve_sensor
from .client import upload_media
from .client import validate_media_name
from .client import warm_media_url
from .protocols import VSTSnapshot

__all__ = [
    "SensorRef",
    "VSTClient",
    "VSTError",
    "VSTSnapshot",
    "add_stream",
    "build_screenshot_url",
    "classify_source",
    "confirm_absent",
    "delete_media",
    "get_name_to_stream_id_map",
    "get_sensor_id_from_stream_id",
    "get_snapshot_url",
    "get_stream_id",
    "get_streams_info",
    "get_timeline",
    "get_timelines_map",
    "get_video_clip_url",
    "list_media",
    "list_sensors",
    "map_interval_to_timeline",
    "map_timestamp_to_timeline",
    "normalise_media_url",
    "recorded_span",
    "resolve_sensor",
    "upload_media",
    "validate_media_name",
    "warm_media_url",
]

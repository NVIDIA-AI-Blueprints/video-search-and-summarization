# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Unit tests for the capability-flag dispatcher in CustomFastApiFrontEndWorker.

These tests pin down the contract that drives every dev profile:

    * ``streaming_ingest`` MUST be present in each profile YAML — missing
      config raises so a misconfigured profile can't silently boot with no
      custom routes.
    * Each ``register_*`` function is called iff its corresponding
      ``enable_*`` flag is True. Adding a new profile is a YAML-only change.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.api.custom_fastapi_worker import CustomFastApiFrontEndWorker
from vss_agents.api.front_end_config import StreamingIngestConfig


def _make_worker(streaming_ingest):
    """Construct a worker bypassing the parent ``__init__`` so we can drive
    ``_register_streaming_routes`` directly without standing up a full NAT
    Config object. The dispatcher only reads ``self.config`` so a duck-typed
    MagicMock is sufficient.
    """
    worker = CustomFastApiFrontEndWorker.__new__(CustomFastApiFrontEndWorker)
    config = MagicMock()
    if streaming_ingest is _MISSING:
        config.general.front_end = MagicMock(spec=[])  # no streaming_ingest attr at all
    else:
        config.general.front_end.streaming_ingest = streaming_ingest
    # Base class exposes ``config`` as a read-only property backed by ``_config``.
    worker._config = config
    return worker


_MISSING = object()


def _streaming_ingest(
    *,
    enable_videos_for_search: bool = False,
    enable_rtsp_streams: bool = False,
    enable_video_delete: bool = False,
):
    cfg = MagicMock()
    cfg.enable_videos_for_search = enable_videos_for_search
    cfg.enable_rtsp_streams = enable_rtsp_streams
    cfg.enable_video_delete = enable_video_delete
    return cfg


@pytest.fixture
def patched_register_fns():
    """Patch the three register fns the dispatcher delegates to so we can
    inspect which ones were called for a given capability set."""
    with (
        patch("vss_agents.api.custom_fastapi_worker.register_streaming_routes") as videos_for_search,
        patch("vss_agents.api.custom_fastapi_worker.register_rtsp_stream_api_routes") as rtsp_streams,
        patch("vss_agents.api.custom_fastapi_worker.register_video_delete_routes") as video_delete,
    ):
        yield videos_for_search, rtsp_streams, video_delete


class TestRegisterStreamingRoutesDispatcher:
    """``CustomFastApiFrontEndWorker._register_streaming_routes``."""

    def test_search_profile_registers_all_three(self, patched_register_fns):
        videos_for_search, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(
            _streaming_ingest(
                enable_videos_for_search=True,
                enable_rtsp_streams=True,
                enable_video_delete=True,
            )
        )

        worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_called_once()
        rtsp_streams.assert_called_once()
        video_delete.assert_called_once()

    def test_alerts_profile_registers_rtsp_and_delete_only(self, patched_register_fns):
        videos_for_search, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(
            _streaming_ingest(
                enable_videos_for_search=False,
                enable_rtsp_streams=True,
                enable_video_delete=True,
            )
        )

        worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        rtsp_streams.assert_called_once()
        video_delete.assert_called_once()

    def test_base_lvs_profile_registers_video_delete_only(self, patched_register_fns):
        videos_for_search, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(
            _streaming_ingest(
                enable_videos_for_search=False,
                enable_rtsp_streams=False,
                enable_video_delete=True,
            )
        )

        worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        rtsp_streams.assert_not_called()
        video_delete.assert_called_once()

    def test_no_capabilities_registers_nothing(self, patched_register_fns):
        """A profile that explicitly opts out of every capability still has
        a valid streaming_ingest block; the dispatcher just registers no
        custom routes. Useful for boot-time smoke tests."""
        videos_for_search, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(_streaming_ingest())

        worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        rtsp_streams.assert_not_called()
        video_delete.assert_not_called()

    def test_missing_streaming_ingest_raises(self, patched_register_fns):
        """Q4 of the design — fail loudly. Every profile must declare
        streaming_ingest so a misconfigured profile can't silently boot
        with no custom routes."""
        videos_for_search, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(_MISSING)

        with pytest.raises(ValueError, match="streaming_ingest"):
            worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        rtsp_streams.assert_not_called()
        video_delete.assert_not_called()

    def test_legacy_stream_mode_in_yaml_raises(self, patched_register_fns):
        """A profile YAML that still carries the legacy `stream_mode` knob
        on streaming_ingest must fail loudly at startup. ``StreamingIngestConfig``
        accepts extra fields (``extra="allow"``) so the value lands in
        ``model_extra``; the dispatcher rejects it and points at the new
        per-route capability flags.
        """
        videos_for_search, rtsp_streams, video_delete = patched_register_fns
        cfg = StreamingIngestConfig(
            enable_videos_for_search=True,
            enable_rtsp_streams=True,
            enable_video_delete=True,
            stream_mode="search",
        )
        worker = _make_worker(cfg)

        with pytest.raises(ValueError, match="stream_mode is no longer supported"):
            worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        rtsp_streams.assert_not_called()
        video_delete.assert_not_called()

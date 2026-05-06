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
"""Unit tests for the route dispatcher in CustomFastApiFrontEndWorker.

The dispatcher registers four sets of routes on every profile:
  * ``register_video_upload_complete`` — universal /complete endpoint
  * ``register_rtsp_stream_api_routes`` — RTSP add/delete
  * ``register_video_delete_routes`` — DELETE /videos/{video_id}

… plus, only on profiles that opt in with ``enable_videos_for_search: true``,
the deprecated ``/api/v1/videos-for-search/*`` routes (search profile).
"""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.api.custom_fastapi_worker import CustomFastApiFrontEndWorker
from vss_agents.api.front_end_config import StreamingIngestConfig

_MISSING = object()


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
    worker._config = config
    return worker


def _streaming_ingest(*, enable_videos_for_search: bool = False):
    cfg = MagicMock()
    cfg.enable_videos_for_search = enable_videos_for_search
    return cfg


@pytest.fixture
def patched_register_fns():
    """Patch every register fn the dispatcher delegates to.

    Returns a 4-tuple in the order:
        (videos_for_search, video_upload_complete, rtsp_streams, video_delete)
    """
    with (
        patch("vss_agents.api.custom_fastapi_worker.register_video_search_ingest_routes") as videos_for_search,
        patch("vss_agents.api.custom_fastapi_worker.register_video_upload_complete") as video_upload_complete,
        patch("vss_agents.api.custom_fastapi_worker.register_rtsp_stream_api_routes") as rtsp_streams,
        patch("vss_agents.api.custom_fastapi_worker.register_video_delete_routes") as video_delete,
    ):
        yield videos_for_search, video_upload_complete, rtsp_streams, video_delete


class TestRegisterStreamingRoutesDispatcher:
    """``CustomFastApiFrontEndWorker._register_streaming_routes``."""

    def test_universal_routes_register_unconditionally(self, patched_register_fns):
        """upload-complete + RTSP + video-delete fire on every profile, with
        no per-profile flag. Each handler self-skips downstream calls when its
        backing service isn't configured."""
        videos_for_search, video_upload_complete, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(_streaming_ingest())

        worker._register_streaming_routes(MagicMock())

        video_upload_complete.assert_called_once()
        rtsp_streams.assert_called_once()
        video_delete.assert_called_once()
        # Search-only deprecated route stays gated.
        videos_for_search.assert_not_called()

    def test_search_profile_also_registers_videos_for_search(self, patched_register_fns):
        """enable_videos_for_search: true (search profile) additionally
        registers the deprecated /api/v1/videos-for-search/* routes on top of
        the universal set."""
        videos_for_search, video_upload_complete, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(_streaming_ingest(enable_videos_for_search=True))

        worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_called_once()
        video_upload_complete.assert_called_once()
        rtsp_streams.assert_called_once()
        video_delete.assert_called_once()

    def test_missing_streaming_ingest_raises(self, patched_register_fns):
        """Every profile must declare streaming_ingest so a misconfigured
        profile can't silently boot with no custom routes."""
        videos_for_search, video_upload_complete, rtsp_streams, video_delete = patched_register_fns
        worker = _make_worker(_MISSING)

        with pytest.raises(ValueError, match="streaming_ingest"):
            worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        video_upload_complete.assert_not_called()
        rtsp_streams.assert_not_called()
        video_delete.assert_not_called()

    def test_legacy_stream_mode_in_yaml_raises(self, patched_register_fns):
        """A profile YAML that still carries the legacy ``stream_mode`` knob
        on streaming_ingest must fail loudly at startup."""
        videos_for_search, video_upload_complete, rtsp_streams, video_delete = patched_register_fns
        cfg = StreamingIngestConfig(enable_videos_for_search=True, stream_mode="search")
        worker = _make_worker(cfg)

        with pytest.raises(ValueError, match="stream_mode is no longer supported"):
            worker._register_streaming_routes(MagicMock())

        videos_for_search.assert_not_called()
        video_upload_complete.assert_not_called()
        rtsp_streams.assert_not_called()
        video_delete.assert_not_called()

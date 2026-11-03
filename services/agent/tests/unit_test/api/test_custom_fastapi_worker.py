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

The dispatcher registers nine sets of routes on every profile, with no
per-profile capability flags:

  * ``register_video_upload``               — POST /api/v1/videos
  * ``register_video_upload_complete``      — POST /api/v1/videos/{sensor_id}/complete
  * ``register_video_search_ingest_routes`` — PUT  /api/v1/videos-for-search/{filename} (deprecated)
  * ``register_rtsp_ingest_routes``         — POST /api/v1/rtsp-streams/add
  * ``register_rtsp_delete_routes``         — DELETE /api/v1/rtsp-streams/delete/{name}
  * ``register_video_delete_routes``        — DELETE /api/v1/videos/{video_id}
  * ``register_incident_analyze_routes``    — POST /api/v1/incidents/{incident_id}/analyze
  * ``register_incident_search_routes``     — POST /api/v1/incidents/search
  * ``register_incident_chat_routes``       — POST /api/v1/incidents/chat
"""

from types import SimpleNamespace
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


@pytest.fixture
def patched_register_fns():
    """Patch every register fn the dispatcher delegates to.

    Returns a 9-tuple in the order:
        (video_upload, video_upload_complete, video_search_ingest,
         rtsp_ingest, rtsp_delete, video_delete, incident_analyze,
         incident_search, incident_chat)
    """
    with (
        patch("vss_agents.api.custom_fastapi_worker.register_video_upload") as video_upload,
        patch("vss_agents.api.custom_fastapi_worker.register_video_upload_complete") as video_upload_complete,
        patch("vss_agents.api.custom_fastapi_worker.register_video_search_ingest_routes") as video_search_ingest,
        patch("vss_agents.api.custom_fastapi_worker.register_rtsp_ingest_routes") as rtsp_ingest,
        patch("vss_agents.api.custom_fastapi_worker.register_rtsp_delete_routes") as rtsp_delete,
        patch("vss_agents.api.custom_fastapi_worker.register_video_delete_routes") as video_delete,
        patch("vss_agents.api.custom_fastapi_worker.register_incident_analyze_routes") as incident_analyze,
        patch("vss_agents.api.custom_fastapi_worker.register_incident_search_routes") as incident_search,
        patch("vss_agents.api.custom_fastapi_worker.register_incident_chat_routes") as incident_chat,
    ):
        yield (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
            incident_analyze,
            incident_search,
            incident_chat,
        )


class TestRegisterStreamingRoutesDispatcher:
    """``CustomFastApiFrontEndWorker._register_streaming_routes``."""

    def test_chart_generator_llm_name_reaches_the_chart_route(self, patched_register_fns):
        """The chart-request route gets the ``llm_name`` of the configured chart_generator function."""
        worker = _make_worker(MagicMock())
        worker._config.functions = {"chart_generator": SimpleNamespace(llm_name="nim_llm")}

        with patch("vss_agents.api.custom_fastapi_worker.register_incident_charts_routes") as charts:
            worker._register_streaming_routes(MagicMock(), MagicMock())

        charts.assert_called_once()
        assert charts.call_args.args[2] == "nim_llm"

    def test_no_chart_generator_passes_no_llm_name(self, patched_register_fns):
        worker = _make_worker(MagicMock())
        worker._config.functions = {}

        with patch("vss_agents.api.custom_fastapi_worker.register_incident_charts_routes") as charts:
            worker._register_streaming_routes(MagicMock(), MagicMock())

        assert charts.call_args.args[2] is None

    def test_universal_routes_register_unconditionally(self, patched_register_fns):
        """All register fns fire on every profile, with no per-profile
        flag. Each handler self-skips downstream calls when its backing
        service isn't configured."""
        (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
            incident_analyze,
            incident_search,
            incident_chat,
        ) = patched_register_fns
        worker = _make_worker(MagicMock())  # any non-None streaming_ingest

        worker._register_streaming_routes(MagicMock(), MagicMock())

        video_upload.assert_called_once()
        video_upload_complete.assert_called_once()
        video_search_ingest.assert_called_once()
        rtsp_ingest.assert_called_once()
        rtsp_delete.assert_called_once()
        video_delete.assert_called_once()
        incident_analyze.assert_called_once()
        incident_search.assert_called_once()
        incident_chat.assert_called_once()

    def test_missing_streaming_ingest_raises(self, patched_register_fns):
        """Every profile must declare streaming_ingest so a misconfigured
        profile can't silently boot with no custom routes."""
        (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
            incident_analyze,
            incident_search,
            incident_chat,
        ) = patched_register_fns
        worker = _make_worker(_MISSING)

        with pytest.raises(ValueError, match="streaming_ingest"):
            worker._register_streaming_routes(MagicMock(), MagicMock())

        video_upload.assert_not_called()
        video_upload_complete.assert_not_called()
        video_search_ingest.assert_not_called()
        rtsp_ingest.assert_not_called()
        rtsp_delete.assert_not_called()
        video_delete.assert_not_called()
        incident_analyze.assert_not_called()
        incident_search.assert_not_called()
        incident_chat.assert_not_called()

    def test_legacy_stream_mode_in_yaml_raises(self, patched_register_fns):
        """A profile YAML that still carries the legacy ``stream_mode`` knob
        on streaming_ingest must fail loudly at startup."""
        (
            video_upload,
            video_upload_complete,
            video_search_ingest,
            rtsp_ingest,
            rtsp_delete,
            video_delete,
            incident_analyze,
            incident_search,
            incident_chat,
        ) = patched_register_fns
        cfg = StreamingIngestConfig(stream_mode="search")
        worker = _make_worker(cfg)

        with pytest.raises(ValueError, match="stream_mode is no longer supported"):
            worker._register_streaming_routes(MagicMock(), MagicMock())

        video_upload.assert_not_called()
        video_upload_complete.assert_not_called()
        video_search_ingest.assert_not_called()
        rtsp_ingest.assert_not_called()
        rtsp_delete.assert_not_called()
        video_delete.assert_not_called()
        incident_analyze.assert_not_called()
        incident_search.assert_not_called()
        incident_chat.assert_not_called()

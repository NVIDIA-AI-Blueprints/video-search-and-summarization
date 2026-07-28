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

"""Unit tests for the VST storage, sensor-mapping and snapshot helpers.

The time-window computation in ``ITS_VST_HANDLER`` already has its own tests
(``test_its_vst_handler.py``); this file covers the rest of the handler — the
parts that talk to VST over HTTP.

What is worth pinning:

* **``STORAGE_MODULE_ENDPOINT`` fully overrides the configured storage base**
  and is ``expandvars``-expanded, because deployments set it to
  ``http://${HOST_IP}:${STORAGE_HTTP_PORT}``. Getting the precedence wrong
  points media lookups at the wrong host.
* **``ALERT_REVIEW_MEDIA_BASE_DIR`` re-roots the returned media path**, and the
  VST-supplied path is stripped of its leading ``/`` first — otherwise
  ``os.path.join`` discards the base dir entirely and the alert-review UI gets
  a container-local path it cannot open.
* **The sensor-details cache has a TTL.** ``_get_stream_id_from_name`` is on
  the hot path for every event; without the cache each event would hit the VST
  sensor list.
* **Every network helper degrades to ``None`` / ``{}`` / ``False``** rather
  than raising, because they run inside the per-event handler loop.

``requests`` is patched throughout; no HTTP request is issued.
"""

import base64
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest
import requests

from vst.its_vst_handler import ITS_VST_HANDLER

BASE_CONFIG = {
    "vst_config": {
        "base_url": "http://vst:30011",
        "sensor_list_endpoint": "/api/v1/sensor/list",
    }
}


def make_handler(config=None):
    return ITS_VST_HANDLER(config if config is not None else {"vst_config": dict(BASE_CONFIG["vst_config"])})


def make_response(status_code=200, json_body=None, content=b"", headers=None):
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = json_body
    response.content = content
    response.headers = headers or {}
    if status_code >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(f"{status_code}")
    return response


SENSOR_DATA = [
    {
        "cam-1": [
            {"name": "Lobby", "streamId": "stream-lobby", "vodUrl": "rtsp://vst/lobby"},
            {"name": "Dock", "streamId": "stream-dock"},
        ]
    }
]


@pytest.fixture
def handler():
    return make_handler()


class TestConstruction:
    def test_defaults(self, handler):
        assert handler.base_url == "http://vst:30011"
        assert handler._cache_duration == 60
        assert handler.add_overlay is False
        assert handler.url_retention_minutes == 1440

    def test_overlay_defaults(self, handler):
        assert handler.overlay_config == {
            "color": "green",
            "thickness": 5,
            "opacity": 254,
            "debug": True,
            "showObjId": False,
            "objIdPosition": 0,
        }

    def test_overlay_config_is_read_from_config(self):
        config = {
            "vst_config": dict(
                BASE_CONFIG["vst_config"],
                overlay={"color": "red", "thickness": 2, "showObjId": True},
            )
        }
        handler = make_handler(config)

        assert handler.overlay_config["color"] == "red"
        assert handler.overlay_config["thickness"] == 2
        assert handler.overlay_config["showObjId"] is True
        assert handler.overlay_config["opacity"] == 254  # untouched default

    def test_missing_base_url_raises(self):
        with pytest.raises(ValueError, match="VST base URL not configured"):
            ITS_VST_HANDLER({"vst_config": {}})

    def test_missing_vst_config_raises(self):
        with pytest.raises(ValueError, match="VST base URL not configured"):
            ITS_VST_HANDLER({})

    def test_cache_starts_empty(self, handler):
        assert handler._vst_stream_status_cache == {}


class TestGetStorageConfig:
    def test_falls_back_to_the_vst_base_url(self, handler):
        base, endpoint, timeout = handler._get_storage_config()

        assert base == "http://vst:30011"
        assert endpoint == "/api/v1/storage/file/path"
        assert timeout == 10

    def test_storage_section_overrides_the_base_url(self):
        config = {
            "vst_config": dict(
                BASE_CONFIG["vst_config"],
                storage={"base_url": "http://storage:8080", "media_file_path_by_id_endpoint": "/p"},
                request_timeout=30,
            )
        }
        base, endpoint, timeout = make_handler(config)._get_storage_config()

        assert base == "http://storage:8080"
        assert endpoint == "/p"
        assert timeout == 30

    def test_env_endpoint_wins_over_config(self, handler):
        with patch.dict(os.environ, {"STORAGE_MODULE_ENDPOINT": "http://env-host:9000"}):
            base, _endpoint, _timeout = handler._get_storage_config()

        assert base == "http://env-host:9000"

    def test_env_endpoint_is_variable_expanded(self, handler):
        env = {
            "STORAGE_MODULE_ENDPOINT": "http://${HOST_IP}:${STORAGE_HTTP_PORT}",
            "HOST_IP": "10.0.0.5",
            "STORAGE_HTTP_PORT": "8080",
        }
        with patch.dict(os.environ, env):
            base, _endpoint, _timeout = handler._get_storage_config()

        assert base == "http://10.0.0.5:8080"

    def test_unset_variables_are_left_verbatim(self, handler):
        with patch.dict(os.environ, {"STORAGE_MODULE_ENDPOINT": "http://${NOT_SET}:9000"}):
            base, _endpoint, _timeout = handler._get_storage_config()

        assert base == "http://${NOT_SET}:9000"

    def test_a_blank_env_endpoint_is_ignored(self, handler):
        with patch.dict(os.environ, {"STORAGE_MODULE_ENDPOINT": ""}):
            base, _endpoint, _timeout = handler._get_storage_config()

        assert base == "http://vst:30011"


class TestRequestStorageLookup:
    def test_returns_the_parsed_body(self, handler):
        with patch("requests.get", return_value=make_response(json_body={"mediaFilePath": "/m.mp4"}, content=b"{}")) as get:
            assert handler._request_storage_lookup("http://s/p", "vst-1", 10) == (
                {"mediaFilePath": "/m.mp4"}
            )

        assert get.call_args.kwargs["params"] == {"id": "vst-1"}
        assert get.call_args.kwargs["timeout"] == 10

    def test_an_empty_body_becomes_an_empty_dict(self, handler):
        with patch("requests.get", return_value=make_response(content=b"")):
            assert handler._request_storage_lookup("http://s/p", "vst-1", 10) == {}

    def test_http_errors_propagate(self, handler):
        with patch("requests.get", return_value=make_response(status_code=500)):
            with pytest.raises(requests.HTTPError):
                handler._request_storage_lookup("http://s/p", "vst-1", 10)


class TestExtractMediaPath:
    @pytest.mark.parametrize("key", ["mediaFilePath", "media_file_path", "mediafilepath"])
    def test_every_key_variant_is_accepted(self, handler, key):
        assert handler._extract_media_path({key: "/videos/a.mp4"}) == "/videos/a.mp4"

    def test_camel_case_wins(self, handler):
        payload = {"mediaFilePath": "/camel.mp4", "media_file_path": "/snake.mp4"}
        assert handler._extract_media_path(payload) == "/camel.mp4"

    def test_missing_key_returns_none(self, handler):
        assert handler._extract_media_path({"other": 1}) is None


class TestResolveMediaPath:
    def test_without_a_base_dir_the_path_is_returned_verbatim(self, handler):
        with patch.dict(os.environ, {}, clear=True):
            assert handler._resolve_media_path("vst-1", "/videos/a.mp4") == "/videos/a.mp4"

    def test_env_base_dir_re_roots_the_path(self, handler):
        with patch.dict(os.environ, {"ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/media"}):
            assert handler._resolve_media_path("vst-1", "/videos/a.mp4") == "/mnt/media/videos/a.mp4"

    def test_config_base_dir_is_used_when_the_env_is_unset(self):
        config = {
            "vst_config": dict(BASE_CONFIG["vst_config"]),
            "ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/cfg",
        }
        handler = make_handler(config)

        with patch.dict(os.environ, {}, clear=True):
            assert handler._resolve_media_path("vst-1", "/videos/a.mp4") == "/mnt/cfg/videos/a.mp4"

    def test_env_wins_over_config(self):
        config = {
            "vst_config": dict(BASE_CONFIG["vst_config"]),
            "ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/cfg",
        }
        handler = make_handler(config)

        with patch.dict(os.environ, {"ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/env"}):
            assert handler._resolve_media_path("vst-1", "/videos/a.mp4").startswith("/mnt/env")

    def test_the_leading_slash_is_stripped_before_joining(self, handler):
        """Without this, os.path.join would discard the base dir."""
        with patch.dict(os.environ, {"ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/media"}):
            assert handler._resolve_media_path("vst-1", "/a.mp4") == "/mnt/media/a.mp4"

    def test_a_relative_path_is_joined_as_is(self, handler):
        with patch.dict(os.environ, {"ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/media"}):
            assert handler._resolve_media_path("vst-1", "a.mp4") == "/mnt/media/a.mp4"


class TestGetMediaFilePathByVstId:
    @pytest.mark.parametrize("vst_id", [None, "", 42, []])
    def test_an_invalid_id_returns_none_without_a_request(self, handler, vst_id):
        with patch("requests.get", side_effect=AssertionError("must not request")):
            assert handler.get_media_file_path_by_vst_id(vst_id) is None

    def test_happy_path(self, handler):
        response = make_response(json_body={"mediaFilePath": "/videos/a.mp4"}, content=b"{}")
        with patch("requests.get", return_value=response), patch.dict(os.environ, {}, clear=True):
            assert handler.get_media_file_path_by_vst_id("vst-1") == "/videos/a.mp4"

    def test_the_lookup_url_is_assembled_from_base_and_endpoint(self, handler):
        response = make_response(json_body={"mediaFilePath": "/a.mp4"}, content=b"{}")
        with patch("requests.get", return_value=response) as get, patch.dict(
            os.environ, {}, clear=True
        ):
            handler.get_media_file_path_by_vst_id("vst-1")

        assert get.call_args.args[0] == "http://vst:30011/api/v1/storage/file/path"

    def test_a_payload_without_a_media_path_returns_none(self, handler):
        with patch("requests.get", return_value=make_response(json_body={}, content=b"{}")):
            assert handler.get_media_file_path_by_vst_id("vst-1") is None

    def test_a_network_error_returns_none(self, handler):
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            assert handler.get_media_file_path_by_vst_id("vst-1") is None

    def test_an_unexpected_error_returns_none(self, handler):
        with patch("requests.get", side_effect=RuntimeError("boom")):
            assert handler.get_media_file_path_by_vst_id("vst-1") is None

    def test_the_base_dir_mapping_is_applied(self, handler):
        response = make_response(json_body={"mediaFilePath": "/videos/a.mp4"}, content=b"{}")
        with patch("requests.get", return_value=response), patch.dict(
            os.environ, {"ALERT_REVIEW_MEDIA_BASE_DIR": "/mnt/media"}
        ):
            assert handler.get_media_file_path_by_vst_id("vst-1") == "/mnt/media/videos/a.mp4"


class TestGetVstSensorDetails:
    def test_fetches_and_caches(self, handler):
        with patch("requests.get", return_value=make_response(json_body=SENSOR_DATA)) as get:
            first = handler.get_vst_sensor_details("http://vst/sensors")
            second = handler.get_vst_sensor_details("http://vst/sensors")

        assert first == SENSOR_DATA
        assert second == SENSOR_DATA
        assert get.call_count == 1

    def test_the_cache_expires(self, handler):
        handler._cache_duration = 60
        with patch("requests.get", return_value=make_response(json_body=SENSOR_DATA)) as get:
            with patch("time.time", side_effect=[1000.0, 1100.0]):
                handler.get_vst_sensor_details("http://vst/sensors")
                handler.get_vst_sensor_details("http://vst/sensors")

        assert get.call_count == 2

    def test_different_urls_are_cached_separately(self, handler):
        with patch("requests.get", return_value=make_response(json_body=SENSOR_DATA)) as get:
            handler.get_vst_sensor_details("http://vst/a")
            handler.get_vst_sensor_details("http://vst/b")

        assert get.call_count == 2

    def test_http_errors_propagate(self, handler):
        with patch("requests.get", return_value=make_response(status_code=500)):
            with pytest.raises(requests.HTTPError):
                handler.get_vst_sensor_details("http://vst/sensors")


class TestSensorMappings:
    def test_name_to_stream_id_mapping(self, handler):
        assert handler.build_sensor_id_sensor_name_mapping(SENSOR_DATA) == {
            "Lobby": "stream-lobby",
            "Dock": "stream-dock",
        }

    def test_entries_without_a_name_are_skipped(self, handler):
        data = [{"cam-1": [{"streamId": "s-1"}]}]
        assert handler.build_sensor_id_sensor_name_mapping(data) == {}

    def test_empty_sensor_data_yields_an_empty_mapping(self, handler):
        assert handler.build_sensor_id_sensor_name_mapping([]) == {}

    def test_name_to_rtsp_mapping_skips_entries_without_a_vod_url(self, handler):
        assert handler.build_sensor_name_rtsp_url_mapping(SENSOR_DATA) == {
            "Lobby": "rtsp://vst/lobby"
        }

    def test_blank_vod_url_is_skipped(self, handler):
        data = [{"cam-1": [{"name": "Lobby", "vodUrl": ""}]}]
        assert handler.build_sensor_name_rtsp_url_mapping(data) == {}

    def test_empty_sensor_data_yields_an_empty_rtsp_mapping(self, handler):
        assert handler.build_sensor_name_rtsp_url_mapping([]) == {}


class TestGetVstRtspUrls:
    def test_returns_the_name_to_rtsp_mapping(self, handler):
        with patch("requests.get", return_value=make_response(json_body=SENSOR_DATA)):
            assert handler.get_vst_rtsp_urls() == {"Lobby": "rtsp://vst/lobby"}

    def test_a_fetch_failure_degrades_to_an_empty_mapping(self, handler):
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            assert handler.get_vst_rtsp_urls() == {}


class TestGetStreamIdFromName:
    def test_resolves_a_known_sensor(self, handler):
        with patch("requests.get", return_value=make_response(json_body=SENSOR_DATA)):
            assert handler._get_stream_id_from_name("Lobby") == "stream-lobby"

    def test_an_unknown_sensor_returns_none(self, handler):
        with patch("requests.get", return_value=make_response(json_body=SENSOR_DATA)):
            assert handler._get_stream_id_from_name("Nope") is None

    def test_a_fetch_failure_returns_none(self, handler):
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            assert handler._get_stream_id_from_name("Lobby") is None

    def test_missing_endpoint_config_returns_none(self):
        handler = make_handler({"vst_config": {"base_url": "http://vst:30011"}})
        assert handler._get_stream_id_from_name("Lobby") is None


class TestBuildVstUrls:
    def test_builds_a_url_per_entry(self, handler):
        results = [
            {
                "sensor_id": "cam-1",
                "start": "2021-01-01T00:00:10.000000Z",
                "end": "2021-01-01T00:00:20.000000Z",
            }
        ]

        out = handler.build_vst_urls(results, {"cam-1": "stream-lobby"}, "http://vst/vod")

        assert out[0]["url"].startswith("http://vst/vod/stream-lobby?startTime=")
        assert "container=mp4" in out[0]["url"]

    def test_the_buffer_widens_the_window(self, handler):
        results = [
            {
                "sensor_id": "cam-1",
                "start": "2021-01-01T00:01:00.000000Z",
                "end": "2021-01-01T00:01:10.000000Z",
            }
        ]

        out = handler.build_vst_urls(
            results, {"cam-1": "s-1"}, "http://vst/vod", video_buffer_time=30
        )

        assert "startTime=2021-01-01T00:00:30" in out[0]["url"]

    def test_an_unmapped_sensor_is_skipped(self, handler):
        results = [
            {
                "sensor_id": "unknown",
                "start": "2021-01-01T00:00:10.000000Z",
                "end": "2021-01-01T00:00:20.000000Z",
            }
        ]

        assert "url" not in handler.build_vst_urls(results, {}, "http://vst/vod")[0]

    def test_a_future_end_time_is_capped_to_now(self, handler):
        results = [
            {
                "sensor_id": "cam-1",
                "start": "2099-01-01T00:00:00.000000Z",
                "end": "2099-01-01T00:01:00.000000Z",
            }
        ]

        out = handler.build_vst_urls(results, {"cam-1": "s-1"}, "http://vst/vod")

        assert "endTime=2099" not in out[0]["url"]


class TestBuildSnapshotUrl:
    def test_default_endpoints(self, handler):
        assert handler._build_snapshot_url("s-1") == (
            "http://vst:30011/live/stream/s-1/picture"
        )

    def test_configured_endpoints(self):
        config = {
            "vst_config": dict(
                BASE_CONFIG["vst_config"], live_endpoint="/l", picture_endpoint="/pic"
            )
        }
        assert make_handler(config)._build_snapshot_url("s-1") == "http://vst:30011/l/stream/s-1/pic"

    def test_a_trailing_slash_on_the_base_is_stripped(self):
        config = {"vst_config": dict(BASE_CONFIG["vst_config"], base_url="http://vst:30011/")}
        assert make_handler(config)._build_snapshot_url("s-1").startswith("http://vst:30011/live")

    @pytest.mark.parametrize("stream_id", [None, ""])
    def test_a_blank_stream_id_returns_none(self, handler, stream_id):
        assert handler._build_snapshot_url(stream_id) is None


class TestDownloadSnapshot:
    def test_direct_image_response(self, handler):
        response = make_response(content=b"\x89PNG", headers={"content-type": "image/png"})
        with patch("requests.get", return_value=response):
            assert handler._download_snapshot("http://vst/pic") == b"\x89PNG"

    def test_json_response_with_base64_data(self, handler):
        encoded = base64.b64encode(b"\x89PNG").decode()
        response = make_response(
            json_body={"status": "success", "data": encoded},
            headers={"content-type": "application/json"},
        )
        with patch("requests.get", return_value=response):
            assert handler._download_snapshot("http://vst/pic") == b"\x89PNG"

    def test_a_json_error_status_returns_none(self, handler):
        response = make_response(
            json_body={"status": "error"}, headers={"content-type": "application/json"}
        )
        with patch("requests.get", return_value=response):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_json_without_data_returns_none(self, handler):
        response = make_response(
            json_body={"status": "success", "data": ""},
            headers={"content-type": "application/json"},
        )
        with patch("requests.get", return_value=response):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_undecodable_json_returns_none(self, handler):
        response = make_response(headers={"content-type": "application/json"})
        response.json.side_effect = ValueError("not json")
        with patch("requests.get", return_value=response):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_an_unexpected_content_type_returns_none(self, handler):
        response = make_response(headers={"content-type": "text/html"})
        with patch("requests.get", return_value=response):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_a_missing_content_type_returns_none(self, handler):
        with patch("requests.get", return_value=make_response()):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_a_network_error_returns_none(self, handler):
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_an_http_error_returns_none(self, handler):
        with patch("requests.get", return_value=make_response(status_code=404)):
            assert handler._download_snapshot("http://vst/pic") is None

    def test_the_configured_timeout_is_used(self):
        config = {"vst_config": dict(BASE_CONFIG["vst_config"], request_timeout=3)}
        handler = make_handler(config)
        response = make_response(content=b"x", headers={"content-type": "image/png"})

        with patch("requests.get", return_value=response) as get:
            handler._download_snapshot("http://vst/pic")

        assert get.call_args.kwargs["timeout"] == 3


class TestGetStreamSnapshot:
    def test_happy_path(self, handler):
        with patch.object(handler, "_get_stream_id_from_name", return_value="s-1"), patch.object(
            handler, "_download_snapshot", return_value=b"\x89PNG"
        ):
            result = handler._get_stream_snapshot("Lobby")

        assert result == ("s-1", "http://vst:30011/live/stream/s-1/picture", b"\x89PNG")

    def test_an_unknown_sensor_returns_none(self, handler):
        with patch.object(handler, "_get_stream_id_from_name", return_value=None):
            assert handler._get_stream_snapshot("Nope") is None

    def test_a_failed_url_build_returns_none(self, handler):
        with patch.object(handler, "_get_stream_id_from_name", return_value="s-1"), patch.object(
            handler, "_build_snapshot_url", return_value=None
        ):
            assert handler._get_stream_snapshot("Lobby") is None

    def test_a_failed_download_returns_none(self, handler):
        with patch.object(handler, "_get_stream_id_from_name", return_value="s-1"), patch.object(
            handler, "_download_snapshot", return_value=None
        ):
            assert handler._get_stream_snapshot("Lobby") is None

    def test_an_unexpected_error_returns_none(self, handler):
        with patch.object(
            handler, "_get_stream_id_from_name", side_effect=RuntimeError("boom")
        ):
            assert handler._get_stream_snapshot("Lobby") is None


class TestCheckTimeInRecording:
    TIMELINES = [
        {"startTime": "2021-01-01T00:00:00.000Z", "endTime": "2021-01-01T01:00:00.000Z"}
    ]

    def test_both_endpoints_inside_a_timeline(self, handler):
        with patch("requests.get", return_value=make_response(json_body=self.TIMELINES)):
            assert handler.check_time_in_recording(
                "s-1", "2021-01-01T00:10:00Z", "2021-01-01T00:20:00Z"
            ) is True

    def test_the_stream_id_header_is_sent(self, handler):
        with patch("requests.get", return_value=make_response(json_body=self.TIMELINES)) as get:
            handler.check_time_in_recording("s-1", "2021-01-01T00:10:00Z", "2021-01-01T00:20:00Z")

        assert get.call_args.kwargs["headers"]["streamId"] == "s-1"
        assert get.call_args.args[0] == "http://vst:30011/vst/api/v1/record/s-1/timelines"

    def test_a_window_outside_the_timeline_is_false(self, handler):
        with patch("requests.get", return_value=make_response(json_body=self.TIMELINES)):
            assert handler.check_time_in_recording(
                "s-1", "2021-01-02T00:10:00Z", "2021-01-02T00:20:00Z"
            ) is False

    def test_a_partially_covered_window_is_false(self, handler):
        with patch("requests.get", return_value=make_response(json_body=self.TIMELINES)):
            assert handler.check_time_in_recording(
                "s-1", "2021-01-01T00:10:00Z", "2021-01-01T02:00:00Z"
            ) is False

    def test_endpoints_may_span_two_timelines(self, handler):
        timelines = [
            {"startTime": "2021-01-01T00:00:00.000Z", "endTime": "2021-01-01T01:00:00.000Z"},
            {"startTime": "2021-01-01T01:00:00.000Z", "endTime": "2021-01-01T02:00:00.000Z"},
        ]
        with patch("requests.get", return_value=make_response(json_body=timelines)):
            assert handler.check_time_in_recording(
                "s-1", "2021-01-01T00:30:00Z", "2021-01-01T01:30:00Z"
            ) is True

    def test_no_timelines_is_false(self, handler):
        with patch("requests.get", return_value=make_response(json_body=[])):
            assert handler.check_time_in_recording("s-1", "2021-01-01T00:10:00Z", "x") is False

    def test_a_malformed_timeline_is_false(self, handler):
        with patch("requests.get", return_value=make_response(json_body=[{"startTime": "x"}])):
            assert handler.check_time_in_recording(
                "s-1", "2021-01-01T00:10:00Z", "2021-01-01T00:20:00Z"
            ) is False

    def test_offset_timestamps_are_converted_to_utc(self, handler):
        with patch("requests.get", return_value=make_response(json_body=self.TIMELINES)):
            assert handler.check_time_in_recording(
                "s-1", "2021-01-01T01:10:00+01:00", "2021-01-01T01:20:00+01:00"
            ) is True

    def test_an_unparseable_timestamp_is_false(self, handler):
        with patch("requests.get", return_value=make_response(json_body=self.TIMELINES)):
            assert handler.check_time_in_recording("s-1", "not-a-time", "x") is False

    def test_a_network_error_is_false(self, handler):
        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            assert handler.check_time_in_recording("s-1", "2021-01-01T00:10:00Z", "x") is False


class TestCheckTimeInRecordingWithRetries:
    def test_returns_on_the_first_success(self, handler):
        latency = {}
        with patch.object(handler, "check_time_in_recording", return_value=True) as check:
            assert handler.check_time_in_recording_with_retries("s-1", "a", "b", latency) is True

        assert check.call_count == 1
        assert latency["stream_existence_validation"]["success"] is True

    def test_retries_up_to_the_configured_maximum(self, handler):
        latency = {}
        with patch.object(handler, "check_time_in_recording", return_value=False) as check, patch(
            "time.sleep"
        ):
            assert handler.check_time_in_recording_with_retries("s-1", "a", "b", latency) is False

        assert check.call_count == 3
        assert latency["stream_existence_validation"]["success"] is False

    def test_the_maximum_is_configurable(self):
        config = {"vst_config": dict(BASE_CONFIG["vst_config"], recording_check_max_attempts=1)}
        handler = make_handler(config)

        with patch.object(handler, "check_time_in_recording", return_value=False) as check, patch(
            "time.sleep"
        ):
            handler.check_time_in_recording_with_retries("s-1", "a", "b", {})

        assert check.call_count == 1

    def test_a_late_success_still_returns_true(self, handler):
        with patch.object(
            handler, "check_time_in_recording", side_effect=[False, True]
        ) as check, patch("time.sleep"):
            assert handler.check_time_in_recording_with_retries("s-1", "a", "b", {}) is True

        assert check.call_count == 2

    def test_the_latency_duration_is_recorded(self, handler):
        latency = {}
        with patch.object(handler, "check_time_in_recording", return_value=True):
            handler.check_time_in_recording_with_retries("s-1", "a", "b", latency)

        assert isinstance(latency["stream_existence_validation"]["duration"], float)


class TestDownloadClips:
    def _entry(self, url="http://vst/temp/clip.mp4?token=1"):
        return {"request_id": "req-1", "url": url}

    @pytest.fixture
    def handler(self, tmp_path):
        config = {
            "vst_config": dict(BASE_CONFIG["vst_config"], download_dir=str(tmp_path / "clips"))
        }
        return make_handler(config)

    def test_downloads_each_clip(self, handler, tmp_path):
        response = make_response()
        response.iter_content.return_value = [b"abc"]
        entries = [self._entry()]

        with patch("requests.get", return_value=response):
            out = handler.download_clips(entries)

        assert out[0]["download_status"] == "Complete"
        assert out[0]["file_path"].endswith("clip.mp4_req-1.mp4")
        with open(out[0]["file_path"], "rb") as handle:
            assert handle.read() == b"abc"

    def test_the_download_directory_is_created(self, handler, tmp_path):
        response = make_response()
        response.iter_content.return_value = [b"abc"]

        with patch("requests.get", return_value=response):
            handler.download_clips([self._entry()])

        assert (tmp_path / "clips").is_dir()

    def test_an_entry_without_a_url_is_skipped(self, handler):
        entries = [{"request_id": "req-1"}]

        with patch("requests.get", side_effect=AssertionError("must not request")):
            out = handler.download_clips(entries)

        assert "download_status" not in out[0]

    def test_a_failed_download_is_retried_once(self, handler):
        with patch(
            "requests.get", side_effect=requests.ConnectionError("refused")
        ) as get, patch("time.sleep"):
            out = handler.download_clips([self._entry()])

        assert get.call_count == 2
        assert out[0]["download_status"] == "Not Complete"

    def test_a_successful_retry_marks_the_entry_complete(self, handler):
        response = make_response()
        response.iter_content.return_value = [b"abc"]

        with patch(
            "requests.get", side_effect=[requests.ConnectionError("refused"), response]
        ), patch("time.sleep"):
            out = handler.download_clips([self._entry()])

        assert out[0]["download_status"] == "Complete"

    def test_an_empty_batch_is_tolerated(self, handler):
        assert handler.download_clips([]) == []


class TestDownloadImage:
    @pytest.fixture
    def handler(self):
        config = {
            "vst_config": dict(
                BASE_CONFIG["vst_config"],
                image_api_base_url="http://vst/live/stream",
                image_download_retry_delay=0,
            )
        }
        return make_handler(config)

    def test_downloads_on_the_first_attempt(self, handler, tmp_path):
        output = tmp_path / "img" / "frame.jpg"
        response = make_response(content=b"\xff\xd8jpeg")

        with patch("requests.get", return_value=response), patch("time.sleep"):
            assert handler._download_image("2021-01-01T00:00:00Z", "s-1", str(output)) is True

        assert output.read_bytes() == b"\xff\xd8jpeg"

    def test_the_output_directory_is_created(self, handler, tmp_path):
        output = tmp_path / "nested" / "dir" / "frame.jpg"
        with patch("requests.get", return_value=make_response(content=b"x")), patch("time.sleep"):
            handler._download_image("2021-01-01T00:00:00Z", "s-1", str(output))

        assert output.parent.is_dir()

    def test_the_api_url_carries_the_millisecond_timestamp(self, handler, tmp_path):
        output = tmp_path / "frame.jpg"
        with patch("requests.get", return_value=make_response(content=b"x")) as get, patch(
            "time.sleep"
        ):
            handler._download_image("2021-01-01T00:00:00.123456Z", "s-1", str(output))

        assert "startTime=2021-01-01T00:00:00.123+00:00Z" in get.call_args.args[0]

    def test_a_non_200_status_is_retried_then_fails(self, handler, tmp_path):
        output = tmp_path / "frame.jpg"
        with patch("requests.get", return_value=make_response(status_code=503)) as get, patch(
            "time.sleep"
        ):
            assert handler._download_image("2021-01-01T00:00:00Z", "s-1", str(output)) is False

        assert get.call_count == 3

    def test_a_late_success_is_accepted(self, handler, tmp_path):
        output = tmp_path / "frame.jpg"
        responses = [make_response(status_code=503), make_response(content=b"x")]

        with patch("requests.get", side_effect=responses), patch("time.sleep"):
            assert handler._download_image("2021-01-01T00:00:00Z", "s-1", str(output)) is True

    def test_an_empty_download_is_rejected_and_removed(self, handler, tmp_path):
        output = tmp_path / "frame.jpg"
        with patch("requests.get", return_value=make_response(content=b"")), patch("time.sleep"):
            assert handler._download_image("2021-01-01T00:00:00Z", "s-1", str(output)) is False

        assert not output.exists()

    def test_a_malformed_timestamp_returns_false(self, handler, tmp_path):
        assert handler._download_image("not-a-time", "s-1", str(tmp_path / "f.jpg")) is False

    def test_a_network_error_is_retried_then_returns_false(self, handler, tmp_path):
        with patch(
            "requests.get", side_effect=requests.ConnectionError("refused")
        ) as get, patch("time.sleep"):
            assert handler._download_image(
                "2021-01-01T00:00:00Z", "s-1", str(tmp_path / "f.jpg")
            ) is False

        assert get.call_count == 3


class TestGetSamplingImages:
    def _frame(self):
        import pandas as pd

        return pd.DataFrame([{"sensorName": "Lobby"}, {"sensorName": "Dock"}])

    def test_enriches_every_row(self, handler):
        with patch.object(
            handler,
            "_get_stream_snapshot",
            side_effect=[("s-1", "http://vst/a", b"img-a"), ("s-2", "http://vst/b", b"img-b")],
        ):
            out = handler.get_sampling_images(self._frame())

        assert len(out) == 2
        assert list(out["streamId"]) == ["s-1", "s-2"]
        assert list(out["imageUrl"]) == ["http://vst/a", "http://vst/b"]

    def test_rows_without_a_snapshot_are_dropped(self, handler):
        with patch.object(
            handler, "_get_stream_snapshot", side_effect=[("s-1", "http://vst/a", b"img"), None]
        ):
            out = handler.get_sampling_images(self._frame())

        assert len(out) == 1
        assert out.iloc[0]["streamId"] == "s-1"

    def test_the_input_frame_is_not_mutated(self, handler):
        frame = self._frame()
        with patch.object(handler, "_get_stream_snapshot", return_value=("s-1", "u", b"i")):
            handler.get_sampling_images(frame)

        assert "streamId" not in frame.columns

    def test_a_row_level_error_does_not_abort_the_batch(self, handler):
        with patch.object(
            handler, "_get_stream_snapshot", side_effect=[RuntimeError("boom"), ("s-2", "u", b"i")]
        ):
            out = handler.get_sampling_images(self._frame())

        assert len(out) == 1

    def test_an_unexpected_error_returns_an_empty_frame(self, handler):
        with patch.object(handler, "_get_stream_snapshot", return_value=("s-1", "u", b"i")):
            out = handler.get_sampling_images("not-a-dataframe")

        assert out.empty

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

"""Unit tests for ``realtime.services.rtvi_client``.

This is the HTTP boundary to the RTVI VLM microservice. Three things here are
worth pinning:

* **Credential redaction.** RTSP URLs carry ``user:pass@host`` and the
  payload has explicit ``username`` / ``password`` fields; the whole payload
  is logged at INFO on every ``streams/add``. ``_redact_stream_payload`` must
  mask all three and must not mutate the caller's dict — the unredacted copy
  is what actually goes on the wire.
* **Envelope normalisation.** ``get_stream_info`` accepts four different
  response shapes from different RTVI builds and normalises them to a list.
  Getting this wrong makes the service re-add a stream that already exists.
* **Omit-vs-send for optional fields.** ``generate_captions`` must omit a key
  entirely when it is ``None`` so RTVI applies its own server-side default;
  sending an explicit ``null`` would override it.

``httpx.AsyncClient`` is replaced with an ``AsyncMock`` — no socket is opened.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from realtime.services.rtvi_client import RTVIVLMClient, _redact_stream_payload

BASE_URL = "http://rtvi:8000"


def make_response(status_code=200, json_body=None, text=""):
    response = MagicMock()
    response.status_code = status_code
    response.is_success = 200 <= status_code < 300
    response.text = text
    response.json.return_value = json_body
    if not response.is_success:
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    return response


@pytest.fixture
def client():
    with patch("httpx.AsyncClient") as async_client_cls:
        async_client_cls.return_value = AsyncMock()
        rtvi = RTVIVLMClient(BASE_URL)
    return rtvi


class TestRedactStreamPayload:
    def test_masks_url_userinfo(self):
        payload = {"streams": [{"liveStreamUrl": "rtsp://admin:hunter2@cam-1:554/s"}]}
        assert _redact_stream_payload(payload)["streams"][0]["liveStreamUrl"] == (
            "rtsp://***@cam-1:554/s"
        )

    def test_masks_username_and_password_fields(self):
        payload = {"streams": [{"username": "admin", "password": "hunter2"}]}
        redacted = _redact_stream_payload(payload)["streams"][0]

        assert redacted["username"] == "***"
        assert redacted["password"] == "***"

    def test_url_without_userinfo_is_unchanged(self):
        payload = {"streams": [{"liveStreamUrl": "rtsp://cam-1:554/s"}]}
        assert _redact_stream_payload(payload)["streams"][0]["liveStreamUrl"] == (
            "rtsp://cam-1:554/s"
        )

    def test_the_original_payload_is_not_mutated(self):
        payload = {"streams": [{"liveStreamUrl": "rtsp://admin:hunter2@cam-1/s", "password": "p"}]}
        _redact_stream_payload(payload)

        assert payload["streams"][0]["liveStreamUrl"] == "rtsp://admin:hunter2@cam-1/s"
        assert payload["streams"][0]["password"] == "p"

    def test_other_fields_survive(self):
        payload = {"streams": [{"id": "cam-1", "description": "lobby"}]}
        redacted = _redact_stream_payload(payload)["streams"][0]

        assert redacted["id"] == "cam-1"
        assert redacted["description"] == "lobby"

    @pytest.mark.parametrize("payload", [{}, {"streams": None}, {"streams": []}])
    def test_missing_streams_block_is_tolerated(self, payload):
        assert _redact_stream_payload(payload) == payload

    def test_every_stream_is_redacted(self):
        payload = {
            "streams": [
                {"liveStreamUrl": "rtsp://a:b@cam-1/s"},
                {"liveStreamUrl": "rtsp://c:d@cam-2/s"},
            ]
        }
        redacted = _redact_stream_payload(payload)["streams"]
        assert all("***@" in s["liveStreamUrl"] for s in redacted)


class TestConstruction:
    def test_trailing_slash_is_stripped(self):
        with patch("httpx.AsyncClient"):
            assert RTVIVLMClient("http://rtvi:8000/").base_url == "http://rtvi:8000"

    def test_timeout_is_passed_to_httpx(self):
        with patch("httpx.AsyncClient") as async_client_cls:
            RTVIVLMClient(BASE_URL, timeout=7)
        assert async_client_cls.call_args.kwargs["timeout"] == 7

    def test_default_timeout(self):
        with patch("httpx.AsyncClient"):
            assert RTVIVLMClient(BASE_URL).timeout == 30

    @pytest.mark.asyncio
    async def test_aclose_closes_the_pool(self, client):
        await client.aclose()
        client._client.aclose.assert_awaited_once()


class TestStartStream:
    @pytest.mark.asyncio
    async def test_posts_a_single_stream_entry(self, client):
        client._client.post.return_value = make_response(json_body={"results": []})

        await client.start_stream(
            {"id": "cam-1", "liveStreamUrl": "rtsp://cam-1/s", "description": "lobby"}
        )

        url, = client._client.post.call_args.args
        body = client._client.post.call_args.kwargs["json"]
        assert url == "http://rtvi:8000/streams/add"
        assert body["streams"] == [
            {"id": "cam-1", "liveStreamUrl": "rtsp://cam-1/s", "description": "lobby"}
        ]

    @pytest.mark.asyncio
    async def test_rtsp_url_is_accepted_as_an_alias(self, client):
        client._client.post.return_value = make_response(json_body={})

        await client.start_stream({"id": "cam-1", "rtsp_url": "rtsp://cam-1/s"})

        assert client._client.post.call_args.kwargs["json"]["streams"][0][
            "liveStreamUrl"
        ] == "rtsp://cam-1/s"

    @pytest.mark.asyncio
    async def test_missing_id_is_forwarded_as_null(self, client):
        """RTVI generates its own identifier when id is null."""
        client._client.post.return_value = make_response(json_body={})

        await client.start_stream({"liveStreamUrl": "rtsp://cam-1/s"})

        assert client._client.post.call_args.kwargs["json"]["streams"][0]["id"] is None

    @pytest.mark.asyncio
    async def test_missing_description_becomes_an_empty_string(self, client):
        client._client.post.return_value = make_response(json_body={})

        await client.start_stream({"id": "cam-1", "liveStreamUrl": "rtsp://cam-1/s"})

        assert client._client.post.call_args.kwargs["json"]["streams"][0]["description"] == ""

    @pytest.mark.asyncio
    async def test_optional_fields_are_forwarded_when_set(self, client):
        client._client.post.return_value = make_response(json_body={})

        await client.start_stream(
            {
                "id": "cam-1",
                "liveStreamUrl": "rtsp://cam-1/s",
                "sensor_name": "Lobby cam",
                "username": "admin",
                "password": "hunter2",
                "place_name": "HQ",
                "place_lat": 1.5,
            }
        )

        entry = client._client.post.call_args.kwargs["json"]["streams"][0]
        assert entry["sensor_name"] == "Lobby cam"
        assert entry["username"] == "admin"
        assert entry["password"] == "hunter2"
        assert entry["place_name"] == "HQ"
        assert entry["place_lat"] == 1.5

    @pytest.mark.asyncio
    async def test_none_valued_optional_fields_are_omitted(self, client):
        client._client.post.return_value = make_response(json_body={})

        await client.start_stream(
            {"id": "cam-1", "liveStreamUrl": "rtsp://cam-1/s", "username": None}
        )

        assert "username" not in client._client.post.call_args.kwargs["json"]["streams"][0]

    @pytest.mark.asyncio
    async def test_credentials_are_sent_unredacted(self, client):
        """Redaction is for the log line only — the wire payload is real."""
        client._client.post.return_value = make_response(json_body={})

        await client.start_stream(
            {"id": "cam-1", "liveStreamUrl": "rtsp://admin:hunter2@cam-1/s", "password": "hunter2"}
        )

        entry = client._client.post.call_args.kwargs["json"]["streams"][0]
        assert entry["liveStreamUrl"] == "rtsp://admin:hunter2@cam-1/s"
        assert entry["password"] == "hunter2"

    @pytest.mark.asyncio
    async def test_returns_the_parsed_body(self, client):
        client._client.post.return_value = make_response(json_body={"results": [{"id": "s-1"}]})
        assert await client.start_stream({"id": "cam-1"}) == {"results": [{"id": "s-1"}]}

    @pytest.mark.asyncio
    async def test_error_status_raises(self, client):
        client._client.post.return_value = make_response(status_code=500, text="boom")

        with pytest.raises(httpx.HTTPStatusError):
            await client.start_stream({"id": "cam-1"})


class TestGetStreamInfo:
    @pytest.mark.asyncio
    async def test_bare_list_is_returned(self, client):
        client._client.get.return_value = make_response(json_body=[{"id": "s-1"}])
        assert await client.get_stream_info() == [{"id": "s-1"}]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("key", ["results", "streams", "items", "data"])
    async def test_every_envelope_key_is_unwrapped(self, client, key):
        client._client.get.return_value = make_response(json_body={key: [{"id": "s-1"}]})
        assert await client.get_stream_info() == [{"id": "s-1"}]

    @pytest.mark.asyncio
    async def test_results_wins_over_later_keys(self, client):
        client._client.get.return_value = make_response(
            json_body={"results": [{"id": "a"}], "streams": [{"id": "b"}]}
        )
        assert await client.get_stream_info() == [{"id": "a"}]

    @pytest.mark.asyncio
    async def test_unknown_envelope_degrades_to_an_empty_list(self, client):
        client._client.get.return_value = make_response(json_body={"unexpected": {"id": "s-1"}})
        assert await client.get_stream_info() == []

    @pytest.mark.asyncio
    async def test_scalar_body_degrades_to_an_empty_list(self, client):
        client._client.get.return_value = make_response(json_body="not-a-list")
        assert await client.get_stream_info() == []

    @pytest.mark.asyncio
    async def test_calls_the_documented_endpoint(self, client):
        client._client.get.return_value = make_response(json_body=[])
        await client.get_stream_info()
        assert client._client.get.call_args.args[0] == (
            "http://rtvi:8000/streams/get-stream-info"
        )

    @pytest.mark.asyncio
    async def test_error_status_raises(self, client):
        client._client.get.return_value = make_response(status_code=503, text="unavailable")

        with pytest.raises(httpx.HTTPStatusError):
            await client.get_stream_info()


class TestStopStream:
    @pytest.mark.asyncio
    async def test_deletes_the_stream(self, client):
        client._client.delete.return_value = make_response(text="")

        result = await client.stop_stream("s-1")

        assert client._client.delete.call_args.args[0] == "http://rtvi:8000/streams/delete/s-1"
        assert result == {"status": "deleted", "stream_id": "s-1"}

    @pytest.mark.asyncio
    async def test_json_body_is_returned_when_present(self, client):
        client._client.delete.return_value = make_response(
            text='{"deleted": true}', json_body={"deleted": True}
        )
        assert await client.stop_stream("s-1") == {"deleted": True}

    @pytest.mark.asyncio
    async def test_whitespace_only_body_falls_back_to_the_synthetic_result(self, client):
        client._client.delete.return_value = make_response(text="   \n")
        assert await client.stop_stream("s-1") == {"status": "deleted", "stream_id": "s-1"}

    @pytest.mark.asyncio
    async def test_error_status_raises(self, client):
        client._client.delete.return_value = make_response(status_code=404)

        with pytest.raises(httpx.HTTPStatusError):
            await client.stop_stream("s-1")


class TestGenerateCaptions:
    @pytest.mark.asyncio
    async def test_required_fields_are_always_sent(self, client):
        client._client.post.return_value = make_response()

        result = await client.generate_captions("s-1", "describe", "cosmos")

        payload = client._client.post.call_args.kwargs["json"]
        assert payload["id"] == "s-1"
        assert payload["prompt"] == "describe"
        assert payload["model"] == "cosmos"
        assert payload["stream"] is True
        assert payload["chunk_duration"] == 30
        assert payload["chunk_overlap_duration"] == 5
        assert payload["vlm_input_width"] == 256
        assert payload["enable_reasoning"] is True
        assert result == {"status": "started", "stream_id": "s-1"}

    @pytest.mark.asyncio
    async def test_alert_category_is_omitted_when_blank(self, client):
        client._client.post.return_value = make_response()

        await client.generate_captions("s-1", "p", "m", alert_category="")

        assert "alert_category" not in client._client.post.call_args.kwargs["json"]

    @pytest.mark.asyncio
    async def test_alert_category_is_sent_when_set(self, client):
        client._client.post.return_value = make_response()

        await client.generate_captions("s-1", "p", "m", alert_category="collision")

        assert client._client.post.call_args.kwargs["json"]["alert_category"] == "collision"

    @pytest.mark.asyncio
    async def test_unset_extended_options_are_omitted(self, client):
        """None means "let RTVI apply its own default", not "send null"."""
        client._client.post.return_value = make_response()

        await client.generate_captions("s-1", "p", "m")

        payload = client._client.post.call_args.kwargs["json"]
        for field in ("max_tokens", "temperature", "top_p", "top_k", "seed", "api_type"):
            assert field not in payload

    @pytest.mark.asyncio
    async def test_set_extended_options_are_forwarded(self, client):
        client._client.post.return_value = make_response()

        await client.generate_captions(
            "s-1", "p", "m", max_tokens=128, temperature=0.3, top_p=0.9, seed=7
        )

        payload = client._client.post.call_args.kwargs["json"]
        assert payload["max_tokens"] == 128
        assert payload["temperature"] == 0.3
        assert payload["top_p"] == 0.9
        assert payload["seed"] == 7

    @pytest.mark.asyncio
    async def test_chunking_options_are_forwarded(self, client):
        client._client.post.return_value = make_response()

        await client.generate_captions(
            "s-1", "p", "m",
            chunk_duration=10, chunk_overlap_duration=2,
            num_frames_per_second_or_fixed_frames_chunk=4,
            use_fps_for_chunking=False,
        )

        payload = client._client.post.call_args.kwargs["json"]
        assert payload["chunk_duration"] == 10
        assert payload["chunk_overlap_duration"] == 2
        assert payload["num_frames_per_second_or_fixed_frames_chunk"] == 4
        assert payload["use_fps_for_chunking"] is False

    @pytest.mark.asyncio
    async def test_timeout_is_raised_to_at_least_two_minutes(self, client):
        client._client.post.return_value = make_response()

        await client.generate_captions("s-1", "p", "m")

        assert client._client.post.call_args.kwargs["timeout"] == 120

    @pytest.mark.asyncio
    async def test_a_longer_configured_timeout_wins(self):
        with patch("httpx.AsyncClient") as async_client_cls:
            async_client_cls.return_value = AsyncMock()
            rtvi = RTVIVLMClient(BASE_URL, timeout=300)
        rtvi._client.post.return_value = make_response()

        await rtvi.generate_captions("s-1", "p", "m")

        assert rtvi._client.post.call_args.kwargs["timeout"] == 300

    @pytest.mark.asyncio
    async def test_error_status_raises(self, client):
        client._client.post.return_value = make_response(status_code=422, text="bad model")

        with pytest.raises(httpx.HTTPStatusError):
            await client.generate_captions("s-1", "p", "m")


class TestStopCaptions:
    @pytest.mark.asyncio
    async def test_deletes_the_caption_job(self, client):
        client._client.delete.return_value = make_response(text="")

        result = await client.stop_captions("s-1")

        assert client._client.delete.call_args.args[0] == (
            "http://rtvi:8000/generate_captions/s-1"
        )
        assert result == {"status": "stopped", "stream_id": "s-1"}

    @pytest.mark.asyncio
    async def test_json_body_is_returned_when_present(self, client):
        client._client.delete.return_value = make_response(
            text='{"stopped": true}', json_body={"stopped": True}
        )
        assert await client.stop_captions("s-1") == {"stopped": True}

    @pytest.mark.asyncio
    async def test_error_status_raises(self, client):
        client._client.delete.return_value = make_response(status_code=404)

        with pytest.raises(httpx.HTTPStatusError):
            await client.stop_captions("s-1")


class TestHealth:
    @pytest.mark.asyncio
    async def test_true_when_ready_succeeds(self, client):
        client._client.get.return_value = make_response()

        assert await client.health() is True
        assert client._client.get.call_args.args[0] == "http://rtvi:8000/ready"

    @pytest.mark.asyncio
    async def test_false_on_an_error_status(self, client):
        client._client.get.return_value = make_response(status_code=503)
        assert await client.health() is False

    @pytest.mark.asyncio
    async def test_false_when_the_connection_fails(self, client):
        client._client.get.side_effect = httpx.ConnectError("refused")
        assert await client.health() is False

    @pytest.mark.asyncio
    async def test_timeout_is_reported_as_unhealthy(self, client):
        client._client.get.side_effect = httpx.ReadTimeout("timed out")
        assert await client.health() is False

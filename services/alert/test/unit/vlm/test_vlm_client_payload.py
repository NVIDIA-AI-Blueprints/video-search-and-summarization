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

"""Unit tests for the payload builders and sync surface of ``vlm.vlm_client``.

Everything the VLM endpoint receives is assembled by ``_VLMClientBase``, and a
wrong ``extra_body`` is not an error the NIM reports — it silently changes how
many frames are sampled or at what resolution, which changes the verdict. So
the shape rules are pinned exhaustively:

* ``use_vlm_media_defaults`` short-circuits to ``{}`` (server-side defaults),
  and a per-request override of that flag beats the constructor value.
* ``do_resize: False`` omits the resize block entirely rather than sending
  ``do_resize: False`` — the comment in the source records that the latter is
  rejected by the cosmos-reason2 NIM.
* Sampling is video-only; ``enable_sampling`` swaps ``num_frames`` for ``fps``.
* Per-request ``config_overrides`` beat constructor config for every key.

``_build_chat_kwargs`` omits falsy ``max_tokens`` but keeps ``temperature=0``
— the distinction matters because 0 is a meaningful sampling temperature.

The ``ModelType.CR1`` branches are not exercised: ``detect_model_type`` only
ever returns ``COSMOS_REASON`` or ``OTHER``, which the existing model-override
tests already record with an explicit skip.

``OpenAI`` is patched at the module boundary — no client is constructed and no
request leaves the process.
"""

import base64
import os
from unittest.mock import MagicMock, patch

import pytest

from schemas.vlm_responses import ModelType
from vlm.vlm_client import VLMClient, _VLMClientBase

COSMOS = "nvidia/cosmos-reason2-8b"
OTHER_MODEL = "meta/llama-3.2-11b-vision"


def make_base(**config):
    config.setdefault("model", COSMOS)
    return _VLMClientBase(config)


def make_client(**config):
    config.setdefault("model", COSMOS)
    config.setdefault("base_url", "http://vlm:8080/v1")
    with patch("vlm.vlm_client.OpenAI") as openai_cls:
        client = VLMClient(config)
    client._openai_cls = openai_cls
    return client


def make_completion(content="verdict: yes"):
    """Build a stand-in for an OpenAI ChatCompletion."""
    message = MagicMock()
    message.content = content
    response = MagicMock()
    response.choices = [MagicMock(message=message)]
    return response


@pytest.fixture
def client():
    client = make_client(max_tokens=256)
    client.client.chat.completions.create.return_value = make_completion()
    return client


@pytest.fixture
def video_file(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"\x00\x00\x00\x1cftypisom")
    return str(path)


@pytest.fixture
def image_file(tmp_path):
    path = tmp_path / "frame.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")
    return str(path)


class TestBaseConstruction:
    def test_defaults(self):
        base = make_base(model=None)
        assert base.base_url == "http://localhost:8080/v1"
        assert base.request_timeout == 5
        assert base.stream is False
        assert base.use_vlm_media_defaults is False
        assert base.max_tokens is None
        assert base.temperature is None

    def test_config_values_win(self):
        base = make_base(
            base_url="http://vlm:9000/v1",
            model="custom/model",
            max_tokens=512,
            temperature=0.2,
            request_timeout=90,
            use_vlm_media_defaults=True,
        )
        assert base.base_url == "http://vlm:9000/v1"
        assert base.model == "custom/model"
        assert base.max_tokens == 512
        assert base.temperature == 0.2
        assert base.request_timeout == 90
        assert base.use_vlm_media_defaults is True

    def test_default_model_is_set_for_an_empty_config(self):
        assert _VLMClientBase({}).model == "nvidia/cosmos-reason1-7b"


class TestNormalizePrompt:
    def test_user_prompt_wins(self):
        assert _VLMClientBase._normalize_prompt("mine", "fallback") == "mine"

    @pytest.mark.parametrize("empty", [None, ""])
    def test_falsy_prompt_falls_back(self, empty):
        assert _VLMClientBase._normalize_prompt(empty, "fallback") == "fallback"


class TestGetModelType:
    def test_uses_the_client_model_by_default(self):
        assert make_base(model=COSMOS)._get_model_type() == ModelType.COSMOS_REASON

    def test_override_wins(self):
        base = make_base(model=COSMOS)
        assert base._get_model_type(OTHER_MODEL) == ModelType.OTHER

    def test_unknown_model_is_other(self):
        assert make_base(model=OTHER_MODEL)._get_model_type() == ModelType.OTHER

    def test_blank_override_falls_back_to_the_client_model(self):
        base = make_base(model=COSMOS)
        assert base._get_model_type("") == ModelType.COSMOS_REASON


class TestBuildMessagesWithMedia:
    def test_media_first_then_text(self):
        messages = make_base()._build_messages_with_media(
            "video", "http://host/v.mp4", "is there a fire?"
        )
        content = messages[0]["content"]

        assert messages[0]["role"] == "user"
        assert content[0] == {"type": "video_url", "video_url": {"url": "http://host/v.mp4"}}
        assert content[1] == {"type": "text", "text": "is there a fire?"}

    def test_system_prompt_is_prepended(self):
        messages = make_base()._build_messages_with_media(
            "video", "http://host/v.mp4", "prompt", system_prompt="You are an analyst."
        )
        assert messages[0] == {"role": "system", "content": "You are an analyst."}
        assert messages[1]["role"] == "user"

    def test_no_system_message_when_absent(self):
        messages = make_base()._build_messages_with_media("image", "u", "p")
        assert len(messages) == 1

    def test_media_key_follows_the_media_type(self):
        messages = make_base()._build_messages_with_media("image", "http://host/f.png", "p")
        assert messages[0]["content"][0]["type"] == "image_url"


class TestBuildExtraBody:
    def test_video_defaults(self):
        body = make_base()._build_extra_body(video=True, num_frames=8)

        assert body["mm_processor_kwargs"] == {
            "size": {"shortest_edge": 1568, "longest_edge": 345600}
        }
        assert body["media_io_kwargs"] == {"video": {"num_frames": 8}}

    def test_image_has_no_sampling_block(self):
        body = make_base()._build_extra_body(video=False)
        assert "media_io_kwargs" not in body
        assert "mm_processor_kwargs" in body

    def test_pixel_bounds_come_from_config(self):
        body = make_base(min_pixels=100, max_pixels=200)._build_extra_body(video=False)
        assert body["mm_processor_kwargs"]["size"] == {"shortest_edge": 100, "longest_edge": 200}

    def test_enable_sampling_swaps_num_frames_for_fps(self):
        body = make_base(enable_sampling=True, sampling_fps=2)._build_extra_body(video=True)
        assert body["media_io_kwargs"] == {"video": {"fps": 2}}

    def test_sampling_fps_defaults_to_four(self):
        body = make_base(enable_sampling=True)._build_extra_body(video=True)
        assert body["media_io_kwargs"] == {"video": {"fps": 4}}

    def test_do_resize_false_omits_the_resize_block_entirely(self):
        """Sending ``do_resize: False`` is rejected by the NIM; omit instead."""
        body = make_base(do_resize=False)._build_extra_body(video=True, num_frames=4)

        assert "mm_processor_kwargs" not in body
        assert body["media_io_kwargs"] == {"video": {"num_frames": 4}}

    def test_do_resize_false_on_an_image_yields_an_empty_body(self):
        assert make_base(do_resize=False)._build_extra_body(video=False) == {}

    def test_use_vlm_media_defaults_short_circuits(self):
        assert make_base(use_vlm_media_defaults=True)._build_extra_body(video=True) == {}

    def test_per_request_override_can_enable_media_defaults(self):
        base = make_base(use_vlm_media_defaults=False)
        assert base._build_extra_body(config_overrides={"use_vlm_media_defaults": True}) == {}

    def test_per_request_override_can_disable_media_defaults(self):
        base = make_base(use_vlm_media_defaults=True)
        body = base._build_extra_body(config_overrides={"use_vlm_media_defaults": False})
        assert body != {}

    def test_num_frames_override_wins_over_the_argument(self):
        body = make_base()._build_extra_body(num_frames=10, config_overrides={"num_frames": 3})
        assert body["media_io_kwargs"] == {"video": {"num_frames": 3}}

    @pytest.mark.parametrize(
        "key,value,probe",
        [
            ("min_pixels", 64, lambda b: b["mm_processor_kwargs"]["size"]["shortest_edge"]),
            ("max_pixels", 999, lambda b: b["mm_processor_kwargs"]["size"]["longest_edge"]),
        ],
    )
    def test_pixel_overrides_win_over_config(self, key, value, probe):
        base = make_base(min_pixels=1, max_pixels=2)
        assert probe(base._build_extra_body(video=True, config_overrides={key: value})) == value

    def test_sampling_override_wins_over_config(self):
        base = make_base(enable_sampling=False)
        body = base._build_extra_body(config_overrides={"enable_sampling": True, "sampling_fps": 7})
        assert body["media_io_kwargs"] == {"video": {"fps": 7}}

    def test_none_num_frames_is_passed_through(self):
        body = make_base()._build_extra_body(video=True, num_frames=None)
        assert body["media_io_kwargs"] == {"video": {"num_frames": None}}


class TestBuildChatKwargs:
    def test_max_tokens_from_config(self):
        assert make_base(max_tokens=256)._build_chat_kwargs({})["max_tokens"] == 256

    def test_max_tokens_override_wins(self):
        base = make_base(max_tokens=256)
        assert base._build_chat_kwargs({}, {"max_tokens": 64})["max_tokens"] == 64

    @pytest.mark.parametrize("falsy", [None, 0])
    def test_falsy_max_tokens_is_omitted(self, falsy):
        assert "max_tokens" not in make_base(max_tokens=falsy)._build_chat_kwargs({})

    def test_temperature_zero_is_kept(self):
        """0 is a meaningful temperature, not "unset"."""
        assert make_base(temperature=0)._build_chat_kwargs({})["temperature"] == 0

    def test_temperature_none_is_omitted(self):
        assert "temperature" not in make_base(temperature=None)._build_chat_kwargs({})

    def test_temperature_override_wins(self):
        base = make_base(temperature=0.9)
        assert base._build_chat_kwargs({}, {"temperature": 0.1})["temperature"] == 0.1

    def test_empty_extra_body_is_omitted(self):
        assert "extra_body" not in make_base()._build_chat_kwargs({})

    def test_non_empty_extra_body_is_forwarded(self):
        body = {"media_io_kwargs": {"video": {"num_frames": 4}}}
        assert make_base()._build_chat_kwargs(body)["extra_body"] is body


class TestPrepareLocalMedia:
    def test_infers_video_from_the_extension(self, video_file):
        media_type, data_url = make_base()._prepare_local_media(video_file)

        assert media_type == "video"
        assert data_url.startswith("data:video/mp4;base64,")

    def test_infers_image_from_the_extension(self, image_file):
        media_type, data_url = make_base()._prepare_local_media(image_file)

        assert media_type == "image"
        assert data_url.startswith("data:image/png;base64,")

    def test_payload_round_trips(self, video_file):
        _media_type, data_url = make_base()._prepare_local_media(video_file)
        encoded = data_url.split("base64,", 1)[1]

        with open(video_file, "rb") as handle:
            assert base64.b64decode(encoded) == handle.read()

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Media file not found"):
            make_base()._prepare_local_media(str(tmp_path / "nope.mp4"))

    def test_directory_is_rejected(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            make_base()._prepare_local_media(str(tmp_path))

    def test_unknown_extension_without_media_type_raises(self, tmp_path):
        path = tmp_path / "blob.unknownext"
        path.write_bytes(b"data")

        with pytest.raises(ValueError, match="Unable to infer media type"):
            make_base()._prepare_local_media(str(path))

    def test_explicit_media_type_supplies_a_default_mime(self, tmp_path):
        path = tmp_path / "blob.unknownext"
        path.write_bytes(b"data")

        media_type, data_url = make_base()._prepare_local_media(str(path), media_type="video")

        assert media_type == "video"
        assert data_url.startswith("data:video/mp4;base64,")

    def test_explicit_image_type_supplies_a_png_mime(self, tmp_path):
        path = tmp_path / "blob.unknownext"
        path.write_bytes(b"data")

        _media_type, data_url = make_base()._prepare_local_media(str(path), media_type="image")
        assert data_url.startswith("data:image/png;base64,")

    def test_invalid_media_type_raises(self, video_file):
        with pytest.raises(ValueError, match="media_type must be either"):
            make_base()._prepare_local_media(video_file, media_type="audio")

    def test_explicit_type_does_not_override_a_known_mime(self, image_file):
        """The guessed mime wins; only the declared media_type is honoured."""
        media_type, data_url = make_base()._prepare_local_media(image_file, media_type="image")
        assert media_type == "image"
        assert "image/png" in data_url


class TestBuildMessagesWithMultipleImages:
    def test_images_first_then_text(self):
        messages = make_base()._build_messages_with_multiple_images(
            ["u1", "u2"], "compare these"
        )
        content = messages[0]["content"]

        assert [c["type"] for c in content] == ["image_url", "image_url", "text"]
        assert content[0]["image_url"] == {"url": "u1"}
        assert content[-1]["text"] == "compare these"

    def test_system_prompt_is_prepended(self):
        messages = make_base()._build_messages_with_multiple_images(
            ["u1"], "p", system_prompt="You are an analyst."
        )
        assert messages[0]["role"] == "system"

    def test_empty_url_list_yields_text_only_content(self):
        messages = make_base()._build_messages_with_multiple_images([], "p")
        assert [c["type"] for c in messages[0]["content"]] == ["text"]


class TestLogTokenUsage:
    def test_disabled_by_default(self, caplog):
        response = MagicMock()
        with caplog.at_level("DEBUG", logger="vlm.vlm_client"):
            make_base()._log_token_usage(response, "video")
        assert "token usage" not in caplog.text

    @pytest.mark.parametrize("flag", ["1", "true", "YES"])
    def test_enabled_by_env(self, caplog, flag):
        usage = MagicMock(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        response = MagicMock(usage=usage)

        with patch.dict(os.environ, {"LOG_VLM_USAGE": flag}), caplog.at_level(
            "DEBUG", logger="vlm.vlm_client"
        ):
            make_base()._log_token_usage(response, "video")

        assert "total_tokens=15" in caplog.text

    def test_response_without_usage_is_reported(self, caplog):
        response = MagicMock()
        response.usage = None

        with patch.dict(os.environ, {"LOG_VLM_USAGE": "true"}), caplog.at_level(
            "DEBUG", logger="vlm.vlm_client"
        ):
            make_base()._log_token_usage(response)

        assert "no usage data" in caplog.text

    def test_missing_counters_default_to_zero(self, caplog):
        usage = MagicMock(prompt_tokens=None, completion_tokens=None, total_tokens=None)

        with patch.dict(os.environ, {"LOG_VLM_USAGE": "true"}), caplog.at_level(
            "DEBUG", logger="vlm.vlm_client"
        ):
            make_base()._log_token_usage(MagicMock(usage=usage))

        assert "total_tokens=0" in caplog.text

    def test_malformed_usage_never_raises(self):
        usage = MagicMock()
        usage.prompt_tokens = "not-a-number"

        with patch.dict(os.environ, {"LOG_VLM_USAGE": "true"}):
            make_base()._log_token_usage(MagicMock(usage=usage))


class TestSyncCreateChat:
    def test_sends_the_client_model_and_stream_flag(self, client):
        client._create_chat([{"role": "user", "content": []}])

        kwargs = client.client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == COSMOS
        assert kwargs["stream"] is False
        assert kwargs["max_tokens"] == 256

    def test_model_override_is_sent(self, client):
        client._create_chat([], config_overrides={"model": OTHER_MODEL})
        assert client.client.chat.completions.create.call_args.kwargs["model"] == OTHER_MODEL

    def test_extra_body_is_attached(self, client):
        client._create_chat([], video=True, num_frames=4)
        extra = client.client.chat.completions.create.call_args.kwargs["extra_body"]
        assert extra["media_io_kwargs"] == {"video": {"num_frames": 4}}

    def test_media_defaults_omit_extra_body(self):
        client = make_client(use_vlm_media_defaults=True)
        client.client.chat.completions.create.return_value = make_completion()

        client._create_chat([], video=True)

        assert "extra_body" not in client.client.chat.completions.create.call_args.kwargs


class TestSyncAnalyzeImageUrl:
    def test_returns_the_first_choice_message(self, client):
        result = client.analyze_image_url("http://host/f.png", "what is this?")
        assert result.content == "verdict: yes"

    def test_sends_an_image_payload_without_a_sampling_block(self, client):
        client.analyze_image_url("http://host/f.png", "what is this?")

        kwargs = client.client.chat.completions.create.call_args.kwargs
        assert kwargs["messages"][0]["content"][0]["type"] == "image_url"
        assert "media_io_kwargs" not in kwargs.get("extra_body", {})

    def test_system_prompt_is_forwarded(self, client):
        client.analyze_image_url("http://host/f.png", "p", system_prompt="You are an analyst.")
        messages = client.client.chat.completions.create.call_args.kwargs["messages"]
        assert messages[0]["role"] == "system"


class TestSyncAnalyzeVideoUrl:
    def test_returns_the_first_choice_message(self, client):
        assert client.analyze_video_url("http://host/v.mp4", "p").content == "verdict: yes"

    def test_num_frames_reaches_extra_body(self, client):
        client.analyze_video_url("http://host/v.mp4", "p", num_frames=3)
        extra = client.client.chat.completions.create.call_args.kwargs["extra_body"]
        assert extra["media_io_kwargs"] == {"video": {"num_frames": 3}}

    def test_config_overrides_reach_the_request(self, client):
        client.analyze_video_url(
            "http://host/v.mp4", "p", config_overrides={"model": OTHER_MODEL, "max_tokens": 32}
        )
        kwargs = client.client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == OTHER_MODEL
        assert kwargs["max_tokens"] == 32


class TestSyncUploadMediaFile:
    def test_sends_a_base64_data_url(self, client, video_file):
        client.upload_media_file(video_file, "p")

        content = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert content[0]["video_url"]["url"].startswith("data:video/mp4;base64,")

    def test_image_upload_has_no_sampling_block(self, client, image_file):
        client.upload_media_file(image_file, "p")
        extra = client.client.chat.completions.create.call_args.kwargs.get("extra_body", {})
        assert "media_io_kwargs" not in extra

    def test_missing_file_raises(self, client, tmp_path):
        with pytest.raises(FileNotFoundError):
            client.upload_media_file(str(tmp_path / "nope.mp4"), "p")

    def test_analyze_local_video_delegates_with_video_type(self, client, video_file):
        client.analyze_local_video(video_file, "p", num_frames=2)

        kwargs = client.client.chat.completions.create.call_args.kwargs
        assert kwargs["extra_body"]["media_io_kwargs"] == {"video": {"num_frames": 2}}

    def test_analyze_local_video_requires_a_prompt(self, client, video_file):
        with pytest.raises(ValueError, match="user_prompt is required"):
            client.analyze_local_video(video_file, "")


class TestSyncMultipleImages:
    def test_paths_become_data_urls(self, client, image_file):
        client.analyze_multiple_images([image_file, image_file], "compare")

        content = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert [c["type"] for c in content] == ["image_url", "image_url", "text"]
        assert content[0]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_empty_path_list_raises(self, client):
        with pytest.raises(ValueError, match="image_paths cannot be empty"):
            client.analyze_multiple_images([], "p")

    def test_blank_prompt_falls_back_to_the_default(self, client, image_file):
        client.analyze_multiple_images([image_file], user_prompt="")

        content = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert content[-1]["text"] == "Analyze these images."

    def test_urls_are_sent_verbatim(self, client):
        client.analyze_multiple_image_urls(["http://host/a.png", "http://host/b.png"], "compare")

        content = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert content[0]["image_url"] == {"url": "http://host/a.png"}

    def test_empty_url_list_raises(self, client):
        with pytest.raises(ValueError, match="image_urls cannot be empty"):
            client.analyze_multiple_image_urls([], "p")

    def test_url_variant_falls_back_to_the_default_prompt(self, client):
        client.analyze_multiple_image_urls(["http://host/a.png"], user_prompt=None)

        content = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert content[-1]["text"] == "Analyze these images."


class TestSyncAnalyzeMediaWithBase64:
    """Download-then-upload path used when the NIM cannot reach the media URL."""

    @pytest.fixture
    def downloader(self, video_file):
        with patch(
            "handlers.direct_media.media_downloader.MediaDownloader"
        ) as cls, patch("handlers.direct_media.media_downloader.DownloadConfig") as config_cls:
            cls.return_value.download.return_value = video_file
            yield cls, config_cls

    def test_downloads_then_uploads_then_cleans_up(self, client, downloader, video_file):
        cls, _config_cls = downloader

        result = client.analyze_media_with_base64("http://host/v.mp4", "p")

        cls.return_value.download.assert_called_once_with("http://host/v.mp4", worker_id=0)
        cls.cleanup.assert_called_once_with(video_file)
        assert result.content == "verdict: yes"

    def test_download_config_comes_from_the_client_config(self, video_file):
        client = make_client(
            media_download={"download_dir": "/data", "timeout_seconds": 5, "max_size_mb": 7}
        )
        client.client.chat.completions.create.return_value = make_completion()

        with patch("handlers.direct_media.media_downloader.MediaDownloader") as cls, patch(
            "handlers.direct_media.media_downloader.DownloadConfig"
        ) as config_cls:
            cls.return_value.download.return_value = video_file
            client.analyze_media_with_base64("http://host/v.mp4", "p")

        assert config_cls.call_args.kwargs["download_dir"] == "/data"
        assert config_cls.call_args.kwargs["timeout_seconds"] == 5
        assert config_cls.call_args.kwargs["max_size_mb"] == 7
        assert config_cls.call_args.kwargs["allow_private_urls"] is True

    def test_failed_download_raises_and_skips_cleanup(self, client, downloader):
        cls, _config_cls = downloader
        cls.return_value.download.return_value = None

        with pytest.raises(ValueError, match="Failed to download video"):
            client.analyze_media_with_base64("http://host/v.mp4", "p")

        cls.cleanup.assert_not_called()

    def test_cleanup_runs_even_when_the_upload_fails(self, client, downloader, video_file):
        cls, _config_cls = downloader
        client.client.chat.completions.create.side_effect = RuntimeError("VLM down")

        with pytest.raises(RuntimeError, match="VLM down"):
            client.analyze_media_with_base64("http://host/v.mp4", "p")

        cls.cleanup.assert_called_once_with(video_file)

    def test_missing_prompt_raises_before_downloading(self, client, downloader):
        cls, _config_cls = downloader

        with pytest.raises(ValueError, match="user_prompt is required"):
            client.analyze_media_with_base64("http://host/v.mp4", "")

        cls.return_value.download.assert_not_called()

    def test_invalid_media_type_raises_before_downloading(self, client, downloader):
        cls, _config_cls = downloader

        with pytest.raises(ValueError, match="media_type must be"):
            client.analyze_media_with_base64("http://host/v.mp4", "p", media_type="audio")

        cls.return_value.download.assert_not_called()

    def test_image_media_type_is_supported(self, client, image_file):
        with patch("handlers.direct_media.media_downloader.MediaDownloader") as cls, patch(
            "handlers.direct_media.media_downloader.DownloadConfig"
        ):
            cls.return_value.download.return_value = image_file
            client.analyze_media_with_base64("http://host/f.png", "p", media_type="image")

        content = client.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
        assert content[0]["type"] == "image_url"

    def test_video_alias_delegates_with_video_type(self, client, downloader, video_file):
        cls, _config_cls = downloader

        client.analyze_video_with_base64("http://host/v.mp4", "p", num_frames=6)

        extra = client.client.chat.completions.create.call_args.kwargs["extra_body"]
        assert extra["media_io_kwargs"] == {"video": {"num_frames": 6}}
        cls.cleanup.assert_called_once_with(video_file)


class TestMalformedVlmResponses:
    """The client returns ``response.choices[0].message`` unguarded.

    A VLM that answers with an empty ``choices`` array, a choice without a
    message, or a body that is not a completion at all is a failure mode this
    service has hit. The client does not absorb it — the pipeline's retry and
    parse-failure handling does — so what matters here is that the error
    surfaces as a plain exception rather than a silently wrong result.
    """

    @pytest.fixture
    def client(self):
        return make_client(max_tokens=256)

    def test_empty_choices_raises_index_error(self, client):
        response = MagicMock()
        response.choices = []
        client.client.chat.completions.create.return_value = response

        with pytest.raises(IndexError):
            client.analyze_video_url("http://host/v.mp4", "p")

    def test_a_choice_without_a_message_raises_attribute_error(self, client):
        choice = object()  # no .message
        response = MagicMock()
        response.choices = [choice]
        client.client.chat.completions.create.return_value = response

        with pytest.raises(AttributeError):
            client.analyze_video_url("http://host/v.mp4", "p")

    def test_a_none_response_raises_attribute_error(self, client):
        client.client.chat.completions.create.return_value = None

        with pytest.raises(AttributeError):
            client.analyze_image_url("http://host/f.png", "p")

    def test_a_message_without_content_is_returned_as_is(self, client):
        """The client does not validate content — the parser owns that."""
        message = object()
        response = MagicMock()
        response.choices = [MagicMock(message=message)]
        client.client.chat.completions.create.return_value = response

        assert client.analyze_video_url("http://host/v.mp4", "p") is message

    def test_empty_content_is_returned_as_is(self, client):
        client.client.chat.completions.create.return_value = make_completion(content="")

        assert client.analyze_video_url("http://host/v.mp4", "p").content == ""

    def test_the_failure_surfaces_on_the_upload_path_too(self, client, video_file):
        response = MagicMock()
        response.choices = []
        client.client.chat.completions.create.return_value = response

        with pytest.raises(IndexError):
            client.upload_media_file(video_file, "p")

    def test_the_failure_surfaces_on_the_multi_image_path_too(self, client):
        response = MagicMock()
        response.choices = []
        client.client.chat.completions.create.return_value = response

        with pytest.raises(IndexError):
            client.analyze_multiple_image_urls(["http://host/a.png"], "p")


class TestMalformedResponseParsing:
    """The parser is what turns a VLM body into a verdict.

    Every rejection below is what drives the pipeline's parse-failure path, so
    the boundary between "parsed" and "rejected" is worth pinning explicitly.
    """

    MODEL = "nvidia/cosmos-reason2-8b"

    def _parse(self, text):
        from schemas.vlm_responses import VLMResponse

        return VLMResponse.model_validate_text(
            text, model_name=self.MODEL, response_format="auto"
        )

    def test_a_well_formed_answer_is_parsed(self):
        assert self._parse("<think>two cars hit</think><answer>yes</answer>").verdict == "YES"

    def test_a_bare_verdict_is_accepted(self):
        assert self._parse("yes").verdict == "yes"

    @pytest.mark.parametrize(
        "body,label",
        [
            ("", "empty body"),
            ("   ", "whitespace only"),
            ("<think>reasoning only</think>", "no verdict"),
            ("<think>x</think><answer>yes", "truncated answer tag"),
            ("<think>x</think><answer>maybe</answer>", "verdict outside the allowed set"),
            ('{"verdict": "yes"}', "JSON body for a non-JSON format"),
        ],
    )
    def test_malformed_bodies_are_rejected(self, body, label):
        with pytest.raises(ValueError):
            self._parse(body)

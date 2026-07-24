# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

"""
Unit tests for src/via_client_cli.py

Tests pure-logic functions (is_url, convert_seconds_to_string, format_ntp_timestamp,
get_parser, get_api_url, check_err_response) and the print_curl_command paths of
do_add_file, do_list_files, do_get_file_info, do_delete_file, do_summarize,
do_list_models, do_server_health_check, and main().

All tests use @pytest.mark.unit.
"""

import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level import with mocked optional dependencies
# ---------------------------------------------------------------------------

_mock_mods = {
    "requests": MagicMock(),
    "sseclient": MagicMock(),
    "tabulate": MagicMock(),
    "tqdm": MagicMock(),
    "tqdm.tqdm": MagicMock(),
}


def setup_module(module):
    with patch.dict(sys.modules, _mock_mods):
        import via_client_cli as _cli

        module.cli = _cli


cli = None  # populated by setup_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(status_code=200, json_data=None):
    """Return a MagicMock that looks like a requests.Response."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data or {}
    return mock_resp


def _make_summarize_args(**overrides):
    """Return a SimpleNamespace with all summarize-relevant attrs set to safe defaults."""
    defaults = dict(
        model="test-model",
        id=["file-id-123"],
        url=None,
        model_temperature=None,
        model_seed=None,
        model_top_p=None,
        model_top_k=None,
        model_max_tokens=None,
        chunk_duration=None,
        chunk_overlap_duration=None,
        prompt=None,
        system_prompt=None,
        vlm_input_width=None,
        vlm_input_height=None,
        file_start_offset=None,
        file_end_offset=None,
        stream=False,
        custom_metadata=None,
        delete_external_collection=False,
        enable_audio=False,
        enable_vlm_structured_output=False,
        disable_vlm_structured_output=False,
        events=None,
        objects_of_interest=None,
        scenario=None,
        schema=None,
        batch_response_method=None,
        auto_generate_prompt=False,
        time_metadata_keys=None,
        override_vlm_prompt=False,
        enable_reasoning=False,
        print_curl_command=True,
        backend="http://localhost:8000",
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# ===========================================================================
# is_url
# ===========================================================================


@pytest.mark.unit
class TestIsUrl:
    def test_http_url_returns_true(self):
        assert cli.is_url("http://example.com/video.mp4") is True

    def test_https_url_returns_true(self):
        assert cli.is_url("https://example.com/video.mp4") is True

    def test_s3_scheme_returns_true(self):
        assert cli.is_url("s3://my-bucket/path/to/object.mp4") is True

    def test_local_absolute_path_returns_false(self):
        assert cli.is_url("/local/path/video.mp4") is False

    def test_relative_path_returns_false(self):
        assert cli.is_url("relative/path/video.mp4") is False

    def test_empty_string_returns_false(self):
        assert cli.is_url("") is False

    def test_ftp_url_returns_false(self):
        # ftp is not supported
        assert cli.is_url("ftp://example.com/video.mp4") is False


# ===========================================================================
# convert_seconds_to_string
# ===========================================================================


@pytest.mark.unit
class TestConvertSecondsToString:
    def test_minutes_and_seconds(self):
        assert cli.convert_seconds_to_string(65) == "01:05"

    def test_hours_minutes_seconds(self):
        assert cli.convert_seconds_to_string(3661) == "01:01:01"

    def test_need_hour_forces_hour_display(self):
        result = cli.convert_seconds_to_string(65, need_hour=True)
        assert result == "00:01:05"

    def test_hours_included_automatically_when_nonzero(self):
        result = cli.convert_seconds_to_string(3661, need_hour=False)
        assert result == "01:01:01"

    def test_milliseconds_flag(self):
        result = cli.convert_seconds_to_string(65, millisec=True)
        assert result.startswith("01:05.")
        # Should contain a dot followed by two digits
        parts = result.split(".")
        assert len(parts) == 2
        assert len(parts[1]) == 2

    def test_zero_seconds(self):
        assert cli.convert_seconds_to_string(0) == "00:00"

    def test_exactly_one_hour(self):
        assert cli.convert_seconds_to_string(3600) == "01:00:00"


# ===========================================================================
# format_ntp_timestamp
# ===========================================================================


@pytest.mark.unit
class TestFormatNtpTimestamp:
    def test_valid_ntp_timestamp_returns_hhmmss(self):
        result = cli.format_ntp_timestamp("2024-05-30T01:41:25.000Z")
        assert result == "01:41:25"

    def test_invalid_timestamp_returns_original(self):
        bad_ts = "not-a-timestamp"
        result = cli.format_ntp_timestamp(bad_ts)
        assert result == bad_ts

    def test_midnight_timestamp(self):
        result = cli.format_ntp_timestamp("2024-01-01T00:00:00.000Z")
        assert result == "00:00:00"

    def test_end_of_day_timestamp(self):
        result = cli.format_ntp_timestamp("2024-01-01T23:59:59.000Z")
        assert result == "23:59:59"


# ===========================================================================
# get_api_url
# ===========================================================================


@pytest.mark.unit
class TestGetApiUrl:
    def test_returns_base_url_plus_path(self):
        # Save and restore BASE_URL
        original = cli.BASE_URL
        try:
            cli.BASE_URL = "http://myserver:8000"
            result = cli.get_api_url("/files")
            assert result == "http://myserver:8000/files"
        finally:
            cli.BASE_URL = original

    def test_empty_base_url(self):
        original = cli.BASE_URL
        try:
            cli.BASE_URL = ""
            result = cli.get_api_url("/models")
            assert result == "/models"
        finally:
            cli.BASE_URL = original

    def test_concatenates_path_exactly(self):
        original = cli.BASE_URL
        try:
            cli.BASE_URL = "http://localhost:9000"
            result = cli.get_api_url("/health/ready")
            assert result == "http://localhost:9000/health/ready"
        finally:
            cli.BASE_URL = original


# ===========================================================================
# get_parser
# ===========================================================================


@pytest.mark.unit
class TestGetParser:
    def test_returns_parser(self):
        parser = cli.get_parser()
        assert parser is not None

    def test_summarize_with_id_and_model(self):
        parser = cli.get_parser()
        args = parser.parse_args(["summarize", "--id", "abc", "--model", "gpt4"])
        assert args.id == ["abc"]
        assert args.model == "gpt4"

    def test_summarize_with_url_and_model(self):
        parser = cli.get_parser()
        args = parser.parse_args(
            [
                "summarize",
                "--url",
                "http://example.com/v.mp4",
                "--model",
                "gpt4",
            ]
        )
        assert args.url == "http://example.com/v.mp4"
        assert args.model == "gpt4"

    def test_list_models_subcommand(self):
        parser = cli.get_parser()
        args = parser.parse_args(["list-models"])
        assert args.request == "list-models"

    def test_server_health_check_subcommand(self):
        parser = cli.get_parser()
        args = parser.parse_args(["server-health-check"])
        assert args.request == "server-health-check"

    def test_server_health_check_liveness_flag(self):
        parser = cli.get_parser()
        args = parser.parse_args(["server-health-check", "--liveness"])
        assert args.liveness is True

    def test_summarize_requires_model(self):
        parser = cli.get_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["summarize", "--id", "abc"])

    def test_summarize_requires_id_or_url(self):
        parser = cli.get_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["summarize", "--model", "gpt4"])

    def test_default_backend(self):
        parser = cli.get_parser()
        args = parser.parse_args(["list-models"])
        assert "localhost" in args.backend or args.backend.startswith("http")


# ===========================================================================
# check_err_response
# ===========================================================================


@pytest.mark.unit
class TestCheckErrResponse:
    def test_200_response_does_not_print(self, capsys):
        resp = _make_response(status_code=200)
        cli.check_err_response(resp, exit_on_error=False)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_400_response_prints_error(self, capsys):
        resp = _make_response(status_code=400, json_data={"code": 400, "message": "Bad Request"})
        cli.check_err_response(resp, exit_on_error=False)
        captured = capsys.readouterr()
        assert "400" in captured.out
        assert "Bad Request" in captured.out

    def test_400_response_exits_when_exit_on_error_true(self):
        resp = _make_response(status_code=400, json_data={"code": 400, "message": "Bad Request"})
        with pytest.raises(SystemExit) as exc_info:
            cli.check_err_response(resp, exit_on_error=True)
        assert exc_info.value.code == -1

    def test_500_response_prints_error(self, capsys):
        resp = _make_response(
            status_code=500, json_data={"code": 500, "message": "Internal Server Error"}
        )
        cli.check_err_response(resp, exit_on_error=False)
        captured = capsys.readouterr()
        assert "500" in captured.out

    def test_404_no_exit_when_exit_on_error_false(self):
        resp = _make_response(status_code=404, json_data={"code": 404, "message": "Not Found"})
        # Should not raise
        cli.check_err_response(resp, exit_on_error=False)

    def test_399_response_does_not_print(self, capsys):
        resp = _make_response(status_code=399)
        cli.check_err_response(resp, exit_on_error=False)
        captured = capsys.readouterr()
        assert captured.out == ""


# ===========================================================================
# do_add_file (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoAddFile:
    def test_prints_curl_command_for_local_file(self, capsys):
        args = SimpleNamespace(
            file="/tmp/test_video.mp4",
            add_as_path=False,
            print_curl_command=True,
            backend="http://localhost:8000",
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            with patch("builtins.open", MagicMock()):
                cli.do_add_file(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "/files" in captured.out

    def test_prints_curl_command_for_path_mode(self, capsys):
        args = SimpleNamespace(
            file="/tmp/test_video.mp4",
            add_as_path=True,
            print_curl_command=True,
            backend="http://localhost:8000",
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_add_file(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "/files" in captured.out

    def test_prints_curl_command_for_url_file(self, capsys):
        args = SimpleNamespace(
            file="http://example.com/video.mp4",
            add_as_path=False,
            print_curl_command=True,
            backend="http://localhost:8000",
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_add_file(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "/files" in captured.out


# ===========================================================================
# do_list_files (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoListFiles:
    def test_prints_curl_command(self, capsys):
        args = SimpleNamespace(print_curl_command=True, backend="http://localhost:8000")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_list_files(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "/files" in captured.out

    def test_curl_command_uses_get_method(self, capsys):
        args = SimpleNamespace(print_curl_command=True, backend="http://localhost:8000")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_list_files(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "GET" in captured.out


# ===========================================================================
# do_get_file_info (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoGetFileInfo:
    def test_prints_curl_command_with_file_id(self, capsys):
        args = SimpleNamespace(
            file_id="test-file-id-456",
            print_curl_command=True,
            backend="http://localhost:8000",
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_get_file_info(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "test-file-id-456" in captured.out


# ===========================================================================
# do_delete_file (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoDeleteFile:
    def test_prints_curl_command_with_delete_method(self, capsys):
        args = SimpleNamespace(
            file_id="delete-me-789",
            print_curl_command=True,
            backend="http://localhost:8000",
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_delete_file(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "DELETE" in captured.out
        assert "delete-me-789" in captured.out


# ===========================================================================
# do_summarize (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoSummarize:
    def test_prints_curl_command_basic(self, capsys):
        args = _make_summarize_args()
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "/summarize" in captured.out

    def test_req_json_contains_model(self, capsys):
        args = _make_summarize_args(model="my-model")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "my-model" in captured.out

    def test_req_json_contains_file_id(self, capsys):
        args = _make_summarize_args(id=["my-file-id"])
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "my-file-id" in captured.out

    def test_req_json_contains_url_when_provided(self, capsys):
        args = _make_summarize_args(id=None, url="http://example.com/video.mp4")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "http://example.com/video.mp4" in captured.out

    def test_req_json_contains_prompt_when_provided(self, capsys):
        args = _make_summarize_args(prompt="Describe this video")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "Describe this video" in captured.out

    def test_req_json_contains_events_as_list(self, capsys):
        args = _make_summarize_args(events="fire,flood,accident")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "fire" in captured.out
        assert "flood" in captured.out

    def test_req_json_contains_temperature_when_provided(self, capsys):
        args = _make_summarize_args(model_temperature=0.7)
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "0.7" in captured.out

    def test_enable_vlm_structured_output_flag(self, capsys):
        args = _make_summarize_args(enable_vlm_structured_output=True)
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "enable_vlm_structured_output" in captured.out

    def test_media_info_offset_included_when_offsets_provided(self, capsys):
        args = _make_summarize_args(file_start_offset="10", file_end_offset="60")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_summarize(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "media_info" in captured.out
        assert "offset" in captured.out


# ===========================================================================
# do_list_models (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoListModels:
    def test_prints_curl_command(self, capsys):
        args = SimpleNamespace(print_curl_command=True, backend="http://localhost:8000")
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_list_models(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "/models" in captured.out


# ===========================================================================
# do_server_health_check (print_curl_command=True)
# ===========================================================================


@pytest.mark.unit
class TestDoServerHealthCheck:
    def test_prints_curl_command_for_readiness(self, capsys):
        args = SimpleNamespace(
            liveness=False, print_curl_command=True, backend="http://localhost:8000"
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_server_health_check(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "ready" in captured.out

    def test_prints_curl_command_for_liveness(self, capsys):
        args = SimpleNamespace(
            liveness=True, print_curl_command=True, backend="http://localhost:8000"
        )
        original = cli.BASE_URL
        try:
            cli.BASE_URL = args.backend
            cli.do_server_health_check(args)
        finally:
            cli.BASE_URL = original
        captured = capsys.readouterr()
        assert "curl" in captured.out
        assert "live" in captured.out


# ===========================================================================
# main() — argument dispatch via mocked sys.argv and requests
# ===========================================================================


@pytest.mark.unit
class TestMain:
    def _run_main(self, argv, mock_response=None):
        """Run cli.main() with patched sys.argv and mocked requests methods."""
        if mock_response is None:
            mock_response = _make_response(200, {"data": []})

        mock_requests = MagicMock()
        mock_requests.get.return_value = mock_response
        mock_requests.post.return_value = mock_response
        mock_requests.delete.return_value = mock_response

        with patch.object(sys, "argv", argv):
            with patch.object(cli, "requests", mock_requests):
                cli.main()
        return mock_requests

    def test_list_models_dispatches_get(self):
        mock_resp = _make_response(200, {"data": []})
        mock_requests = self._run_main(
            ["via_client_cli.py", "list-models"], mock_response=mock_resp
        )
        mock_requests.get.assert_called_once()
        call_url = mock_requests.get.call_args[0][0]
        assert "/models" in call_url

    def test_server_health_check_dispatches_get(self):
        mock_resp = _make_response(200)
        mock_requests = self._run_main(
            ["via_client_cli.py", "server-health-check"], mock_response=mock_resp
        )
        mock_requests.get.assert_called_once()
        call_url = mock_requests.get.call_args[0][0]
        assert "/health/" in call_url

    def test_sets_base_url_from_backend_arg(self):
        mock_resp = _make_response(200, {"data": []})
        # --backend must come after the subcommand; parent-level --backend is overridden
        # by the subcommand's default due to argparse namespace merging.
        with patch.object(
            sys, "argv", ["via_client_cli.py", "list-models", "--backend", "http://myserver:9999"]
        ):
            with patch.object(cli, "requests", MagicMock(get=MagicMock(return_value=mock_resp))):
                cli.main()
        assert cli.BASE_URL == "http://myserver:9999"

    def test_summarize_dispatches_post(self):
        mock_resp = _make_response(
            200,
            {
                "id": "req-1",
                "created": 0,
                "model": "gpt4",
                "object": "summary",
                "media_info": {"type": "offset", "start_offset": 0, "end_offset": 10},
                "usage": {"total_chunks_processed": 1, "query_processing_time": 1.0},
                "choices": [{"message": {"content": "summary text"}, "finish_reason": "stop"}],
            },
        )
        mock_requests = self._run_main(
            [
                "via_client_cli.py",
                "summarize",
                "--id",
                "file-abc",
                "--model",
                "gpt4",
            ],
            mock_response=mock_resp,
        )
        mock_requests.post.assert_called_once()
        call_url = mock_requests.post.call_args[0][0]
        assert "/summarize" in call_url

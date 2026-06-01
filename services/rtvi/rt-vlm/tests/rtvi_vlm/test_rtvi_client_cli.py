# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit tests for RTVI CLI Client (rtvi_client_cli.py)

Tests cover:
- CLI argument parsing
- Command validation
- Request building
- Response handling
- Error handling
"""

import json
import os
import sys
from unittest.mock import Mock, patch

import pytest

# Add the src directory to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cli.rtvi_client_cli import (
    add_common_args,
    check_err_response,
    do_add_file,
    do_add_live_stream,
    do_delete_file,
    do_delete_live_stream,
    do_generate_captions,
    do_list_files,
    do_list_live_streams,
    do_list_models,
    do_server_health_check,
    do_server_metrics,
    do_stop_live_stream_processing,
    get_api_url,
    get_parser,
)


class TestCLIParser:
    """Test CLI argument parser"""

    def test_get_parser(self):
        """Test parser creation"""
        parser = get_parser()
        assert parser is not None

    def test_parser_subcommands(self):
        """Test all subcommands exist"""
        parser = get_parser()
        subparsers = [action for action in parser._actions if hasattr(action, "choices")]
        assert len(subparsers) > 0

    def test_add_file_subcommand(self):
        """Test add-file subcommand"""
        parser = get_parser()
        args = parser.parse_args(["add-file", "test.mp4"])
        assert args.request == "add-file"
        assert args.file == "test.mp4"

    def test_list_files_subcommand(self):
        """Test list-files subcommand"""
        parser = get_parser()
        args = parser.parse_args(["list-files"])
        assert args.request == "list-files"

    def test_generate_captions_subcommand(self):
        """Test generate-captions subcommand"""
        parser = get_parser()
        args = parser.parse_args(["generate-captions", "--id", "test-id", "--model", "test-model"])
        assert args.request == "generate-captions"
        assert args.id == ["test-id"]
        assert args.model == "test-model"

    def test_add_live_stream_subcommand(self):
        """Test add-live-stream subcommand"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "add-live-stream",
                "rtsp://example.com/stream",
                "--description",
                "Test stream",
            ]
        )
        assert args.request == "add-live-stream"
        assert args.live_stream_url == "rtsp://example.com/stream"
        assert args.description == "Test stream"

    def test_backend_argument(self):
        """Test backend argument"""
        parser = get_parser()
        args = parser.parse_args(["list-files", "--backend", "http://localhost:9000"])
        assert args.backend == "http://localhost:9000"

    def test_backend_from_env(self):
        """Test backend from environment variable"""
        with patch.dict(os.environ, {"RTVI_BACKEND": "http://test:8000"}):
            parser = get_parser()
            args = parser.parse_args(["list-files"])
            assert args.backend == "http://test:8000"


class TestAPIURL:
    """Test API URL construction"""

    @patch("cli.rtvi_client_cli.BASE_URL", "http://localhost:8000")
    def test_get_api_url(self):
        """Test API URL construction"""
        url = get_api_url("/files")
        assert url == "http://localhost:8000/v1/files"

    @patch("cli.rtvi_client_cli.BASE_URL", "http://localhost:8000")
    def test_get_api_url_with_prefix(self):
        """Test API URL with existing prefix"""
        url = get_api_url("/v1/files")
        assert url == "http://localhost:8000/v1/files"

    @patch("cli.rtvi_client_cli.BASE_URL", "http://localhost:8000")
    def test_get_api_url_no_leading_slash(self):
        """Test API URL without leading slash"""
        url = get_api_url("files")
        assert url == "http://localhost:8000/v1/files"


class TestErrorHandling:
    """Test error response handling"""

    def test_check_err_response_success(self):
        """Test successful response"""
        mock_response = Mock()
        mock_response.status_code = 200
        # Should not raise exception
        check_err_response(mock_response, exit_on_error=False)

    def test_check_err_response_error(self):
        """Test error response"""
        mock_response = Mock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"message": "Bad request"}
        # Should not raise with exit_on_error=False
        check_err_response(mock_response, exit_on_error=False)

    def test_check_err_response_with_detail(self):
        """Test error response with detail field"""
        mock_response = Mock()
        mock_response.status_code = 500
        mock_response.json.return_value = {"detail": "Internal error"}
        check_err_response(mock_response, exit_on_error=False)


class TestCommandFunctions:
    """Test command handler functions"""

    @patch("builtins.open", create=True)
    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_add_file(self, mock_check_err, mock_get_url, mock_post, mock_open):
        """Test add file command"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "test-id",
            "filename": "test.mp4",
            "bytes": 1000,
            "purpose": "vision",
            "media_type": "video",
            "creation_time": None,
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/files"
        # Mock file opening
        mock_file = Mock()
        mock_file.__enter__ = Mock(return_value=mock_file)
        mock_file.__exit__ = Mock(return_value=None)
        mock_open.return_value = mock_file

        args = Mock()
        args.add_as_path = False
        args.file = "test.mp4"
        args.is_image = False
        args.creation_time = None
        args.print_curl_command = False

        do_add_file(args)
        mock_post.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.get")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_list_files(self, mock_check_err, mock_get_url, mock_get):
        """Test list files command"""
        mock_response = Mock()
        mock_response.json.return_value = {"data": [], "object": "list"}
        mock_get.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/files?purpose=vision"

        args = Mock()
        args.print_curl_command = False

        do_list_files(args)
        mock_get.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.get")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_list_models(self, mock_check_err, mock_get_url, mock_get):
        """Test list models command"""
        mock_response = Mock()
        mock_response.json.return_value = {"data": [], "object": "list"}
        mock_get.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/models"

        args = Mock()
        args.print_curl_command = False

        do_list_models(args)
        mock_get.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_add_live_stream(self, mock_check_err, mock_get_url, mock_post):
        """Test add live stream command"""
        mock_response = Mock()
        mock_response.json.return_value = {"results": [{"id": "test-id"}], "errors": []}
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/streams/add"

        args = Mock()
        args.live_stream_url = "rtsp://example.com/stream"
        args.description = "Test stream"
        args.username = None
        args.password = None
        args.place_name = None
        args.place_type = None
        args.place_lat = None
        args.place_lon = None
        args.place_alt = None
        args.place_coordinate_x = None
        args.place_coordinate_y = None
        args.print_curl_command = False

        do_add_live_stream(args)
        mock_post.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_generate_captions(self, mock_check_err, mock_get_url, mock_post):
        """Test generate captions command"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "req-id",
            "model": "test-model",
            "created": 1234567890,
            "chunk_responses": [],
            "usage": {"total_chunks_processed": 0, "query_processing_time": 0},
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_captions"

        args = Mock()
        args.id = ["test-id"]
        args.model = "test-model"
        args.stream = False
        args.chunk_duration = None
        args.chunk_overlap_duration = None
        args.prompt = None
        args.system_prompt = None
        args.file_start_offset = None
        args.file_end_offset = None
        args.model_temperature = None
        args.model_top_p = None
        args.model_top_k = None
        args.model_max_tokens = None
        args.model_seed = None
        args.response_format = "text"
        args.num_frames_per_second_or_fixed_frames_chunk = None
        args.use_fps_for_chunking = False
        args.vlm_input_width = None
        args.vlm_input_height = None
        args.enable_reasoning = False
        args.print_curl_command = False

        do_generate_captions(args)
        mock_post.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.delete")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_delete_file(self, mock_check_err, mock_get_url, mock_delete):
        """Test delete file command"""
        mock_response = Mock()
        mock_response.json.return_value = {"id": "test-id", "object": "file", "deleted": True}
        mock_delete.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/files/test-id"

        args = Mock()
        args.file_id = "test-id"
        args.print_curl_command = False

        do_delete_file(args)
        mock_delete.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.delete")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_delete_live_stream(self, mock_check_err, mock_get_url, mock_delete):
        """Test delete live stream command"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_delete.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/streams/delete/test-id"

        args = Mock()
        args.video_id = "test-id"
        args.print_curl_command = False

        do_delete_live_stream(args)
        mock_delete.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.delete")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_stop_live_stream_processing(self, mock_check_err, mock_get_url, mock_delete):
        """Test stop live stream processing command"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_delete.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_captions/test-id"

        args = Mock()
        args.stream_id = "test-id"
        args.print_curl_command = False

        do_stop_live_stream_processing(args)
        mock_delete.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.get")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_server_health_check(self, mock_check_err, mock_get_url, mock_get):
        """Test server health check command"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "Service is healthy"
        mock_get.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/ready"

        args = Mock()
        args.liveness = False
        args.print_curl_command = False

        do_server_health_check(args)
        mock_get.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.get")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_server_metrics(self, mock_check_err, mock_get_url, mock_get):
        """Test server metrics command"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.text = "# HELP test_metric Test metric"
        mock_get.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/metrics"

        args = Mock()
        args.print_curl_command = False

        do_server_metrics(args)
        mock_get.assert_called_once()


class TestCurlCommandGeneration:
    """Test curl command generation"""

    def test_print_curl_command_add_file(self):
        """Test curl command generation for add-file"""
        parser = get_parser()
        args = parser.parse_args(["add-file", "test.mp4", "--print-curl-command"])
        assert args.print_curl_command is True

    def test_print_curl_command_list_files(self):
        """Test curl command generation for list-files"""
        parser = get_parser()
        args = parser.parse_args(["list-files", "--print-curl-command"])
        assert args.print_curl_command is True

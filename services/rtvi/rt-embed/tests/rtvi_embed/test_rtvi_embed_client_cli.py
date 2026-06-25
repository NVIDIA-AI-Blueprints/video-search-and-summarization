# SPDX-FileCopyrightText: Copyright (c) 2024-2025, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
Unit tests for RTVI Embed CLI Client (rtvi_client_cli.py with embeddings commands)

Tests cover:
- CLI argument parsing for embeddings commands
- Command validation
- Request building for text/video embeddings
- Response handling
- Error handling
"""

import os
import sys
from unittest.mock import Mock, patch

from cli.rtvi_client_cli import (
    check_err_response,
    do_add_file,
    do_add_live_stream,
    do_delete_file,
    do_delete_live_stream,
    do_generate_text_embeddings,
    do_generate_video_embeddings,
    do_list_files,
    do_list_live_streams,
    do_list_models,
    do_server_health_check,
    do_server_metrics,
    do_stop_live_stream_processing,
    get_api_url,
    get_parser,
)

# Add the project-root src directory to path for imports (tests/rtvi_embed/../.. -> repo root)
sys.path.insert(
    0,
    os.path.join(os.path.dirname(__file__), "..", "..", "src"),
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
        args = parser.parse_args(["add-file", "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"])
        assert args.request == "add-file"
        assert args.file == "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"

    def test_list_files_subcommand(self):
        """Test list-files subcommand"""
        parser = get_parser()
        args = parser.parse_args(["list-files"])
        assert args.request == "list-files"

    def test_generate_text_embeddings_subcommand(self):
        """Test generate-text-embeddings subcommand"""
        parser = get_parser()
        args = parser.parse_args(
            ["generate-text-embeddings", "--text-input", "test text", "--model", "test-model"]
        )
        assert args.request == "generate-text-embeddings"
        assert args.text_input == ["test text"]
        assert args.model == "test-model"

    def test_generate_video_embeddings_subcommand(self):
        """Test generate-video-embeddings subcommand"""
        parser = get_parser()
        args = parser.parse_args(
            ["generate-video-embeddings", "--id", "test-id", "--model", "test-model"]
        )
        assert args.request == "generate-video-embeddings"
        assert args.id == ["test-id"]
        assert args.model == "test-model"

    def test_generate_video_embeddings_with_options(self):
        """Test generate-video-embeddings with optional parameters"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-video-embeddings",
                "--id",
                "test-id",
                "--model",
                "test-model",
                "--stream",
                "--chunk-duration",
                "5",
                "--chunk-overlap-duration",
                "1",
            ]
        )
        assert args.request == "generate-video-embeddings"
        assert args.stream is True
        assert args.chunk_duration == 5
        assert args.chunk_overlap_duration == 1

    def test_add_live_stream_subcommand(self):
        """Test add-live-stream subcommand"""
        parser = get_parser()
        args = parser.parse_args(
            ["add-live-stream", "rtsp://example.com/stream", "--description", "Test stream"]
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

    @patch("cli.rtvi_client_cli.BASE_URL", "http://localhost:8000")
    def test_get_api_url_embeddings_endpoints(self):
        """Test API URL for embeddings endpoints"""
        url = get_api_url("/generate_text_embeddings")
        assert url == "http://localhost:8000/v1/generate_text_embeddings"

        url = get_api_url("/generate_video_embeddings")
        assert url == "http://localhost:8000/v1/generate_video_embeddings"


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


class TestFileCommands:
    """Test file management command functions"""

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_add_file(self, mock_check_err, mock_get_url, mock_post):
        """Test add file command"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "test-id",
            "filename": "/opt/nvidia/rtvi/warmup_streams/its_264.mp4",
            "bytes": 1000,
            "purpose": "vision",
            "media_type": "video",
            "creation_time": None,
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/files"

        args = Mock()
        args.add_as_path = False
        args.file = "/opt/nvidia/rtvi/warmup_streams/its_264.mp4"
        args.is_image = False
        args.creation_time = None
        args.print_curl_command = False

        do_add_file(args)
        mock_post.assert_called_once()
        mock_check_err.assert_called_once()

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
        mock_check_err.assert_called_once()

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
        mock_check_err.assert_called_once()


class TestModelCommands:
    """Test model management command functions"""

    @patch("cli.rtvi_client_cli.requests.get")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_list_models(self, mock_check_err, mock_get_url, mock_get):
        """Test list models command"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "data": [
                {
                    "id": "cosmos-embed-1",
                    "object": "model",
                    "created": 1234567890,
                    "owned_by": "nvidia",
                    "api_type": "embeddings",
                }
            ],
            "object": "list",
        }
        mock_get.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/models"

        args = Mock()
        args.print_curl_command = False

        do_list_models(args)
        mock_get.assert_called_once()
        mock_check_err.assert_called_once()


class TestStreamCommands:
    """Test live stream command functions"""

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
        mock_check_err.assert_called_once()

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
        mock_check_err.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.get")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_list_live_streams(self, mock_check_err, mock_get_url, mock_get):
        """Test list live streams command"""
        mock_response = Mock()
        mock_response.json.return_value = []
        mock_get.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/streams/get-stream-info"

        args = Mock()
        args.print_curl_command = False

        do_list_live_streams(args)
        mock_get.assert_called_once()
        mock_check_err.assert_called_once()


class TestTextEmbeddingsCommands:
    """Test text embeddings generation command functions"""

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_generate_text_embeddings_single(self, mock_check_err, mock_get_url, mock_post):
        """Test generate text embeddings command with single input"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "req-id",
            "model": "test-model",
            "created": 1234567890,
            "data": [{"text_input": "test text", "embeddings": [0.1] * 100}],
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_text_embeddings"

        args = Mock()
        args.text_input = ["test text"]
        args.model = "test-model"
        args.print_curl_command = False

        do_generate_text_embeddings(args)
        mock_post.assert_called_once()
        mock_check_err.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_generate_text_embeddings_multiple(self, mock_check_err, mock_get_url, mock_post):
        """Test generate text embeddings command with multiple inputs"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "req-id",
            "model": "test-model",
            "created": 1234567890,
            "data": [
                {"text_input": "text 1", "embeddings": [0.1] * 100},
                {"text_input": "text 2", "embeddings": [0.2] * 100},
            ],
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_text_embeddings"

        args = Mock()
        args.text_input = ["text 1", "text 2"]
        args.model = "test-model"
        args.print_curl_command = False

        do_generate_text_embeddings(args)
        mock_post.assert_called_once()
        mock_check_err.assert_called_once()

    def test_generate_text_embeddings_curl_command(self):
        """Test curl command generation for text embeddings"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-text-embeddings",
                "--text-input",
                "test",
                "--model",
                "test-model",
                "--print-curl-command",
            ]
        )
        assert args.print_curl_command is True


class TestVideoEmbeddingsCommands:
    """Test video embeddings generation command functions"""

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_generate_video_embeddings_non_streaming(
        self, mock_check_err, mock_get_url, mock_post
    ):
        """Test generate video embeddings command without streaming"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "req-id",
            "model": "test-model",
            "created": 1234567890,
            "media_info": {"type": "offset", "start_offset": "0", "end_offset": "10"},
            "chunk_responses": [{"start_time": "0", "end_time": "5", "embeddings": [0.1] * 100}],
            "usage": {"total_chunks_processed": 1, "query_processing_time": 5},
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_video_embeddings"

        args = Mock(
            id=["test-id"],
            model="test-model",
            stream=False,
            chunk_duration=None,
            chunk_overlap_duration=None,
            file_start_offset=None,
            file_end_offset=None,
            print_curl_command=False,
            url=None,
            base64=None,
            media_type=None,
            creation_time=None,
        )

        do_generate_video_embeddings(args)
        mock_post.assert_called_once()
        mock_check_err.assert_called_once()

    @patch("cli.rtvi_client_cli.requests.post")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_generate_video_embeddings_with_options(
        self, mock_check_err, mock_get_url, mock_post
    ):
        """Test generate video embeddings command with optional parameters"""
        mock_response = Mock()
        mock_response.json.return_value = {
            "id": "req-id",
            "model": "test-model",
            "created": 1234567890,
            "chunk_responses": [],
            "usage": {"total_chunks_processed": 0, "query_processing_time": 0},
        }
        mock_post.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_video_embeddings"

        args = Mock(
            id=["test-id"],
            model="test-model",
            stream=False,
            chunk_duration=5,
            chunk_overlap_duration=1,
            file_start_offset="0",
            file_end_offset="60",
            print_curl_command=False,
            url=None,
            base64=None,
            media_type=None,
            creation_time=None,
        )

        do_generate_video_embeddings(args)
        mock_post.assert_called_once()
        mock_check_err.assert_called_once()

        # Verify the request JSON includes the parameters
        call_args = mock_post.call_args
        req_json = call_args[1]["json"]
        assert req_json["chunk_duration"] == 5
        assert req_json["chunk_overlap_duration"] == 1
        assert "media_info" in req_json
        mock_check_err.assert_called_once()

    def test_generate_video_embeddings_curl_command(self):
        """Test curl command generation for video embeddings"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-video-embeddings",
                "--id",
                "test-id",
                "--model",
                "test-model",
                "--print-curl-command",
            ]
        )
        assert args.print_curl_command is True


class TestHealthAndMetrics:
    """Test health check and metrics commands"""

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
        mock_check_err.assert_called_once()

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
        mock_check_err.assert_called_once()


class TestStopProcessing:
    """Test stop processing commands"""

    @patch("cli.rtvi_client_cli.requests.delete")
    @patch("cli.rtvi_client_cli.get_api_url")
    @patch("cli.rtvi_client_cli.check_err_response")
    def test_do_stop_live_stream_processing(self, mock_check_err, mock_get_url, mock_delete):
        """Test stop live stream processing command"""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_delete.return_value = mock_response
        mock_get_url.return_value = "http://localhost:8000/v1/generate_video_embeddings/test-id"

        args = Mock()
        args.stream_id = "test-id"
        args.print_curl_command = False

        do_stop_live_stream_processing(args)
        mock_delete.assert_called_once()
        mock_check_err.assert_called_once()


class TestCurlCommandGeneration:
    """Test curl command generation"""

    def test_print_curl_command_add_file(self):
        """Test curl command generation for add-file"""
        parser = get_parser()
        args = parser.parse_args(
            ["add-file", "/opt/nvidia/rtvi/warmup_streams/its_264.mp4", "--print-curl-command"]
        )
        assert args.print_curl_command is True

    def test_print_curl_command_list_files(self):
        """Test curl command generation for list-files"""
        parser = get_parser()
        args = parser.parse_args(["list-files", "--print-curl-command"])
        assert args.print_curl_command is True

    def test_print_curl_command_text_embeddings(self):
        """Test curl command generation for text embeddings"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-text-embeddings",
                "--text-input",
                "test",
                "--model",
                "test-model",
                "--print-curl-command",
            ]
        )
        assert args.print_curl_command is True

    def test_print_curl_command_video_embeddings(self):
        """Test curl command generation for video embeddings"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-video-embeddings",
                "--id",
                "test-id",
                "--model",
                "test-model",
                "--print-curl-command",
            ]
        )
        assert args.print_curl_command is True


class TestMultipleInputs:
    """Test handling of multiple inputs"""

    def test_multiple_text_inputs(self):
        """Test multiple text inputs for embeddings"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-text-embeddings",
                "--text-input",
                "first text",
                "--text-input",
                "second text",
                "--text-input",
                "third text",
                "--model",
                "test-model",
            ]
        )
        assert len(args.text_input) == 3
        assert args.text_input[0] == "first text"
        assert args.text_input[1] == "second text"
        assert args.text_input[2] == "third text"

    def test_multiple_file_ids(self):
        """Test multiple file IDs for video embeddings"""
        parser = get_parser()
        args = parser.parse_args(
            [
                "generate-video-embeddings",
                "--id",
                "id-1",
                "--id",
                "id-2",
                "--id",
                "id-3",
                "--model",
                "test-model",
            ]
        )
        assert len(args.id) == 3
        assert args.id[0] == "id-1"
        assert args.id[1] == "id-2"
        assert args.id[2] == "id-3"

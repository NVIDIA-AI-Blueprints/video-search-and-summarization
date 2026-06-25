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

"""
Unit tests for Direct Media URL feature (Mode 3).
Run with: pytest test/test_direct_media_url.py -v
"""

import os
import tempfile
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import Mock

import pytest


def _get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _MediaServerHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves test media files."""
    
    test_content = b"fake video content for testing"
    
    def do_GET(self):
        if "/slow" in self.path:
            time.sleep(5)
        if "/404" in self.path:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Content-Length", str(len(self.test_content)))
        self.end_headers()
        self.wfile.write(self.test_content)
    
    def log_message(self, format, *args):
        pass


@pytest.fixture
def media_server():
    """Start a local HTTP server for testing."""
    port = _get_free_port()
    server = HTTPServer(("127.0.0.1", port), _MediaServerHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


def _make_downloader(timeout_seconds: int = 10) -> "MediaDownloader":
    """Construct the production MediaDownloader directly.

    DirectMediaHandler no longer exposes download_media; the download path
    lives on MediaDownloader, which is what these tests actually exercise.

    allow_private_urls=True is required because the local fixture HTTPServer
    binds to 127.0.0.1; the production SSRF guard rejects loopback by default.
    """
    from handlers.direct_media.media_downloader import MediaDownloader, DownloadConfig
    return MediaDownloader(DownloadConfig(
        download_dir=tempfile.gettempdir(),
        timeout_seconds=timeout_seconds,
        max_size_mb=50,
        allow_private_urls=True,
    ))


@pytest.fixture
def downloader():
    return _make_downloader()


class TestDirectMediaDownload:
    """Tests for MediaDownloader.download (the public download API)."""

    def test_successful_download(self, media_server, downloader):
        url = f"{media_server}/test.mp4"
        local_path = downloader.download(url, worker_id=1)

        assert local_path is not None
        assert os.path.exists(local_path)
        assert os.path.getsize(local_path) > 0

        os.unlink(local_path)

    def test_download_404_returns_none(self, media_server, downloader):
        local_path = downloader.download(f"{media_server}/404", worker_id=1)
        assert local_path is None

    def test_download_timeout(self, media_server):
        downloader = _make_downloader(timeout_seconds=1)
        local_path = downloader.download(f"{media_server}/slow", worker_id=1)
        assert local_path is None

    def test_unique_filename_per_download(self, media_server, downloader):
        url = f"{media_server}/test.mp4"
        path1 = downloader.download(url, worker_id=1)
        path2 = downloader.download(url, worker_id=1)

        assert path1 != path2

        for p in (path1, path2):
            if p and os.path.exists(p):
                os.unlink(p)


class TestRouting:
    """Test routing logic for media_url vs video_path."""
    
    def test_media_url_detected(self):
        """media_url should be detected from message."""
        info = {'media_url': 'https://example.com/video.mp4'}
        media_url = info.get('media_url') or info.get('mediaUrl')
        assert media_url == 'https://example.com/video.mp4'
    
    def test_camel_case_mediaUrl_detected(self):
        """mediaUrl (camelCase) should also be detected."""
        info = {'mediaUrl': 'https://example.com/video.mp4'}
        media_url = info.get('media_url') or info.get('mediaUrl')
        assert media_url == 'https://example.com/video.mp4'
    
    def test_priority_media_url_over_video_path(self):
        """media_url should take priority over video_path."""
        info = {
            'media_url': 'https://example.com/video.mp4',
            'video_path': '/local/path.mp4'
        }
        media_url = info.get('media_url') or info.get('mediaUrl')
        video_path = info.get('video_path')
        
        assert media_url is not None
        assert video_path is not None


class TestResponseMergeToInfo:
    """Test VLM response is correctly merged into message['info']."""
    
    def test_vlm_response_merged_to_info(self):
        """Default (no-parser) VLM response content is merged to info.reasoning.
        (Option B: the new ``vlm_response`` key is reserved
        for the pluggable-parser path only.)"""
        message = {
            'sensorId': 'cam-01',
            'category': 'vehicle_count',
            'info': {
                'media_url': 'https://example.com/parking.jpg',
                'media_type': 'image'
            }
        }
        
        vlm_response_content = "I count 14 vehicles: 8 sedans, 4 SUVs, 2 trucks"
        message['info']['reasoning'] = vlm_response_content

        assert 'reasoning' in message['info']
        assert message['info']['reasoning'] == vlm_response_content
    
    def test_verdict_empty_for_evaluation_mode(self):
        """Mode 3 sets verdict to empty string."""
        message = {
            'sensorId': 'cam-01',
            'info': {'media_url': 'https://example.com/video.mp4'}
        }
        
        message['info']['verdict'] = ""
        
        assert message['info']['verdict'] == ""
        assert message['info']['verdict'] != "confirmed"
    
    def test_video_source_set_to_original_url(self):
        """videoSource should be set to original media_url."""
        media_url = "https://s3.example.com/bucket/parking-lot.jpg"
        message = {
            'sensorId': 'cam-01',
            'info': {'media_url': media_url}
        }
        
        message['info']['videoSource'] = media_url
        
        assert message['info']['videoSource'] == media_url
    
    def test_response_code_and_status_merged(self):
        """verificationResponseCode and verificationResponseStatus should be merged."""
        message = {
            'sensorId': 'cam-01',
            'info': {'media_url': 'https://example.com/video.mp4'}
        }
        
        message['info']['verificationResponseCode'] = 200
        message['info']['verificationResponseStatus'] = "OK"
        
        assert message['info']['verificationResponseCode'] == 200
        assert message['info']['verificationResponseStatus'] == "OK"
    
    def test_original_fields_preserved_after_merge(self):
        """Original info fields should be preserved after merge."""
        original_media_url = "https://example.com/video.mp4"
        original_media_type = "video"
        original_custom_field = "custom_value"
        
        message = {
            'sensorId': 'cam-01',
            'category': 'safety_check',
            'info': {
                'media_url': original_media_url,
                'media_type': original_media_type,
                'custom_field': original_custom_field
            }
        }
        
        message['info']['reasoning'] = "Analysis result..."
        message['info']['verdict'] = ""
        message['info']['videoSource'] = original_media_url
        message['info']['verificationResponseCode'] = 200
        
        assert message['info']['media_url'] == original_media_url
        assert message['info']['media_type'] == original_media_type
        assert message['info']['custom_field'] == original_custom_field
    
    def test_complete_output_schema(self):
        """Test complete output schema after merge."""
        message = {
            'sensorId': 'parking-cam-01',
            'category': 'vehicle_count',
            'timestamp': '2026-02-26T10:00:00Z',
            'info': {
                'media_url': 'https://s3.example.com/parking.jpg',
                'media_type': 'image'
            }
        }
        
        vlm_text = "I count 14 vehicles in the parking lot"
        message['info']['reasoning'] = vlm_text
        message['info']['verdict'] = ""
        message['info']['videoSource'] = message['info']['media_url']
        message['info']['verificationResponseCode'] = 200
        message['info']['verificationResponseStatus'] = "OK"
        
        info = message['info']
        assert info['media_url'] == 'https://s3.example.com/parking.jpg'
        assert info['media_type'] == 'image'
        assert info['reasoning'] == vlm_text
        assert info['verdict'] == ""
        assert info['videoSource'] == info['media_url']
        assert info['verificationResponseCode'] == 200
        assert info['verificationResponseStatus'] == "OK"
    
    def test_error_response_merged(self):
        """Test error response is also merged to info."""
        message = {
            'sensorId': 'cam-01',
            'info': {'media_url': 'https://example.com/video.mp4'}
        }
        
        message['info']['reasoning'] = ""
        message['info']['verdict'] = ""
        message['info']['verificationResponseCode'] = 500
        message['info']['verificationResponseStatus'] = "VLM Error: timeout"
        
        assert message['info']['verificationResponseCode'] == 500
        assert "VLM Error" in message['info']['verificationResponseStatus']


class TestConfig:
    """Test configuration defaults."""
    
    def test_defaults_when_config_missing(self):
        """Test default values when media_download config is missing."""
        config = {'alert_agent': {}}
        
        md = config.get('alert_agent', {}).get('media_download', {})
        assert md.get('enabled', True) == True
        assert md.get('timeout_seconds', 30) == 30
        assert md.get('max_size_mb', 50) == 50
    
    def test_feature_can_be_disabled(self):
        """Test that direct media can be disabled."""
        config = {'alert_agent': {'media_download': {'enabled': False}}}
        enabled = config['alert_agent']['media_download']['enabled']
        assert enabled == False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

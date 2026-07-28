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

"""Unit tests for ``handlers.direct_media.media_downloader``.

Mode 3 (direct media) fetches a URL supplied in the incoming event, so this
module is the service's SSRF boundary and its only defence against a hostile
or oversized payload. What is pinned here:

* **SSRF.** Only http/https; localhost literals rejected by name; every
  address a hostname resolves to is checked, so a DNS entry pointing at a
  private range, the loopback, or the 169.254 cloud-metadata endpoint is
  refused. ``allow_private_urls`` is the documented escape hatch and is
  covered as such.
* **Size enforcement happens twice** — a ``content-length`` pre-check and a
  running total while streaming — because a hostile server can lie about or
  omit ``content-length``. The streaming check must delete the partial file.
* **Every failure returns ``None``** rather than raising: the worker loop
  treats a failed download as a skipped event.

``socket.getaddrinfo`` and ``requests.get`` are patched throughout — no
network access and no DNS lookups happen.
"""

import socket
from unittest.mock import MagicMock, mock_open, patch

import pytest

from handlers.direct_media.media_downloader import DownloadConfig, MediaDownloader


def resolves_to(*ips):
    """Build a ``getaddrinfo`` return value for the given addresses."""
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0)) for ip in ips]


@pytest.fixture
def downloader(tmp_path):
    return MediaDownloader(DownloadConfig(download_dir=str(tmp_path / "media")))


@pytest.fixture
def public_dns():
    with patch("socket.getaddrinfo", return_value=resolves_to("93.184.216.34")) as resolve:
        yield resolve


class TestDownloadConfig:
    def test_defaults(self):
        config = DownloadConfig()
        assert config.download_dir == "/tmp/alert_bridge_media"
        assert config.timeout_seconds == 30
        assert config.max_size_mb == 50
        assert config.allow_private_urls is False

    def test_overrides(self):
        config = DownloadConfig(download_dir="/data", timeout_seconds=5, max_size_mb=1)
        assert config.download_dir == "/data"
        assert config.timeout_seconds == 5
        assert config.max_size_mb == 1


class TestConstruction:
    def test_download_dir_is_created(self, tmp_path):
        target = tmp_path / "nested" / "media"
        MediaDownloader(DownloadConfig(download_dir=str(target)))
        assert target.is_dir()

    def test_existing_download_dir_is_reused(self, tmp_path):
        target = tmp_path / "media"
        target.mkdir()
        MediaDownloader(DownloadConfig(download_dir=str(target)))
        assert target.is_dir()


class TestValidateUrl:
    @pytest.mark.parametrize("url", ["http://example.com/v.mp4", "https://example.com/v.mp4"])
    def test_public_http_urls_are_allowed(self, downloader, public_dns, url):
        assert downloader.validate_url(url) == (True, "")

    @pytest.mark.parametrize(
        "url", ["ftp://example.com/v.mp4", "file:///etc/passwd", "gopher://example.com"]
    )
    def test_non_http_schemes_are_rejected(self, downloader, url):
        is_valid, error = downloader.validate_url(url)
        assert is_valid is False
        assert "only http/https allowed" in error

    def test_url_without_a_hostname_is_rejected(self, downloader):
        is_valid, error = downloader.validate_url("http:///v.mp4")
        assert is_valid is False
        assert error == "URL has no hostname"

    @pytest.mark.parametrize(
        "host", ["localhost", "LOCALHOST", "localhost.localdomain", "127.0.0.1", "0.0.0.0"]
    )
    def test_localhost_literals_are_rejected_by_name(self, downloader, host):
        is_valid, error = downloader.validate_url(f"http://{host}/v.mp4")
        assert is_valid is False
        assert "Localhost URLs not allowed" in error

    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.5",       # private
            "192.168.1.10",   # private
            "172.16.0.1",     # private
            "127.0.0.2",      # loopback
            "169.254.169.254",  # cloud metadata
            "224.0.0.1",      # multicast
            "240.0.0.1",      # reserved
        ],
    )
    def test_hostnames_resolving_to_internal_ranges_are_rejected(self, downloader, ip):
        with patch("socket.getaddrinfo", return_value=resolves_to(ip)):
            is_valid, error = downloader.validate_url("http://sneaky.example.com/v.mp4")

        assert is_valid is False
        assert "Private IP addresses not allowed" in error

    def test_every_resolved_address_is_checked(self, downloader):
        """A public A record does not excuse a private one on the same host."""
        with patch("socket.getaddrinfo", return_value=resolves_to("93.184.216.34", "10.0.0.5")):
            is_valid, _error = downloader.validate_url("http://dual.example.com/v.mp4")

        assert is_valid is False

    def test_unresolvable_hostname_is_rejected(self, downloader):
        with patch("socket.getaddrinfo", side_effect=socket.gaierror("Name or service not known")):
            is_valid, error = downloader.validate_url("http://nope.example.com/v.mp4")

        assert is_valid is False
        assert "Could not resolve hostname" in error

    def test_unparseable_resolved_address_is_skipped(self, downloader):
        with patch("socket.getaddrinfo", return_value=[(socket.AF_INET, 1, 6, "", ("not-an-ip", 0))]):
            assert downloader.validate_url("http://example.com/v.mp4") == (True, "")

    def test_unexpected_validation_error_is_reported_not_raised(self, downloader):
        with patch(
            "handlers.direct_media.media_downloader.urlsplit",
            side_effect=RuntimeError("boom"),
        ):
            is_valid, error = downloader.validate_url("http://example.com/v.mp4")

        assert is_valid is False
        assert "URL validation error" in error


class TestValidateUrlWithPrivateUrlsAllowed:
    @pytest.fixture
    def downloader(self, tmp_path):
        return MediaDownloader(
            DownloadConfig(download_dir=str(tmp_path / "media"), allow_private_urls=True)
        )

    def test_private_addresses_are_accepted(self, downloader):
        assert downloader.validate_url("http://10.0.0.5/v.mp4") == (True, "")

    def test_localhost_is_accepted(self, downloader):
        assert downloader.validate_url("http://localhost:8080/v.mp4") == (True, "")

    def test_scheme_is_still_enforced(self, downloader):
        is_valid, error = downloader.validate_url("file:///etc/passwd")
        assert is_valid is False
        assert "only http/https allowed" in error

    def test_dns_is_not_consulted(self, downloader):
        with patch("socket.getaddrinfo", side_effect=AssertionError("must not resolve")):
            assert downloader.validate_url("http://10.0.0.5/v.mp4")[0] is True


def make_response(chunks=(b"data",), headers=None, raise_for_status=None):
    response = MagicMock()
    response.headers = headers if headers is not None else {}
    response.iter_content.return_value = list(chunks)
    if raise_for_status is not None:
        response.raise_for_status.side_effect = raise_for_status
    return response


class TestDownload:
    def test_writes_the_stream_and_returns_the_path(self, downloader, public_dns, tmp_path):
        with patch("requests.get", return_value=make_response([b"abc", b"def"])):
            path = downloader.download("http://example.com/clip.mp4", worker_id=3)

        assert path is not None
        assert path.startswith(str(tmp_path / "media"))
        with open(path, "rb") as handle:
            assert handle.read() == b"abcdef"

    def test_filename_carries_the_worker_id_and_extension(self, downloader, public_dns):
        with patch("requests.get", return_value=make_response()):
            path = downloader.download("http://example.com/clip.mp4", worker_id=7)

        assert "worker_7_" in path
        assert path.endswith(".mp4")

    def test_extensionless_url_defaults_to_mp4(self, downloader, public_dns):
        with patch("requests.get", return_value=make_response()):
            path = downloader.download("http://example.com/clip", worker_id=1)

        assert path.endswith(".mp4")

    def test_content_type_overrides_the_url_extension(self, downloader, public_dns):
        response = make_response(headers={"content-type": "image/png; charset=binary"})
        with patch("requests.get", return_value=response):
            path = downloader.download("http://example.com/clip.mp4", worker_id=1)

        assert path.endswith(".png")

    def test_request_uses_the_configured_timeout_and_streams(self, downloader, public_dns):
        with patch("requests.get", return_value=make_response()) as get:
            downloader.download("http://example.com/clip.mp4", worker_id=1)

        assert get.call_args.kwargs["stream"] is True
        assert get.call_args.kwargs["timeout"] == 30
        assert get.call_args.kwargs["allow_redirects"] is True

    def test_rejected_url_is_not_fetched(self, downloader):
        with patch("requests.get", side_effect=AssertionError("must not fetch")):
            assert downloader.download("file:///etc/passwd", worker_id=1) is None

    def test_http_error_returns_none(self, downloader, public_dns):
        import requests

        response = make_response(raise_for_status=requests.HTTPError("404"))
        with patch("requests.get", return_value=response):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is None

    def test_transport_error_returns_none(self, downloader, public_dns):
        import requests

        with patch("requests.get", side_effect=requests.ConnectionError("refused")):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is None

    def test_unexpected_error_returns_none(self, downloader, public_dns):
        with patch("requests.get", side_effect=RuntimeError("boom")):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is None

    def test_oversized_content_length_is_rejected_before_reading(self, downloader, public_dns):
        response = make_response(headers={"content-length": str(60 * 1024 * 1024)})
        with patch("requests.get", return_value=response):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is None

        response.iter_content.assert_not_called()
        response.close.assert_called_once()

    def test_content_length_within_the_limit_is_accepted(self, downloader, public_dns):
        response = make_response(headers={"content-length": str(1024)})
        with patch("requests.get", return_value=response):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is not None

    def test_streaming_overrun_is_rejected_and_the_partial_file_removed(self, tmp_path, public_dns):
        import os

        downloader = MediaDownloader(
            DownloadConfig(download_dir=str(tmp_path / "media"), max_size_mb=1)
        )
        oversized = [b"x" * (512 * 1024)] * 4  # 2 MB, no content-length header

        with patch("requests.get", return_value=make_response(oversized)):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is None

        assert os.listdir(tmp_path / "media") == []

    def test_empty_response_body_is_rejected(self, downloader, public_dns, tmp_path):
        import os

        with patch("requests.get", return_value=make_response([])):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is None

        assert os.listdir(tmp_path / "media") == []

    def test_download_dir_is_recreated_if_removed(self, tmp_path, public_dns):
        target = tmp_path / "media"
        downloader = MediaDownloader(DownloadConfig(download_dir=str(target)))
        target.rmdir()

        with patch("requests.get", return_value=make_response()):
            assert downloader.download("http://example.com/clip.mp4", worker_id=1) is not None


class TestCleanup:
    def test_removes_an_existing_file(self, tmp_path):
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"data")

        MediaDownloader.cleanup(str(target))

        assert not target.exists()

    def test_missing_file_is_tolerated(self, tmp_path):
        MediaDownloader.cleanup(str(tmp_path / "nope.mp4"))

    @pytest.mark.parametrize("path", ["", None])
    def test_blank_path_is_tolerated(self, path):
        MediaDownloader.cleanup(path)

    def test_unlink_failure_is_swallowed(self, tmp_path):
        target = tmp_path / "clip.mp4"
        target.write_bytes(b"data")

        with patch("os.unlink", side_effect=PermissionError("read-only fs")):
            MediaDownloader.cleanup(str(target))

        assert target.exists()

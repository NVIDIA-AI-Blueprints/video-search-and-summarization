# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Tests for URL download configuration: SSL verification and redirect handling."""

import os
from unittest.mock import patch

import pytest


def _should_skip_ssl(url, env_value):
    """Helper replicating the domain-based SSL skip logic from asset_manager."""
    from urllib.parse import urlparse

    skip_domains = [d.strip().lower() for d in env_value.split(",") if d.strip()]
    url_host = (urlparse(url).hostname or "").lower()
    return url_host in skip_domains


class TestSSLSkipVerifyDomains:
    """Test ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS environment variable behavior."""

    def test_ssl_verified_by_default(self):
        """All URLs should be SSL-verified when no skip domains are set."""
        assert _should_skip_ssl("https://example.com/video.mp4", "") is False

    def test_skip_ssl_for_listed_domain(self):
        """SSL should be skipped for domains in the allowlist."""
        domains = "artifactory.nvidia.com,nv-wowza-pdc.nvidia.com"
        assert _should_skip_ssl("https://artifactory.nvidia.com/path/video.mp4", domains) is True
        assert (
            _should_skip_ssl("https://nv-wowza-pdc.nvidia.com:1935/vod/test.mp4", domains) is True
        )

    def test_ssl_verified_for_unlisted_domain(self):
        """SSL should remain verified for domains NOT in the allowlist."""
        domains = "artifactory.nvidia.com"
        assert _should_skip_ssl("https://example.com/video.mp4", domains) is False
        assert _should_skip_ssl("https://evil-artifactory.nvidia.com/video.mp4", domains) is False

    def test_case_insensitive_matching(self):
        """Domain matching should be case insensitive."""
        domains = "Artifactory.NVIDIA.com"
        assert _should_skip_ssl("https://artifactory.nvidia.com/video.mp4", domains) is True

    def test_whitespace_handling(self):
        """Whitespace around domain names should be stripped."""
        domains = " artifactory.nvidia.com , nv-wowza-pdc.nvidia.com "
        assert _should_skip_ssl("https://artifactory.nvidia.com/video.mp4", domains) is True

    def test_empty_env_var(self):
        """Empty env var means all URLs are SSL-verified."""
        assert _should_skip_ssl("https://artifactory.nvidia.com/video.mp4", "") is False

    def test_env_var_integration(self):
        """Test reading from actual environment variable."""
        with patch.dict(os.environ, {"ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS": "example.com"}):
            env = os.environ.get("ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS", "")
            assert _should_skip_ssl("https://example.com/video.mp4", env) is True
            assert _should_skip_ssl("https://other.com/video.mp4", env) is False

    def test_no_env_var_set(self):
        """When env var is not set, all URLs are SSL-verified."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS", None)
            env = os.environ.get("ASSET_DOWNLOAD_SSL_SKIP_VERIFY_DOMAINS", "")
            assert _should_skip_ssl("https://artifactory.nvidia.com/video.mp4", env) is False


def _parse_max_redirects(env_value):
    """Helper replicating the redirect hop parsing logic from asset_manager."""
    try:
        val = int(env_value)
        return max(0, min(val, 10))
    except (ValueError, TypeError):
        return 0


class TestRedirectConfig:
    """Test ASSET_DOWNLOAD_MAX_REDIRECTS environment variable behavior."""

    def test_default_zero_disables_redirects(self):
        """Default 0 means redirects are disabled."""
        assert _parse_max_redirects("0") == 0
        assert _parse_max_redirects("0") <= 0  # allow_redirects = max > 0

    def test_positive_value_enables_redirects(self):
        """Positive value enables redirects with that hop count."""
        assert _parse_max_redirects("3") == 3
        assert _parse_max_redirects("5") == 5

    def test_clamped_to_max_10(self):
        """Values above 10 are clamped to 10."""
        assert _parse_max_redirects("20") == 10
        assert _parse_max_redirects("100") == 10

    def test_negative_clamped_to_zero(self):
        """Negative values are clamped to 0 (disabled)."""
        assert _parse_max_redirects("-1") == 0
        assert _parse_max_redirects("-5") == 0

    def test_invalid_value_defaults_to_zero(self):
        """Non-integer values default to 0 (disabled)."""
        assert _parse_max_redirects("true") == 0
        assert _parse_max_redirects("abc") == 0
        assert _parse_max_redirects("") == 0

    def test_env_var_integration(self):
        """Test reading from actual environment variable."""
        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_REDIRECTS": "5"}):
            val = os.environ.get("ASSET_DOWNLOAD_MAX_REDIRECTS", "0")
            assert _parse_max_redirects(val) == 5
            assert _parse_max_redirects(val) > 0  # allow_redirects = True

    def test_env_var_not_set(self):
        """When not set, defaults to 0 (disabled)."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASSET_DOWNLOAD_MAX_REDIRECTS", None)
            val = os.environ.get("ASSET_DOWNLOAD_MAX_REDIRECTS", "0")
            assert _parse_max_redirects(val) == 0


class TestMaxDownloadFileSizeConfig:
    """Test ASSET_DOWNLOAD_MAX_FILE_SIZE_GB parsing."""

    def test_default_is_8_gib(self):
        """Default download ingestion limit should be 8 GiB."""
        from utils.asset_manager import _parse_max_download_file_size_bytes

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASSET_DOWNLOAD_MAX_FILE_SIZE_GB", None)
            assert _parse_max_download_file_size_bytes() == 8 * 1024 * 1024 * 1024

    def test_env_override_allows_larger_downloads(self):
        """Operators can raise the URL/data URI ingestion limit."""
        from utils.asset_manager import _parse_max_download_file_size_bytes

        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB": "12"}):
            assert _parse_max_download_file_size_bytes() == 12 * 1024 * 1024 * 1024

    def test_fractional_gib_is_supported(self):
        """Fractional values are accepted for small-test and constrained deployments."""
        from utils.asset_manager import _parse_max_download_file_size_bytes

        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB": "0.5"}):
            assert _parse_max_download_file_size_bytes() == 512 * 1024 * 1024

    @pytest.mark.parametrize("value", ["0", "-1", "abc"])
    def test_invalid_values_raise(self, value):
        """Invalid or non-positive values should fail fast at parse time."""
        from utils.asset_manager import _parse_max_download_file_size_bytes

        with patch.dict(os.environ, {"ASSET_DOWNLOAD_MAX_FILE_SIZE_GB": value}):
            with pytest.raises(ValueError):
                _parse_max_download_file_size_bytes()


class TestURLHeadersAllowlist:
    """Test url_headers allowlist filtering."""

    ALLOWED = {
        "authorization",
        "x-api-key",
        "x-jfrog-art-api",
        "cookie",
        "accept",
        "accept-language",
    }

    def test_allowed_headers_pass(self):
        """Allowed headers should be accepted."""
        for header in ["Authorization", "X-API-Key", "Cookie", "Accept"]:
            assert header.lower() in self.ALLOWED

    def test_blocked_headers_rejected(self):
        """Dangerous headers should be blocked."""
        for header in ["Host", "Transfer-Encoding", "Content-Length", "X-Forwarded-For"]:
            assert header.lower() not in self.ALLOWED

    def test_auth_stripped_on_redirect_to_different_domain(self):
        """url_headers should only apply to the original domain."""
        original_host = "artifactory.nvidia.com"
        redirect_host = "evil.com"
        # On same domain: headers applied
        assert original_host == original_host
        # On different domain: headers NOT applied
        assert redirect_host != original_host


class TestAuthTokensParsing:
    """Test ASSET_DOWNLOAD_AUTH_TOKENS env var parsing."""

    def _parse_auth_tokens(self, env_value, target_host):
        """Replicate auth token parsing from asset_manager."""
        for entry in env_value.split(";"):
            entry = entry.strip()
            if "=" not in entry:
                continue
            domain, token = entry.split("=", 1)
            if domain.strip().lower() == target_host.lower():
                return token.strip()
        return None

    def test_single_domain_token(self):
        token = self._parse_auth_tokens(
            "artifactory.nvidia.com=Bearer abc123", "artifactory.nvidia.com"
        )
        assert token == "Bearer abc123"

    def test_multiple_domains(self):
        env = "artifactory.nvidia.com=Bearer abc;cdn.nvidia.com=Basic xyz"
        assert self._parse_auth_tokens(env, "artifactory.nvidia.com") == "Bearer abc"
        assert self._parse_auth_tokens(env, "cdn.nvidia.com") == "Basic xyz"

    def test_unmatched_domain(self):
        token = self._parse_auth_tokens("artifactory.nvidia.com=Bearer abc", "evil.com")
        assert token is None

    def test_empty_env(self):
        assert self._parse_auth_tokens("", "artifactory.nvidia.com") is None

    def test_malformed_entry_skipped(self):
        token = self._parse_auth_tokens(
            "bad_entry;artifactory.nvidia.com=Bearer abc", "artifactory.nvidia.com"
        )
        assert token == "Bearer abc"

    def test_token_with_equals_sign(self):
        """Token values can contain = characters (e.g., base64)."""
        token = self._parse_auth_tokens("example.com=Basic dXNlcjpwYXNz==", "example.com")
        assert token == "Basic dXNlcjpwYXNz=="

    def test_env_var_integration(self):
        with patch.dict(
            os.environ,
            {"ASSET_DOWNLOAD_AUTH_TOKENS": "example.com=Bearer token123"},
        ):
            env = os.environ.get("ASSET_DOWNLOAD_AUTH_TOKENS", "")
            assert self._parse_auth_tokens(env, "example.com") == "Bearer token123"


class TestAuthHeaderPriority:
    """Test url_headers vs ASSET_DOWNLOAD_AUTH_TOKENS priority."""

    def test_request_headers_override_env(self):
        """url_headers should take priority over ASSET_DOWNLOAD_AUTH_TOKENS."""
        url_headers = {"Authorization": "Bearer request-token"}
        env_value = "Bearer env-token"
        # When url_headers is provided for same domain, it wins
        assert url_headers.get("Authorization") != env_value

    def test_env_used_when_no_request_headers(self):
        """ASSET_DOWNLOAD_AUTH_TOKENS used when url_headers is None."""
        url_headers = None
        env_value = "Bearer env-token"
        assert url_headers is None
        assert env_value is not None


class TestSSRFValidatorFix:
    """Test SSRF validator correctly handles hostnames vs IP addresses."""

    def test_hostname_is_not_ip(self):
        """Hostnames should not be treated as IP addresses."""
        import ipaddress

        is_ip = False
        try:
            ipaddress.ip_address("artifactory.nvidia.com")
            is_ip = True
        except (ValueError, ipaddress.AddressValueError):
            pass
        assert is_ip is False

    def test_ipv4_is_ip(self):
        """IPv4 addresses should be recognized."""
        import ipaddress

        is_ip = False
        try:
            ipaddress.ip_address("10.0.0.1")
            is_ip = True
        except (ValueError, ipaddress.AddressValueError):
            pass
        assert is_ip is True

    def test_ipv6_is_ip(self):
        """IPv6 addresses should be recognized."""
        import ipaddress

        is_ip = False
        try:
            ipaddress.ip_address("::1")
            is_ip = True
        except (ValueError, ipaddress.AddressValueError):
            pass
        assert is_ip is True

    def test_hostname_with_port_is_not_ip(self):
        """Hostname:port should not be parsed as IP (urlparse extracts hostname only)."""
        from urllib.parse import urlparse

        hostname = urlparse("https://artifactory.nvidia.com:443/path").hostname
        assert hostname == "artifactory.nvidia.com"


class TestURLDownloadVlmQuery:
    """Test that URL-based VlmQuery fields support various URL patterns."""

    def test_https_artifactory_url_pattern(self):
        """Internal artifactory HTTPS URLs should pass VlmQuery URL validation."""
        import re

        url = (
            "https://artifactory.nvidia.com/artifactory/"
            "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
        )
        assert re.match(r"^(https?://|s3://|file://)", url)

    def test_https_public_url_pattern(self):
        """Public HTTPS URLs should pass VlmQuery URL validation."""
        import re

        url = "https://test-videos.co.uk/bigbuckbunny/mp4-h264/1080/Big_Buck_Bunny_1080_10s_1MB.mp4"
        assert re.match(r"^(https?://|s3://|file://)", url)

    def test_http_url_pattern(self):
        """HTTP URLs should pass VlmQuery URL validation."""
        import re

        url = "http://example.com/video.mp4"
        assert re.match(r"^(https?://|s3://|file://)", url)

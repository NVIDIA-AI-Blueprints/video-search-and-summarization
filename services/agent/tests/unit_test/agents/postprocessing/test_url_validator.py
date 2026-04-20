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

"""Unit tests for URLValidator."""

from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

from vss_agents.agents.postprocessing.validators.url_validator import URLValidator
from vss_agents.agents.postprocessing.validators.url_validator import extract_urls
from vss_agents.agents.postprocessing.validators.url_validator import extract_urls_from_tags_with_alt


class TestExtractUrls:
    """Tests for the extract_urls helper."""

    def test_extracts_http_urls(self):
        text = "Visit http://example.com for more info."
        assert extract_urls(text) == ["http://example.com"]

    def test_extracts_https_urls(self):
        text = "See https://example.com/page?q=1"
        assert extract_urls(text) == ["https://example.com/page?q=1"]

    def test_deduplicates(self):
        text = "http://example.com and http://example.com again"
        assert extract_urls(text) == ["http://example.com"]

    def test_strips_trailing_punctuation(self):
        text = "Check http://example.com. Also http://other.com,"
        urls = extract_urls(text)
        assert "http://example.com" in urls
        assert "http://other.com" in urls

    def test_no_urls(self):
        assert extract_urls("No URLs here") == []

    def test_multiple_urls(self):
        text = "First http://a.com then https://b.com/path"
        urls = extract_urls(text)
        assert len(urls) == 2
        assert urls[0] == "http://a.com"
        assert urls[1] == "https://b.com/path"

    def test_ignores_non_http_schemes(self):
        text = "ftp://files.example.com and rtsp://stream.example.com"
        assert extract_urls(text) == []


def _mock_response(status):
    """Create a mock aiohttp response with the given status code."""
    resp = AsyncMock()
    resp.status = status
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


class TestURLValidator:
    """Tests for URLValidator."""

    @pytest.fixture
    def validator(self):
        return URLValidator(internal_ip="127.0.0.1", timeout=5.0, max_retries=0)

    @pytest.mark.asyncio
    async def test_passes_when_no_urls(self, validator):
        """Text with no tags-with-alt, no markdown links, no plain http(s) URLs passes."""
        result = await validator.validate("No links here")
        assert result.passed is True
        assert result.issues == []

    @pytest.mark.asyncio
    async def test_fails_when_tag_src_is_placeholder(self, validator):
        """Tag with alt and invalid src (e.g. placeholder) fails; issues contain the URL only."""
        result = await validator.validate('<tag src="placeholder_url" alt="placeholder_alt">placeholder_alt</tag>')
        assert result.passed is False
        assert result.issues == ["placeholder_url"]

    @pytest.mark.asyncio
    async def test_fails_when_markdown_link_url_is_placeholder(self, validator):
        """Markdown link with invalid URL fails; issues contain the URL only."""
        result = await validator.validate("See [text](placeholder_url) for details.")
        assert result.passed is False
        assert result.issues == ["placeholder_url"]

    @pytest.mark.asyncio
    async def test_passes_when_tag_src_url_returns_200(self, validator):
        """Tag with alt and valid http URL that is accessible passes."""
        mock_resp = _mock_response(200)
        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate('<tag src="http://example.com/path" alt="caption">caption</tag>')
        assert result.passed is True
        assert result.issues == []

    @pytest.mark.asyncio
    async def test_fails_when_tag_src_url_returns_500(self, validator):
        """Tag with alt and valid URL that returns 500 fails; issues contain the URL only."""
        mock_resp = _mock_response(500)
        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate('<tag src="http://example.com/path" alt="caption">caption</tag>')
        assert result.passed is False
        assert result.issues == ["http://example.com/path"]

    @pytest.mark.asyncio
    async def test_head_405_falls_back_to_get(self, validator):
        """When HEAD returns 405, should fall back to GET."""
        head_resp = _mock_response(405)
        get_resp = _mock_response(200)

        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=head_resp)
        mock_session.get = MagicMock(return_value=get_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate('<tag src="http://example.com/path" alt="caption">caption</tag>')
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_head_exception_falls_back_to_get(self, validator):
        """When HEAD raises an exception, should fall back to GET."""
        get_resp = _mock_response(200)

        mock_session = AsyncMock()
        mock_session.head = MagicMock(side_effect=Exception("connection refused"))
        mock_session.get = MagicMock(return_value=get_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate('<tag src="http://example.com/path" alt="caption">caption</tag>')
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_returns_all_invalid_and_inaccessible_urls_at_once(self, validator):
        """One invalid (placeholder) and one inaccessible URL: issues contain both."""
        bad_resp = _mock_response(500)
        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=bad_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate(
                '<tag src="placeholder_url" alt="a">a</tag> Also see <a href="http://bad.com/page" alt="Link">link</a>'
            )
        assert result.passed is False
        assert "placeholder_url" in result.issues
        assert "http://bad.com/page" in result.issues
        assert len(result.issues) == 2

    @pytest.mark.asyncio
    async def test_multiple_tags_partial_failure(self, validator):
        """One accessible and one inaccessible URL in tags with alt: issues contain only the failed URL."""
        good_resp = _mock_response(200)
        bad_resp = _mock_response(500)
        call_count = 0

        def make_head_response(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return good_resp if call_count == 1 else bad_resp

        mock_session = AsyncMock()
        mock_session.head = MagicMock(side_effect=make_head_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate(
                '<tag src="http://good.com/a" alt="A">A</tag> <tag src="http://bad.com/b" alt="B">B</tag>'
            )
        assert result.passed is False
        assert result.issues == ["http://bad.com/b"]

    @pytest.mark.asyncio
    async def test_deduplicates_invalid_urls(self, validator):
        """Same placeholder URL in a tag and a markdown link: issues contain it only once."""
        result = await validator.validate(
            '<tag src="placeholder_url" alt="a">a</tag> Also see [text](placeholder_url) for details.'
        )
        assert result.passed is False
        assert result.issues == ["placeholder_url"]

    @pytest.mark.asyncio
    async def test_accepts_uppercase_scheme(self, validator):
        """URLs with uppercase HTTP/HTTPS schemes are treated as valid."""
        mock_resp = _mock_response(200)
        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate('<tag src="HTTP://example.com/path" alt="caption">caption</tag>')
        assert result.passed is True
        assert result.issues == []

    def test_feedback_template(self):
        v = URLValidator(internal_ip="127.0.0.1", feedback_template="Broken: {issues}")
        feedback = v.format_feedback(["http://bad.com"])
        assert "Broken:" in feedback

    @pytest.mark.asyncio
    async def test_fails_when_tag_has_backslash_escaped_src(self, validator):
        """Tag with backslash-escaped quotes around src URL should still be detected."""
        result = await validator.validate(
            '<video src=\\"http://placeholder.invalid/video.mp4\\" alt=\\"Video\\">Video</video>'
        )
        assert result.passed is False
        assert result.issues == ["http://placeholder.invalid/video.mp4"]

    @pytest.mark.asyncio
    async def test_passes_when_backslash_escaped_src_url_returns_200(self, validator):
        """Tag with backslash-escaped quotes and accessible URL should pass."""
        mock_resp = _mock_response(200)
        mock_session = AsyncMock()
        mock_session.head = MagicMock(return_value=mock_resp)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch("aiohttp.ClientSession", return_value=mock_session):
            result = await validator.validate(
                '<video src=\\"http://example.com/video.mp4\\" alt=\\"Warehouse Safety\\">video</video>'
            )
        assert result.passed is True
        assert result.issues == []


class TestExtractUrlsFromTagsWithAlt:
    """Tests for backslash-escaped quote handling in extract_urls_from_tags_with_alt."""

    def test_normal_double_quotes(self):
        text = '<video src="http://example.com/v.mp4" alt="Video">Video</video>'
        urls = extract_urls_from_tags_with_alt(text)
        assert urls == ["http://example.com/v.mp4"]

    def test_normal_single_quotes(self):
        text = "<video src='http://example.com/v.mp4' alt='Video'>Video</video>"
        urls = extract_urls_from_tags_with_alt(text)
        assert urls == ["http://example.com/v.mp4"]

    def test_backslash_escaped_double_quotes(self):
        text = '<video src=\\"http://example.com/v.mp4\\" alt=\\"Video\\">Video</video>'
        urls = extract_urls_from_tags_with_alt(text)
        assert urls == ["http://example.com/v.mp4"]

    def test_backslash_escaped_single_quotes(self):
        text = "<video src=\\'http://example.com/v.mp4\\' alt=\\'Video\\'>Video</video>"
        urls = extract_urls_from_tags_with_alt(text)
        assert urls == ["http://example.com/v.mp4"]

    def test_alt_before_src_with_backslash_quotes(self):
        text = '<img alt=\\"Snapshot\\" src=\\"http://example.com/img.jpg\\">'
        urls = extract_urls_from_tags_with_alt(text)
        assert urls == ["http://example.com/img.jpg"]

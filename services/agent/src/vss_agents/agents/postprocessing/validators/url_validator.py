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

"""URL validator to verify URLs are accessible."""

import logging
import re
from typing import Any

import aiohttp
from tenacity import AsyncRetrying
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_random_exponential

from vss_agents.agents.postprocessing.data_models import ValidatorResult
from vss_agents.agents.postprocessing.validators.base import BaseValidator
from vss_agents.utils.url_translation import rewrite_url_host

logger = logging.getLogger(__name__)

# 1) Tags with alt: <tag ... alt="text" ...> or <tag ... alt='text' ...>. We capture src= or href= (the URL).
#    Attribute order varies, so we have two patterns each: url first (group 1), or alt first (group 2 = url).
#    \\?" / \\?' handles optional backslash-escaped quotes produced by LLMs in JSON contexts.
_Q = r"""\\?["']"""  # matches an optional backslash followed by a single or double quote
_VAL = r"""[^"'\\]+"""  # URL value: excludes quotes and backslashes
_ALTVAL = r"""[^"'\\]*"""  # alt text value: same but may be empty
TAG_ALT_SRC_PATTERN = re.compile(
    rf"<[a-zA-Z][^>]*\ssrc={_Q}({_VAL}){_Q}[^>]*\salt={_Q}({_ALTVAL}){_Q}[^>]*/?>",
    re.IGNORECASE,
)
TAG_ALT_SRC_ORDER2 = re.compile(
    rf"<[a-zA-Z][^>]*\salt={_Q}({_ALTVAL}){_Q}[^>]*\ssrc={_Q}({_VAL}){_Q}[^>]*/?>",
    re.IGNORECASE,
)
TAG_ALT_HREF_PATTERN = re.compile(
    rf"<[a-zA-Z][^>]*\shref={_Q}({_VAL}){_Q}[^>]*\salt={_Q}({_ALTVAL}){_Q}[^>]*/?>",
    re.IGNORECASE,
)
TAG_ALT_HREF_ORDER2 = re.compile(
    rf"<[a-zA-Z][^>]*\salt={_Q}({_ALTVAL}){_Q}[^>]*\shref={_Q}({_VAL}){_Q}[^>]*/?>",
    re.IGNORECASE,
)

# 2) Markdown: [text](url) and ![alt](url)
# Assumes URLs do not contain nested parentheses
MARKDOWN_LINK_URL_PATTERN = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")

# Plain http(s) URLs (for other http(s) URLs not in tags or markdown)
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+', re.IGNORECASE)

# Transient aiohttp errors worth retrying.
_RETRYABLE_EXCEPTIONS = (
    aiohttp.ClientConnectorError,
    aiohttp.ServerTimeoutError,
    aiohttp.ServerDisconnectedError,
    TimeoutError,
)

_BACKOFF_SECONDS = 1.0


def _strip_url(url: str) -> str:
    """Strip trailing punctuation that might have been captured with the URL."""
    return (url or "").strip().rstrip(".,;:!?)'\"\\")


def extract_urls_from_tags_with_alt(text: str) -> list[str]:
    """Extract src= or href= (the URL) from any tag that has alt=. Generic: <tag ... alt=\"text\" ... src=\"url\"> or alt='text'."""
    urls: list[str] = []
    for pattern in (TAG_ALT_SRC_PATTERN, TAG_ALT_HREF_PATTERN):
        for m in pattern.finditer(text):
            url = _strip_url(m.group(1))
            if url:
                urls.append(url)
    for pattern in (TAG_ALT_SRC_ORDER2, TAG_ALT_HREF_ORDER2):
        for m in pattern.finditer(text):
            url = _strip_url(m.group(2))  # url is group 2 when alt comes first
            if url:
                urls.append(url)
    return urls


def extract_urls_from_markdown_links(text: str) -> list[str]:
    """Extract URLs from Markdown [text](url) and ![alt](url)."""
    urls: list[str] = []
    for m in MARKDOWN_LINK_URL_PATTERN.finditer(text):
        url = _strip_url(m.group(1))
        if url:
            urls.append(url)
    return urls


def is_valid_url(src: str) -> bool:
    """True if src starts with http:// or https:// (This function only checks scheme, not full URL accessibility)."""
    return bool(src and (src.lower().startswith("http://") or src.lower().startswith("https://")))


def extract_urls(text: str) -> list[str]:
    """Extract and dedupe http(s) URLs from text (used for other URLs not in tags/markdown)."""
    seen: set[str] = set()
    result: list[str] = []
    for u in URL_PATTERN.findall(text):
        url = _strip_url(u)
        if url and url not in seen:
            seen.add(url)
            result.append(url)
    return result


class URLValidator(BaseValidator):
    """Verify URLs are accessible."""

    name = "url_validator"

    def __init__(
        self,
        internal_ip: str,
        timeout: float = 10.0,
        feedback_template: str = "",
        max_retries: int = 2,
        **kwargs: Any,  # noqa: ARG002
    ) -> None:
        """Initialize the URL validator.

        Args:
            internal_ip: Internal IP address (e.g. ``10.0.1.1``).
                The host in each URL is rewritten to this IP before validation
                so that accessibility checks always hit the internal endpoint.
            timeout: HTTP request timeout in seconds.
            feedback_template: Template for feedback message. Use {issues} placeholder.
            max_retries: Number of retries for transient HTTP errors per URL.
        """
        super().__init__(
            feedback_template=feedback_template,
        )
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.max_retries = max_retries
        self.internal_ip = internal_ip

    async def validate(self, output: str, **kwargs: Any) -> ValidatorResult:  # noqa: ARG002
        """Check URLs from: (1) any tag with alt (src/href), (2) Markdown [text](url), (3) other plain URLs. All must be valid and accessible."""
        issues: list[str] = []
        seen: set[str] = set()
        urls_to_check_accessibility: list[str] = []

        # 1-2) Tags with alt (src/href) and Markdown links: must be http(s) or fail; if valid, add to urls_to_check_accessibility
        url_sources = [
            (extract_urls_from_tags_with_alt(output), "tag"),
            (extract_urls_from_markdown_links(output), "Markdown link"),
        ]
        for urls, source_label in url_sources:
            for url in urls:
                if url in seen:
                    continue
                seen.add(url)
                if not is_valid_url(url):
                    issues.append(url)
                    logger.info(f"{self.name}: invalid URL in {source_label}: {url!r}")
                else:
                    urls_to_check_accessibility.append(url)

        # 3) Other plain http(s) URLs not in the above (extract_urls already returns http(s) only)
        for url in extract_urls(output):
            if url not in seen:
                seen.add(url)
                urls_to_check_accessibility.append(url)

        if urls_to_check_accessibility:
            logger.info(f"{self.name}: checking {len(urls_to_check_accessibility)} URL(s) for accessibility")
            async with aiohttp.ClientSession(timeout=self.timeout) as session:
                for url in urls_to_check_accessibility:
                    accessible = await self._validate_url(session, url)
                    if accessible:
                        logger.debug(f"{self.name}: PASSED for {url}")
                    else:
                        logger.info(f"{self.name}: FAILED (not accessible): {url}")
                        issues.append(url)

        # Return with all issues (invalid URLs from tags/markdown + inaccessible URLs)
        return ValidatorResult(name=self.name, passed=len(issues) == 0, issues=issues)

    async def _validate_url(self, session: aiohttp.ClientSession, url: str) -> bool:
        """Check if URL is accessible, with retries on transient errors.

        When ``internal_ip`` is configured, the URL host is rewritten to this
        IP so that the request always reaches the internal service directly.
        """
        if self.internal_ip:
            url = rewrite_url_host(url, self.internal_ip)
            logger.debug(f"{self.name}: rewritten URL for validation: {url}")
        if self.max_retries > 0:
            retrying = AsyncRetrying(
                retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
                stop=stop_after_attempt(self.max_retries + 1),
                wait=wait_random_exponential(multiplier=_BACKOFF_SECONDS, max=_BACKOFF_SECONDS * 8),
                reraise=True,
            )
            async for attempt in retrying:
                with attempt:
                    return await self._try_request(session, url)

        return await self._try_request(session, url)

    async def _try_request(self, session: aiohttp.ClientSession, url: str) -> bool:
        """HEAD first, fall back to GET on 404/405/501 or transport error."""
        try:
            async with session.head(url, allow_redirects=True) as resp:
                if resp.status < 400:
                    return True
                if resp.status in (404, 405, 501):
                    logger.debug(f"HEAD failed for {url} (HTTP {resp.status}), trying GET")
                else:
                    logger.info(f"URL failed: {url} (HTTP {resp.status})")
                    return False
        except Exception as e:
            logger.debug(f"HEAD failed for {url} ({e}), trying GET")

        try:
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status < 400:
                    return True
                logger.info(f"URL failed: {url} (HTTP {resp.status})")
                return False
        except Exception as e:
            logger.info(f"URL failed: {url} ({e})")
            return False

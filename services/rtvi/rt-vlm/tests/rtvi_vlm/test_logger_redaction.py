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
"""Tests for sensitive URL data redaction at the logging boundary."""

import logging
import sys

import pytest

from common.logger import (
    _SensitiveURLFilter,
    sanitize_data_for_logging,
    sanitize_url_for_logging,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "http://example.com/video.mp4?access_token=CANARY_SECRET",
            "http://example.com/video.mp4",
        ),
        (
            "https://bucket.s3.amazonaws.com/video.mp4?X-Amz-Credential=SECRET&X-Amz-Signature=SIG",
            "https://bucket.s3.amazonaws.com/video.mp4",
        ),
        (
            "https://user:password@example.com:8443/path/video.mp4#token=SECRET",
            "https://example.com:8443/path/video.mp4",
        ),
        (
            "rtsp://user:CANARY_SECRET@[camera/path",
            "[malformed URL redacted]",
        ),
        (
            "http:////user:CANARY_SECRET@example.com/video.mp4",
            "[malformed URL redacted]",
        ),
        (
            "data:video/mp4,CANARY_DATA_URL_SECRET",
            "[data URL redacted]",
        ),
        ("file:///data/video.mp4", "file:///data/video.mp4"),
    ],
)
def test_sanitize_url_for_logging(url, expected):
    assert sanitize_url_for_logging(url) == expected


def test_sensitive_url_filter_sanitizes_formatted_log_record():
    record = logging.LogRecord(
        name="common.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Received query=%s",
        args=(
            '{"url":"http://example.com/video.mp4?access_token=CANARY_SECRET",'
            '"prompt":"describe"}',
        ),
        exc_info=None,
    )

    assert _SensitiveURLFilter().filter(record)
    assert record.getMessage() == (
        'Received query={"url":"http://example.com/video.mp4 [URL query redacted]'
    )
    assert "CANARY_SECRET" not in record.getMessage()


def test_sensitive_url_filter_redacts_malformed_url():
    record = logging.LogRecord(
        name="common.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Opening %s",
        args=("rtsp://user:CANARY_SECRET@[camera/path",),
        exc_info=None,
    )

    assert _SensitiveURLFilter().filter(record)
    assert record.getMessage() == "Opening [malformed URL redacted]"
    assert "CANARY_SECRET" not in record.getMessage()


def test_sensitive_url_filter_redacts_empty_authority_url():
    record = logging.LogRecord(
        name="common.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Opening %s",
        args=("http:////user:CANARY_SECRET@example.com/video.mp4",),
        exc_info=None,
    )

    assert _SensitiveURLFilter().filter(record)
    assert record.getMessage() == "Opening [malformed URL redacted]"
    assert "CANARY_SECRET" not in record.getMessage()


def test_sensitive_url_filter_sanitizes_propagated_exception():
    try:
        raise RuntimeError(
            "download failed for "
            "https://user:password@example.com/video.mp4?token=CANARY_SECRET"
        )
    except RuntimeError:
        record = logging.LogRecord(
            name="common.logger",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="Download failed",
            args=(),
            exc_info=sys.exc_info(),
        )

    assert _SensitiveURLFilter().filter(record)
    propagated_output = logging.Formatter().format(record)
    assert "Traceback (most recent call last)" in propagated_output
    assert "https://example.com/video.mp4" in propagated_output
    assert "CANARY_SECRET" not in propagated_output
    assert "user:password" not in propagated_output


def test_sensitive_url_filter_redacts_data_url_payload():
    record = logging.LogRecord(
        name="common.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Processing media: %s (type: video)",
        args=("data:video/mp4,CANARY_DATA_URL_SECRET",),
        exc_info=None,
    )

    assert _SensitiveURLFilter().filter(record)
    assert record.getMessage() == "Processing media: [data URL redacted]"
    assert "CANARY_DATA_URL_SECRET" not in record.getMessage()


@pytest.mark.parametrize(
    "query_suffix",
    [
        "prefix'CANARY_SECRET",
        'prefix\\\"CANARY_SECRET',
        "prefix%22CANARY_SECRET",
        "prefix\tCANARY_SECRET",
        "prefix\nCANARY_SECRET",
    ],
)
def test_sensitive_url_filter_drops_untrusted_query_remainder(query_suffix):
    record = logging.LogRecord(
        name="common.logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='Received query={"url":"http://example.com/video.mp4?access_token=%s"}',
        args=(query_suffix,),
        exc_info=None,
    )

    assert _SensitiveURLFilter().filter(record)
    assert record.getMessage() == (
        'Received query={"url":"http://example.com/video.mp4 [URL query redacted]'
    )
    assert "CANARY_SECRET" not in record.getMessage()


@pytest.mark.parametrize(
    "query_suffix",
    [
        "prefix'CANARY_SECRET",
        'prefix\\\"CANARY_SECRET',
        "prefix\tCANARY_SECRET",
        "prefix\nCANARY_SECRET",
    ],
)
def test_sanitize_data_for_logging_preserves_structure_and_removes_secrets(
    query_suffix,
):
    value = {
        "url": f"http://example.com/video.mp4?access_token={query_suffix}",
        "url_headers": {"Authorization": "Bearer CANARY_HEADER_SECRET"},
        "prompt": "describe",
    }

    assert sanitize_data_for_logging(value) == {
        "url": "http://example.com/video.mp4",
        "url_headers": "[redacted]",
        "prompt": "describe",
    }

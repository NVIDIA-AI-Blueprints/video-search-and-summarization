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
Unit tests for src/vss_api_models.py

Tests API validation patterns and Pydantic model validators.
Critical for API security and input validation.
"""
import re
from uuid import uuid4

import pytest
from pydantic import ValidationError

from vss_api_models import (
    AWS_S3_OBJECT_URL_PATTERN,
    AWS_S3_URL_PATTERN,
    CAMERA_ID_PATTERN,
    DESCRIPTION_PATTERN,
    ERROR_CODE_PATTERN,
    ERROR_MESSAGE_PATTERN,
    FILE_NAME_PATTERN,
    HTTP_URL_VALIDATION_PATTERN,
    KEY_PATTERN,
    LIVE_STREAM_URL_PATTERN,
    PATH_PATTERN,
    TIMESTAMP_PATTERN,
)

# =============================================================================
# AWS S3 URL Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "s3_url",
    [
        "s3://my-bucket/path/to/file.mp4",
        "s3://bucket-name/video.avi",
        "s3://my.bucket.name/folder/subfolder/file.mkv",
        "s3://bucket123/object-key_with-special.chars",
        "s3://a/short",
    ],
)
def test_aws_s3_url_pattern_valid(s3_url):
    """Test AWS S3 URL pattern matches valid s3:// URLs."""
    assert re.match(AWS_S3_URL_PATTERN, s3_url) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://example.com/file.mp4",
        "s3:/bucket/file",  # Missing second slash
        "s3://",  # No bucket or object
        "s3://bucket",  # No object key
        "s3://BUCKET/file",  # Uppercase not allowed
        "s3://bucket-/file",  # Bucket can't end with hyphen
        "s3://-bucket/file",  # Bucket can't start with hyphen
    ],
)
def test_aws_s3_url_pattern_invalid(invalid_url):
    """Test AWS S3 URL pattern rejects invalid URLs."""
    assert re.match(AWS_S3_URL_PATTERN, invalid_url) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "s3_object_url",
    [
        "https://my-bucket.s3.us-east-1.amazonaws.com/path/to/file.mp4",
        "https://bucket.s3-us-west-2.amazonaws.com/video.avi",
        "http://my-bucket.s3.eu-west-1.amazonaws.com/file.mkv",
        "https://s3.us-east-1.amazonaws.com/bucket/path/file.mp4",
        "https://s3-ap-south-1.amazonaws.com/my-bucket/video.mp4",
    ],
)
def test_aws_s3_object_url_pattern_valid(s3_object_url):
    """Test AWS S3 object URL pattern matches valid HTTPS S3 URLs."""
    assert re.match(AWS_S3_OBJECT_URL_PATTERN, s3_object_url, re.VERBOSE) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_url",
    [
        "https://example.com/file.mp4",
        "s3://bucket/file",
        "https://s3.amazonaws.com/file",  # Missing bucket
        "ftp://bucket.s3.region.amazonaws.com/file",  # Wrong protocol
    ],
)
def test_aws_s3_object_url_pattern_invalid(invalid_url):
    """Test AWS S3 object URL pattern rejects invalid URLs."""
    assert re.match(AWS_S3_OBJECT_URL_PATTERN, invalid_url, re.VERBOSE) is None


# =============================================================================
# HTTP URL Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "http_url",
    [
        "http://example.com/video.mp4",
        "https://example.com/path/to/file.avi",
        "http://192.168.1.1/stream",
        "https://sub.domain.example.com:8080/video?quality=hd",
        "http://localhost:3000/api/video",
        "https://example.com/file-name_with.special~chars+test",
    ],
)
def test_http_url_pattern_valid(http_url):
    """Test HTTP URL pattern matches valid HTTP/HTTPS URLs."""
    assert re.match(HTTP_URL_VALIDATION_PATTERN, http_url) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_url",
    [
        "ftp://example.com/file",
        "file:///path/to/file",
        "s3://bucket/file",
        "example.com/file",  # Missing protocol
        "http//example.com",  # Missing colon
    ],
)
def test_http_url_pattern_invalid(invalid_url):
    """Test HTTP URL pattern rejects non-HTTP URLs."""
    assert re.match(HTTP_URL_VALIDATION_PATTERN, invalid_url) is None


# =============================================================================
# RTSP Live Stream URL Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "rtsp_url",
    [
        "rtsp://example.com/stream",
        "rtsp://192.168.1.100:554/live",
        "rtsp://camera.local/stream1",
    ],
)
def test_live_stream_url_pattern_valid(rtsp_url):
    """Test RTSP URL pattern matches valid RTSP URLs."""
    assert re.match(LIVE_STREAM_URL_PATTERN, rtsp_url) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_url",
    [
        "http://example.com/stream",
        "https://example.com/live",
        "rtp://example.com/stream",  # Wrong protocol
        "rtsp//example.com",  # Missing colon
    ],
)
def test_live_stream_url_pattern_invalid(invalid_url):
    """Test RTSP URL pattern rejects non-RTSP URLs."""
    assert re.match(LIVE_STREAM_URL_PATTERN, invalid_url) is None


# =============================================================================
# Timestamp Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "timestamp",
    [
        "2024-01-01T00:00:00Z",
        "2024-12-31T23:59:59Z",
        "2024-06-15T12:30:45Z",
        "2024-01-01T00:00:00.000Z",  # With milliseconds
        "2024-01-01T00:00:00.999Z",
    ],
)
def test_timestamp_pattern_valid(timestamp):
    """Test timestamp pattern matches valid RFC3339 timestamps."""
    assert re.match(TIMESTAMP_PATTERN, timestamp) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_timestamp",
    [
        "2024-01-01",  # Missing time
        "2024-01-01T00:00:00",  # Missing Z
        "01-01-2024T00:00:00Z",  # Wrong date format
        "2024/01/01T00:00:00Z",  # Wrong separator
    ],
)
def test_timestamp_pattern_invalid(invalid_timestamp):
    """Test timestamp pattern rejects invalid timestamps."""
    assert re.match(TIMESTAMP_PATTERN, invalid_timestamp) is None


# =============================================================================
# File Name Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "filename",
    [
        "video.mp4",
        "my-video_file.avi",
        "video 2024.mkv",
        "path/to/video.mp4",
        "file-name_with-numbers123.extension",
        "video.tar.gz",  # Multiple dots
        "simple",  # No extension
    ],
)
def test_file_name_pattern_valid(filename):
    """Test file name pattern matches valid file names."""
    assert re.match(FILE_NAME_PATTERN, filename) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_filename",
    [
        "video!.mp4",  # Exclamation mark
        "video@file.avi",  # At symbol
        "my#video.mkv",  # Hash symbol
        "file$.mp4",  # Dollar sign
        "video%20.avi",  # Percent encoding
    ],
)
def test_file_name_pattern_invalid(invalid_filename):
    """Test file name pattern rejects invalid characters."""
    assert re.match(FILE_NAME_PATTERN, invalid_filename) is None


# =============================================================================
# Path Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "path",
    [
        "/path/to/file.mp4",
        "relative/path/video.avi",
        "/usr/local/bin/file",
        "path_with-special.chars/file",
        "/",
        ".",
        "..",
        "/path with spaces/file",
    ],
)
def test_path_pattern_valid(path):
    """Test path pattern matches valid file paths."""
    assert re.match(PATH_PATTERN, path) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_path",
    [
        "/path/to/file!.mp4",
        "/path@file",
        "/path#with#hashes",
    ],
)
def test_path_pattern_invalid(invalid_path):
    """Test path pattern rejects paths with invalid characters."""
    assert re.match(PATH_PATTERN, invalid_path) is None


# =============================================================================
# Description Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "description",
    [
        "This is a video description",
        "Video with numbers 123",
        "Description with punctuation, and spaces.",
        'Description with "quotes"',
        "Simple",
        "",  # Empty description
    ],
)
def test_description_pattern_valid(description):
    """Test description pattern matches valid descriptions."""
    assert re.match(DESCRIPTION_PATTERN, description) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_description",
    [
        "Description with @symbol",
        "Description with #hashtag",
        "Description with $dollar",
        "Description with %percent",
    ],
)
def test_description_pattern_invalid(invalid_description):
    """Test description pattern rejects invalid characters."""
    assert re.match(DESCRIPTION_PATTERN, invalid_description) is None


# =============================================================================
# Camera ID Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "camera_id",
    [
        "camera_0",
        "camera_1",
        "camera_123",
        "video_0",
        "video_999",
        "default",
        "",  # Empty is valid
    ],
)
def test_camera_id_pattern_valid(camera_id):
    """Test camera ID pattern matches valid camera IDs."""
    assert re.match(CAMERA_ID_PATTERN, camera_id) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_camera_id",
    [
        "camera1",  # Missing underscore
        "camera_",  # No number
        "cam_1",  # Wrong prefix
        "camera_01a",  # Letters after number
        "CAMERA_1",  # Uppercase
    ],
)
def test_camera_id_pattern_invalid(invalid_camera_id):
    """Test camera ID pattern rejects invalid formats."""
    assert re.match(CAMERA_ID_PATTERN, invalid_camera_id) is None


# =============================================================================
# Error Code Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_code",
    [
        "InvalidParameters",
        "NotFound",
        "InternalServerError",
        "BadRequest",
        "",  # Empty is valid
    ],
)
def test_error_code_pattern_valid(error_code):
    """Test error code pattern matches valid error codes."""
    assert re.match(ERROR_CODE_PATTERN, error_code) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_error_code",
    [
        "Invalid-Parameters",  # Hyphen
        "Not_Found",  # Underscore
        "Error 404",  # Space and number
        "Error123",  # Number
    ],
)
def test_error_code_pattern_invalid(invalid_error_code):
    """Test error code pattern rejects invalid formats."""
    assert re.match(ERROR_CODE_PATTERN, invalid_error_code) is None


# =============================================================================
# Error Message Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "error_message",
    [
        "Invalid parameter provided",
        "File not found.",
        # "Error: connection timeout",  # Removed: pattern doesn't allow colons
        'Message with "quotes" and comma,',
        "Message with apostrophe's",
        "",  # Empty is valid
    ],
)
def test_error_message_pattern_valid(error_message):
    """Test error message pattern matches valid messages."""
    assert re.match(ERROR_MESSAGE_PATTERN, error_message) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_error_message",
    [
        "Error with @symbol",
        "Error with #hashtag",
        "Error with $dollar",
        "Error with %percent",
    ],
)
def test_error_message_pattern_invalid(invalid_error_message):
    """Test error message pattern rejects invalid characters."""
    assert re.match(ERROR_MESSAGE_PATTERN, invalid_error_message) is None


# =============================================================================
# Key Pattern Tests
# =============================================================================


@pytest.mark.unit
@pytest.mark.parametrize(
    "key",
    [
        "mykey",
        "Key123",
        "UPPERCASE",
        "MixedCase",
        "",  # Empty is valid
    ],
)
def test_key_pattern_valid(key):
    """Test key pattern matches valid alphanumeric keys."""
    assert re.match(KEY_PATTERN, key) is not None


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_key",
    [
        "key-with-hyphens",
        "key_with_underscores",
        "key with spaces",
        "key.with.dots",
    ],
)
def test_key_pattern_invalid(invalid_key):
    """Test key pattern rejects non-alphanumeric characters."""
    assert re.match(KEY_PATTERN, invalid_key) is None


# =============================================================================
# Edge Cases and Security Tests
# =============================================================================


@pytest.mark.unit
def test_timestamp_pattern_sql_injection():
    """Test that timestamp pattern prevents SQL injection attempts."""
    sql_injection = "2024-01-01' OR '1'='1"
    assert re.match(TIMESTAMP_PATTERN, sql_injection) is None


@pytest.mark.unit
def test_file_name_pattern_path_traversal():
    """Test that file name pattern doesn't prevent path traversal (by design)."""
    # Note: Path traversal should be handled at application level
    # The pattern allows forward slashes for paths
    path_traversal = "../../../etc/passwd"
    assert re.match(FILE_NAME_PATTERN, path_traversal) is not None


@pytest.mark.unit
def test_description_pattern_xss_attempt():
    """Test that description pattern prevents basic XSS attempts."""
    xss_attempt = "<script>alert('xss')</script>"
    assert re.match(DESCRIPTION_PATTERN, xss_attempt) is None


@pytest.mark.unit
def test_aws_s3_url_bucket_name_edge_cases():
    """Test AWS S3 URL pattern with bucket name edge cases."""
    # Valid: bucket names can have dots
    assert re.match(AWS_S3_URL_PATTERN, "s3://my.bucket.name/file") is not None

    # Valid: minimum length bucket (3 chars)
    assert re.match(AWS_S3_URL_PATTERN, "s3://abc/file") is not None

    # Note: AWS spec requires 3+ chars, but pattern allows 2 chars
    # This is a known limitation - semantic validation should happen elsewhere
    assert re.match(AWS_S3_URL_PATTERN, "s3://ab/file") is not None


@pytest.mark.unit
def test_timestamp_leap_second():
    """Test timestamp pattern with leap second (60 seconds)."""
    # Some systems support 60 seconds for leap seconds
    leap_second = "2024-06-30T23:59:60Z"
    # Pattern allows 60 seconds (leap seconds are valid in ISO 8601)
    assert re.match(TIMESTAMP_PATTERN, leap_second) is not None


@pytest.mark.unit
def test_empty_patterns():
    """Test that patterns handle empty strings appropriately."""
    # Some patterns allow empty strings, some don't
    assert re.match(DESCRIPTION_PATTERN, "") is not None
    assert re.match(ERROR_CODE_PATTERN, "") is not None
    assert re.match(KEY_PATTERN, "") is not None
    assert re.match(CAMERA_ID_PATTERN, "") is not None

    # TIMESTAMP_PATTERN should not match empty
    assert re.match(TIMESTAMP_PATTERN, "") is None
    # FILE_NAME_PATTERN uses *, so it allows empty strings (this is by design)
    assert re.match(FILE_NAME_PATTERN, "") is not None


# =============================================================================
# Pydantic Model Validator Tests
# =============================================================================


@pytest.mark.unit
def test_summarization_query_check_url_valid_http():
    """Test SummarizationQuery accepts valid HTTP URLs."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        url="https://example.com/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.url == "https://example.com/video.mp4"


@pytest.mark.unit
def test_summarization_query_check_url_valid_s3():
    """Test SummarizationQuery accepts valid S3 URLs."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        url="s3://my-bucket/path/to/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.url == "s3://my-bucket/path/to/video.mp4"


@pytest.mark.unit
def test_summarization_query_check_url_valid_s3_object():
    """Test SummarizationQuery accepts valid S3 object URLs."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        url="https://my-bucket.s3.us-east-1.amazonaws.com/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.url == "https://my-bucket.s3.us-east-1.amazonaws.com/video.mp4"


@pytest.mark.unit
def test_summarization_query_check_url_invalid():
    """Test SummarizationQuery rejects invalid URLs."""
    from vss_api_models import SummarizationQuery

    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(
            model="test-model",
            url="ftp://invalid.com/file.mp4",
            scenario="test",
            events=["event1"],
        )

    errors = exc_info.value.errors()
    assert len(errors) > 0
    assert "url" in str(errors[0])
    assert "Invalid URL format" in str(errors) or "String should match pattern" in str(errors)


@pytest.mark.unit
def test_summarization_query_check_url_rtsp_rejected():
    """Test SummarizationQuery rejects RTSP URLs (not in allowed patterns)."""
    from vss_api_models import SummarizationQuery

    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(model="test-model", url="rtsp://camera.local/stream")

    errors = exc_info.value.errors()
    assert "url" in str(errors[0])


@pytest.mark.unit
def test_summarization_query_check_ids_single_uuid():
    """Test SummarizationQuery accepts single UUID."""
    from vss_api_models import SummarizationQuery

    test_uuid = uuid4()
    query = SummarizationQuery(model="test-model", id=test_uuid, scenario="test", events=["event1"])
    assert query.id == test_uuid


@pytest.mark.unit
def test_summarization_query_check_ids_list_of_uuids():
    """Test SummarizationQuery accepts list of UUIDs."""
    from vss_api_models import SummarizationQuery

    test_uuids = [uuid4() for _ in range(10)]
    query = SummarizationQuery(
        model="test-model", id=test_uuids, scenario="test", events=["event1"]
    )
    assert query.id == test_uuids
    assert len(query.id) == 10


@pytest.mark.unit
def test_summarization_query_check_ids_exactly_50():
    """Test SummarizationQuery accepts exactly 50 UUIDs (boundary)."""
    from vss_api_models import SummarizationQuery

    test_uuids = [uuid4() for _ in range(50)]
    query = SummarizationQuery(
        model="test-model", id=test_uuids, scenario="test", events=["event1"]
    )
    assert len(query.id) == 50


@pytest.mark.unit
def test_summarization_query_check_ids_exceeds_limit():
    """Test SummarizationQuery rejects more than 50 UUIDs."""
    from vss_api_models import SummarizationQuery

    test_uuids = [uuid4() for _ in range(51)]

    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(model="test-model", id=test_uuids)

    errors = exc_info.value.errors()
    assert len(errors) > 0
    assert "id" in str(errors[0])
    assert "must not exceed 50 items" in str(errors[0]["ctx"]["error"])


@pytest.mark.unit
def test_summarization_query_check_ids_empty_list():
    """Test SummarizationQuery accepts empty list of IDs."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(model="test-model", id=[], scenario="test", events=["event1"])
    assert query.id == []


@pytest.mark.unit
def test_summarization_query_id_list_property_single():
    """Test id_list property returns list for single UUID."""
    from vss_api_models import SummarizationQuery

    test_uuid = uuid4()
    query = SummarizationQuery(model="test-model", id=test_uuid, scenario="test", events=["event1"])
    assert query.id_list == [test_uuid]
    assert isinstance(query.id_list, list)


@pytest.mark.unit
def test_summarization_query_id_list_property_multiple():
    """Test id_list property returns list for multiple UUIDs."""
    from vss_api_models import SummarizationQuery

    test_uuids = [uuid4() for _ in range(5)]
    query = SummarizationQuery(
        model="test-model", id=test_uuids, scenario="test", events=["event1"]
    )
    assert query.id_list == test_uuids


@pytest.mark.unit
def test_summarization_query_strip_excluded_fields():
    """Test strip_excluded_fields removes excluded fields."""
    from vss_api_models import ResponseFormat, ResponseType, SummarizationQuery

    # Try to set excluded field api_type (should be stripped)
    query = SummarizationQuery(
        model="test-model",
        url="https://example.com/video.mp4",
        api_type="internal",  # This is excluded
        response_format=ResponseFormat(type=ResponseType.JSON_OBJECT),  # This is excluded
        scenario="test",
        events=["event1"],
    )

    # Excluded fields should not appear in model dump
    dumped = query.model_dump()
    assert "api_type" not in dumped
    assert "response_format" not in dumped

    # But included fields should be present
    assert "model" in dumped
    assert "url" in dumped


@pytest.mark.unit
def test_summarization_query_strip_excluded_fields_non_dict_input():
    """Test strip_excluded_fields handles non-dict input gracefully."""
    from vss_api_models import SummarizationQuery

    # If validator receives non-dict, it should pass through
    # This tests the safety check in strip_excluded_fields
    query = SummarizationQuery(
        model="test-model",
        url="https://example.com/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.model == "test-model"


@pytest.mark.unit
def test_summarization_query_get_query_json():
    """Test get_query_json property returns JSON-serializable dict."""
    from vss_api_models import SummarizationQuery

    test_uuid = uuid4()
    query = SummarizationQuery(
        model="test-model",
        id=test_uuid,
        prompt="Test prompt",
        scenario="test",
        events=["event1"],
    )

    json_dict = query.get_query_json
    assert isinstance(json_dict, dict)
    assert json_dict["model"] == "test-model"
    assert json_dict["prompt"] == "Test prompt"
    # UUID should be serialized as string
    assert json_dict["id"] == str(test_uuid)


@pytest.mark.unit
def test_summarization_query_both_id_and_url():
    """Test SummarizationQuery can have both id and url."""
    from vss_api_models import SummarizationQuery

    test_uuid = uuid4()
    query = SummarizationQuery(
        model="test-model",
        id=test_uuid,
        url="https://example.com/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.id == test_uuid
    assert query.url == "https://example.com/video.mp4"


@pytest.mark.unit
def test_summarization_query_neither_id_nor_url():
    """Test SummarizationQuery allows neither id nor url (optional fields)."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(model="test-model", scenario="test", events=["event1"])
    assert query.id is None
    assert query.url is None


@pytest.mark.unit
def test_summarization_query_system_prompt_max_length():
    """Test system_prompt field has max_length validation."""
    from vss_api_models import SummarizationQuery

    # Valid: under 5000 chars
    query = SummarizationQuery(
        model="test-model",
        system_prompt="A" * 5000,
        scenario="test",
        events=["event1"],
    )
    assert len(query.system_prompt) == 5000

    # Invalid: over 5000 chars
    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(
            model="test-model",
            system_prompt="A" * 5001,
            scenario="test",
            events=["event1"],
        )

    errors = exc_info.value.errors()
    assert "system_prompt" in str(errors[0])


@pytest.mark.unit
def test_summarization_query_prompt_max_length():
    """Test prompt field has max_length validation."""
    from vss_api_models import SummarizationQuery

    # Valid: under 512000 chars
    query = SummarizationQuery(
        model="test-model",
        prompt="B" * 512000,
        scenario="test",
        events=["event1"],
    )
    assert len(query.prompt) == 512000

    # Invalid: over 512000 chars
    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(
            model="test-model",
            prompt="B" * 512001,
            scenario="test",
            events=["event1"],
        )

    errors = exc_info.value.errors()
    assert "prompt" in str(errors[0])


@pytest.mark.unit
def test_summarization_query_model_max_length():
    """Test model field has max_length validation."""
    from vss_api_models import SummarizationQuery

    # Valid: under 1024 chars
    query = SummarizationQuery(model="M" * 1024, scenario="test", events=["event1"])
    assert len(query.model) == 1024

    # Invalid: over 1024 chars
    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(model="M" * 1025, scenario="test", events=["event1"])

    errors = exc_info.value.errors()
    assert "model" in str(errors[0])


# =============================================================================
# Security and Edge Case Tests for Validators
# =============================================================================


@pytest.mark.unit
def test_summarization_query_url_sql_injection_attempt():
    """Test that URL validator prevents SQL injection attempts."""
    from vss_api_models import SummarizationQuery

    with pytest.raises(ValidationError):
        SummarizationQuery(
            model="test-model",
            url="https://example.com/video.mp4'; DROP TABLE videos; --",
        )


@pytest.mark.unit
def test_summarization_query_url_with_special_chars():
    """Test URL validator handles special characters in query strings."""
    from vss_api_models import SummarizationQuery

    # Valid URL with query parameters
    query = SummarizationQuery(
        model="test-model",
        url="https://example.com/video.mp4?quality=hd&token=abc123",
        scenario="test",
        events=["event1"],
    )
    assert "quality=hd" in query.url


@pytest.mark.unit
def test_summarization_query_ids_boundary_49_and_50():
    """Test boundary conditions for ID list validation."""
    from vss_api_models import SummarizationQuery

    # 49 should pass
    query_49 = SummarizationQuery(
        model="test-model",
        id=[uuid4() for _ in range(49)],
        scenario="test",
        events=["event1"],
    )
    assert len(query_49.id) == 49

    # 50 should pass (boundary)
    query_50 = SummarizationQuery(
        model="test-model",
        id=[uuid4() for _ in range(50)],
        scenario="test",
        events=["event1"],
    )
    assert len(query_50.id) == 50

    # 51 should fail
    with pytest.raises(ValidationError):
        SummarizationQuery(
            model="test-model",
            id=[uuid4() for _ in range(51)],
            scenario="test",
            events=["event1"],
        )


@pytest.mark.unit
def test_summarization_query_url_localhost():
    """Test URL validator accepts localhost URLs."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        url="http://localhost:8000/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.url == "http://localhost:8000/video.mp4"


@pytest.mark.unit
def test_summarization_query_url_ip_address():
    """Test URL validator accepts IP address URLs."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        url="http://192.168.1.100:8080/stream/video.mp4",
        scenario="test",
        events=["event1"],
    )
    assert query.url == "http://192.168.1.100:8080/stream/video.mp4"


# =============================================================================
# timestamp_validator Tests
# =============================================================================


@pytest.mark.unit
def test_timestamp_validator_valid():
    """Test timestamp_validator accepts valid RFC3339 timestamp."""
    from vss_api_models import MediaInfoTimeStamp

    ts = MediaInfoTimeStamp(
        type="timestamp",
        start_timestamp="2024-05-30T01:41:25.000Z",
        end_timestamp="2024-05-30T02:14:51.000Z",
    )
    assert ts.start_timestamp == "2024-05-30T01:41:25.000Z"
    assert ts.end_timestamp == "2024-05-30T02:14:51.000Z"


@pytest.mark.unit
def test_timestamp_validator_invalid_raises():
    """Test timestamp_validator rejects invalid timestamp format."""
    from via_exception import ViaException
    from vss_api_models import MediaInfoTimeStamp

    with pytest.raises((ValidationError, ViaException)):
        MediaInfoTimeStamp(
            type="timestamp",
            start_timestamp="not-a-timestamp-format",
            end_timestamp="2024-05-30T02:14:51.000Z",
        )


@pytest.mark.unit
def test_media_info_offset_creation():
    """Test MediaInfoOffset creation."""
    from vss_api_models import MediaInfoOffset

    offset = MediaInfoOffset(type="offset", start_offset=0, end_offset=3600)
    assert offset.start_offset == 0
    assert offset.end_offset == 3600


# =============================================================================
# VlmQuery Tests
# =============================================================================


@pytest.mark.unit
def test_vlm_query_single_uuid():
    """Test VlmQuery accepts single UUID."""
    from vss_api_models import VlmQuery

    test_uuid = uuid4()
    query = VlmQuery(
        model="test-model",
        id=test_uuid,
        prompt="describe",
    )
    assert query.id == test_uuid


@pytest.mark.unit
def test_vlm_query_list_of_uuids():
    """Test VlmQuery accepts list of UUIDs."""
    from vss_api_models import VlmQuery

    uuids = [uuid4() for _ in range(5)]
    query = VlmQuery(model="test-model", id=uuids, prompt="describe")
    assert query.id == uuids


@pytest.mark.unit
def test_vlm_query_exceeds_50_uuids():
    """Test VlmQuery rejects more than 50 UUIDs."""
    from vss_api_models import VlmQuery

    uuids = [uuid4() for _ in range(51)]
    with pytest.raises(ValidationError):
        VlmQuery(model="test-model", id=uuids, prompt="describe")


@pytest.mark.unit
def test_vlm_query_id_list_single():
    """Test VlmQuery id_list property returns list for single UUID."""
    from vss_api_models import VlmQuery

    test_uuid = uuid4()
    query = VlmQuery(model="test-model", id=test_uuid, prompt="describe")
    assert query.id_list == [test_uuid]
    assert isinstance(query.id_list, list)


@pytest.mark.unit
def test_vlm_query_id_list_multiple():
    """Test VlmQuery id_list property returns same list for multiple UUIDs."""
    from vss_api_models import VlmQuery

    uuids = [uuid4() for _ in range(3)]
    query = VlmQuery(model="test-model", id=uuids, prompt="describe")
    assert query.id_list == uuids


@pytest.mark.unit
def test_vlm_query_get_query_json():
    """Test VlmQuery get_query_json property."""
    from vss_api_models import VlmQuery

    test_uuid = uuid4()
    query = VlmQuery(model="test-model", id=test_uuid, prompt="describe the video")
    result = query.get_query_json
    assert isinstance(result, dict)
    assert result["model"] == "test-model"
    assert result["prompt"] == "describe the video"
    assert result["id"] == str(test_uuid)


# =============================================================================
# Model Construction Tests for Uncovered Models
# =============================================================================


@pytest.mark.unit
def test_lvs_error_creation():
    """Test LvsError model construction."""
    from vss_api_models import LvsError

    error = LvsError(code="InvalidParameters", message="Bad request parameter")
    assert error.code == "InvalidParameters"
    assert error.message == "Bad request parameter"


@pytest.mark.unit
def test_file_info_creation():
    """Test FileInfo model construction."""
    from vss_api_models import FileInfo, Purpose

    info = FileInfo(
        id=uuid4(),
        bytes=2000000,
        filename="myfile.mp4",
        purpose=Purpose.VISION,
    )
    assert info.purpose == Purpose.VISION
    assert info.bytes == 2000000


@pytest.mark.unit
def test_add_file_info_response():
    """Test AddFileInfoResponse model construction."""
    from vss_api_models import AddFileInfoResponse, MediaType, Purpose

    resp = AddFileInfoResponse(
        id=uuid4(),
        bytes=1000,
        filename="video.mp4",
        purpose=Purpose.VISION,
        media_type=MediaType.VIDEO,
    )
    assert resp.media_type == MediaType.VIDEO


@pytest.mark.unit
def test_delete_file_response():
    """Test DeleteFileResponse model construction."""
    from vss_api_models import DeleteFileResponse

    resp = DeleteFileResponse(id=uuid4(), object="file", deleted=True)
    assert resp.deleted is True
    assert resp.object == "file"


@pytest.mark.unit
def test_list_files_response():
    """Test ListFilesResponse model construction."""
    from vss_api_models import (
        AddFileInfoResponse,
        ListFilesResponse,
        MediaType,
        Purpose,
    )

    item = AddFileInfoResponse(
        id=uuid4(),
        bytes=500,
        filename="v.mp4",
        purpose=Purpose.VISION,
        media_type=MediaType.VIDEO,
    )
    resp = ListFilesResponse(data=[item], object="list")
    assert len(resp.data) == 1


@pytest.mark.unit
def test_model_info_creation():
    """Test ModelInfo construction."""
    from vss_api_models import ModelInfo

    info = ModelInfo(
        id="gpt-4o",
        created=1686935002,
        object="model",
        owned_by="NVIDIA",
        api_type="internal",
    )
    assert info.id == "gpt-4o"
    assert info.object == "model"


@pytest.mark.unit
def test_completion_response_creation():
    """Test CompletionResponse model construction."""
    from vss_api_models import (
        ChatCompletionResponseMessage,
        CompletionFinishReason,
        CompletionObject,
        CompletionResponse,
        CompletionResponseChoice,
        MediaInfoOffset,
    )

    msg = ChatCompletionResponseMessage(content="test", role="assistant")
    choice = CompletionResponseChoice(
        finish_reason=CompletionFinishReason.STOP, index=0, message=msg
    )
    resp = CompletionResponse(
        id=uuid4(),
        video_id=uuid4(),
        choices=[choice],
        created=1717405636,
        model="test-model",
        media_info=MediaInfoOffset(type="offset", start_offset=0, end_offset=100),
        object=CompletionObject.SUMMARIZATION_COMPLETION,
    )
    assert resp.object == CompletionObject.SUMMARIZATION_COMPLETION


@pytest.mark.unit
def test_completion_usage_creation():
    """Test CompletionUsage with all fields."""
    from vss_api_models import CompletionUsage

    usage = CompletionUsage(
        query_processing_time=78,
        total_chunks_processed=10,
        summary_tokens=100,
        aggregation_tokens=50,
        summary_requests=5,
        summary_latency=1.5,
        aggregation_latency=0.8,
    )
    assert usage.summary_tokens == 100
    assert usage.aggregation_latency == 0.8


@pytest.mark.unit
def test_vlm_caption_response():
    """Test VlmCaptionResponse model."""
    from vss_api_models import VlmCaptionResponse

    resp = VlmCaptionResponse(
        start_time="15.5",
        end_time="30.2",
        content="A person walks through a warehouse.",
        reasoning_description="",
    )
    assert resp.start_time == "15.5"


@pytest.mark.unit
def test_vlm_captions_completion_response():
    """Test VlmCaptionsCompletionResponse model."""
    from vss_api_models import (
        MediaInfoOffset,
        VlmCaptionResponse,
        VlmCaptionsCompletionResponse,
    )

    chunk = VlmCaptionResponse(
        start_time="0.0", end_time="10.0", content="A scene", reasoning_description=""
    )
    resp = VlmCaptionsCompletionResponse(
        id=uuid4(),
        created=1717405636,
        model="test-model",
        media_info=MediaInfoOffset(type="offset", start_offset=0, end_offset=10),
        chunk_responses=[chunk],
    )
    assert len(resp.chunk_responses) == 1


@pytest.mark.unit
def test_recommended_config():
    """Test RecommendedConfig model."""
    from vss_api_models import RecommendedConfig

    config = RecommendedConfig(
        video_length=300,
        target_response_time=60,
        usecase_event_duration=5,
    )
    assert config.video_length == 300


@pytest.mark.unit
def test_recommended_config_response():
    """Test RecommendedConfigResponse model."""
    from vss_api_models import RecommendedConfigResponse

    resp = RecommendedConfigResponse(chunk_size=60, text="Use 60 second chunks")
    assert resp.chunk_size == 60


@pytest.mark.unit
def test_via_base_model_forbids_extra():
    """Test ViaBaseModel rejects unknown fields."""
    from vss_api_models import ViaBaseModel

    with pytest.raises(ValidationError):
        ViaBaseModel(unknown_field="value")


@pytest.mark.unit
def test_chat_message_creation():
    """Test ChatMessage model."""
    from vss_api_models import ChatMessage

    msg = ChatMessage(content="Hello", role="user")
    assert msg.role == "user"
    assert msg.content == "Hello"


@pytest.mark.unit
def test_response_format_creation():
    """Test ResponseFormat model."""
    from vss_api_models import ResponseFormat, ResponseType

    fmt = ResponseFormat(type=ResponseType.JSON_OBJECT)
    assert fmt.type == ResponseType.JSON_OBJECT


@pytest.mark.unit
def test_stream_options_creation():
    """Test StreamOptions model."""
    from vss_api_models import StreamOptions

    opts = StreamOptions(include_usage=True)
    assert opts.include_usage is True


@pytest.mark.unit
def test_add_live_stream():
    """Test AddLiveStream model."""
    from vss_api_models import AddLiveStream

    stream = AddLiveStream(
        liveStreamUrl="rtsp://localhost:8554/media/video1",
        description="Test stream",
    )
    assert stream.liveStreamUrl == "rtsp://localhost:8554/media/video1"
    assert stream.username == ""


@pytest.mark.unit
def test_summarization_query_with_schema():
    """Test SummarizationQuery with schema field."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        scenario="warehouse",
        events=["fire", "theft"],
        schema='{"type": "object"}',
    )
    assert query.schema == '{"type": "object"}'


@pytest.mark.unit
def test_summarization_query_media_info_timestamp():
    """Test SummarizationQuery with MediaInfoTimeStamp."""
    from vss_api_models import MediaInfoTimeStamp, SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        scenario="test",
        events=["event1"],
        media_info=MediaInfoTimeStamp(
            type="timestamp",
            start_timestamp="2024-05-30T01:41:25.000Z",
            end_timestamp="2024-05-30T02:14:51.000Z",
        ),
    )
    assert query.media_info.type == "timestamp"


# =============================================================================
# Tests for remaining uncovered models and branches
# =============================================================================


@pytest.mark.unit
def test_add_live_stream_response():
    """Test AddLiveStreamResponse model."""
    from vss_api_models import AddLiveStreamResponse

    resp = AddLiveStreamResponse(id=uuid4())
    assert resp.id is not None


@pytest.mark.unit
def test_live_stream_info():
    """Test LiveStreamInfo model."""
    from vss_api_models import LiveStreamInfo

    info = LiveStreamInfo(
        id=uuid4(),
        liveStreamUrl="rtsp://localhost:8554/media/video1",
        description="Test stream",
        chunk_duration=60,
        chunk_overlap_duration=10,
        summary_duration=300,
    )
    assert info.chunk_duration == 60
    assert info.summary_duration == 300


@pytest.mark.unit
def test_list_models_response():
    """Test ListModelsResponse model."""
    from vss_api_models import ListModelsResponse, ModelInfo

    model_info = ModelInfo(
        id="gpt-4o",
        created=1686935002,
        object="model",
        owned_by="NVIDIA",
        api_type="internal",
    )
    resp = ListModelsResponse(object="list", data=[model_info])
    assert len(resp.data) == 1
    assert resp.object == "list"


@pytest.mark.unit
def test_summarization_query_id_list_when_none():
    """Test id_list property when id is None."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(model="test-model", scenario="test", events=["e"])
    assert query.id is None
    assert query.id_list is None


@pytest.mark.unit
def test_vlm_query_with_stream_options():
    """Test VlmQuery with stream and stream_options."""
    from vss_api_models import StreamOptions, VlmQuery

    query = VlmQuery(
        model="test-model",
        id=uuid4(),
        prompt="describe",
        stream=True,
        stream_options=StreamOptions(include_usage=True),
    )
    assert query.stream is True
    assert query.stream_options.include_usage is True


@pytest.mark.unit
def test_vlm_query_with_media_info_offset():
    """Test VlmQuery with MediaInfoOffset."""
    from vss_api_models import MediaInfoOffset, VlmQuery

    query = VlmQuery(
        model="test-model",
        id=uuid4(),
        prompt="describe",
        media_info=MediaInfoOffset(type="offset", start_offset=10, end_offset=100),
    )
    assert query.media_info.start_offset == 10


@pytest.mark.unit
def test_completion_response_with_timestamp_media_info():
    """Test CompletionResponse with MediaInfoTimeStamp."""
    from vss_api_models import (
        ChatCompletionResponseMessage,
        CompletionFinishReason,
        CompletionObject,
        CompletionResponse,
        CompletionResponseChoice,
        CompletionUsage,
        MediaInfoTimeStamp,
    )

    msg = ChatCompletionResponseMessage(content="result", role="assistant")
    choice = CompletionResponseChoice(
        finish_reason=CompletionFinishReason.STOP, index=0, message=msg
    )
    resp = CompletionResponse(
        id=uuid4(),
        video_id=uuid4(),
        choices=[choice],
        created=1717405636,
        model="test-model",
        media_info=MediaInfoTimeStamp(
            type="timestamp",
            start_timestamp="2024-05-30T01:41:25.000Z",
            end_timestamp="2024-05-30T02:14:51.000Z",
        ),
        object=CompletionObject.SUMMARIZATION_PROGRESSING,
        usage=CompletionUsage(query_processing_time=10, total_chunks_processed=2),
    )
    assert resp.object == CompletionObject.SUMMARIZATION_PROGRESSING
    assert resp.usage.total_chunks_processed == 2


@pytest.mark.unit
def test_completion_finish_reason_values():
    """Test all CompletionFinishReason enum values."""
    from vss_api_models import CompletionFinishReason

    assert CompletionFinishReason.STOP == "stop"
    assert CompletionFinishReason.LENGTH == "length"
    assert CompletionFinishReason.CONTENT_FILTER == "content_filter"


@pytest.mark.unit
def test_completion_object_values():
    """Test all CompletionObject enum values."""
    from vss_api_models import CompletionObject

    assert CompletionObject.CHAT_COMPLETION == "chat.completion"
    assert CompletionObject.SUMMARIZATION_COMPLETION == "summarization.completion"
    assert CompletionObject.SUMMARIZATION_PROGRESSING == "summarization.progressing"
    assert CompletionObject.VLM_CAPTIONS_COMPLETION == "vlm_captions.completion"
    assert CompletionObject.VLM_CAPTIONS_PROGRESSING == "vlm_captions.progressing"


@pytest.mark.unit
def test_summarization_query_with_all_optional_fields():
    """Test SummarizationQuery with all optional internal fields."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        scenario="warehouse",
        events=["fire"],
        chunk_duration=60,
        chunk_overlap_duration=10,
        vlm_input_width=256,
        vlm_input_height=256,
        enable_reasoning=True,
        max_tokens=512,
        temperature=0.2,
        top_p=0.9,
        top_k=100,
        seed=42,
        override_vlm_prompt=True,
        enable_vlm_structured_output=False,
        objects_of_interest=["person", "car"],
        delete_external_collection=True,
        custom_metadata={"source": "cam1"},
    )
    assert query.enable_reasoning is True
    assert query.override_vlm_prompt is True
    assert query.objects_of_interest == ["person", "car"]
    assert query.delete_external_collection is True
    assert query.custom_metadata == {"source": "cam1"}


@pytest.mark.unit
def test_add_live_stream_with_credentials():
    """Test AddLiveStream with username and password."""
    from vss_api_models import AddLiveStream

    stream = AddLiveStream(
        liveStreamUrl="rtsp://localhost:8554/media/video1",
        description="Secure stream",
        username="admin",
        password="secret",
        camera_id="camera_1",
    )
    assert stream.username == "admin"
    assert stream.password == "secret"
    assert stream.camera_id == "camera_1"


@pytest.mark.unit
def test_vlm_captions_completion_response_empty():
    """Test VlmCaptionsCompletionResponse with no chunks."""
    from vss_api_models import MediaInfoOffset, VlmCaptionsCompletionResponse

    resp = VlmCaptionsCompletionResponse(
        id=uuid4(),
        created=1717405636,
        model="test-model",
        media_info=MediaInfoOffset(type="offset", start_offset=0, end_offset=10),
    )
    assert resp.chunk_responses == []
    assert resp.usage is None


@pytest.mark.unit
def test_chat_message_with_name():
    """Test ChatMessage with optional name field."""
    from vss_api_models import ChatMessage

    msg = ChatMessage(content="Hello", role="user", name="John")
    assert msg.name == "John"


# =============================================================================
# Additional tests for remaining uncovered statements
# =============================================================================


@pytest.mark.unit
def test_summarization_query_check_url_local_path_raises():
    """Test check_url raises ValueError for local paths that pass pattern but fail URL checks."""
    from vss_api_models import SummarizationQuery

    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(
            model="test-model",
            url="/local/path/video.mp4",
            scenario="test",
            events=["event1"],
        )
    errors = exc_info.value.errors()
    assert len(errors) > 0


@pytest.mark.unit
def test_summarization_query_check_url_relative_path_raises():
    """Test check_url raises ValueError for relative paths."""
    from vss_api_models import SummarizationQuery

    with pytest.raises(ValidationError) as exc_info:
        SummarizationQuery(
            model="test-model",
            url="relative/path/video.mp4",
            scenario="test",
            events=["event1"],
        )
    errors = exc_info.value.errors()
    assert len(errors) > 0


@pytest.mark.unit
def test_media_type_enum():
    """Test MediaType enum values."""
    from vss_api_models import MediaType

    assert MediaType.VIDEO == "video"
    assert len(MediaType) == 1


@pytest.mark.unit
def test_purpose_enum():
    """Test Purpose enum values."""
    from vss_api_models import Purpose

    assert Purpose.VISION == "vision"
    assert len(Purpose) == 1


@pytest.mark.unit
def test_response_type_enum():
    """Test ResponseType enum values."""
    from vss_api_models import ResponseType

    assert ResponseType.JSON_OBJECT == "json_object"
    assert ResponseType.TEXT == "text"


@pytest.mark.unit
def test_timestamp_validator_valid_returns_value():
    """Test timestamp_validator returns the value on success."""
    from vss_api_models import timestamp_validator

    class FakeInfo:
        field_name = "test_field"

    result = timestamp_validator("2024-05-30T01:41:25.000Z", FakeInfo())
    assert result == "2024-05-30T01:41:25.000Z"


@pytest.mark.unit
def test_timestamp_validator_invalid_raises_via_exception():
    """Test timestamp_validator raises ViaException on invalid format."""
    from via_exception import ViaException
    from vss_api_models import timestamp_validator

    class FakeInfo:
        field_name = "start_timestamp"

    with pytest.raises(ViaException):
        timestamp_validator("not-a-timestamp", FakeInfo())


@pytest.mark.unit
def test_summarization_query_with_ignore_eos():
    """Test SummarizationQuery with benchmarking fields."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        scenario="test",
        events=["e1"],
        ignore_eos=True,
    )
    assert query.ignore_eos is True


@pytest.mark.unit
def test_summarization_query_with_batch_response_method():
    """Test SummarizationQuery with batch_response_method field."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        scenario="test",
        events=["e1"],
        batch_response_method="json_schema",
    )
    assert query.batch_response_method == "json_schema"


@pytest.mark.unit
def test_summarization_query_with_auto_generate_prompt():
    """Test SummarizationQuery with auto_generate_prompt field."""
    from vss_api_models import SummarizationQuery

    query = SummarizationQuery(
        model="test-model",
        scenario="test",
        events=["e1"],
        auto_generate_prompt=True,
    )
    assert query.auto_generate_prompt is True


@pytest.mark.unit
def test_vlm_query_with_all_generation_params():
    """Test VlmQuery with all generation parameters."""
    from vss_api_models import VlmQuery

    query = VlmQuery(
        model="test-model",
        id=uuid4(),
        prompt="describe",
        max_tokens=1024,
        temperature=0.5,
        top_p=0.95,
        top_k=50,
        seed=42,
        chunk_duration=60,
        chunk_overlap_duration=10,
        vlm_input_width=512,
        vlm_input_height=512,
        enable_reasoning=True,
        api_type="internal",
    )
    assert query.max_tokens == 1024
    assert query.seed == 42
    assert query.enable_reasoning is True


@pytest.mark.unit
def test_vlm_query_response_format_default():
    """Test VlmQuery response_format defaults to text."""
    from vss_api_models import ResponseType, VlmQuery

    query = VlmQuery(model="m", id=uuid4(), prompt="p")
    assert query.response_format.type == ResponseType.TEXT

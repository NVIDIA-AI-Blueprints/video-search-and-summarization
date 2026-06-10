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
Shared utilities for unit tests (API tests) across all VST microservices.

Provides common HTTP request helpers, response validation, and context management.
"""
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: int = 30


@dataclass
class UnitTestContext:
    """Context object to store state between BDD steps."""

    response: Optional[requests.Response] = None
    response_json: Any = None
    status_code: int = 0
    streams: List[Dict[str, Any]] = field(default_factory=list)
    sensor_list: List[Dict[str, Any]] = field(default_factory=list)
    first_sensor_id: Optional[str] = None
    first_stream_id: Optional[str] = None
    error: Optional[str] = None


def api_get(
    base_url: str,
    path: str,
    verify_ssl: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    """
    Perform a GET request against the API.

    Args:
        base_url: API base URL (e.g. http://host:30888)
        path: API path (e.g. /vst/api/v1/sensor/list)
        verify_ssl: Whether to verify SSL certificates
        timeout: Request timeout in seconds
        params: Optional query parameters
        headers: Optional request headers

    Returns:
        requests.Response object
    """
    url = f"{base_url}{path}"
    logger.info("GET %s", url)
    response = requests.get(
        url,
        timeout=timeout,
        verify=verify_ssl,
        params=params,
        headers=headers,
    )
    logger.info("Response: %d (%d bytes)", response.status_code, len(response.content))
    return response


def api_delete(
    base_url: str,
    path: str,
    verify_ssl: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    params: Optional[Dict[str, Any]] = None,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    """
    Perform a DELETE request against the API.

    Args:
        base_url: API base URL (e.g. http://host:30888)
        path: API path (e.g. /vst/api/v1/sensor/{sensorId})
        verify_ssl: Whether to verify SSL certificates
        timeout: Request timeout in seconds
        params: Optional query parameters
        headers: Optional request headers

    Returns:
        requests.Response object
    """
    url = f"{base_url}{path}"
    logger.info("DELETE %s", url)
    response = requests.delete(
        url,
        timeout=timeout,
        verify=verify_ssl,
        params=params,
        headers=headers,
    )
    logger.info("Response: %d (%d bytes)", response.status_code, len(response.content))
    return response


def api_post(
    base_url: str,
    path: str,
    json_body: Optional[Dict[str, Any]] = None,
    verify_ssl: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    headers: Optional[Dict[str, str]] = None,
) -> requests.Response:
    """
    Perform a POST request against the API.

    Args:
        base_url: API base URL
        path: API path
        json_body: Optional JSON body
        verify_ssl: Whether to verify SSL certificates
        timeout: Request timeout in seconds
        headers: Optional request headers

    Returns:
        requests.Response object
    """
    url = f"{base_url}{path}"
    logger.info("POST %s", url)
    response = requests.post(
        url,
        json=json_body,
        timeout=timeout,
        verify=verify_ssl,
        headers=headers,
    )
    logger.info("Response: %d (%d bytes)", response.status_code, len(response.content))
    return response


def _truncated_body(response: requests.Response, max_len: int = 2000) -> str:
    """Return the response body truncated to *max_len* characters."""
    text = response.text
    if len(text) > max_len:
        return text[:max_len] + f"... (truncated, total {len(text)} chars)"
    return text


def validate_json_response(response: requests.Response) -> Any:
    """
    Validate that a response contains valid JSON and return it.

    Args:
        response: requests.Response object

    Returns:
        Parsed JSON data

    Raises:
        AssertionError: If response is not valid JSON
    """
    assert response.status_code == 200, (
        f"Expected status 200, got {response.status_code}\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    content_type = response.headers.get("Content-Type", "")
    assert "application/json" in content_type or "text/plain" in content_type, (
        f"Expected JSON content type, got: {content_type}\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    return response.json()


def validate_list_response(response: requests.Response) -> List[Any]:
    """Validate response is a JSON array and return it."""
    data = validate_json_response(response)
    assert isinstance(data, list), (
        f"Expected a JSON array, got {type(data).__name__}\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    return data


def validate_dict_response(response: requests.Response) -> Dict[str, Any]:
    """Validate response is a JSON object and return it."""
    data = validate_json_response(response)
    assert isinstance(data, dict), (
        f"Expected a JSON object, got {type(data).__name__}\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    return data


def validate_string_response(response: requests.Response) -> str:
    """Validate response is a JSON string (version endpoint) and return it.

    Some services return the version as a plain JSON string (e.g. ``"1.0.0"``),
    while others wrap it in an object (e.g. ``{"version": "1.0.0"}``).
    Both forms are accepted.
    """
    assert response.status_code == 200, (
        f"Expected status 200, got {response.status_code}\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    data = response.json()
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        for key in ("version", "Version"):
            if key in data:
                return str(data[key])
        return str(data)
    raise AssertionError(
        f"Expected a string or dict with version info, got {type(data).__name__}\n"
        f"Response body:\n{_truncated_body(response)}"
    )


def validate_help_response(response: requests.Response) -> List[Any]:
    """Validate a help endpoint response and return the list.

    Help endpoints may return either:
    - A list of strings (e.g. ``["/api/v1/sensor/list", ...]``)
    - A list of objects (e.g. ``[{"endpoint": "...", "method": "..."}, ...]``)

    Both forms are accepted.
    """
    data = validate_list_response(response)
    assert len(data) > 0, (
        f"Help API returned empty list\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    first = data[0]
    assert isinstance(first, (str, dict)), (
        f"Help list items must be strings or objects, got {type(first).__name__}\n"
        f"Response body:\n{_truncated_body(response)}"
    )
    return data


def extract_stream_ids(streams_response: Any) -> List[str]:
    """
    Extract stream IDs from the streams API response.

    The streams response is typically a list of objects where each object
    has sensor names as keys and arrays of stream info as values.

    Args:
        streams_response: Parsed JSON from the streams endpoint

    Returns:
        List of stream IDs
    """
    stream_ids: List[str] = []
    if isinstance(streams_response, list):
        for item in streams_response:
            if isinstance(item, dict):
                for _sensor_name, streams in item.items():
                    if isinstance(streams, list):
                        for stream in streams:
                            if isinstance(stream, dict) and "streamId" in stream:
                                stream_ids.append(stream["streamId"])
                    elif isinstance(streams, dict) and "streamId" in streams:
                        stream_ids.append(streams["streamId"])
    elif isinstance(streams_response, dict):
        for _key, streams in streams_response.items():
            if isinstance(streams, list):
                for stream in streams:
                    if isinstance(stream, dict) and "streamId" in stream:
                        stream_ids.append(stream["streamId"])
    return stream_ids


def extract_sensor_ids(sensor_list: List[Dict[str, Any]]) -> List[str]:
    """
    Extract sensor IDs from the sensor list response.

    Args:
        sensor_list: Parsed JSON from /v1/sensor/list

    Returns:
        List of sensor IDs
    """
    return [
        s["sensorId"]
        for s in sensor_list
        if isinstance(s, dict) and "sensorId" in s
    ]

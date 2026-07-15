#!/usr/bin/env python3
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
VIA Engine API Endpoint Test Suite (Pytest-based)

Professional pytest-based API testing framework for CI/CD integration.
Tests all VIA Engine REST API endpoints with comprehensive validation.

Usage with pytest:
    # Run all tests
    pytest tests/functional/test_api_endpoints.py -v

    # Run specific category
    pytest tests/functional/test_api_endpoints.py -m health -v
    pytest tests/functional/test_api_endpoints.py -m models -v
    pytest tests/functional/test_api_endpoints.py -m summarization -v

    # Run with custom base URL
    pytest tests/functional/test_api_endpoints.py --base-url http://localhost:38111

    # Generate JUnit XML
    pytest tests/functional/test_api_endpoints.py --junitxml=/tmp/results/junit.xml

Pytest Markers:
    @pytest.mark.health              - Health check endpoints
    @pytest.mark.metrics             - Metrics endpoint
    @pytest.mark.models              - Models API
    @pytest.mark.summarization       - Video summarization API
    @pytest.mark.recommended_config  - Recommended config API
    @pytest.mark.error_handling      - Error validation
    @pytest.mark.test_in_ci          - Run in CI pipeline (all tests have this)

CI note: If tests fail with "Connection reset by peer" and no response body, the service
may not be HTTP-ready yet. The pipeline should wait for HTTP /v1/ready before running tests.
"""

import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, Optional

import pytest
import requests

# Configure logging for pytest - this WILL be captured in JUnit XML
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# ============================================================================
# Configuration and Fixtures (see conftest.py for command-line options)
# ============================================================================


_ARTIFACT_VIDEO_URL = (
    "https://artifactory.nvidia.com/artifactory/"
    "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
)

SUMMARIZATION_PAYLOAD = {
    "url": _ARTIFACT_VIDEO_URL,
    "model": "nvidia/cosmos-reason2-8b",
    "events": ["accident", "emergency vehicle"],
    "scenario": "traffic monitoring",
    "chunk_duration": 10,
    "num_frames_per_second_or_fixed_frames_chunk": 5,
    "use_fps_for_chunking": False,
    "max_tokens": 1024,
}

# ============================================================================
# Helper Functions
# ============================================================================


def generate_curl_command(method: str, url: str, json_data: Optional[Dict] = None) -> str:
    """Generate equivalent curl command for reproduction."""
    curl_parts = ["curl"]

    if method.upper() != "GET":
        curl_parts.append(f"-X {method.upper()}")

    curl_parts.append(f"'{url}'")

    # Add headers
    curl_parts.append("-H 'Content-Type: application/json'")

    # Add JSON data
    if json_data:
        json_str = json.dumps(json_data, separators=(",", ":"))
        curl_parts.append(f"-d '{json_str}'")

    return " \\\n  ".join(curl_parts)


def log_request(method: str, url: str, json_data: Optional[Dict] = None):
    """Log request details with curl command."""
    logger.info("=" * 80)
    logger.info(f"REQUEST: {method.upper()} {url}")
    logger.info("-" * 80)

    if json_data:
        logger.info("JSON Payload:")
        logger.info(json.dumps(json_data, indent=2))

    # Generate and log curl command
    curl_cmd = generate_curl_command(method, url, json_data)
    logger.info("Equivalent curl command:")
    logger.info(curl_cmd)
    logger.info("=" * 80)


def log_response(response: requests.Response, truncate: int = 2000):
    """Log response details."""
    logger.info("=" * 80)
    logger.info(f"RESPONSE: {response.status_code} {response.reason}")
    logger.info(f"Time: {response.elapsed.total_seconds():.3f}s")
    logger.info("-" * 80)

    try:
        data = response.json()
        json_str = json.dumps(data, indent=2)
        if truncate and len(json_str) > truncate:
            logger.info(f"{json_str[:truncate]}\n... (truncated, showing first {truncate} chars)")
        else:
            logger.info(json_str)
    except Exception:
        text = response.text
        if truncate and len(text) > truncate:
            logger.info(f"{text[:truncate]}\n... (truncated, showing first {truncate} chars)")
        else:
            logger.info(text)
    logger.info("=" * 80)


def request_and_log(
    session: requests.Session,
    method: str,
    url: str,
    timeout: int,
    json_data: Optional[Dict[str, Any]] = None,
    response_truncate: int = 2000,
) -> requests.Response:
    """
    Perform HTTP request, log request/response, and on connection/HTTP errors
    log the failure so CI shows a clear cause (e.g. Connection reset by peer).
    """
    log_request(method, url, json_data)
    try:
        if method.upper() == "GET":
            response = session.get(url, timeout=timeout)
        else:
            response = session.post(url, json=json_data or {}, timeout=timeout)
        log_response(response, truncate=response_truncate)
        return response
    except requests.RequestException as e:
        logger.error(
            "REQUEST FAILED: %s: %s",
            type(e).__name__,
            e,
            exc_info=True,
        )
        if getattr(e, "response", None) is not None:
            logger.info("Response at failure (if any):")
            log_response(e.response, truncate=response_truncate)
        raise


# ============================================================================
# Health Endpoint Tests
# ============================================================================


@pytest.mark.health
@pytest.mark.test_in_ci
def test_liveness_probe(base_url, session, timeout):
    """Test /v1/live endpoint."""
    url = f"{base_url}/v1/live"
    response = request_and_log(session, "GET", url, timeout)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


@pytest.mark.health
@pytest.mark.test_in_ci
def test_readiness_probe(base_url, session, timeout):
    """Test /v1/ready endpoint."""
    url = f"{base_url}/v1/ready"
    response = request_and_log(session, "GET", url, timeout)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


@pytest.mark.health
@pytest.mark.test_in_ci
def test_startup_probe(base_url, session, timeout):
    """Test /v1/startup endpoint."""
    url = f"{base_url}/v1/startup"
    response = request_and_log(session, "GET", url, timeout)
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"


@pytest.mark.health
@pytest.mark.test_in_ci
def test_healthz_endpoint(base_url, session, timeout):
    """Test /v1/healthz endpoint returns 200 with status and version fields."""
    url = f"{base_url}/v1/healthz"
    response = request_and_log(session, "GET", url, timeout)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data.get("status") == "ok", f"Expected status 'ok', got {data.get('status')}"
    assert isinstance(data.get("version"), str) and data.get(
        "version"
    ), "Expected non-empty version string in response body"


@pytest.mark.health
@pytest.mark.test_in_ci
def test_metadata_endpoint(base_url, session, timeout):
    """Test /v1/metadata endpoint."""
    url = f"{base_url}/v1/metadata"
    response = request_and_log(session, "GET", url, timeout)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    required_fields = ["version", "host", "port"]
    for field in required_fields:
        assert field in data, f"Missing required field: {field}"


# ============================================================================
# Models Endpoint Tests
# ============================================================================


@pytest.mark.models
@pytest.mark.test_in_ci
def test_list_models(base_url, session, timeout, shared_state):
    """Test /models endpoint."""
    url = f"{base_url}/models"
    response = request_and_log(session, "GET", url, timeout)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert data.get("object") == "list", "Response object is not 'list'"
    assert data.get("data") and len(data["data"]) > 0, "No models found"

    # Store model ID for later tests
    shared_state["model_id"] = data["data"][0]["id"]


@pytest.mark.models
@pytest.mark.test_in_ci
def test_models_response_structure(base_url, session, timeout):
    """Test that models response has correct structure."""
    url = f"{base_url}/models"
    response = request_and_log(session, "GET", url, timeout)
    data = response.json()

    assert "object" in data
    assert "data" in data
    assert isinstance(data["data"], list)

    if len(data["data"]) > 0:
        model = data["data"][0]
        assert "id" in model
        assert "object" in model


# ============================================================================
# Metrics Endpoint Tests
# ============================================================================


@pytest.mark.metrics
@pytest.mark.test_in_ci
def test_metrics_endpoint(base_url, session, timeout):
    """Test /metrics endpoint for prometheus format metrics."""
    url = f"{base_url}/metrics"
    response = request_and_log(session, "GET", url, timeout, response_truncate=1000)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # Metrics endpoint returns prometheus format (plain text).
    # At cold start all default collectors are unregistered; custom metrics
    # may be empty until the first request is processed.  Only check format
    # when the response body is non-empty.
    if response.text.strip():
        assert any(
            line.startswith(("# HELP", "# TYPE")) or (line and line[0].isalpha())
            for line in response.text.splitlines()
            if line.strip()
        ), f"Response doesn't look like prometheus format: {response.text[:200]}"


# ============================================================================
# Summarization Endpoint Tests
# ============================================================================


@pytest.mark.summarization
@pytest.mark.test_in_ci
def test_summarize_video_from_url(base_url, session, timeout, shared_state):
    """Test video summarization via /summarize POST with URL."""
    # Get model ID
    model_id = shared_state.get("model_id")
    if not model_id:
        # Fetch models first
        response = request_and_log(session, "GET", f"{base_url}/models", timeout)
        data = response.json()
        model_id = data["data"][0]["id"]
        shared_state["model_id"] = model_id

    url = f"{base_url}/summarize"
    payload = {**SUMMARIZATION_PAYLOAD, "model": model_id}

    logger.info("Note: This test may take 30-60 seconds...")
    response = request_and_log(session, "POST", url, 120, json_data=payload, response_truncate=1000)

    if response.status_code in (500, 503):
        pytest.skip(
            f"LLM/CA-RAG back-end not available in test environment: "
            f"{response.status_code} {response.text[:200]}"
        )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # Validate response structure per OpenAPI spec
    data = response.json()
    assert isinstance(data, dict), "Response is not a JSON object"

    # Required fields per CompletionResponse schema
    assert "id" in data, "Missing 'id' field"
    assert "video_id" in data, "Missing 'video_id' field"
    assert "choices" in data, "Missing 'choices' field"
    assert "created" in data, "Missing 'created' field"
    assert "model" in data, "Missing 'model' field"
    assert "media_info" in data, "Missing 'media_info' field"
    assert "object" in data, "Missing 'object' field"

    # Validate choices array
    assert isinstance(data["choices"], list), "'choices' is not an array"
    assert len(data["choices"]) > 0, "'choices' array is empty"

    # Validate first choice per CompletionResponseChoice schema
    choice = data["choices"][0]
    assert "message" in choice, "Missing 'message' field in choice"
    assert "finish_reason" in choice, "Missing 'finish_reason' field"
    assert "index" in choice, "Missing 'index' field"

    # Validate message per ChatCompletionResponseMessage schema
    message = choice["message"]
    assert "content" in message, "Missing 'content' field in message"
    assert "role" in message, "Missing 'role' field in message"
    assert message["role"] == "assistant", f"Expected role 'assistant', got '{message['role']}'"

    # Validate content
    content = message["content"]
    assert isinstance(content, str) or content is None, "Content must be string or null"
    if content:
        assert len(content.strip()) >= 10, "Content too short"

    # Check for optional usage field per CompletionUsage schema
    if "usage" in data:
        usage = data["usage"]
        assert "query_processing_time" in usage, "Missing query_processing_time in usage"
        assert "total_chunks_processed" in usage, "Missing total_chunks_processed in usage"


@pytest.mark.summarization
@pytest.mark.test_in_ci
def test_summarization_v1_endpoint(base_url, session, shared_state):
    """Test video summarization via /v1/summarize POST endpoint."""
    model_id = shared_state.get("model_id")
    if not model_id:
        pytest.skip("No model ID available")

    url = f"{base_url}/v1/summarize"
    payload = {**SUMMARIZATION_PAYLOAD, "model": model_id}

    logger.info("Note: This test may take 30-60 seconds...")
    response = request_and_log(session, "POST", url, 120, json_data=payload, response_truncate=1000)

    if response.status_code in (500, 503):
        pytest.skip(
            f"LLM/CA-RAG back-end not available in test environment: "
            f"{response.status_code} {response.text[:200]}"
        )

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    # Verify required fields per OpenAPI spec
    assert "id" in data, "Missing 'id' field"
    assert "video_id" in data, "Missing 'video_id' field"
    assert "model" in data, "Missing 'model' field"
    assert "object" in data, "Missing 'object' field"

    # Check for optional usage field per CompletionUsage schema
    if "usage" in data:
        usage = data["usage"]
        assert "query_processing_time" in usage, "Missing query_processing_time in usage"
        assert "total_chunks_processed" in usage, "Missing total_chunks_processed in usage"


# ============================================================================
# Recommended Config Endpoint Tests
# ============================================================================


@pytest.mark.recommended_config
@pytest.mark.test_in_ci
def test_recommended_config_short_video(base_url, session, timeout):
    """Test /recommended_config for 4-minute video per RecommendedConfig schema."""
    url = f"{base_url}/recommended_config"
    payload = {
        "video_length": 240,  # 4 minutes in seconds
        "target_response_time": 60,
        "usecase_event_duration": 10,
    }

    response = request_and_log(session, "POST", url, timeout, json_data=payload)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # Validate per RecommendedConfigResponse schema
    data = response.json()
    assert "chunk_size" in data, "Missing chunk_size in response"
    assert "text" in data, "Missing text in response"
    assert isinstance(data["chunk_size"], int), "chunk_size must be integer"
    assert data["chunk_size"] >= 0, f"Invalid chunk_size: {data['chunk_size']} (must be >= 0)"


@pytest.mark.recommended_config
@pytest.mark.test_in_ci
def test_recommended_config_long_video(base_url, session, timeout):
    """Test /recommended_config for 1h 31min video per RecommendedConfig schema."""
    url = f"{base_url}/recommended_config"
    payload = {
        "video_length": 5460,  # 1h 31min in seconds
        "target_response_time": 60,
        "usecase_event_duration": 10,
    }

    response = request_and_log(session, "POST", url, timeout, json_data=payload)

    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    # Validate per RecommendedConfigResponse schema
    data = response.json()
    assert "chunk_size" in data, "Missing chunk_size in response"
    assert "text" in data, "Missing text in response"
    assert isinstance(data["chunk_size"], int), "chunk_size must be integer"
    assert data["chunk_size"] >= 0, f"Invalid chunk_size: {data['chunk_size']} (must be >= 0)"


# ============================================================================
# Error Handling Tests
# ============================================================================


@pytest.mark.error_handling
@pytest.mark.test_in_ci
def test_invalid_video_id_returns_error(base_url, session, shared_state):
    """Test that invalid video ID returns 4xx error per LvsError schema."""
    model_id = shared_state.get("model_id", "unknown-model")

    url = f"{base_url}/summarize"
    payload = {
        "id": "00000000-0000-0000-0000-000000000000",  # Invalid UUID
        "url": "http://example.com/nonexistent.mp4",
        "model": model_id,
        "chunk_duration": 10,
        "max_tokens": 512,
    }

    response = request_and_log(session, "POST", url, 10, json_data=payload)

    # Per OpenAPI spec, expect 400/422 error
    assert response.status_code in [
        400,
        404,
        422,
    ], f"Expected 4xx error, got {response.status_code}"

    # Validate error response structure per LvsError schema
    if response.status_code != 404:
        data = response.json()
        assert "code" in data, "Missing 'code' in error response"
        assert "message" in data, "Missing 'message' in error response"


@pytest.mark.error_handling
@pytest.mark.test_in_ci
def test_missing_required_model_returns_422(base_url, session):
    """Test that missing required 'model' field returns 422 error."""
    url = f"{base_url}/summarize"
    payload = {
        "url": "http://example.com/video.mp4",
        "chunk_duration": 10,
        "max_tokens": 512,
        # Missing required 'model' field
    }

    response = request_and_log(session, "POST", url, 10, json_data=payload)

    assert response.status_code == 422, f"Expected 422, got {response.status_code}"

    # Validate error response per LvsError schema
    data = response.json()
    assert "code" in data, "Missing 'code' in error response"
    assert "message" in data, "Missing 'message' in error response"


# ============================================================================
# Standalone CLI Wrapper (Backward Compatibility)
# ============================================================================


def main():
    """Main entry point for standalone execution."""
    import argparse

    parser = argparse.ArgumentParser(
        description="VIA Engine API Test Suite (pytest wrapper)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
    python3 test_api_endpoints.py http://localhost:38111 health
    python3 test_api_endpoints.py http://localhost:38111 all --verbose
    python3 test_api_endpoints.py http://localhost:38111 summarization --timeout 120

Note: This is a wrapper around pytest. For advanced usage, use pytest directly:
    pytest tests/functional/test_api_endpoints.py -m health -v
        """,
    )
    parser.add_argument("base_url", help="Base URL (e.g., http://localhost:38111)")
    parser.add_argument(
        "category",
        choices=[
            "health",
            "metrics",
            "models",
            "summarization",
            "recommended-config",
            "error-handling",
            "all",
        ],
        help="Test category",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("/tmp/api-test-results"),
        help="Output directory (default: /tmp/api-test-results)",
    )
    parser.add_argument("--timeout", type=int, default=30, help="Timeout (default: 30)")
    parser.add_argument("--verbose", action="store_true", help="Verbose output")
    parser.add_argument(
        "--response-truncate",
        type=int,
        default=2000,
        help="Response truncate (default: 2000, 0=unlimited)",
    )

    args = parser.parse_args()

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Map category to pytest marker
    marker_map = {
        "health": "health",
        "metrics": "metrics",
        "models": "models",
        "summarization": "summarization",
        "recommended-config": "recommended_config",
        "error-handling": "error_handling",
        "all": "test_in_ci",
    }

    script_dir = Path(__file__).parent

    # Build pytest arguments
    pytest_args = [
        str(script_dir / "test_api_endpoints.py"),
        "-m",
        marker_map[args.category],
        f"--base-url={args.base_url}",
        f"--timeout={args.timeout}",
        f"--response-truncate={args.response_truncate}",
        f"--junitxml={args.output_dir / f'{args.category}-junit.xml'}",
        "--tb=short",
        "--capture=tee-sys",
    ]

    if args.verbose:
        pytest_args.append("-v")

    print("=" * 60)
    print("VIA Engine API Tests (pytest-based)")
    print(f"Base URL: {args.base_url}")
    print(f"Category: {args.category}")
    print(f"Marker:   {marker_map[args.category]}")
    print("=" * 60)

    exit_code = pytest.main(pytest_args)

    if exit_code == 0:
        print("\nAll tests passed!")
    else:
        print("\nSome tests failed!")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()

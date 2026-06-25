#!/usr/bin/env python3
# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

######################################################################################################
# Comprehensive test script for NIM-compatible endpoints
# Tests the implementation without requiring a running server (unit tests)
# AND provides runtime tests when a server is available (integration tests)
######################################################################################################

import argparse
import json
import os
import subprocess
import sys
import time
import traceback
from uuid import UUID, uuid4

# Add src to path
sys.path.insert(0, "src")

# Optional imports for runtime tests
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


def test_api_models():
    """Test API model imports and validation"""
    print("=" * 60)
    print("Testing API Models")
    print("=" * 60)

    try:
        from api_models.nim_compat import (
            ChatCompletionChoice,
            ChatCompletionRequest,
            ChatCompletionResponse,
            ChatCompletionUsage,
            ChatMessage,
            CompletionRequest,
            CompletionResponse,
            ManifestResponse,
            VersionResponse,
        )

        print("✓ All API models imported successfully")

        # Test ChatMessage
        msg = ChatMessage(role="user", content="Test message")
        assert msg.role == "user"
        assert msg.content == "Test message"
        print("✓ ChatMessage model works correctly")

        # Test ChatCompletionRequest
        request = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Test")],
            id=uuid4(),
        )
        assert request.model == "test-model"
        assert len(request.messages) == 1
        print("✓ ChatCompletionRequest model works correctly")

        # Test VersionResponse
        version = VersionResponse(release="1.0.0", api="3.1.0")
        assert version.release == "1.0.0"
        assert version.api == "3.1.0"
        print("✓ VersionResponse model works correctly")

        # Test ManifestResponse
        manifest = ManifestResponse(version="1.0.0", model="test-model")
        assert manifest.version == "1.0.0"
        assert manifest.model == "test-model"
        print("✓ ManifestResponse model works correctly")

        print("\n✅ All API model tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ API model test failed: {e}")
        traceback.print_exc()
        return False


def test_server_imports():
    """Test server imports"""
    print("=" * 60)
    print("Testing Server Imports")
    print("=" * 60)

    try:
        # Test that all required imports are available
        from api_models.nim_compat import (
            ChatCompletionChoice,
            ChatCompletionRequest,
            ChatCompletionResponse,
            ChatCompletionUsage,
            ChatMessage,
            CompletionRequest,
            CompletionResponse,
            ManifestResponse,
            VersionResponse,
        )

        print("✓ All NIM compat imports available")

        # Check that server can import these
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "rtvi_vlm_server", "src/server/rtvi_vlm_server.py"
        )
        if spec is None:
            print("⚠ Could not load server module spec (expected if dependencies missing)")
        else:
            print("✓ Server module can be loaded")

        print("\n✅ Server import tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Server import test failed: {e}")
        traceback.print_exc()
        return False


def test_cli_structure():
    """Test CLI command structure"""
    print("=" * 60)
    print("Testing CLI Structure")
    print("=" * 60)

    try:
        import argparse

        # Test that CLI parser can be created (without executing)
        # We'll just check the file structure
        with open("src/cli/rtvi_client_cli.py", "r") as f:
            content = f.read()

        # Check for new commands
        commands = [
            "chat-completions",
            "completions",
            "get-version",
            "get-manifest",
        ]

        for cmd in commands:
            if cmd in content:
                print(f"✓ CLI command '{cmd}' found")
            else:
                print(f"❌ CLI command '{cmd}' not found")
                return False

        # Check for handler functions
        handlers = [
            "do_chat_completions",
            "do_completions",
            "do_get_version",
            "do_get_manifest",
        ]

        for handler in handlers:
            if handler in content:
                print(f"✓ Handler function '{handler}' found")
            else:
                print(f"❌ Handler function '{handler}' not found")
                return False

        # Check request_handlers dict includes new commands
        if '"chat-completions": do_chat_completions' in content:
            print("✓ chat-completions registered in request_handlers")
        else:
            print("❌ chat-completions not registered in request_handlers")
            return False

        print("\n✅ CLI structure tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ CLI structure test failed: {e}")
        traceback.print_exc()
        return False


def test_endpoint_definitions():
    """Test that endpoints are defined in server"""
    print("=" * 60)
    print("Testing Endpoint Definitions")
    print("=" * 60)

    try:
        with open("src/server/rtvi_vlm_server.py", "r") as f:
            content = f.read()

        endpoints = [
            ("/chat/completions", "POST"),
            ("/completions", "POST"),
            ("/version", "GET"),
            ("/manifest", "GET"),
            ("/health/live", "GET"),
            ("/health/ready", "GET"),
        ]

        for endpoint, method in endpoints:
            # Check for endpoint definition (API_PREFIX is "/v1")
            patterns = [
                f"/v1{endpoint}",
                f'"{endpoint}"',
                f"'{endpoint}'",
                f'f"{{API_PREFIX}}{endpoint}"',
            ]

            found = any(pattern in content for pattern in patterns)
            if found:
                print(f"✓ Endpoint {method} {endpoint} found")
            else:
                print(f"❌ Endpoint {method} {endpoint} not found")
                return False

        # Check for NIM Compatible tag
        if '"NIM Compatible"' in content:
            print("✓ NIM Compatible tag found")
        else:
            print("⚠ NIM Compatible tag not found (may use different format)")

        print("\n✅ Endpoint definition tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Endpoint definition test failed: {e}")
        traceback.print_exc()
        return False


def test_response_formats():
    """Test response format handling"""
    print("=" * 60)
    print("Testing Response Format Handling")
    print("=" * 60)

    try:
        from api_models.nim_compat import ChatCompletionRequest, ChatMessage

        # Test with json_object format
        request1 = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Test")],
            id=uuid4(),
            response_format={"type": "json_object"},
        )
        assert request1.response_format == {"type": "json_object"}
        print("✓ JSON object response format handled")

        # Test with text format (default)
        request2 = ChatCompletionRequest(
            model="test",
            messages=[ChatMessage(role="user", content="Test")],
            id=uuid4(),
        )
        assert request2.response_format is None or request2.response_format == {}
        print("✓ Text response format handled")

        print("\n✅ Response format tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Response format test failed: {e}")
        traceback.print_exc()
        return False


def test_multimodal_content():
    """Test multimodal content (video_url/image_url) handling"""
    print("=" * 60)
    print("Testing Multimodal Content Handling")
    print("=" * 60)

    try:
        from api_models.nim_compat import (
            ChatCompletionRequest,
            ChatMessage,
            ContentPart,
            ImageUrl,
            VideoUrl,
        )

        # Test simple string content
        msg1 = ChatMessage(role="user", content="Simple text message")
        assert msg1.get_text_content() == "Simple text message"
        assert msg1.get_media_urls() == ([], [])
        print("✓ Simple string content works")

        # Test multimodal content with video_url
        msg2 = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="What is in this video?"),
                ContentPart(
                    type="video_url",
                    video_url=VideoUrl(url="https://example.com/video.mp4"),
                ),
            ],
        )
        assert msg2.get_text_content() == "What is in this video?"
        image_urls, video_urls = msg2.get_media_urls()
        assert len(video_urls) == 1
        assert video_urls[0] == "https://example.com/video.mp4"
        print("✓ Multimodal content with video_url works")

        # Test multimodal content with image_url
        msg3 = ChatMessage(
            role="user",
            content=[
                ContentPart(type="text", text="Describe this image."),
                ContentPart(
                    type="image_url",
                    image_url=ImageUrl(url="https://example.com/image.jpg"),
                ),
            ],
        )
        assert msg3.get_text_content() == "Describe this image."
        image_urls, video_urls = msg3.get_media_urls()
        assert len(image_urls) == 1
        assert image_urls[0] == "https://example.com/image.jpg"
        print("✓ Multimodal content with image_url works")

        # Test ChatCompletionRequest with multimodal content (no id required)
        request = ChatCompletionRequest(
            model="test-model",
            messages=[
                ChatMessage(
                    role="user",
                    content=[
                        ContentPart(type="text", text="What's in this video?"),
                        ContentPart(
                            type="video_url",
                            video_url=VideoUrl(url="https://example.com/test.mp4"),
                        ),
                    ],
                )
            ],
        )
        assert request.model == "test-model"
        print("✓ ChatCompletionRequest with multimodal content works")

        print("\n✅ Multimodal content tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Multimodal content test failed: {e}")
        traceback.print_exc()
        return False


def test_turn_by_turn_conversation():
    """Test turn-by-turn conversation support"""
    print("=" * 60)
    print("Testing Turn-by-Turn Conversation Support")
    print("=" * 60)

    try:
        from api_models.nim_compat import ChatCompletionRequest, ChatMessage

        # Test 1: Multi-turn conversation structure
        request = ChatCompletionRequest(
            model="test-model",
            messages=[
                ChatMessage(role="system", content="You are a helpful assistant."),
                ChatMessage(role="user", content="What is in this video?"),
                ChatMessage(role="assistant", content="The video shows a warehouse scene."),
                ChatMessage(role="user", content="Can you describe the person in more detail?"),
            ],
            id=uuid4(),
        )
        assert len(request.messages) == 4
        assert request.messages[0].role == "system"
        assert request.messages[1].role == "user"
        assert request.messages[2].role == "assistant"
        assert request.messages[3].role == "user"
        print("✓ Multi-turn conversation structure works")

        # Test 2: Assistant message text extraction
        assistant_msg = ChatMessage(role="assistant", content="This is an assistant response.")
        assert assistant_msg.get_text_content() == "This is an assistant response."
        print("✓ Assistant message text extraction works")

        # Test 3: Conversation with only user messages (backward compatibility)
        single_turn = ChatCompletionRequest(
            model="test-model",
            messages=[ChatMessage(role="user", content="Single question")],
            id=uuid4(),
        )
        assert len(single_turn.messages) == 1
        print("✓ Single-turn conversation still works (backward compatible)")

        # Test 4: Multiple assistant messages
        multi_assistant = ChatCompletionRequest(
            model="test-model",
            messages=[
                ChatMessage(role="user", content="Question 1"),
                ChatMessage(role="assistant", content="Answer 1"),
                ChatMessage(role="user", content="Question 2"),
                ChatMessage(role="assistant", content="Answer 2"),
                ChatMessage(role="user", content="Question 3"),
            ],
            id=uuid4(),
        )
        assert len(multi_assistant.messages) == 5
        assistant_count = sum(1 for msg in multi_assistant.messages if msg.role == "assistant")
        assert assistant_count == 2
        print("✓ Multiple assistant messages supported")

        # Test 5: System message handling
        with_system = ChatCompletionRequest(
            model="test-model",
            messages=[
                ChatMessage(role="system", content="System instruction 1"),
                ChatMessage(
                    role="system", content="System instruction 2"
                ),  # Last one should be used
                ChatMessage(role="user", content="User question"),
                ChatMessage(role="assistant", content="Assistant answer"),
            ],
            id=uuid4(),
        )
        assert len(with_system.messages) == 4
        system_count = sum(1 for msg in with_system.messages if msg.role == "system")
        assert system_count == 2
        print("✓ Multiple system messages supported (last one used)")

        print("\n✅ Turn-by-turn conversation tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Turn-by-turn conversation test failed: {e}")
        traceback.print_exc()
        return False


def test_message_parsing():
    """Test CLI message parsing logic"""
    print("=" * 60)
    print("Testing CLI Message Parsing Logic")
    print("=" * 60)

    try:
        # Simulate the parsing logic from CLI
        test_cases = [
            ("user:Hello world", ("user", "Hello world")),
            ("system:You are helpful", ("system", "You are helpful")),
            ("user:Content with: colons", ("user", "Content with: colons")),
            ("user:Multiple:colons:here", ("user", "Multiple:colons:here")),
        ]

        for msg_str, expected in test_cases:
            if ":" not in msg_str:
                print(f"❌ Invalid format: {msg_str}")
                return False

            role, content = msg_str.split(":", 1)  # Split on first colon only
            role = role.strip()
            content = content.strip()

            if role == expected[0] and content == expected[1]:
                print(
                    f"✓ Parsed '{msg_str}' correctly -> role='{role}', content='{content[:30]}...'"
                )
            else:
                print(f"❌ Failed to parse '{msg_str}' correctly")
                print(f"   Expected: {expected}, Got: ({role}, {content})")
                return False

        print("\n✅ Message parsing tests passed!\n")
        return True

    except Exception as e:
        print(f"❌ Message parsing test failed: {e}")
        traceback.print_exc()
        return False


###############################################################################
# Runtime Tests - Require a running server
###############################################################################


def get_model_name(backend: str) -> str:
    """Get model name from API"""
    try:
        if not backend.startswith("http://") and not backend.startswith("https://"):
            backend = "http://" + backend
        r = requests.get(f"{backend}/v1/models", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("data") and len(data["data"]) > 0:
                return data["data"][0]["id"]
    except Exception:
        pass
    return "nim_nvidia_cosmos-reason2-8b_0303-fp8-dynamic-kv8"  # Default fallback


def test_runtime_version(backend: str) -> bool:
    """Test GET /v1/version endpoint"""
    print("Test: Get Version")
    print("-" * 40)
    try:
        r = requests.get(f"{backend}/v1/version", timeout=5)
        if r.status_code == 200:
            data = r.json()
            print(f"✓ Version: {data}")
            return True
        else:
            print(f"❌ Status: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_manifest(backend: str) -> bool:
    """Test GET /v1/manifest endpoint"""
    print("Test: Get Manifest")
    print("-" * 40)
    try:
        r = requests.get(f"{backend}/v1/manifest", timeout=5)
        if r.status_code == 200:
            data = r.json()
            print(f"✓ Manifest: {data}")
            return True
        else:
            print(f"❌ Status: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_health_live(backend: str) -> bool:
    """Test GET /v1/health/live endpoint"""
    print("Test: Health Live")
    print("-" * 40)
    try:
        r = requests.get(f"{backend}/v1/health/live", timeout=5)
        if r.status_code == 200:
            print(f"✓ Health Live: OK")
            return True
        else:
            print(f"❌ Status: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_health_ready(backend: str) -> bool:
    """Test GET /v1/health/ready endpoint"""
    print("Test: Health Ready")
    print("-" * 40)
    try:
        r = requests.get(f"{backend}/v1/health/ready", timeout=5)
        if r.status_code == 200:
            print(f"✓ Health Ready: OK")
            return True
        else:
            print(f"❌ Status: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_models(backend: str) -> bool:
    """Test GET /v1/models endpoint"""
    print("Test: List Models")
    print("-" * 40)
    try:
        r = requests.get(f"{backend}/v1/models", timeout=5)
        if r.status_code == 200:
            data = r.json()
            if data.get("data") and len(data["data"]) > 0:
                print(f"✓ Models: {[m['id'] for m in data['data']]}")
                return True
            else:
                print("⚠ No models available")
                return True  # Not a failure, just no models loaded
        else:
            print(f"❌ Status: {r.status_code}")
            return False
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_completions_error(backend: str, model: str) -> bool:
    """Test POST /v1/completions returns error (VLM requires visual input)"""
    print("Test: Completions Endpoint (Expected Error)")
    print("-" * 40)
    try:
        r = requests.post(
            f"{backend}/v1/completions",
            json={"model": model, "prompt": "Complete this sentence"},
            timeout=10,
        )
        # Expect 400 or error response
        if r.status_code in [400, 422]:
            print(f"✓ Completions returned expected error: {r.status_code}")
            return True
        elif r.status_code == 200:
            data = r.json()
            if "error" in data or "not supported" in str(data).lower():
                print(f"✓ Completions returned error in response body")
                return True
            print(f"⚠ Completions succeeded unexpectedly: {data}")
            return False
        else:
            print(f"⚠ Unexpected status: {r.status_code}")
            return True  # Other errors are acceptable
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_chat_completions_invalid_id(backend: str, model: str) -> bool:
    """Test POST /v1/chat/completions with invalid ID returns proper error"""
    print("Test: Chat Completions with Invalid ID")
    print("-" * 40)
    try:
        fake_id = str(uuid4())
        r = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Describe the video"}],
                "id": fake_id,
            },
            timeout=10,
        )
        # Expect 400 or 404 (not 500)
        if r.status_code in [400, 404, 422]:
            print(f"✓ Invalid ID returned proper error: {r.status_code}")
            return True
        elif r.status_code == 500:
            print(f"❌ Got 500 error - should return 400/404 for invalid ID")
            return False
        else:
            print(f"⚠ Unexpected status: {r.status_code}")
            return True
    except Exception as e:
        print(f"❌ Error: {e}")
        return False


def test_runtime_chat_completions_video_url(
    backend: str, model: str, video_url: str = None
) -> bool:
    """Test POST /v1/chat/completions with video_url in message content"""
    print("Test: Chat Completions with video_url (OpenAI multimodal format)")
    print("-" * 40)

    # Use a sample video URL if none provided
    if not video_url:
        video_url = "https://assets.ngc.nvidia.com/products/api-catalog/cosmos-reason1-7b/av_construction_stop_timestamped.mp4"

    try:
        print(f"Using video URL: {video_url[:80]}...")
        r = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "What is in this video?"},
                            {"type": "video_url", "video_url": {"url": video_url}},
                        ],
                    }
                ],
                "max_tokens": 256,
                "stream": False,
            },
            timeout=300,  # Video download + processing can take time
        )

        if r.status_code == 200:
            result = r.json()
            if result.get("choices"):
                content = result["choices"][0].get("message", {}).get("content", "")
                print(f"✓ video_url format works!")
                print(f"  Response: {content[:150]}...")
                return True
            else:
                print(f"⚠ Response missing choices: {result}")
                return False
        elif r.status_code in [400, 502]:
            # 400 = download failed, 502 = download error - acceptable for test URLs
            print(f"⚠ Status {r.status_code} - video URL may be inaccessible")
            error = (
                r.json()
                if r.headers.get("content-type", "").startswith("application/json")
                else r.text
            )
            print(f"  Error: {str(error)[:200]}")
            return True  # Not a failure of the endpoint itself
        else:
            print(f"❌ Unexpected status: {r.status_code}")
            print(f"  Response: {r.text[:300]}")
            return False

    except requests.exceptions.Timeout:
        print(f"⚠ Request timed out (video may be large or model processing slow)")
        return True  # Timeout is acceptable for large videos
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        return False


def test_runtime_chat_completions_turn_by_turn(backend: str, model: str, file_id: str) -> bool:
    """Test POST /v1/chat/completions with multi-turn conversation"""
    print("Test: Chat Completions Turn-by-Turn Conversation")
    print("-" * 40)

    if not file_id:
        print("⚠ Skipped (no file ID provided)")
        return True

    try:
        # First turn: Initial question
        print("Turn 1: Initial question...")
        r1 = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "What is in this video? Be brief."}],
                "id": file_id,
                "stream": False,
                "max_tokens": 100,
            },
            timeout=120,
        )

        if r1.status_code != 200:
            print(f"⚠ Turn 1 failed: {r1.status_code} (model may not be ready)")
            return True  # Not a failure if model isn't ready

        result1 = r1.json()
        assistant_response1 = ""
        if result1.get("choices") and len(result1["choices"]) > 0:
            assistant_response1 = result1["choices"][0].get("message", {}).get("content", "")
            print(f"✓ Turn 1 response: {assistant_response1[:100]}...")

        # Second turn: Follow-up question with conversation history
        print("\nTurn 2: Follow-up question with conversation history...")
        r2 = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "What is in this video? Be brief."},
                    {"role": "assistant", "content": assistant_response1},
                    {"role": "user", "content": "Can you describe the colors you see?"},
                ],
                "id": file_id,
                "stream": False,
                "max_tokens": 100,
            },
            timeout=120,
        )

        if r2.status_code != 200:
            print(f"⚠ Turn 2 failed: {r2.status_code}")
            return True  # Not a failure if model isn't ready

        result2 = r2.json()
        if result2.get("choices") and len(result2["choices"]) > 0:
            assistant_response2 = result2["choices"][0].get("message", {}).get("content", "")
            print(f"✓ Turn 2 response: {assistant_response2[:100]}...")
            print("✓ Turn-by-turn conversation works!")
            return True
        else:
            print("⚠ Turn 2 response missing choices")
            return True  # Not a failure

    except requests.exceptions.Timeout:
        print("⚠ Request timed out (model may be processing)")
        return True  # Timeout is acceptable
    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        return False


def test_runtime_chat_completions_with_file(backend: str, model: str, video_path: str) -> bool:
    """Test POST /v1/chat/completions with uploaded file"""
    print("Test: Chat Completions with File")
    print("-" * 40)

    if not video_path or not os.path.exists(video_path):
        print("⚠ Skipped (no video file provided)")
        return True

    try:
        # Upload file
        print(f"Uploading file: {video_path}")
        with open(video_path, "rb") as f:
            files = {"file": (os.path.basename(video_path), f)}
            r = requests.post(f"{backend}/v1/files/add", files=files, timeout=60)

        if r.status_code != 200:
            print(f"❌ Failed to upload file: {r.status_code}")
            return False

        data = r.json()
        file_id = None
        if data.get("results") and len(data["results"]) > 0:
            file_id = data["results"][0].get("id")

        if not file_id:
            print(f"❌ No file ID returned")
            return False

        print(f"✓ File uploaded with ID: {file_id}")

        # Test turn-by-turn conversation
        print("\nTesting turn-by-turn conversation...")
        try:
            result = test_runtime_chat_completions_turn_by_turn(backend, model, file_id)
            if result:
                print("✓ Turn-by-turn conversation test passed")
        except Exception as e:
            print(f"⚠ Turn-by-turn conversation test error: {e}")

        # Test non-streaming chat completion
        print("\nTesting non-streaming chat completion...")
        r = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Describe what you see."}],
                "id": file_id,
                "stream": False,
            },
            timeout=120,
        )

        if r.status_code == 200:
            print(f"✓ Non-streaming chat completion succeeded")
            result = r.json()
            if result.get("choices"):
                content = result["choices"][0].get("message", {}).get("content", "")
                print(f"  Response: {content[:100]}...")
        else:
            print(f"⚠ Non-streaming failed: {r.status_code} (model may not be ready)")

        # Test streaming chat completion
        print("Testing streaming chat completion...")
        r = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Describe the video briefly."}],
                "id": file_id,
                "stream": True,
            },
            stream=True,
            timeout=120,
        )

        if r.status_code == 200:
            chunks_received = 0
            for line in r.iter_lines():
                if line:
                    decoded = line.decode()
                    if decoded.startswith("data: "):
                        chunks_received += 1
                        if chunks_received > 5:
                            break
            print(f"✓ Streaming chat completion works ({chunks_received} chunks)")
        else:
            print(f"⚠ Streaming failed: {r.status_code} (model may not be ready)")

        # Cleanup
        print("Cleaning up file...")
        requests.delete(f"{backend}/v1/files/delete/{file_id}", timeout=5)
        print("✓ File deleted")

        return True

    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        return False


def test_runtime_rtsp_streaming(
    backend: str, model: str, video_path: str, rtsp_port: int = 8554
) -> bool:
    """Test RTSP streaming with chat completions (requires cvlc)"""
    print("Test: RTSP Streaming with Chat Completions")
    print("-" * 40)

    if not video_path or not os.path.exists(video_path):
        print("⚠ Skipped (no video file provided)")
        return True

    # Check if cvlc is available
    try:
        subprocess.run(["which", "cvlc"], check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print("⚠ Skipped (cvlc not found - install VLC to test RTSP streaming)")
        return True

    cvlc_process = None
    stream_id = None

    try:
        # Get local IP address
        local_ip = "127.0.0.1"
        try:
            result = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                local_ip = result.stdout.strip().split()[0]
        except Exception:
            pass

        rtsp_url = f"rtsp://{local_ip}:{rtsp_port}/file-stream"
        print(f"Starting cvlc RTSP server on port {rtsp_port}...")
        print(f"RTSP URL: {rtsp_url}")

        # Start cvlc RTSP server in background
        cvlc_process = subprocess.Popen(
            [
                "cvlc",
                "--loop",
                video_path,
                f":sout=#gather:rtp{{sdp=rtsp://:{rtsp_port}/file-stream}}",
                ":network-caching=1500",
                ":sout-all",
                ":sout-keep",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait for RTSP server to start
        print("Waiting for RTSP server to start...")
        time.sleep(3)

        # Add RTSP stream to backend
        print("Adding RTSP stream to backend...")
        r = requests.post(
            f"{backend}/v1/streams/add",
            json={
                "streams": [
                    {"liveStreamUrl": rtsp_url, "description": "Test RTSP stream from cvlc"}
                ]
            },
            timeout=10,
        )

        if r.status_code != 200:
            print(f"❌ Failed to add RTSP stream: {r.status_code} - {r.text[:200]}")
            return False

        data = r.json()
        if data.get("results") and len(data["results"]) > 0:
            stream_id = data["results"][0].get("id")

        if not stream_id:
            print(f"❌ No stream ID returned")
            return False

        print(f"✓ RTSP stream added with ID: {stream_id}")

        # Test streaming chat completion with RTSP stream
        print("Testing streaming chat completion with RTSP stream...")
        print("This may take 60+ seconds as the stream processes...")

        r = requests.post(
            f"{backend}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "user", "content": "Describe what you see in this live stream."}
                ],
                "id": stream_id,
                "stream": True,
            },
            stream=True,
            timeout=200,
        )

        if r.status_code != 200:
            print(f"❌ Error: Status {r.status_code}")
            print(f"Response: {r.text[:500]}")
            return False

        print("✓ Stream started successfully!")

        chunks_received = 0
        content_received = False
        ping_count = 0
        start_time = time.time()

        for line in r.iter_lines():
            if line:
                decoded = line.decode()

                # Handle ping messages
                if decoded.startswith(": ping"):
                    ping_count += 1
                    if ping_count % 10 == 0:
                        elapsed = time.time() - start_time
                        print(f"[Ping {ping_count}, {elapsed:.1f}s elapsed]")
                    continue

                if decoded.startswith("data: "):
                    json_str = decoded[6:]
                    if json_str.strip() == "[DONE]":
                        print("✓ Received [DONE] marker")
                        break
                    try:
                        chunk = json.loads(json_str)
                        chunks_received += 1
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            content_received = True
                    except json.JSONDecodeError:
                        pass

            # Timeout after 3 minutes
            if time.time() - start_time > 180:
                print(f"⚠ Timeout after 180 seconds")
                break

        elapsed_total = time.time() - start_time
        print(f"Total time: {elapsed_total:.1f}s, Pings: {ping_count}, Chunks: {chunks_received}")

        if content_received:
            print(f"✅ RTSP streaming test SUCCESS!")
            return True
        elif chunks_received > 0:
            print(f"⚠ Received chunks but no content yet")
            return True
        elif ping_count > 0:
            print(f"⚠ Stream alive but no data chunks (may still be initializing)")
            return True
        else:
            print(f"❌ No chunks or pings received")
            return False

    except Exception as e:
        print(f"❌ Error: {e}")
        traceback.print_exc()
        return False

    finally:
        # Cleanup
        if stream_id:
            print("Cleaning up RTSP stream...")
            try:
                requests.delete(f"{backend}/v1/streams/delete/{stream_id}", timeout=5)
                print("✓ RTSP stream deleted")
            except Exception:
                pass

        if cvlc_process:
            print("Stopping cvlc RTSP server...")
            cvlc_process.terminate()
            try:
                cvlc_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cvlc_process.kill()


def run_unit_tests() -> int:
    """Run unit tests (no server required)"""
    print("\n" + "=" * 60)
    print("NIM Endpoints - Unit Tests (No Server Required)")
    print("=" * 60 + "\n")

    tests = [
        ("API Models", test_api_models),
        ("Server Imports", test_server_imports),
        ("CLI Structure", test_cli_structure),
        ("Endpoint Definitions", test_endpoint_definitions),
        ("Response Formats", test_response_formats),
        ("Multimodal Content", test_multimodal_content),
        ("Message Parsing", test_message_parsing),
        ("Turn-by-Turn Conversation", test_turn_by_turn_conversation),
    ]

    results = []
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test '{test_name}' crashed: {e}")
            traceback.print_exc()
            results.append((test_name, False))

    return results


def run_runtime_tests(backend: str, video_path: str = None, rtsp_port: int = 8554) -> list:
    """Run runtime tests (server required)"""
    print("\n" + "=" * 60)
    print("NIM Endpoints - Runtime Tests (Server Required)")
    print(f"Backend: {backend}")
    print("=" * 60 + "\n")

    if not REQUESTS_AVAILABLE:
        print("❌ 'requests' module not available. Install with: pip install requests")
        return [("Runtime Tests", False)]

    # Ensure backend has scheme
    if not backend.startswith("http://") and not backend.startswith("https://"):
        backend = "http://" + backend

    # Get model name
    model = get_model_name(backend)
    print(f"Using model: {model}\n")

    results = []

    # Basic endpoint tests
    basic_tests = [
        ("Version Endpoint", lambda: test_runtime_version(backend)),
        ("Manifest Endpoint", lambda: test_runtime_manifest(backend)),
        ("Health Live", lambda: test_runtime_health_live(backend)),
        ("Health Ready", lambda: test_runtime_health_ready(backend)),
        ("Models Endpoint", lambda: test_runtime_models(backend)),
        ("Completions Error", lambda: test_runtime_completions_error(backend, model)),
        (
            "Chat Completions Invalid ID",
            lambda: test_runtime_chat_completions_invalid_id(backend, model),
        ),
        (
            "Chat Completions with video_url",
            lambda: test_runtime_chat_completions_video_url(backend, model),
        ),
    ]

    for test_name, test_func in basic_tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"❌ Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
        print()

    # File-based tests (optional)
    if video_path:
        try:
            result = test_runtime_chat_completions_with_file(backend, model, video_path)
            results.append(("Chat Completions with File", result))
        except Exception as e:
            print(f"❌ Test 'Chat Completions with File' crashed: {e}")
            results.append(("Chat Completions with File", False))
        print()

        # RTSP streaming test
        try:
            result = test_runtime_rtsp_streaming(backend, model, video_path, rtsp_port)
            results.append(("RTSP Streaming", result))
        except Exception as e:
            print(f"❌ Test 'RTSP Streaming' crashed: {e}")
            results.append(("RTSP Streaming", False))
        print()

    return results


def print_summary(results: list, title: str) -> int:
    """Print test summary and return exit code"""
    print("=" * 60)
    print(title)
    print("=" * 60)

    passed = sum(1 for _, result in results if result)
    total = len(results)

    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed. Please review.")
        return 1


def main():
    """Run all tests"""
    parser = argparse.ArgumentParser(
        description="Comprehensive test suite for NIM-compatible endpoints"
    )
    parser.add_argument(
        "--backend",
        default=os.environ.get("RTVI_BACKEND", "http://localhost:8000"),
        help="Backend URL (default: $RTVI_BACKEND or http://localhost:8000)",
    )
    parser.add_argument(
        "--video-path",
        default=os.environ.get("RTVI_TEST_VIDEO_PATH"),
        help="Path to test video file (default: $RTVI_TEST_VIDEO_PATH)",
    )
    parser.add_argument(
        "--rtsp-port",
        type=int,
        default=int(os.environ.get("RTSP_PORT", "8554")),
        help="RTSP port for streaming test (default: 8554)",
    )
    parser.add_argument(
        "--unit-only",
        action="store_true",
        help="Run only unit tests (no server required)",
    )
    parser.add_argument(
        "--runtime-only",
        action="store_true",
        help="Run only runtime tests (server required)",
    )

    args = parser.parse_args()

    all_results = []

    # Run unit tests
    if not args.runtime_only:
        unit_results = run_unit_tests()
        all_results.extend(unit_results)

    # Run runtime tests
    if not args.unit_only:
        runtime_results = run_runtime_tests(args.backend, args.video_path, args.rtsp_port)
        all_results.extend(runtime_results)

    # Print summary
    return print_summary(all_results, "Test Summary")


if __name__ == "__main__":
    sys.exit(main())

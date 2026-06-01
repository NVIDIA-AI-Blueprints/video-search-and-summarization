#!/usr/bin/env python3
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
"""Test context window overflow protection for text-only chat completions."""

import json
import subprocess
import sys

BACKEND = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8017"

# Get model name
model_resp = json.loads(subprocess.check_output(["curl", "-s", f"{BACKEND}/v1/models"]))
model = model_resp["data"][0]["id"]
print(f"Model: {model}")

# Build a conversation that exceeds context window
messages = [
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "What is CUDA?"},
    {"role": "assistant", "content": "CUDA is a parallel computing platform. " * 2000},
    {"role": "user", "content": "Tell me more."},
    {"role": "assistant", "content": "GPU computing enables massive parallelism. " * 2000},
    {"role": "user", "content": "What about the latest GPUs?"},
]

est = sum(len(m["content"]) // 4 + 4 for m in messages)
# VLM_MAX_MODEL_LEN is 262144 — need ~262K tokens to overflow
# Each repetition of 40 chars ≈ 10 tokens. Need ~26000 repetitions per message.
max_model_len = 262144
max_tokens = 4096
limit = max_model_len - max_tokens - 100
print(f"Estimated prompt tokens: {est}, prompt limit: {limit}")
if est <= limit:
    print("Not enough tokens to trigger. Need more messages. Generating...")
    # Add more large messages to exceed the limit
    while est <= limit:
        messages.insert(-1, {"role": "assistant", "content": "GPU computing. " * 8000})
        messages.insert(-1, {"role": "user", "content": "Continue explaining."})
        est = sum(len(m["content"]) // 4 + 4 for m in messages)
    print(f"New estimated tokens: {est} (limit: {limit}, messages: {len(messages)})")
print(f"Should trigger truncation: {est > limit}")

# Write payload
payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
with open("/tmp/test_overflow.json", "w") as f:
    json.dump(payload, f)

# Test non-streaming (check X-Warning header)
print("\n--- Non-streaming test (check X-Warning header) ---")
result = subprocess.run(
    [
        "curl",
        "-v",
        "-X",
        "POST",
        BACKEND + "/v1/chat/completions",
        "-H",
        "Content-Type: application/json",
        "-d",
        "@/tmp/test_overflow.json",
    ],
    capture_output=True,
    text=True,
)
print(result.stderr)  # curl -v outputs headers to stderr
print(result.stdout[:500])

# Test streaming (check first SSE event for warning)
print("\n--- Streaming test (check first SSE event) ---")
payload["stream"] = True
with open("/tmp/test_overflow_stream.json", "w") as f:
    json.dump(payload, f)

result = subprocess.run(
    [
        "curl",
        "-s",
        "-N",
        "-X",
        "POST",
        BACKEND + "/v1/chat/completions",
        "-H",
        "Content-Type: application/json",
        "-d",
        "@/tmp/test_overflow_stream.json",
    ],
    capture_output=True,
    text=True,
    timeout=30,
)
# Show first 3 SSE events (full content)
lines = [line for line in result.stdout.split("\n") if line.startswith("data:")]
print("First event (check for 'warning' field):")
if lines:
    print(lines[0])
    # Check if warning is in first event
    if "warning" in lines[0]:
        print("\n*** WARNING FOUND IN FIRST SSE EVENT ***")
    else:
        print("\n(no warning in first event)")
print("\nRemaining events:")
for line in lines[1:3]:
    print(line[:200])
print("... (%d total SSE events)" % len(lines))

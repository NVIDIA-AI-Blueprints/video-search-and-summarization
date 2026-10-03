# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Optional adapter integration test against a local fake NIM.

uv run --python 3.12 --with 'litellm[proxy]==1.103.0' --with pytest \
    pytest .github/skill-eval/tests/test_local_nim_protocols.py
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

litellm = pytest.importorskip("litellm")


@pytest.fixture
def fake_nim():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["content-length"])))
            requests.append((self.path, body))
            if self.path != "/v1/chat/completions":
                self.send_error(404)
                return
            result = {
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": body["model"],
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "OK"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            }
            if body.get("tools"):
                result["choices"][0]["message"] = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_test",
                            "type": "function",
                            "function": {"name": "check_status", "arguments": "{}"},
                        }
                    ],
                }
                result["choices"][0]["finish_reason"] = "tool_calls"
            if body.get("stream"):
                delta = result["choices"][0]["message"]
                if "tool_calls" in delta:
                    delta["tool_calls"][0]["index"] = 0
                chunk = {
                    **result,
                    "object": "chat.completion.chunk",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                }
                final = {
                    **chunk,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": result["choices"][0]["finish_reason"],
                        }
                    ],
                }
                raw = (
                    "data: "
                    + json.dumps(chunk)
                    + "\n\ndata: "
                    + json.dumps(final)
                    + "\n\ndata: [DONE]\n\n"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("protocol", ["messages", "responses", "chat"])
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("tool_call", [False, True])
def test_harness_protocols_translate_to_nim_chat_completions(
    fake_nim, protocol, stream, tool_call
):
    base, requests = fake_nim
    args = {
        "model": "nvidia_nim/qwen/qwen3-32b",
        "api_base": base,
        "api_key": "local-nim",
        "stream": stream,
    }
    if tool_call:
        args["tool_choice"] = "auto"
        schema = {"type": "object", "properties": {}}
        if protocol == "messages":
            args["tools"] = [
                {
                    "name": "check_status",
                    "description": "Check status",
                    "input_schema": schema,
                }
            ]
        elif protocol == "responses":
            args["tools"] = [
                {
                    "type": "function",
                    "name": "check_status",
                    "description": "Check status",
                    "parameters": schema,
                }
            ]
        else:
            args["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": "check_status",
                        "description": "Check status",
                        "parameters": schema,
                    },
                }
            ]
    if protocol == "responses":
        response = litellm.responses(**args, input="Say OK", max_output_tokens=16)
    elif protocol == "messages":
        response = litellm.anthropic.messages.create(
            **args, messages=[{"role": "user", "content": "Say OK"}], max_tokens=16
        )
    else:
        response = litellm.completion(
            **args, messages=[{"role": "user", "content": "Say OK"}], max_tokens=16
        )
    result = list(response) if stream else response
    assert ("check_status" if tool_call else "OK") in str(result)
    assert len(requests) == 1
    assert requests[0][0] == "/v1/chat/completions"
    assert requests[0][1]["model"] == "qwen/qwen3-32b"

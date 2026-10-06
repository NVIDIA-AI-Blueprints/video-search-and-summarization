# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Optional adapter integration test against a local fake NIM.

uv run --python 3.12 --with 'litellm[proxy]==1.103.0' --with pytest \
    pytest .github/skill-eval/tests/test_local_nim_protocols.py
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
            args["tool_choice"] = {"type": "auto"}
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


def test_proxy_accepts_no_key_and_stale_keys_for_all_harness_protocols(fake_nim, tmp_path):
    """Exercise the pinned HTTP proxy's actual auth and protocol handlers."""
    import httpx

    base, requests = fake_nim
    config = tmp_path / "proxy.json"
    config.write_text(json.dumps({
        "model_list": [{
            "model_name": "eval-model",
            "litellm_params": {
                "model": "nvidia_nim/qwen/qwen3-32b",
                "api_base": base,
                "api_key": "local-nim",
            },
        }],
        "litellm_settings": {"drop_params": True},
    }))
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
    env = os.environ.copy()
    for key in ("LITELLM_MASTER_KEY", "DATABASE_URL", "LITELLM_DATABASE_URL"):
        env.pop(key, None)
    env["LITELLM_TELEMETRY"] = "False"
    log = tmp_path / "proxy.log"
    with log.open("w") as output:
        proxy = subprocess.Popen(
            [str(Path(sys.executable).with_name("litellm")), "--config", str(config),
             "--host", "127.0.0.1", "--port", str(port)],
            env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
        )
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=20, trust_env=False) as client:
                deadline = time.monotonic() + 45
                while time.monotonic() < deadline:
                    assert proxy.poll() is None, log.read_text()[-4000:]
                    try:
                        if client.get("/health/liveliness").status_code == 200:
                            break
                    except httpx.ConnectError:
                        pass
                    time.sleep(0.1)
                else:
                    pytest.fail("LiteLLM did not become ready: " + log.read_text()[-4000:])
                headers_to_check = [{}, {"Authorization": "Bearer stale-eval-key"}, {"x-api-key": "stale-eval-key"}]
                for headers in headers_to_check:
                    for protocol in ("messages", "responses", "chat/completions"):
                        for stream in (False, True):
                            body = {"model": "eval-model", "stream": stream}
                            schema = {"type": "object", "properties": {}}
                            if protocol == "messages":
                                body.update(messages=[{"role": "user", "content": "Check status"}], max_tokens=16,
                                            tools=[{"name": "check_status", "description": "Check status", "input_schema": schema}])
                            elif protocol == "responses":
                                body.update(input="Check status", max_output_tokens=16,
                                            tools=[{"type": "function", "name": "check_status", "description": "Check status", "parameters": schema}])
                            else:
                                body.update(messages=[{"role": "user", "content": "Check status"}], max_tokens=16,
                                            tools=[{"type": "function", "function": {"name": "check_status", "description": "Check status", "parameters": schema}}])
                            response = client.post("/v1/" + protocol, headers={**headers, "anthropic-version": "2023-06-01"}, json=body)
                            assert response.status_code == 200, response.text
                            assert "check_status" in response.text
                assert len(requests) == 18
                assert all(path == "/v1/chat/completions" and body["model"] == "qwen/qwen3-32b" for path, body in requests)
        finally:
            proxy.terminate()
            try:
                proxy.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proxy.kill()
                proxy.wait(timeout=5)

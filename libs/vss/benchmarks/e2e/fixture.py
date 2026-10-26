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

"""Owned loopback VLM/LLM for wiring tests. Never live inference evidence."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Providers:
    def __enter__(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                self.connection.settimeout(5)
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 < length <= 16 * 1024**2:
                    self.send_error(413)
                    return
                body = json.loads(self.rfile.read(length))
                if self.path == "/vlm/chat/completions":
                    text = json.dumps(
                        {
                            "events": [
                                {
                                    "start_time": 0,
                                    "end_time": 1,
                                    "type": "test_pattern",
                                    "description": "A test pattern moves.",
                                }
                            ]
                        }
                    )
                elif self.path == "/llm/chat/completions":
                    text = "A test pattern moves."
                else:
                    self.send_error(404)
                    return
                if not isinstance(body.get("max_tokens"), int) or not body.get("model"):
                    self.send_error(400)
                    return
                data = json.dumps(
                    {
                        "id": "chatcmpl-controlled",
                        "created": 1,
                        "object": "chat.completion",
                        "model": body["model"],
                        "choices": [
                            {
                                "index": 0,
                                "finish_reason": "stop",
                                "message": {"role": "assistant", "content": text},
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 23,
                            "completion_tokens": 12,
                            "total_tokens": 35,
                        },
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            raise RuntimeError("fixture cleanup failed")

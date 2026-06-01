#!/usr/bin/env python3
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

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
import os
import time

DEFAULT_CR2_RESPONSE = (
    "<think>Based on the video analysis, I can see vehicles at the intersection. "
    "The primary vehicle appears to have been involved in a traffic incident. "
    "Multiple vehicles are visible in the scene with clear interaction patterns. "
    "The evidence suggests a collision event occurred at this location.</think>\n\n"
    "YES"
)


class NIMStubHandler(BaseHTTPRequestHandler):
    def _send_json(self, code, payload):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode("utf-8"))

    @staticmethod
    def _get_response_content():
        resp_file = os.getenv("NIM_RESPONSE_FILE")
        if resp_file and os.path.isfile(resp_file):
            with open(resp_file) as f:
                return f.read().strip()
        return DEFAULT_CR2_RESPONSE

    @staticmethod
    def _get_response_delay_seconds() -> float:
        """
        Optional fixed delay for each VLM response.

        Configure with NIM_STUB_DELAY_SECONDS (e.g. "2.5").
        """
        try:
            return max(0.0, float(os.getenv("NIM_STUB_DELAY_SECONDS", "0")))
        except (TypeError, ValueError):
            return 0.0

    def do_GET(self):
        if "/health" in self.path:
            self._send_json(200, {"status": "ready"})
        else:
            self._send_json(200, {"status": "ok"})

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length else '{}'
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        # Keep request-echo logs used by functional tests validating VLM params.
        request_fields = {
            key: data[key]
            for key in ("model", "max_tokens", "temperature", "extra_body")
            if key in data
        }
        print(f"NIM_REQUEST: {json.dumps(request_fields)}", flush=True)

        # Extract user/system text messages so functional tests can verify
        # which prompt the VLM actually received.
        prompt_texts = []
        for msg in data.get('messages', []) or []:
            content = msg.get('content')
            if isinstance(content, str):
                prompt_texts.append(content)
            elif isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get('type') == 'text':
                        prompt_texts.append(part.get('text', ''))
        if prompt_texts:
            print(f"NIM_PROMPT: {json.dumps(prompt_texts)}", flush=True)

        response_delay = self._get_response_delay_seconds()
        if response_delay > 0:
            time.sleep(response_delay)

        resp = {
            "id": data.get("id", "nim-stub-id"),
            "model": data.get("model", "nim-stub-model"),
            "created": data.get("created", 1736035200),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": self._get_response_content()
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150
            }
        }
        self._send_json(200, resp)

def main():
    port = int(os.getenv("NIM_STUB_PORT", "18081"))
    server = HTTPServer(("0.0.0.0", port), NIMStubHandler)
    print(f"NIM stub server listening on :{port}")
    server.serve_forever()

if __name__ == "__main__":
    main()



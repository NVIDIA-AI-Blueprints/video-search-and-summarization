# SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#!/usr/bin/env python3
"""
Minimal HTTP server that responds to GET /v1/models with OpenAI-style JSON.
Used by test-dev-profile.sh to test remote LLM/VLM model name resolution
(get_remote_model_name) without a real API.
"""
import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    model_id = sys.argv[2] if len(sys.argv) > 2 else "mock-remote-model"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/v1/models"):
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    json.dumps({"data": [{"id": model_id}]}).encode()
                )
            else:
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    actual_port = server.server_address[1]
    print(actual_port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()

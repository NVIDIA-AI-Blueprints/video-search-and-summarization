# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persistent HTTP server for VIOS sanity reports and evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


class ArtifactHandler(SimpleHTTPRequestHandler):
    """Serve artifacts inline by default and as attachments on request."""

    server_version = "VIOSSanityArtifacts/1.0"

    def do_GET(self):  # noqa: N802 - inherited HTTP handler API
        if urlparse(self.path).path == "/.vios-sanity-server.json":
            share_id = hashlib.sha256(
                str(Path(self.directory).resolve()).encode("utf-8")).hexdigest()
            payload = json.dumps({
                "share_id": share_id,
                "bind": self.server.server_address[0],
                "port": self.server.server_address[1],
            }).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def list_directory(self, path):
        self.send_error(403, "Directory listing is disabled")
        return None

    def end_headers(self):
        parsed = urlparse(self.path)
        download = parse_qs(parsed.query).get("download", [""])[0].lower()
        if download in {"1", "true", "yes"}:
            filename = Path(unquote(parsed.path)).name or "vios_sanity_report.pdf"
            safe_name = filename.replace('"', "")
            self.send_header("Content-Disposition", f'attachment; filename="{safe_name}"')
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def serve(directory: Path, bind: str, port: int) -> None:
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    handler = partial(ArtifactHandler, directory=str(directory))
    server = ThreadingHTTPServer((bind, port), handler)
    print(f"Serving VIOS sanity artifacts: {directory} on http://{bind}:{port}", flush=True)
    server.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--bind", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    serve(Path(args.directory), args.bind, args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

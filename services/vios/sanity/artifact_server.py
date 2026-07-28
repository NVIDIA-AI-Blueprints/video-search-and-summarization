# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Persistent HTTP server for VIOS sanity reports and evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


class ArtifactHandler(SimpleHTTPRequestHandler):
    """Serve artifacts inline by default and as attachments on request."""

    server_version = "VIOSSanityArtifacts/1.1"
    _range_remaining = None

    def do_GET(self):  # noqa: N802 - inherited HTTP handler API
        if urlparse(self.path).path == "/.vios-sanity-server.json":
            share_id = hashlib.sha256(
                str(Path(self.directory).resolve()).encode("utf-8")).hexdigest()
            payload = json.dumps({
                "protocol_version": 2,
                "capabilities": ["byte_ranges", "forced_download"],
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

    def send_head(self):
        """Open a file and honor one HTTP byte range for browser MP4 playback."""
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            self.send_error(403, "Directory listing is disabled")
            return None
        try:
            source = open(path, "rb")  # noqa: SIM115 - returned to the HTTP handler
        except OSError:
            self.send_error(404, "File not found")
            return None

        stat = os.fstat(source.fileno())
        size = stat.st_size
        start, end = 0, max(0, size - 1)
        status = 200
        requested = self.headers.get("Range")
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested.strip())
            if not match or size == 0:
                source.close()
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
            first, last = match.groups()
            if not first:
                length = int(last or "0")
                if length <= 0:
                    source.close()
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return None
                start = max(0, size - length)
            else:
                start = int(first)
            end = min(size - 1, int(last)) if last and first else size - 1
            if start >= size or end < start:
                source.close()
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return None
            status = 206

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        self.send_header("Last-Modified", self.date_time_string(stat.st_mtime))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self._range_remaining = length
            source.seek(start)
        else:
            self._range_remaining = None
        self.end_headers()
        return source

    def copyfile(self, source, outputfile):
        remaining = self._range_remaining
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)
        self._range_remaining = None

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

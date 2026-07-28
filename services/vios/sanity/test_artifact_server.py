# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import unittest
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from subprocess import CompletedProcess
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

from artifact_server import ArtifactHandler
from run_sanity import _verify_delivery
from sanity_common import SanityContext, UseCaseResult


class ArtifactServerTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        (root / "report.pdf").write_bytes(b"%PDF-test")
        (root / "evidence.mp4").write_bytes(bytes(range(256)) * 16)
        handler = partial(ArtifactHandler, directory=str(root))
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.tempdir.cleanup()

    def test_health_identifies_served_directory(self):
        with urlopen(f"{self.base}/.vios-sanity-server.json") as response:
            payload = json.loads(response.read())
        expected = hashlib.sha256(
            str(Path(self.tempdir.name).resolve()).encode("utf-8")).hexdigest()
        self.assertEqual(payload["share_id"], expected)
        self.assertEqual(payload["protocol_version"], 2)
        self.assertIn("byte_ranges", payload["capabilities"])
        self.assertNotIn("directory", payload)

    def test_directory_listing_is_disabled(self):
        with self.assertRaisesRegex(Exception, "HTTP Error 403"):
            urlopen(f"{self.base}/")

    def test_pdf_opens_inline_by_default(self):
        with urlopen(f"{self.base}/report.pdf") as response:
            self.assertEqual(response.headers.get_content_type(), "application/pdf")
            self.assertIsNone(response.headers.get("Content-Disposition"))

    def test_download_query_returns_attachment(self):
        with urlopen(f"{self.base}/report.pdf?download=1") as response:
            self.assertEqual(
                response.headers["Content-Disposition"],
                'attachment; filename="report.pdf"',
            )

    def test_mp4_byte_range_supports_browser_playback(self):
        request = Request(
            f"{self.base}/evidence.mp4",
            headers={"Range": "bytes=128-255"},
        )
        with urlopen(request) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.headers.get_content_type(), "video/mp4")
            self.assertEqual(response.headers["Accept-Ranges"], "bytes")
            self.assertEqual(response.headers["Content-Range"], "bytes 128-255/4096")
            self.assertEqual(len(response.read()), 128)

    def test_invalid_byte_range_returns_416(self):
        request = Request(
            f"{self.base}/evidence.mp4",
            headers={"Range": "bytes=9000-9100"},
        )
        with self.assertRaises(HTTPError) as error:
            urlopen(request)
        self.assertEqual(error.exception.code, 416)
        self.assertEqual(error.exception.headers["Content-Range"], "bytes */4096")

    def test_delivery_gate_checks_pdf_download_and_video_playback(self):
        result = UseCaseResult(
            name="video",
            status="PASS",
            links=[f"{self.base}/evidence.mp4"],
        )
        _verify_delivery(f"{self.base}/report.pdf", [result])

    def test_publish_uses_namespace_and_browser_readable_permissions(self):
        root = Path(self.tempdir.name)
        source = root / "source.pdf"
        source.write_bytes(b"%PDF-source")
        source.chmod(0o600)
        ctx = SanityContext(
            share_dir=root / "published",
            out_dir=root / "out",
            artifact_namespace="run-1/plan-1",
            file_server_base="http://10.0.0.8:18080",
        )
        link = ctx.publish(source, "report.pdf")
        published = root / "published/run-1/plan-1/report.pdf"
        self.assertEqual(link, "http://10.0.0.8:18080/run-1/plan-1/report.pdf")
        self.assertEqual(published.stat().st_mode & 0o777, 0o644)

    @patch("sanity_common.shutil.which", return_value="/usr/bin/tool")
    @patch("sanity_common.subprocess.run")
    def test_publish_keeps_hevc_original_and_links_h264_preview(self, run, _which):
        root = Path(self.tempdir.name)
        source = root / "source.mp4"
        source.write_bytes(b"original-hevc")

        def execute(command, **_kwargs):
            if command[0] == "ffprobe":
                return CompletedProcess(command, 0, stdout="hevc\n", stderr="")
            Path(command[-1]).write_bytes(b"browser-h264")
            return CompletedProcess(command, 0, stdout="", stderr="")

        run.side_effect = execute
        ctx = SanityContext(
            share_dir=root / "published",
            out_dir=root / "out",
            file_server_base="http://10.0.0.8:18080",
        )
        link = ctx.publish(source, "evidence.mp4")
        self.assertEqual(link, "http://10.0.0.8:18080/evidence.mp4")
        self.assertEqual((root / "published/evidence.mp4").read_bytes(), b"browser-h264")
        self.assertEqual(
            (root / "published/evidence.original-hevc.mp4").read_bytes(),
            b"original-hevc",
        )


if __name__ == "__main__":
    unittest.main()

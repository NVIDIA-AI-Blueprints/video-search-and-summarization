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
from urllib.request import urlopen

from artifact_server import ArtifactHandler


class ArtifactServerTest(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        (root / "report.pdf").write_bytes(b"%PDF-test")
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


if __name__ == "__main__":
    unittest.main()

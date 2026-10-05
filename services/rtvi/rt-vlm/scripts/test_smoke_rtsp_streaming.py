# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline check of caption parsing and cleanup, including failed inference."""

import io
import json
import unittest
from unittest.mock import patch

import smoke_rtsp_streaming


class SmokeTest(unittest.TestCase):
    def test_caption_and_failure_cleanup(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                calls = []

                def respond(request, timeout):
                    calls.append(request.full_url)
                    if request.full_url.endswith("/models"):
                        body = json.dumps({"data": [{"id": "test-model"}]})
                    elif request.full_url.endswith("/stream/add"):
                        body = json.dumps({"asset_id": "test-asset"})
                    elif request.full_url.endswith("/generate_captions"):
                        payload = json.loads(request.data)
                        self.assertEqual(payload["inference_mode"], "streaming_vlm")
                        if fail:
                            raise RuntimeError("inference failed")
                        body = 'data: {"chunk_responses": [{"content": "A visible scene"}]}\n\n'
                    else:
                        body = "{}"
                    return io.BytesIO(body.encode())

                with patch("smoke_rtsp_streaming.urlopen", side_effect=respond), patch(
                    "sys.argv", ["smoke", "--endpoint", "http://example/v1",
                                 "--rtsp-url", "rtsp://example/smoke", "--captions", "1"]
                ), patch("sys.stdout", new_callable=io.StringIO):
                    if fail:
                        with self.assertRaises(RuntimeError):
                            smoke_rtsp_streaming.main()
                    else:
                        smoke_rtsp_streaming.main()
                self.assertEqual(calls[-2:], [
                    "http://example/v1/generate_captions/test-asset",
                    "http://example/v1/stream/remove",
                ])


if __name__ == "__main__":
    unittest.main()

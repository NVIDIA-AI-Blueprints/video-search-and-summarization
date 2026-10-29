# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Offline check of caption parsing and cleanup, including failed inference."""

import io
import json
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

import smoke_rtsp_streaming


class SmokeTest(unittest.TestCase):
    def test_caption_and_failure_cleanup(self):
        for fail in (False, True):
            with self.subTest(fail=fail):
                calls = []
                caption_response = None

                def respond(request, timeout):
                    nonlocal caption_response
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
                        caption_response = io.BytesIO(body.encode())
                        return caption_response
                    else:
                        if request.get_method() == "DELETE" and caption_response is not None:
                            self.assertFalse(caption_response.closed)
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

    def test_cleanup_retries_temporary_conflicts(self):
        attempts = {}

        def respond(request, timeout):
            path = request.full_url.rsplit("/v1", 1)[-1]
            attempts[path] = attempts.get(path, 0) + 1
            if path == "/models":
                body = {"data": [{"id": "test-model"}]}
            elif path == "/stream/add":
                body = {"asset_id": "test-asset"}
            elif path == "/generate_captions":
                raise RuntimeError("inference failed")
            elif attempts[path] == 1:
                raise HTTPError(request.full_url, 409, "stopping", {}, io.BytesIO())
            else:
                body = {}
            return io.BytesIO(json.dumps(body).encode())

        with patch("smoke_rtsp_streaming.urlopen", side_effect=respond), patch(
            "smoke_rtsp_streaming.time.sleep"
        ), patch(
            "sys.argv", ["smoke", "--endpoint", "http://example/v1",
                         "--rtsp-url", "rtsp://example/smoke", "--captions", "1"]
        ), patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, "inference failed"):
                smoke_rtsp_streaming.main()

        self.assertEqual(attempts["/generate_captions/test-asset"], 2)
        self.assertEqual(attempts["/stream/remove"], 2)

    def test_cleanup_failure_does_not_hide_inference_failure(self):
        def respond(request, timeout):
            if request.full_url.endswith("/models"):
                body = {"data": [{"id": "test-model"}]}
            elif request.full_url.endswith("/stream/add"):
                body = {"asset_id": "test-asset"}
            elif request.full_url.endswith("/generate_captions"):
                raise RuntimeError("inference failed")
            elif request.get_method() == "DELETE":
                raise HTTPError(request.full_url, 500, "cleanup failed", {}, io.BytesIO())
            else:
                body = {}
            return io.BytesIO(json.dumps(body).encode())

        with patch("smoke_rtsp_streaming.urlopen", side_effect=respond), patch(
            "sys.argv", ["smoke", "--endpoint", "http://example/v1",
                         "--rtsp-url", "rtsp://example/smoke"]
        ), patch("sys.stdout", new_callable=io.StringIO):
            with self.assertRaisesRegex(RuntimeError, "inference failed"):
                smoke_rtsp_streaming.main()


if __name__ == "__main__":
    unittest.main()

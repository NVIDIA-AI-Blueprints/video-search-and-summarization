# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local HTTP protocol checks; synthetic replies never reach deployed services."""
import base64
import io
import json
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import HTTPServer
from PIL import Image

from d1_protocol import MAX_BODY, decode_media
from server import create_handler


def encoded(color, size=(16, 16)):
    data = io.BytesIO()
    Image.new('RGB', size, color).save(data, format='PNG')
    return base64.b64encode(data.getvalue()).decode()


@contextmanager
def adapter(video=True):
    class FakeModel:
        calls = []

        def system_one(self, state, questions, **kwargs):
            self.calls.append((state, questions, kwargs))
            return {'answers': {'scene': {'noul': .75}}, 'usage': {'output_tokens': 0}}

    model = FakeModel()
    lock = {'model_id': 'test-only/d1-2b' if video else 'test-only/d1-3b', 'revision': 'test-revision'}
    if video:
        lock.update(input_api='media', supported_inputs=['text', 'image', 'video'])
    server = HTTPServer(('127.0.0.1', 0), create_handler(model, lock))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield model, lock, f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()


def request(url, body):
    req = urllib.request.Request(url + '/classify', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


class VideoRuntimeTests(unittest.TestCase):
    def test_ordered_video_fps_and_runtime_provenance_reach_model_and_response(self):
        question = {'scene': {'type': 'noul', 'instructions': 'Is traffic crawling?'}}
        with adapter() as (model, lock, url):
            status, result = request(url, {'state': None, 'questions': question, 'camera_id': 'local-test',
                                          'timestamp': 100, 'video': {'frames_base64': [encoded('red'), encoded('blue')], 'fps': .5}})
            self.assertEqual(status, 200)
            self.assertEqual(result['model'], lock)
            self.assertEqual(result['input'], {'type': 'video', 'frame_count': 2, 'fps': .5, 'frame_span_seconds': 2.0})
            state, sent_question, arguments = model.calls[0]
            self.assertIsNone(state)
            self.assertEqual(sent_question, question)
            video = arguments['media'][0]
            self.assertEqual(arguments['score_readout'], 'mean')
            self.assertEqual(video['type'], 'video')
            self.assertEqual(video['fps'], .5)
            self.assertEqual([frame.getpixel((0, 0)) for frame in video['video']], [(255, 0, 0), (0, 0, 255)])

    def test_legacy_image_api_and_new_media_api_remain_distinct(self):
        for video in (False, True):
            with self.subTest(video=video), adapter(video) as (model, lock, url):
                status, result = request(url, {'questions': {'scene': {}}, 'image_base64': encoded('red')})
                self.assertEqual(status, 200)
                self.assertEqual(result['input']['type'], 'image')
                self.assertEqual(list(model.calls[0][2]), ['media', 'score_readout'] if video else ['images'])
                with urllib.request.urlopen(url + '/health') as response:
                    health = json.load(response)
                self.assertEqual(health['model'], lock)
                self.assertEqual('video' in health['supported_inputs'], video)

    def test_legacy_model_rejects_video_before_inference(self):
        with adapter(False) as (model, lock, url):
            status, _ = request(url, {'questions': {'scene': {}}, 'video': {'frames_base64': [encoded('red')] * 2, 'fps': 1}})
            self.assertEqual(status, 400)
            self.assertEqual(model.calls, [])

    def test_video_contract_rejects_ambiguous_or_invalid_timing_and_dimensions(self):
        frames = [encoded('red'), encoded('blue')]
        for fps in (0, -1, float('nan'), float('inf'), True, '1', 121):
            with self.subTest(fps=fps), self.assertRaises(ValueError):
                decode_media({'video': {'frames_base64': frames, 'fps': fps}}, True)
        invalid = [
            {'video': {'frames_base64': frames[:1], 'fps': 1}},
            {'video': {'frames_base64': frames * 9, 'fps': 1}},
            {'video': {'frames_base64': [encoded('red'), encoded('blue', (12, 12))], 'fps': 1}},
            {'image_base64': frames[0], 'video': {'frames_base64': frames, 'fps': 1}},
            {'video': {'frames_base64': [None, frames[0]], 'fps': 1}},
        ]
        for body in invalid:
            with self.subTest(body=list(body)), self.assertRaises(ValueError):
                decode_media(body, True)

    def test_request_limit_rejects_before_reading_large_body(self):
        with adapter() as (model, lock, url):
            req = urllib.request.Request(url + '/classify', b'{}', {'Content-Length': str(MAX_BODY + 1)})
            with self.assertRaises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(req, timeout=3)
            self.assertEqual(error.exception.code, 413)
            self.assertEqual(model.calls, [])


if __name__ == '__main__':
    unittest.main()

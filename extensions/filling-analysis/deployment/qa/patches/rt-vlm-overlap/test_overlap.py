# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Focused tests execute production functions without importing GPU dependencies."""
import ast
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parent


def load_method(filename, name, extra=None):
    tree = ast.parse((ROOT / 'services/rtvi/rt-vlm/src' / ('vlm_pipeline' if filename == 'vlm_pipeline.py' else 'server') / filename).read_text())
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == name]
    assert len(candidates) == 1
    method = candidates[0]
    method.decorator_list = []
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = dict(extra or {})
    exec(compile(module, filename, 'exec'), namespace)
    return namespace[name]


class RequestRejected(Exception):
    def __init__(self, message, code, status):
        super().__init__(message)
        self.status = status
        self.code = code


class ValidatedBeforeState(Exception):
    pass


def state_creation_boundary():
    raise ValidatedBeforeState()


class OverlapTests(unittest.TestCase):
    def test_live_window_validation_before_state_creation(self):
        method = load_method('rtvi_stream_handler.py', '_create_rtsp_vlm_captions_request', {
            'ServiceException': RequestRejected, 'RequestInfo': state_creation_boundary,
        })
        pipeline = SimpleNamespace(ensure_model_available=lambda: None)
        handler = SimpleNamespace(_vlm_pipeline=pipeline)
        for duration, overlap in [(10, 0), (10, 5), (10, 9), (1, 0)]:
            with self.subTest(duration=duration, overlap=overlap):
                with self.assertRaises(ValidatedBeforeState):
                    method(handler, None, SimpleNamespace(chunk_duration=duration, chunk_overlap_duration=overlap))
        for duration, overlap in [(10, -1), (10, 10), (10, 11), (0, 0), (-1, 0)]:
            with self.subTest(duration=duration, overlap=overlap):
                with self.assertRaises(RequestRejected) as exc:
                    method(handler, None, SimpleNamespace(chunk_duration=duration, chunk_overlap_duration=overlap))
                self.assertEqual(exc.exception.status, 400)
                self.assertEqual(exc.exception.code, 'BadParameter')

    def test_decoder_signature_prevents_sharing_different_overlap(self):
        method = load_method('vlm_pipeline.py', '_live_stream_decode_signature')
        config = dict(chunk_duration=10, chunk_overlap_duration=0,
                      num_frames_per_second_or_fixed_frames_chunk=2, use_fps_for_chunking=True,
                      vlm_input_width=1280, vlm_input_height=720, enable_audio=False)
        baseline = method(None, SimpleNamespace(**config))
        self.assertEqual(baseline, method(None, SimpleNamespace(**config)))
        self.assertNotEqual(baseline, method(None, SimpleNamespace(**dict(config, chunk_overlap_duration=5))))
        self.assertNotEqual(baseline, method(None, SimpleNamespace(**dict(config, chunk_duration=5))))
        self.assertNotEqual(baseline, method(None, SimpleNamespace(**dict(config, vlm_input_width=800))))

    def test_live_decoder_receives_requested_overlap(self):
        tree = ast.parse((ROOT / 'services/rtvi/rt-vlm/src/vlm_pipeline/vlm_pipeline.py').read_text())
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Attribute) and node.func.attr == 'stream'
                 and isinstance(node.func.value, ast.Name) and node.func.value.id == 'fgetter']
        self.assertEqual(len(calls), 1)
        keyword = next(k for k in calls[0].keywords if k.arg == 'chunk_overlap_duration')
        expression = ast.Expression(body=keyword.value)
        self.assertEqual(eval(compile(expression, 'forwarded-overlap', 'eval'), {'vlm_query': SimpleNamespace(chunk_overlap_duration=5)}), 5)
        self.assertEqual(eval(compile(expression, 'forwarded-overlap', 'eval'), {'vlm_query': SimpleNamespace(chunk_overlap_duration=0)}), 0)

    def test_both_files_compile(self):
        for filename in ('vlm_pipeline.py', 'rtvi_stream_handler.py'):
            compile((ROOT / 'services/rtvi/rt-vlm/src' / ('vlm_pipeline' if filename == 'vlm_pipeline.py' else 'server') / filename).read_text(), filename, 'exec')


if __name__ == '__main__':
    unittest.main(verbosity=2)

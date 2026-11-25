# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Local serialized D1 adapter supporting bounded image or sampled-video input.

POST /classify: state, questions, image_base64 OR video={frames_base64, fps}.
No RTSP connection, remote media fetching, or model selection comes from requests.
"""
import json
import logging
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, HTTPServer
from d1_protocol import MAX_BODY, decode_media, model_arguments

LOG = logging.getLogger(__name__)


def create_handler(model, lock, inference_context=nullcontext, synchronize=lambda: None):
    media_api = lock.get('input_api') == 'media'
    supported = lock.get('supported_inputs', ['text', 'image'])

    class Handler(BaseHTTPRequestHandler):
        def reply(self, status, payload):
            data = json.dumps(payload, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.reply(200 if self.path == '/health' else 404,
                       {'ready': True, 'model': lock, 'supported_inputs': supported}
                       if self.path == '/health' else {'error': 'not_found'})

        def do_POST(self):
            if self.path != '/classify':
                return self.reply(404, {'error': 'not_found'})
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if size <= 0 or size > MAX_BODY:
                    return self.reply(413, {'error': 'invalid_body_size'})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError('request must be an object')
                questions = body['questions']
                if not isinstance(questions, dict) or not 1 <= len(questions) <= 16:
                    raise ValueError('questions must contain 1 to 16 named questions')
                frames, fps, input_metadata = decode_media(body, 'video' in supported and media_api)
                started = time.perf_counter()
                with inference_context():
                    answer = model.system_one(body.get('state'), questions,
                                              **model_arguments(frames, fps, media_api))
                    synchronize()
                self.reply(200, {'result': answer, 'model': lock, 'input': input_metadata,
                                'camera_id': body.get('camera_id'), 'timestamp': body.get('timestamp'),
                                'processing_ms': 1000 * (time.perf_counter() - started)})
            except (ValueError, KeyError, TypeError, OSError):
                self.reply(400, {'error': 'invalid_request'})
            except Exception:
                LOG.exception('D1 inference failed')
                self.reply(500, {'error': 'inference_failed'})

        def log_message(self, fmt, *args):
            pass  # Do not log images or camera metadata.
    return Handler


def main():
    import torch
    from d1_runtime import load_model
    logging.basicConfig(level=logging.INFO)
    model, lock = load_model()
    server = HTTPServer(('0.0.0.0', 8001), create_handler(model, lock, torch.inference_mode, torch.cuda.synchronize))
    server.timeout = 30
    print(f"D1 ready on container port 8001: {lock['model_id']} {lock['revision']}", flush=True)
    server.serve_forever()


if __name__ == '__main__':
    main()

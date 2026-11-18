# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bounded local image/video request decoding; no model or network imports."""
import base64
import io
import math
from PIL import Image

MAX_BODY = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 20_000_000
MAX_VIDEO_PIXELS = 32_000_000
MAX_VIDEO_FRAMES = 16
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def decode_image(encoded):
    if not isinstance(encoded, str) or not encoded:
        raise ValueError('image must be a nonempty base64 string')
    data = base64.b64decode(encoded, validate=True)
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise ValueError('image too large')
            return image.convert('RGB')
    except Image.DecompressionBombError as error:
        raise ValueError('image too large') from error


def decode_media(body, video_supported=False):
    """Return image(s), optional video FPS, and truthful input metadata."""
    if not isinstance(body, dict):
        raise ValueError('request must be an object')
    image = body.get('image_base64')
    video = body.get('video')
    if image is not None and video is not None:
        raise ValueError('image and video are mutually exclusive')
    if video is not None:
        if not video_supported:
            raise ValueError('loaded model does not support video')
        if not isinstance(video, dict):
            raise ValueError('video must be an object')
        encoded_frames = video.get('frames_base64')
        fps = video.get('fps')
        if not isinstance(encoded_frames, list) or not 2 <= len(encoded_frames) <= MAX_VIDEO_FRAMES:
            raise ValueError('video requires 2 to 16 frames')
        if isinstance(fps, bool) or not isinstance(fps, (int, float)) or not math.isfinite(fps) or not 0 < fps <= 120:
            raise ValueError('invalid video fps')
        frames = []
        pixels = 0
        for encoded in encoded_frames:
            frame = decode_image(encoded)
            pixels += frame.width * frame.height
            if pixels > MAX_VIDEO_PIXELS:
                raise ValueError('video pixel limit exceeded')
            if frames and frame.size != frames[0].size:
                raise ValueError('all video frames must have the same dimensions')
            frames.append(frame)
        return frames, float(fps), {'type': 'video', 'frame_count': len(frames), 'fps': float(fps),
                                   'frame_span_seconds': (len(frames) - 1) / fps}
    if image is not None:
        return [decode_image(image)], None, {'type': 'image', 'frame_count': 1}
    return None, None, {'type': 'text', 'frame_count': 0}


def model_arguments(frames, fps, media_api):
    if media_api:
        # Preserve the expected-level semantics of existing D1 score rules.
        # d1-2B otherwise defaults to a median level, unlike d1-3B.
        return {'media': [{'type': 'video', 'video': frames, 'fps': fps}] if fps is not None else frames,
                'score_readout': 'mean'}
    return {'images': frames}

# SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Publish a recorded H.264 sample without decoding or re-encoding it."""

import os
import sys

from imageio_ffmpeg import get_ffmpeg_exe

url = os.environ["OUTPUT_RTSP"]
ffmpeg = get_ffmpeg_exe()
args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
if sys.argv[1:] == ["health"]:
    args.extend(
        ["-rtsp_transport", "tcp", "-i", url, "-map", "0:v:0", "-frames:v", "1", "-c", "copy", "-f", "null", "-"]
    )
else:
    args.extend(
        [
            "-re", "-stream_loop", "-1", "-i", "/input.mp4", "-map", "0:v:0", "-an", "-c:v", "copy",
            "-bsf:v", "h264_mp4toannexb", "-f", "rtsp", "-rtsp_transport", "tcp", url,
        ]
    )
os.execv(ffmpeg, args)

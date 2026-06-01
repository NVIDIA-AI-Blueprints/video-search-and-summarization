# SPDX-FileCopyrightText: Copyright (c) 2024-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import base64
import json
import mimetypes
import os
import shutil
import sys
import time
from datetime import datetime, timezone

API_PREFIX = "/v1"

# Max raw bytes for --base64 video embedding: must fit under api_models.common
# MAX_DATA_URL_SERIALIZED_LENGTH after `data:<mime>;base64,` + base64 (~4/3 expansion).
_DATA_URI_HEADER_BUDGET = 256
MAX_FILE_SIZE_BYTES = ((4 * 1024 * 1024 * 1024 - _DATA_URI_HEADER_BUDGET) * 3) // 4

# MIME fallbacks for data: URLs when mimetypes.guess_type returns None.
# Applied after mimetypes.types_map.get(ext) for common image/video extensions.
IMAGE_MIME_MAP = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".ico": "image/vnd.microsoft.icon",
    ".jpe": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}
VIDEO_MIME_MAP = {
    ".3g2": "video/3gpp2",
    ".3gp": "video/3gpp",
    ".avi": "video/x-msvideo",
    ".flv": "video/x-flv",
    ".m1v": "video/mpeg",
    ".m4v": "video/x-m4v",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp4": "video/mp4",
    ".mpe": "video/mpeg",
    ".mpeg": "video/mpeg",
    ".mpg": "video/mpeg",
    ".qt": "video/quicktime",
    ".webm": "video/webm",
    ".wmv": "video/x-ms-wmv",
}
VIDEO_EXTS = frozenset(VIDEO_MIME_MAP)

MANDATORY_ARGUMENTS = "Mandatory Arguments"
OPTIONAL_ARGUMENTS = "Optional Arguments"

try:
    import requests
    import sseclient
    from tabulate import tabulate
    from tqdm import tqdm
except ImportError:
    print("Dependencies missing. Install using:")
    print("python3 -m pip install sseclient-py requests tabulate tqdm pyyaml")
    sys.exit(-1)


def convert_seconds_to_string(seconds_str, need_hour=False, millisec=False):
    try:
        seconds = float(seconds_str)
    except (TypeError, ValueError):
        # Fallback to string representation if the value is not numeric
        return str(seconds_str)

    seconds_in = seconds
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)

    if need_hour or hours > 0:
        ret_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    else:
        ret_str = f"{minutes:02d}:{seconds:02d}"

    if millisec:
        ms = int((seconds_in * 100) % 100)
        ret_str += f".{ms:02d}"
    return ret_str


def format_ntp_timestamp(ntp_timestamp):
    """Format NTP timestamp to a more readable format for display"""
    try:
        # Parse the NTP timestamp (format: 2024-05-30T01:41:25.000Z)
        from datetime import datetime

        dt = datetime.fromisoformat(ntp_timestamp.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except Exception:
        # Fallback to original format if parsing fails
        return ntp_timestamp


def add_common_args(parser: argparse.ArgumentParser):
    g = parser.add_argument_group("Server Options")
    g.add_argument(
        "--backend",
        type=str,
        default=os.environ.get("RTVI_BACKEND", "http://localhost:8000"),
        help="RTVI server address",
    )

    g = parser.add_argument_group("Other Options")
    g.add_argument(
        "--print-curl-command",
        action="store_true",
        help="Print corresponding curl command and exit",
    )


def get_parser():

    parser = argparse.ArgumentParser(
        description="RTVI CLI Client",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--backend",
        type=str,
        help="Backend server address and port",
        default=os.environ.get("RTVI_BACKEND", "http://localhost:8000"),
    )

    subparsers = parser.add_subparsers(help="Request to execute", dest="request")
    subparsers.required = True

    add_file = subparsers.add_parser(
        "add-file",
        help="Add/upload an file to the RTVI server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = add_file.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("file", type=str, help="File to add")

    opt_args = add_file.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument(
        "--add-as-path",
        help="Add the file as a path instead of uploading the file",
        action="store_true",
    )
    opt_args.add_argument(
        "--is-image", help="The file to be added is an image", action="store_true"
    )
    opt_args.add_argument(
        "--creation-time", help="Creation time of the file in ISO 8601 format", type=str
    )
    opt_args.add_argument("--stream-id", help="ID of the stream to add the file to", type=str)
    opt_args.add_argument(
        "--sensor-name",
        help="User-defined sensor name. Defaults to empty string if not provided.",
        type=str,
    )

    add_common_args(add_file)

    list_files = subparsers.add_parser(
        "list-files", help="List all files", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    add_common_args(list_files)

    get_file_info = subparsers.add_parser(
        "file-info",
        help="Get information about a file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = get_file_info.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("file_id", type=str, help="ID of the file to get info of")
    add_common_args(get_file_info)

    file_content = subparsers.add_parser(
        "file-content",
        help="Get content of a file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = file_content.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("file_id", type=str, help="ID of the file to get content of")
    add_common_args(file_content)

    delete_file = subparsers.add_parser(
        "delete-file",
        help="Delete a file from the RTVI server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = delete_file.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("file_id", type=str, help="ID of the file to delete")

    add_common_args(delete_file)

    generate_captions = subparsers.add_parser(
        "generate-captions",
        help="Generate captions for an already added file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = generate_captions.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument(
        "--id",
        required=True,
        action="append",
        type=str,
        help="ID of the file / live stream to generate VLM captions for",
    )
    mandatory_args.add_argument(
        "--model", required=True, type=str, help="The VLM model to use for generating captions"
    )

    opt_args = generate_captions.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument(
        "--stream", help="Stream the output using server side events", action="store_true"
    )
    opt_args.add_argument("--chunk-duration", help="Chunk duration in seconds", type=int)
    opt_args.add_argument(
        "--chunk-overlap-duration", help="Chunk overlap duration in seconds", type=int
    )
    opt_args.add_argument("--prompt", help="Prompt to use for VLM.", type=str)
    opt_args.add_argument("--system-prompt", help="System prompt for the VLM.", type=str)
    opt_args.add_argument(
        "--file-start-offset",
        help="Offset in the media file to start processing from, in seconds",
        type=str,
    )
    opt_args.add_argument(
        "--file-end-offset",
        help="Time in the media file to end processing at, in seconds",
        type=str,
    )
    opt_args.add_argument(
        "--model-temperature", help="Temperature to use while generating from LLM", type=float
    )
    opt_args.add_argument(
        "--model-top-p", help="Top-P to use while generating from LLM", type=float
    )
    opt_args.add_argument("--model-top-k", help="Top-K to use while generating from LLM", type=int)
    opt_args.add_argument(
        "--model-max-tokens", help="Max tokens to use while generating from LLM", type=int
    )
    opt_args.add_argument("--model-seed", help="Seed to use while generating from LLM", type=int)
    opt_args.add_argument(
        "--response-format",
        help="Format of the model output",
        choices=["json_object", "text"],
        default="text",
        type=str,
    )
    opt_args.add_argument(
        "--num-frames-per-second-or-fixed-frames-chunk",
        help="Number of frames per chunk to use for the VLM",
        type=float,
    )
    opt_args.add_argument(
        "--use-fps-for-chunking",
        help=(
            "Use FPS for chunking. If True, num-frames-per-second-or-fixed-frames-chunk "
            "is interpreted as FPS for sampling frames, else as fixed number of frames per chunk"
        ),
        action="store_true",
    )
    opt_args.add_argument("--vlm-input-width", help="VLM Input Width", type=int)
    opt_args.add_argument("--vlm-input-height", help="VLM Input Height", type=int)
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning for VLM captions generation",
        action="store_true",
    )
    opt_args.add_argument(
        "--enable-audio",
        help="Enable audio processing (pass audio to VLM for native audio models)",
        action="store_true",
    )
    opt_args.add_argument(
        "--url",
        help="Video/image URL (http/https/s3/file://). When set, --id is used as the asset key.",
        type=str,
    )
    opt_args.add_argument(
        "--media-type",
        help="Media type for the URL input",
        choices=["video", "image"],
        default="video",
        type=str,
    )
    opt_args.add_argument(
        "--creation-time",
        help="Creation time in ISO 8601 format (offsets frame timestamps in response)",
        type=str,
    )
    add_common_args(generate_captions)

    # NIM-compatible endpoints
    chat_completions = subparsers.add_parser(
        "chat-completions",
        help="OpenAI-compatible chat completions endpoint",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = chat_completions.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument(
        "--id",
        required=False,
        default=None,
        type=str,
        help="ID of the file / live stream. Omit for text-only chat.",
    )
    mandatory_args.add_argument("--model", required=True, type=str, help="The VLM model to use")
    mandatory_args.add_argument(
        "--messages",
        required=True,
        nargs="+",
        help=(
            "Chat messages in format 'role:content' "
            "(e.g., 'user:Describe this video' or 'system:You are helpful')"
        ),
    )

    opt_args = chat_completions.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument(
        "--stream", help="Stream the output using server side events", action="store_true"
    )
    opt_args.add_argument("--chunk-duration", help="Chunk duration in seconds", type=int)
    opt_args.add_argument(
        "--chunk-overlap-duration", help="Chunk overlap duration in seconds", type=int
    )
    opt_args.add_argument(
        "--model-temperature", help="Temperature to use while generating from LLM", type=float
    )
    opt_args.add_argument(
        "--model-top-p", help="Top-P to use while generating from LLM", type=float
    )
    opt_args.add_argument("--model-top-k", help="Top-K to use while generating from LLM", type=int)
    opt_args.add_argument(
        "--model-max-tokens", help="Max tokens to use while generating from LLM", type=int
    )
    opt_args.add_argument("--model-seed", help="Seed to use while generating from LLM", type=int)
    opt_args.add_argument(
        "--response-format",
        help="Format of the model output",
        choices=["json_object", "text"],
        default="text",
        type=str,
    )
    opt_args.add_argument(
        "--num-frames-per-second-or-fixed-frames-chunk",
        help="Number of frames per chunk to use for the VLM",
        type=float,
    )
    opt_args.add_argument(
        "--use-fps-for-chunking",
        help=(
            "Use FPS for chunking. If True, num-frames-per-second-or-fixed-frames-chunk "
            "is interpreted as FPS for sampling frames, else as fixed number of frames per chunk"
        ),
        action="store_true",
    )
    opt_args.add_argument("--vlm-input-width", help="VLM Input Width", type=int)
    opt_args.add_argument("--vlm-input-height", help="VLM Input Height", type=int)
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning for VLM captions generation",
        action="store_true",
    )
    opt_args.add_argument(
        "--enable-audio",
        help="Enable transcription of the audio stream",
        action="store_true",
    )
    add_common_args(chat_completions)

    completions = subparsers.add_parser(
        "completions",
        help="OpenAI-compatible text completions endpoint (returns error for VLM models)",
        description=(
            "Text-only completion endpoint. Note: VLM models require video/image input, "
            "so this endpoint returns an error indicating that visual input is required. "
            "Use 'chat-completions' with --id or video_url for VLM processing instead."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = completions.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("--model", required=True, type=str, help="The model to use")
    mandatory_args.add_argument(
        "--prompt", required=True, type=str, help="Text prompt for completion"
    )
    opt_args = completions.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument("--max-tokens", help="Max tokens to generate", type=int, default=16)
    opt_args.add_argument("--temperature", help="Temperature", type=float, default=1.0)
    opt_args.add_argument("--top-p", help="Top-P", type=float, default=1.0)
    opt_args.add_argument("--top-k", help="Top-K", type=int)
    opt_args.add_argument("--stream", help="Stream the output", action="store_true")
    opt_args.add_argument("--seed", help="Seed", type=int)
    add_common_args(completions)

    get_version = subparsers.add_parser(
        "get-version",
        help="Get service version information",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(get_version)

    get_manifest = subparsers.add_parser(
        "get-manifest",
        help="Get service manifest information",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(get_manifest)

    generate_video_embeddings = subparsers.add_parser(
        "generate-video-embeddings",
        help="Generate embeddings for an already added video, image file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = generate_video_embeddings.add_argument_group(MANDATORY_ARGUMENTS)

    mandatory_args.add_argument(
        "--id",
        required=True,
        action="append",
        type=str,
        help="ID of the file / live stream to generate embeddings for",
    )
    mandatory_args.add_argument(
        "--model", required=True, type=str, help="The model to use for generating embeddings"
    )

    opt_args = generate_video_embeddings.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument(
        "--stream", help="Stream the output using server side events", action="store_true"
    )
    opt_args.add_argument("--chunk-duration", help="Chunk duration in seconds", type=int)
    opt_args.add_argument(
        "--chunk-overlap-duration", help="Chunk overlap duration in seconds", type=int
    )
    opt_args.add_argument(
        "--file-start-offset",
        help="Offset in the media file to start processing from, in seconds",
        type=str,
    )
    opt_args.add_argument(
        "--file-end-offset",
        help="Time in the media file to end processing at, in seconds",
        type=str,
    )
    opt_args.add_argument(
        "--url",
        help="URL of the video to generate embeddings for (http/https/s3). Mutually exclusive with --base64.",
        type=str,
    )
    opt_args.add_argument(
        "--base64",
        metavar="FILE",
        help=(
            "Path to a local file to encode as a base64 data URL and send inline. "
            "Mutually exclusive with --url."
        ),
        type=str,
    )
    opt_args.add_argument(
        "--media-type",
        help="Media type of the video (image / video)",
        type=str,
        choices=["image", "video"],
    )
    opt_args.add_argument(
        "--creation-time",
        help="Creation time of the file in ISO8601 format",
        type=str,
    )
    add_common_args(generate_video_embeddings)

    generate_text_embeddings = subparsers.add_parser(
        "generate-text-embeddings",
        help="Generate embeddings for a given text input",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = generate_text_embeddings.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument(
        "--text-input",
        required=True,
        action="append",
        type=str,
        help="Text input to generate embeddings for",
    )
    mandatory_args.add_argument(
        "--model", required=True, type=str, help="The model to use for generating embeddings"
    )
    add_common_args(generate_text_embeddings)

    add_live_stream = subparsers.add_parser(
        "add-live-stream",
        help="Add a live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = add_live_stream.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("live_stream_url", type=str, help="A Live Stream URL")
    mandatory_args.add_argument(
        "--description", help="Description of the live stream", type=str, required=True
    )

    opt_args = add_live_stream.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument("--username", help="Username to access the live stream", type=str)
    opt_args.add_argument("--password", help="Password to access the live stream", type=str)
    opt_args.add_argument(
        "--place-name", help="Name of the place/location where the camera is located", type=str
    )
    opt_args.add_argument(
        "--place-type", help="Type of place/location (e.g., warehouse-bay, parking-lot)", type=str
    )
    opt_args.add_argument("--place-lat", help="Latitude of the camera location", type=float)
    opt_args.add_argument("--place-lon", help="Longitude of the camera location", type=float)
    opt_args.add_argument(
        "--place-alt", help="Altitude of the camera location in meters", type=float
    )
    opt_args.add_argument(
        "--place-coordinate-x",
        help="X coordinate of the camera within the place (local coordinates)",
        type=float,
    )
    opt_args.add_argument(
        "--place-coordinate-y",
        help="Y coordinate of the camera within the place (local coordinates)",
        type=float,
    )
    opt_args.add_argument("--stream-id", help="ID of the stream to add", type=str)
    opt_args.add_argument(
        "--sensor-name",
        help="User-defined sensor name. Defaults to empty string if not provided.",
        type=str,
    )
    add_common_args(add_live_stream)

    list_live_streams = subparsers.add_parser(
        "list-live-streams",
        help="List all live streams",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(list_live_streams)

    delete_live_stream = subparsers.add_parser(
        "delete-live-stream",
        help="Delete a live stream from the RTVI server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = delete_live_stream.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("video_id", type=str, help="ID of the live-stream to delete")
    add_common_args(delete_live_stream)

    stop_live_stream_processing = subparsers.add_parser(
        "stop-live-stream-processing",
        help="Stop a live stream from generating captions and alerts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = stop_live_stream_processing.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument("stream_id", type=str, help="ID of the live-stream to stop")
    add_common_args(stop_live_stream_processing)

    stop_live_stream_embed_processing = subparsers.add_parser(
        "stop-live-stream-embed-processing",
        help="Stop a live stream from generating embeddings",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = stop_live_stream_embed_processing.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument(
        "stream_id", type=str, help="ID of the live-stream to stop generating embeddings"
    )
    add_common_args(stop_live_stream_embed_processing)

    # --- CV-compatible stream commands ---
    cv_stream_add = subparsers.add_parser(
        "stream-add",
        help="Add a stream (CV-compatible, with optional auto-inference)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = cv_stream_add.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument(
        "--camera-url", required=True, type=str, help="Stream URL (rtsp://, file://, http://)"
    )
    mandatory_args.add_argument(
        "--camera-id", required=True, type=str, help="Unique camera identifier"
    )

    opt_args = cv_stream_add.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument("--camera-name", type=str, help="Human-readable camera name")
    opt_args.add_argument(
        "--prompt", type=str, help="VLM prompt — if set, inference starts automatically"
    )
    opt_args.add_argument("--system-prompt", type=str, help="System prompt for VLM")
    opt_args.add_argument("--model", type=str, help="Model to use for inference")
    opt_args.add_argument("--chunk-duration", type=int, help="Chunk duration in seconds")
    opt_args.add_argument("--chunk-overlap-duration", type=int, help="Chunk overlap in seconds")
    opt_args.add_argument("--max-tokens", type=int, help="Max tokens per chunk")
    opt_args.add_argument("--temperature", type=float, help="Sampling temperature")
    opt_args.add_argument("--vlm-input-width", type=int, help="VLM input width")
    opt_args.add_argument("--vlm-input-height", type=int, help="VLM input height")
    opt_args.add_argument("--enable-audio", action="store_true", help="Enable audio transcription")
    opt_args.add_argument("--enable-reasoning", action="store_true", help="Enable reasoning")
    opt_args.add_argument(
        "--stream", action="store_true", help="Return SSE for live caption results"
    )
    opt_args.add_argument(
        "--response-format",
        choices=["text", "json_object"],
        default=None,
        help="Response format",
    )
    opt_args.add_argument("--source", type=str, default="cli", help="Source identifier for headers")
    add_common_args(cv_stream_add)

    cv_stream_remove = subparsers.add_parser(
        "stream-remove",
        help="Remove a stream by camera_id (CV-compatible)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = cv_stream_remove.add_argument_group(MANDATORY_ARGUMENTS)
    mandatory_args.add_argument(
        "--camera-id", required=True, type=str, help="Camera ID of the stream to remove"
    )
    add_common_args(cv_stream_remove)

    cv_stream_list = subparsers.add_parser(
        "stream-list",
        help="List all streams (CV-compatible format)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(cv_stream_list)

    list_models = subparsers.add_parser(
        "list-models",
        help="List all models",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(list_models)

    server_metrics = subparsers.add_parser(
        "server-metrics",
        help="Get RTVI server metrics",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(server_metrics)

    server_health_check = subparsers.add_parser(
        "server-health-check",
        help="Check RTVI server health",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    opt_args = server_health_check.add_argument_group(OPTIONAL_ARGUMENTS)
    opt_args.add_argument(
        "--liveness", help="Use liveness check instead of readiness (default)", action="store_true"
    )
    add_common_args(server_health_check)

    return parser


BASE_URL = ""


def get_api_url(path: str):
    # Remove leading API_PREFIX if present to avoid double prefixing
    if path.startswith(API_PREFIX):
        path = path[len(API_PREFIX) :]
    # Ensure path starts with /
    if not path.startswith("/"):
        path = "/" + path
    return BASE_URL + API_PREFIX + path


def check_err_response(response: requests.Response, exit_on_error=False):
    if response.status_code >= 400:
        err_json = response.json()
        if "message" in err_json:
            print(f"Request failed, code - {response.status_code} message - {err_json['message']}")
        elif "detail" in err_json:
            print(f"Request failed, code - {response.status_code} detail - {err_json['detail']}")
        else:
            print(f"Request failed, code - {response.status_code}")
        if exit_on_error:
            sys.exit(-1)


def do_add_file(args):
    if args.add_as_path:
        files = {"filename": (None, os.path.abspath(args.file))}
    else:
        files = {
            "file": open(args.file, "rb"),
        }
    files["purpose"] = (None, "vision")
    files["media_type"] = (None, "image" if args.is_image else "video")
    if args.creation_time:
        files["creation_time"] = (None, args.creation_time)
    if args.stream_id is not None:
        files["id"] = (None, str(args.stream_id))
    if args.sensor_name is not None:
        files["sensor_name"] = (None, args.sensor_name)

    # # Add id
    # file_id = uuid.uuid4()
    # files["id"] = (None, str(file_id))
    # print(f"Setting File ID: {file_id}", flush=True)

    # # Add current timestamp as creation time (example use)
    # creation_time = str(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
    # if not args.creation_time:
    #     files["creation_time"] = (None, creation_time)
    #     print(f"File Creation time: {creation_time}", flush=True)

    if args.print_curl_command:
        if "file" in files:
            files["file"] = (None, f"@{args.file}")
        print(
            f"""curl -i -X POST {get_api_url("/files")}"""
            + "".join([f" \\\n    -F '{k}={v[1]}'" for k, v in files.items()])
        )
        return

    result = requests.post(get_api_url("/files"), files=files, timeout=300)
    check_err_response(result, True)
    result_json = result.json()
    sensor_name_str = (
        f", sensor_name: {result_json.get('sensor_name', '')}"
        if result_json.get("sensor_name")
        else ""
    )
    print(
        "File added - id: %s, filename %s, bytes %d, purpose %s, media_type %s, creation_time %s%s"
        % (
            result_json["id"],
            result_json["filename"],
            result_json["bytes"],
            result_json["purpose"],
            result_json["media_type"],
            result_json.get("creation_time") or "N/A",
            sensor_name_str,
        )
    )


def do_list_files(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/files?purpose=vision")}""")
        return
    result = requests.get(get_api_url("/files?purpose=vision"), timeout=30)
    check_err_response(result, True)
    term_width = shutil.get_terminal_size()[0]
    files_list = result.json()
    if not files_list["data"]:
        print("No files added to the server")
        return
    print(
        tabulate(
            [
                [
                    file["id"],
                    file["filename"],
                    file["bytes"],
                    file["media_type"],
                    file["purpose"],
                    file.get("creation_time") or "N/A",
                ]
                for file in files_list["data"]
            ],
            headers=["ID", "File Name", "Size", "Media Type", "Purpose", "Creation Time"],
            tablefmt="simple_grid",
            maxcolwidths=[
                36,
                term_width - 36 - 10 - 10 - 7 - (3 * 5 + 1),
                10,
                10,
                7,
                20,
            ],
        )
    )


def do_get_file_info(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/files/" + args.file_id)}""")
        return
    result = requests.get(get_api_url("/files/" + args.file_id), timeout=30)
    check_err_response(result, True)
    result_json = result.json()
    print(
        "ID: %s\nFile name: %s\nSize: %d bytes\nPurpose: %s\nCreation Time: %s"
        % (
            result_json["id"],
            result_json["filename"],
            result_json["bytes"],
            result_json["purpose"],
            result_json.get("creation_time") or "N/A",
        )
    )


def do_get_file_content(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/files/" + args.file_id + "/content")}""")
        return
    result = requests.get(
        get_api_url("/files/" + args.file_id + "/content"), stream=True, timeout=300
    )
    check_err_response(result, True)

    file_size = int(result.headers.get("content-length", 0))
    bsize = 1024

    with tqdm(total=file_size, unit="B", unit_scale=True) as pb:
        os.makedirs("/tmp/rtvi", exist_ok=True)
        with open(f"/tmp/rtvi/{args.file_id}_content", "wb") as f:
            for data in result.iter_content(bsize):
                pb.update(len(data))
                f.write(data)
    print(f"File content written to /tmp/rtvi/{args.file_id}_content")


def do_delete_file(args):
    if args.print_curl_command:
        print(f"""curl -i -X DELETE {get_api_url("/files/" + args.file_id)}""")
        return
    result = requests.delete(get_api_url("/files/" + args.file_id), timeout=30)
    check_err_response(result, True)
    result_json = result.json()
    print("File deleted - id %s, status %r" % (result_json["id"], result_json["deleted"]))


def do_generate_text_embeddings(args):
    req_json = {
        "text_input": args.text_input,
        "model": args.model,
    }
    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/generate_text_embeddings")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    start_time = time.perf_counter()
    response = requests.post(get_api_url("/generate_text_embeddings"), json=req_json, timeout=300)
    end_time = time.perf_counter()
    check_err_response(response, True)
    result = response.json()
    print("Text Embeddings Generation finished in %.3f seconds" % (end_time - start_time))
    print("Request ID:", result["id"])
    print(
        "Request Creation Time:",
        datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    )
    print("Model:", result["model"])
    table_data = []
    for data in result["data"]:
        table_data.append(
            [
                data["text_input"],
                (data["embeddings"][:25] if len(data["embeddings"]) > 25 else data["embeddings"]),
            ]
        )
    print(
        tabulate(
            table_data,
            headers=["Text Input", "Embeddings [0:25]"],
            tablefmt="grid",
            maxcolwidths=[50, 50],
        )
    )


def do_generate_video_embeddings(args):
    if args.url and args.base64:
        print("Error: --url and --base64 are mutually exclusive.", file=sys.stderr)
        sys.exit(1)

    url = args.url
    if args.base64:
        file_path = args.base64
        if not os.path.isfile(file_path):
            print(f"Error: file not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        file_size = os.path.getsize(file_path)
        if file_size > MAX_FILE_SIZE_BYTES:
            print(
                "Error: file too large for --base64 inline upload "
                f"({file_size} bytes; max {MAX_FILE_SIZE_BYTES} bytes, "
                f"~{MAX_FILE_SIZE_BYTES / (1024 * 1024):.0f} MiB). "
                "Use --url with http(s), s3, or file://, or upload via /v1/files.",
                file=sys.stderr,
            )
            sys.exit(1)
        mime_type, _ = mimetypes.guess_type(file_path)
        if mime_type is None:
            ext = os.path.splitext(file_path)[1].lower()
            mime_type = mimetypes.types_map.get(ext)
            if mime_type is None:
                mime_type = IMAGE_MIME_MAP.get(ext)
            if mime_type is None and ext in VIDEO_EXTS:
                mime_type = VIDEO_MIME_MAP.get(ext)
            if mime_type is None:
                mime_type = "application/octet-stream"
        with open(file_path, "rb") as fh:
            encoded = base64.b64encode(fh.read()).decode()
        url = f"data:{mime_type};base64,{encoded}"

    req_json = {
        "id": args.id,
        "model": args.model,
    }

    if url:
        req_json["url"] = url
    if args.media_type:
        req_json["media_type"] = args.media_type
    if args.creation_time:
        req_json["creation_time"] = args.creation_time
    if args.chunk_duration is not None:
        req_json["chunk_duration"] = args.chunk_duration
    if args.chunk_overlap_duration is not None:
        req_json["chunk_overlap_duration"] = args.chunk_overlap_duration

    # # Add current timestamp as creation time (example use)
    # creation_time = str(datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z")
    # if not args.creation_time:
    #     req_json["creation_time"] = creation_time
    #     print(f"File Creation time: {creation_time}", flush=True)

    media_info = {}
    if args.file_start_offset is not None:
        media_info["type"] = "offset"
        media_info["start_offset"] = args.file_start_offset
    if args.file_end_offset is not None:
        media_info["type"] = "offset"
        media_info["end_offset"] = args.file_end_offset

    if media_info:
        req_json["media_info"] = media_info

    if args.stream:
        req_json["stream"] = True
        req_json["stream_options"] = {"include_usage": True}

    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/generate_video_embeddings")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(
        get_api_url("/generate_video_embeddings"),
        json=req_json,
        stream=args.stream,
        timeout=(10, 600),
    )
    check_err_response(response, True)
    if args.stream:
        client = sseclient.SSEClient(response)
        first_response = True
        try:
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("Embeddings Generation Complete")
                    continue
                result = json.loads(data)
                if first_response:
                    print("Request ID:", result["id"])
                    print(
                        "Request Creation Time:",
                        datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    )
                    print("Model:", result["model"])
                    print("----------------------------------------")
                    first_response = False
                if result.get("media_info", None) and result["media_info"]["type"] == "offset":
                    print(
                        "Media start offset: "
                        + convert_seconds_to_string(result["media_info"]["start_offset"])
                    )
                    print(
                        "Media end offset: "
                        + convert_seconds_to_string(result["media_info"]["end_offset"])
                    )
                if result.get("media_info", None) and result["media_info"]["type"] == "timestamp":
                    print(f"Media start timestamp: {result['media_info']['start_timestamp']}")
                    print(f"Media end timestamp: {result['media_info']['end_timestamp']}")

                # Display chunk responses if available in streaming response
                if "chunk_responses" in result and result["chunk_responses"]:
                    print("Embedding Response:")
                    for chunk in result["chunk_responses"]:
                        start_time = chunk["start_time"]
                        end_time = chunk["end_time"]
                        if "T" in start_time:  # NTP timestamp format
                            start_time = format_ntp_timestamp(start_time)
                            end_time = format_ntp_timestamp(end_time)
                        print(f"[{start_time} - {end_time}] Embeddings:{chunk['embeddings']}")

                if result.get("usage"):
                    print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
                    print(f"Processing Time: {result['usage']['query_processing_time']} seconds")
                print("----------------------------------------")
        except KeyboardInterrupt:
            print("User interrupted")
            response.close()
    else:
        result = response.json()
        print("Embeddings Generation finished")
        print("Request ID:", result["id"])
        print(
            "Request Creation Time:",
            datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        print("Model:", result["model"])
        if result.get("media_info", None) and result["media_info"]["type"] == "offset":
            print(
                "Media start offset: "
                + convert_seconds_to_string(result["media_info"]["start_offset"])
            )
            print(
                "Media end offset: " + convert_seconds_to_string(result["media_info"]["end_offset"])
            )

        if result.get("usage", None):
            print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
            print(f"Processing Time: {result['usage']['query_processing_time']} seconds")
        else:
            print("No usage information available.")

        # Display chunk responses in table format if available
        if "chunk_responses" in result and result["chunk_responses"]:
            print("\nEmbedding Responses (by chunk):")
            print("=" * 80)

            table_data = []
            for i, chunk in enumerate(result["chunk_responses"], 1):
                # Format timestamps for better readability
                start_time = chunk["start_time"]
                end_time = chunk["end_time"]
                if "T" in start_time:  # NTP timestamp format
                    start_time = format_ntp_timestamp(start_time)
                    end_time = format_ntp_timestamp(end_time)

                table_data.append(
                    [
                        i,
                        start_time,
                        end_time,
                        (
                            chunk["embeddings"][:25]
                            if len(chunk["embeddings"]) > 25
                            else chunk["embeddings"]
                        ),
                    ]
                )

            print(
                tabulate(
                    table_data,
                    headers=["Chunk", "Start Time", "End Time", "Video Embeddings [0:25]"],
                    tablefmt="grid",
                    maxcolwidths=[5, 10, 10, 50, 50],
                )
            )
        else:
            print("No response content available.")


def do_generate_captions(args):
    req_json = {
        "id": args.id,
        "model": args.model,
        "response_format": {"type": args.response_format},
    }

    if args.model_temperature is not None:
        req_json["temperature"] = args.model_temperature
    if args.model_seed is not None:
        req_json["seed"] = args.model_seed
    if args.model_top_p is not None:
        req_json["top_p"] = args.model_top_p
    if args.model_top_k is not None:
        req_json["top_k"] = args.model_top_k
    if args.model_max_tokens is not None:
        req_json["max_tokens"] = args.model_max_tokens

    if args.chunk_duration is not None:
        req_json["chunk_duration"] = args.chunk_duration
    if args.chunk_overlap_duration is not None:
        req_json["chunk_overlap_duration"] = args.chunk_overlap_duration

    if args.prompt:
        req_json["prompt"] = args.prompt
    if args.system_prompt:
        req_json["system_prompt"] = args.system_prompt
    if args.num_frames_per_second_or_fixed_frames_chunk is not None:
        req_json["num_frames_per_second_or_fixed_frames_chunk"] = (
            args.num_frames_per_second_or_fixed_frames_chunk
        )
    if args.use_fps_for_chunking:
        req_json["use_fps_for_chunking"] = True
    if args.vlm_input_width is not None:
        req_json["vlm_input_width"] = args.vlm_input_width
    if args.vlm_input_height is not None:
        req_json["vlm_input_height"] = args.vlm_input_height
    if args.enable_reasoning:
        req_json["enable_reasoning"] = args.enable_reasoning
    if getattr(args, "enable_audio", False):
        req_json["enable_audio"] = True

    # URL-based processing (LVS)
    if getattr(args, "url", None):
        req_json["url"] = args.url
    if getattr(args, "media_type", None):
        req_json["media_type"] = args.media_type
    if getattr(args, "creation_time", None):
        req_json["creation_time"] = args.creation_time

    media_info = {}
    if args.file_start_offset is not None:
        media_info["type"] = "offset"
        media_info["start_offset"] = args.file_start_offset
    if args.file_end_offset is not None:
        media_info["type"] = "offset"
        media_info["end_offset"] = args.file_end_offset

    if media_info:
        req_json["media_info"] = media_info

    if args.stream:
        req_json["stream"] = True
        req_json["stream_options"] = {"include_usage": True}

    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/generate_captions")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(
        get_api_url("/generate_captions"),
        json=req_json,
        stream=args.stream,
        timeout=(10, 600),
    )
    check_err_response(response, True)
    if args.stream:
        client = sseclient.SSEClient(response)
        first_response = True
        try:
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("Captions Generation Complete", flush=True)
                    continue
                result = json.loads(data)
                if first_response:
                    print("Request ID:", result["id"], flush=True)
                    print(
                        "Request Creation Time:",
                        datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                        flush=True,
                    )
                    print("Model:", result["model"], flush=True)
                    print("----------------------------------------", flush=True)
                    first_response = False
                if result.get("media_info", None) and result["media_info"]["type"] == "offset":
                    print(
                        "Media start offset: "
                        + convert_seconds_to_string(result["media_info"]["start_offset"]),
                        flush=True,
                    )
                    print(
                        "Media end offset: "
                        + convert_seconds_to_string(result["media_info"]["end_offset"]),
                        flush=True,
                    )
                if result.get("media_info", None) and result["media_info"]["type"] == "timestamp":
                    print(
                        f"Media start timestamp: {result['media_info']['start_timestamp']}",
                        flush=True,
                    )
                    print(
                        f"Media end timestamp: {result['media_info']['end_timestamp']}",
                        flush=True,
                    )

                # Display chunk responses as they arrive
                if "chunk_responses" in result and result["chunk_responses"]:
                    for chunk in result["chunk_responses"]:
                        start_time = chunk["start_time"]
                        end_time = chunk["end_time"]
                        if "T" in start_time:  # NTP timestamp format
                            start_time = format_ntp_timestamp(start_time)
                            end_time = format_ntp_timestamp(end_time)
                        print(f"[{start_time} - {end_time}] {chunk['content']}", flush=True)

                        if chunk.get("reasoning_description"):
                            print(f"Reasoning: {chunk['reasoning_description']}", flush=True)

                if result.get("usage"):
                    print(
                        f"Chunks processed: {result['usage']['total_chunks_processed']}",
                        flush=True,
                    )
                    print(
                        f"Processing Time: {result['usage']['query_processing_time']} seconds",
                        flush=True,
                    )
                    if result["usage"].get("total_tokens") is not None:
                        usage = result["usage"]
                        print(
                            f"Usage: {{'prompt_tokens': {usage['prompt_tokens']}, "
                            f"'completion_tokens': {usage['completion_tokens']}, "
                            f"'total_tokens': {usage['total_tokens']}}}",
                            flush=True,
                        )
                print("----------------------------------------", flush=True)
        except KeyboardInterrupt:
            print("User interrupted")
            response.close()
    else:
        result = response.json()
        print("Captions Generation finished")
        print("Request ID:", result["id"])
        print(
            "Request Creation Time:",
            datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        print("Model:", result["model"])
        if result.get("media_info", None) and result["media_info"]["type"] == "offset":
            print(
                "Media start offset: "
                + convert_seconds_to_string(result["media_info"]["start_offset"])
            )
            print(
                "Media end offset: " + convert_seconds_to_string(result["media_info"]["end_offset"])
            )
        print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
        print(f"Processing Time: {result['usage']['query_processing_time']} seconds")
        if result.get("usage", {}).get("total_tokens") is not None:
            print(f"Usage: {result['usage']}")

        # Display chunk responses in table format if available
        if "chunk_responses" in result and result["chunk_responses"]:
            print("\nCaption Responses (by chunk):")
            print("=" * 80)

            table_data = []
            for i, chunk in enumerate(result["chunk_responses"], 1):
                # Format timestamps for better readability
                start_time = chunk["start_time"]
                end_time = chunk["end_time"]
                if "T" in start_time:  # NTP timestamp format
                    start_time = format_ntp_timestamp(start_time)
                    end_time = format_ntp_timestamp(end_time)

                # Get reasoning description if available
                reasoning = chunk.get("reasoning_description", "")
                reasoning_display = (
                    (reasoning[:100] + "..." if len(reasoning) > 100 else reasoning)
                    if reasoning
                    else "N/A"
                )

                table_data.append(
                    [
                        i,
                        start_time,
                        end_time,
                        (
                            chunk["content"][:100] + "..."
                            if len(chunk["content"]) > 100
                            else chunk["content"]
                        ),
                        reasoning_display,
                    ]
                )

            print(
                tabulate(
                    table_data,
                    headers=["Chunk", "Start Time", "End Time", "Raw Caption", "Reasoning"],
                    tablefmt="grid",
                    maxcolwidths=[5, 10, 10, 50, 50],
                )
            )

            # Display full reasoning descriptions if available
            reasoning_chunks = [
                chunk for chunk in result["chunk_responses"] if chunk.get("reasoning_description")
            ]
            if reasoning_chunks:
                print("\n" + "=" * 80)
                print("REASONING DESCRIPTIONS")
                print("=" * 80)
                for i, chunk in enumerate(reasoning_chunks, 1):
                    start_time = chunk["start_time"]
                    end_time = chunk["end_time"]
                    if "T" in start_time:  # NTP timestamp format
                        start_time = format_ntp_timestamp(start_time)
                        end_time = format_ntp_timestamp(end_time)

                    print(f"\nChunk {i} ({start_time} - {end_time}):")
                    print("-" * 40)
                    print(chunk["reasoning_description"])
                    print("-" * 40)
        else:
            print("No response content available.")


def do_add_live_stream(args):
    stream_data = {
        "liveStreamUrl": args.live_stream_url,
    }
    if args.description:
        stream_data["description"] = args.description
    if args.username:
        stream_data["username"] = args.username
    if args.password:
        stream_data["password"] = args.password
    if args.place_name:
        stream_data["place_name"] = args.place_name
    if args.place_type:
        stream_data["place_type"] = args.place_type
    if args.place_lat is not None:
        stream_data["place_lat"] = args.place_lat
    if args.place_lon is not None:
        stream_data["place_lon"] = args.place_lon
    if args.place_alt is not None:
        stream_data["place_alt"] = args.place_alt
    if args.place_coordinate_x is not None:
        stream_data["place_coordinate_x"] = args.place_coordinate_x
    if args.place_coordinate_y is not None:
        stream_data["place_coordinate_y"] = args.place_coordinate_y
    if args.stream_id is not None:
        stream_data["id"] = str(args.stream_id)
    if args.sensor_name is not None:
        stream_data["sensor_name"] = args.sensor_name

    # # Add id
    # stream_id = uuid.uuid4()
    # stream_data["id"] = str(stream_id)
    # print(f"Setting Stream ID: {stream_id}", flush=True)

    req_json = {"streams": [stream_data]}

    if args.print_curl_command:
        print(f'curl -i -X POST {get_api_url("/streams/add")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    result = requests.post(get_api_url("/streams/add"), json=req_json, timeout=30)
    check_err_response(result, True)
    result_json = result.json()
    if result_json.get("errors") and len(result_json["errors"]) > 0:
        error = result_json["errors"][0]
        raise Exception(f"Failed to add live stream: {error.get('error', 'Unknown error')}")
    if result_json.get("results") and len(result_json["results"]) > 0:
        print(f"Live stream added - id: {result_json['results'][0]['id']}")
    else:
        raise Exception("No results returned from API")


def do_list_live_streams(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/streams/get-stream-info")}""")
        return
    result = requests.get(get_api_url("/streams/get-stream-info"), timeout=30)
    check_err_response(result, True)
    term_width = shutil.get_terminal_size()[0]
    live_stream_list = result.json()
    if not live_stream_list:
        print("No live streams added to the server")
        return
    print(
        tabulate(
            [
                [
                    live_stream["id"],
                    live_stream["liveStreamUrl"],
                    live_stream["description"],
                    live_stream.get("chunk_duration") or "N/A",
                    live_stream.get("chunk_overlap_duration") or "N/A",
                ]
                for live_stream in live_stream_list
            ],
            headers=[
                "ID",
                "URL",
                "Description",
                "Chunk\nDuration",
                "Chunk\nOverlap\nDuration",
            ],
            tablefmt="simple_grid",
            maxcolwidths=[36, 50, max(20, term_width - 36 - 50 - 8 - 8 - 8 - (1 + 3 * 6)), 8, 8],
        )
    )


def do_delete_live_stream(args):
    if args.print_curl_command:
        print(f"""curl -i -X DELETE {get_api_url("/streams/delete/" + args.video_id)}""")
        return
    result = requests.delete(get_api_url("/streams/delete/" + args.video_id), timeout=30)
    check_err_response(result, True)
    print("Live stream deleted")


def do_stop_live_stream_processing(args):
    if args.print_curl_command:
        print(f"""curl -i -X DELETE {get_api_url("/generate_captions/" + args.stream_id)}""")
        return
    result = requests.delete(get_api_url("/generate_captions/" + args.stream_id), timeout=30)
    check_err_response(result, True)
    print("Live stream stopped from generating captions and alerts")


def do_stop_live_stream_embed_processing(args):
    if args.print_curl_command:
        print(
            f"""curl -i -X DELETE {get_api_url("/generate_video_embeddings/" + args.stream_id)}"""
        )
        return
    result = requests.delete(
        get_api_url("/generate_video_embeddings/" + args.stream_id), timeout=30
    )
    check_err_response(result, True)
    print("Live stream stopped from generating embeddings")


def do_cv_stream_add(args):
    """Add a stream using CV-compatible API with optional auto-inference."""
    metadata = {}
    for field in [
        "prompt",
        "system_prompt",
        "model",
        "chunk_duration",
        "chunk_overlap_duration",
        "max_tokens",
        "temperature",
        "vlm_input_width",
        "vlm_input_height",
    ]:
        val = getattr(args, field.replace("-", "_"), None)
        if val is not None:
            metadata[field] = val
    if args.enable_audio:
        metadata["enable_audio"] = True
    if args.enable_reasoning:
        metadata["enable_reasoning"] = True
    if args.stream:
        metadata["stream"] = True
    if args.response_format:
        metadata["response_format_type"] = args.response_format

    req_json = {
        "key": "sensor",
        "value": {
            "camera_id": args.camera_id,
            "camera_url": args.camera_url,
            "change": "camera_add",
        },
    }
    if args.camera_name:
        req_json["value"]["camera_name"] = args.camera_name
    if metadata:
        req_json["value"]["metadata"] = metadata
    req_json["headers"] = {"source": args.source}

    if args.print_curl_command:
        print(f'curl -i -X POST {get_api_url("/stream/add")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    result = requests.post(get_api_url("/stream/add"), json=req_json, timeout=300)
    check_err_response(result, True)
    r = result.json()
    print(
        f"Stream added — camera_id: {r['camera_id']}, asset_id: {r['asset_id']}, "
        f"status: {r['status']}, inference: {r['inference']}"
    )

    # If inference started, tail the SSE caption stream
    if r.get("inference") and metadata.get("prompt"):
        asset_id = r["asset_id"]
        captions_json = {
            "id": [asset_id],
            "model": metadata.get("model", args.model or ""),
            "prompt": metadata["prompt"],
            "stream": True,
            "stream_options": {"include_usage": True},
            "chunk_duration": metadata.get("chunk_duration", 10),
        }
        # Forward optional fields
        for field in [
            "system_prompt",
            "max_tokens",
            "temperature",
            "top_p",
            "chunk_overlap_duration",
            "vlm_input_width",
            "vlm_input_height",
        ]:
            if field in metadata:
                captions_json[field] = metadata[field]
        if metadata.get("enable_reasoning"):
            captions_json["enable_reasoning"] = True
        if metadata.get("enable_audio"):
            captions_json["enable_audio"] = True
        if metadata.get("response_format_type"):
            captions_json["response_format"] = {"type": metadata["response_format_type"]}

        print("\n--- Live caption stream (Ctrl+C to stop) ---")
        try:
            sse_resp = requests.post(
                get_api_url("/generate_captions"),
                json=captions_json,
                stream=True,
                timeout=(10, 600),
            )
            check_err_response(sse_resp, True)
            client = sseclient.SSEClient(sse_resp)
            first_response = True
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("\nCaption stream ended.")
                    break
                result = json.loads(data)
                if first_response:
                    print(f"Request ID: {result['id']}")
                    print(f"Model: {result['model']}")
                    print("-" * 40)
                    first_response = False
                if result.get("media_info") and result["media_info"].get("type") == "timestamp":
                    print(
                        f"[{format_ntp_timestamp(result['media_info']['start_timestamp'])} - "
                        f"{format_ntp_timestamp(result['media_info']['end_timestamp'])}]"
                    )
                if "chunk_responses" in result and result["chunk_responses"]:
                    for chunk in result["chunk_responses"]:
                        start_t = chunk["start_time"]
                        end_t = chunk["end_time"]
                        if "T" in start_t:
                            start_t = format_ntp_timestamp(start_t)
                            end_t = format_ntp_timestamp(end_t)
                        print(f"[{start_t} - {end_t}] {chunk['content']}")
                        if chunk.get("reasoning_description"):
                            print(f"  Reasoning: {chunk['reasoning_description']}")
                if result.get("usage"):
                    print(
                        f"  Chunks: {result['usage']['total_chunks_processed']}, "
                        f"Time: {result['usage']['query_processing_time']}s"
                    )
                    print("-" * 40)
        except KeyboardInterrupt:
            print("\nStopping stream...")
            sse_resp.close()
            # Remove the stream on Ctrl+C
            remove_json = {
                "key": "sensor",
                "value": {"camera_id": args.camera_id, "change": "camera_remove"},
            }
            try:
                rm_resp = requests.post(get_api_url("/stream/remove"), json=remove_json, timeout=30)
                if rm_resp.ok:
                    print(f"Stream removed — camera_id: {args.camera_id}")
                else:
                    print(f"Failed to remove stream: {rm_resp.status_code}")
            except Exception as rm_err:
                print(f"Failed to remove stream: {rm_err}")
        except Exception as e:
            print(f"\nSSE connection error: {e}")


def do_cv_stream_remove(args):
    """Remove a stream by camera_id using CV-compatible API."""
    req_json = {
        "key": "sensor",
        "value": {
            "camera_id": args.camera_id,
            "change": "camera_remove",
        },
    }

    if args.print_curl_command:
        print(f'curl -i -X POST {get_api_url("/stream/remove")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    result = requests.post(get_api_url("/stream/remove"), json=req_json, timeout=300)
    check_err_response(result, True)
    r = result.json()
    print(f"Stream removed — camera_id: {r['camera_id']}, asset_id: {r['asset_id']}")


def do_cv_stream_list(args):
    """List all streams in CV-compatible format."""
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/stream/get-stream-info")}""")
        return
    result = requests.get(get_api_url("/stream/get-stream-info"), timeout=300)
    check_err_response(result, True)
    r = result.json()
    stream_list = r.get("stream_list", [])
    if not stream_list:
        print(f"No streams ({r.get('stream_count', 0)} total)")
        return
    term_width = shutil.get_terminal_size()[0]
    print(
        tabulate(
            [
                [
                    s["camera_id"],
                    s.get("camera_name") or "",
                    s["camera_url"],
                    s["asset_id"],
                    "Yes" if s.get("inference_active") else "No",
                    s.get("chunk_duration", 0),
                ]
                for s in stream_list
            ],
            headers=["Camera ID", "Name", "URL", "Asset ID", "Inference", "Chunk(s)"],
            tablefmt="simple_grid",
            maxcolwidths=[20, 15, max(20, term_width - 20 - 15 - 36 - 10 - 8 - 20), 36, 10, 8],
        )
    )


def do_list_models(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/models")}""")
        return
    result = requests.get(get_api_url("/models"), timeout=30)
    check_err_response(result, True)
    term_width = shutil.get_terminal_size()[0]
    model_list = result.json()
    if not model_list["data"]:
        print("No live streams added to the server")
        return
    print(
        tabulate(
            [
                [
                    model["id"],
                    datetime.fromtimestamp(model["created"], tz=timezone.utc).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    ),
                    model["owned_by"],
                    model["api_type"],
                ]
                for model in model_list["data"]
            ],
            headers=["ID", "Created", "Owned By", "API Type"],
            tablefmt="simple_grid",
            maxcolwidths=[term_width - 19 - 15 - 8 - (1 + 3 * 4), 19, 15, 8],
        )
    )


def do_server_metrics(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET -L {get_api_url("/metrics")}""")
        return
    result = requests.get(get_api_url("/metrics"), timeout=30)
    check_err_response(result, True)
    result = result.text
    print(result)


def do_server_health_check(args):
    health_check_type = "live" if args.liveness else "ready"
    url = get_api_url(f"/{health_check_type}")
    if args.print_curl_command:
        print(f"""curl -i -X GET {url}""")
        return
    result = requests.get(url, timeout=30)
    check_err_response(result, True)
    print("RTVI Server is " + health_check_type)


def do_chat_completions(args):
    """Handle OpenAI-compatible chat completions request"""
    # Parse messages from format "role:content"
    # Note: If content contains colons, use the first colon as separator
    # For complex content, consider using JSON format directly
    messages = []
    for msg_str in args.messages:
        if ":" not in msg_str:
            raise ValueError(
                f"Invalid message format: {msg_str}. Expected 'role:content'. "
                "If content contains colons, only the first colon is used as separator."
            )
        role, content = msg_str.split(":", 1)  # Split on first colon only
        role = role.strip()
        content = content.strip()
        if role not in ["system", "user", "assistant"]:
            raise ValueError(f"Invalid role: {role}. Must be 'system', 'user', or 'assistant'")
        if not content:
            raise ValueError(f"Message content cannot be empty for role: {role}")
        messages.append({"role": role, "content": content})

    req_json = {
        "model": args.model,
        "messages": messages,
    }
    if args.id:
        req_json["id"] = args.id

    if args.model_temperature is not None:
        req_json["temperature"] = args.model_temperature
    if args.model_seed is not None:
        req_json["seed"] = args.model_seed
    if args.model_top_p is not None:
        req_json["top_p"] = args.model_top_p
    if args.model_top_k is not None:
        req_json["top_k"] = args.model_top_k
    if args.model_max_tokens is not None:
        req_json["max_tokens"] = args.model_max_tokens

    if args.chunk_duration is not None:
        req_json["chunk_duration"] = args.chunk_duration
    if args.chunk_overlap_duration is not None:
        req_json["chunk_overlap_duration"] = args.chunk_overlap_duration

    if args.response_format:
        req_json["response_format"] = {"type": args.response_format}
    if args.num_frames_per_second_or_fixed_frames_chunk is not None:
        req_json["num_frames_per_second_or_fixed_frames_chunk"] = (
            args.num_frames_per_second_or_fixed_frames_chunk
        )
    if args.use_fps_for_chunking:
        req_json["use_fps_for_chunking"] = True
    if args.vlm_input_width is not None:
        req_json["vlm_input_width"] = args.vlm_input_width
    if args.vlm_input_height is not None:
        req_json["vlm_input_height"] = args.vlm_input_height
    if args.enable_reasoning:
        req_json["enable_reasoning"] = True
    if args.enable_audio:
        req_json["enable_audio"] = True

    if args.stream:
        req_json["stream"] = True
        req_json["stream_options"] = {"include_usage": True}

    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/chat/completions")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(
        get_api_url("/chat/completions"), json=req_json, stream=args.stream, timeout=(10, 600)
    )
    check_err_response(response, True)
    if args.stream:
        client = sseclient.SSEClient(response)
        first_response = True
        try:
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("Chat Completion Complete")
                    continue
                result = json.loads(data)
                if first_response:
                    print("Request ID:", result["id"])
                    print(
                        "Request Creation Time:",
                        datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        ),
                    )
                    print("Model:", result["model"])
                    print("----------------------------------------")
                    first_response = False
                # Handle streaming chunks
                if "choices" in result and result["choices"]:
                    choice = result["choices"][0]
                    if "delta" in choice and "content" in choice["delta"]:
                        print(choice["delta"]["content"], end="", flush=True)
                    elif choice.get("finish_reason"):
                        print(f"\n[Finished: {choice['finish_reason']}]")
                        break
        except KeyboardInterrupt:
            print("\nUser interrupted")
            response.close()
    else:
        result = response.json()
        print("Chat Completion finished")
        print("Request ID:", result["id"])
        print(
            "Request Creation Time:",
            datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        print("Model:", result["model"])
        if result.get("choices"):
            choice = result["choices"][0]
            print("Role:", choice["message"]["role"])
            print("Content:", choice["message"]["content"])
            if choice.get("finish_reason"):
                print("Finish Reason:", choice["finish_reason"])
        if result.get("usage"):
            print("Usage:", result["usage"])


def do_completions(args):
    """Handle OpenAI-compatible completions request"""
    req_json = {
        "model": args.model,
        "prompt": args.prompt,
        "max_tokens": args.max_tokens,
        "temperature": args.temperature,
        "top_p": args.top_p,
    }

    if args.top_k is not None:
        req_json["top_k"] = args.top_k
    if args.seed is not None:
        req_json["seed"] = args.seed
    if args.stream:
        req_json["stream"] = True

    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/completions")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(
        get_api_url("/completions"), json=req_json, stream=args.stream, timeout=(10, 600)
    )
    check_err_response(response, True)
    if args.stream:
        client = sseclient.SSEClient(response)
        try:
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("\nCompletion Complete")
                    continue
                result = json.loads(data)
                if "choices" in result and result["choices"]:
                    choice = result["choices"][0]
                    if "text" in choice:
                        print(choice["text"], end="", flush=True)
        except KeyboardInterrupt:
            print("\nUser interrupted")
            response.close()
    else:
        result = response.json()
        print("Completion finished")
        print("Request ID:", result["id"])
        print(
            "Request Creation Time:",
            datetime.fromtimestamp(result["created"], tz=timezone.utc).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        )
        print("Model:", result["model"])
        if result.get("choices"):
            choice = result["choices"][0]
            print("Text:", choice["text"])
            if choice.get("finish_reason"):
                print("Finish Reason:", choice["finish_reason"])


def do_get_version(args):
    """Get service version"""
    if args.print_curl_command:
        print(f'curl {get_api_url("/version")}')
        return

    response = requests.get(get_api_url("/version"), timeout=30)
    check_err_response(response, True)
    result = response.json()
    print("Service Version:")
    print(f"  Release: {result.get('release', 'N/A')}")
    print(f"  API: {result.get('api', 'N/A')}")


def do_get_manifest(args):
    """Get service manifest"""
    if args.print_curl_command:
        print(f'curl {get_api_url("/manifest")}')
        return

    response = requests.get(get_api_url("/manifest"), timeout=30)
    check_err_response(response, True)
    result = response.json()
    print("Service Manifest:")
    print(f"  Version: {result.get('version', 'N/A')}")
    print(f"  Model: {result.get('model', 'N/A')}")


def main():
    global BASE_URL
    parser = get_parser()
    args = parser.parse_args()
    BASE_URL = args.backend
    print(f"Base URL: {BASE_URL}")

    # Dispatch table to map request types to handler functions
    request_handlers = {
        "add-file": do_add_file,
        "list-files": do_list_files,
        "file-info": do_get_file_info,
        "file-content": do_get_file_content,
        "delete-file": do_delete_file,
        "generate-captions": do_generate_captions,
        "generate-video-embeddings": do_generate_video_embeddings,
        "generate-text-embeddings": do_generate_text_embeddings,
        "add-live-stream": do_add_live_stream,
        "list-live-streams": do_list_live_streams,
        "delete-live-stream": do_delete_live_stream,
        "stop-live-stream-processing": do_stop_live_stream_processing,
        "stop-live-stream-embed-processing": do_stop_live_stream_embed_processing,
        "stream-add": do_cv_stream_add,
        "stream-remove": do_cv_stream_remove,
        "stream-list": do_cv_stream_list,
        "list-models": do_list_models,
        "server-metrics": do_server_metrics,
        "server-health-check": do_server_health_check,
        "chat-completions": do_chat_completions,
        "completions": do_completions,
        "get-version": do_get_version,
        "get-manifest": do_get_manifest,
    }

    handler = request_handlers.get(args.request)
    if handler:
        handler(args)


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print(f"Failed to connect to server {BASE_URL}")
        sys.exit(-1)

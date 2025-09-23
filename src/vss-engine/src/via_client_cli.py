######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################

import argparse
import json
import os
import shutil
import sys
from datetime import datetime

try:
    import requests
    import sseclient
    import uvicorn
    from fastapi import FastAPI
    from tabulate import tabulate
    from tqdm import tqdm
except (ImportError, ModuleNotFoundError):
    print("Dependencies missing. Install using:")
    print("python3 -m pip install sseclient-py requests tabulate tqdm fastapi uvicorn pyyaml")
    sys.exit(-1)


def convert_seconds_to_string(seconds, need_hour=False, millisec=False):
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
        default=os.environ.get("VIA_BACKEND", "http://localhost:8000"),
        help="VIA server address",
    )

    g = parser.add_argument_group("Other Options")
    g.add_argument(
        "--print-curl-command",
        action="store_true",
        help="Print corresponding curl command and exit",
    )


def get_parser():

    parser = argparse.ArgumentParser(
        description="VIA CLI Client", formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    parser.add_argument(
        "--backend",
        type=str,
        help="Backend server address and port",
        default=os.environ.get("VIA_BACKEND", "http://localhost:8000"),
    )

    subparsers = parser.add_subparsers(help="Request to execute", dest="request")
    subparsers.required = True

    add_file = subparsers.add_parser(
        "add-file",
        help="Add/upload an file to the VIA server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = add_file.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("file", type=str, help="File to add")

    opt_args = add_file.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--add-as-path",
        help="Add the file as a path instead of uploading the file",
        action="store_true",
    )
    opt_args.add_argument(
        "--is-image", help="The file to be added is an image", action="store_true"
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
    mandatory_args = get_file_info.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("file_id", type=str, help="ID of the file to get info of")
    add_common_args(get_file_info)

    file_content = subparsers.add_parser(
        "file-content",
        help="Get content of a file",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = file_content.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("file_id", type=str, help="ID of the file to get content of")
    add_common_args(file_content)

    delete_file = subparsers.add_parser(
        "delete-file",
        help="Delete a file from the VIA server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = delete_file.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("file_id", type=str, help="ID of the file to delete")

    add_common_args(delete_file)

    summarize = subparsers.add_parser(
        "summarize",
        help="Trigger summary on an already added file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = summarize.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument(
        "--id",
        required=True,
        action="append",
        type=str,
        help="ID of the file / live stream to trigger summary on",
    )
    mandatory_args.add_argument(
        "--model", required=True, type=str, help="The VLM model to use for summarizing"
    )

    opt_args = summarize.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--stream", help="Stream the output using server side events", action="store_true"
    )
    opt_args.add_argument("--chunk-duration", help="Chunk duration in seconds", type=int)
    opt_args.add_argument(
        "--chunk-overlap-duration", help="Chunk overlap duration in seconds", type=int
    )
    opt_args.add_argument(
        "--summary-duration",
        help="Summarize every `summaryDuration` seconds of the video.",
        type=int,
    )
    opt_args.add_argument("--prompt", help="Prompt to use for VLM.", type=str)
    opt_args.add_argument("--system-prompt", help="System prompt for the VLM.", type=str)
    opt_args.add_argument(
        "--caption-summarization-prompt",
        help=("Prompt used by CA-RAG to summarize captions generated by VLM."),
        type=str,
    )
    opt_args.add_argument(
        "--summary-aggregation-prompt",
        help=("Prompt used by CA-RAG to generate the final summary."),
        type=str,
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
        "--alert",
        help="Add an alert to be received as a server-sent event for live-streams."
        " Format '<alert_name>:<event1>,<event2>,...'. Can be specified multiple times",
        type=str,
        action="append",
    )
    opt_args.add_argument(
        "--enable-chat",
        help="Enable Q&A on the asset",
        action="store_true",
    )
    opt_args.add_argument(
        "--enable-cv-metadata",
        help="Enable CV metadata",
        action="store_true",
    )
    opt_args.add_argument(
        "--cv-pipeline-prompt",
        help=("Prompt used by CV pipeline."),
        type=str,
    )
    opt_args.add_argument(
        "--response-format",
        help="Format of the model output",
        choices=["json_object", "text"],
        default="text",
        type=str,
    )
    opt_args.add_argument(
        "--num-frames-per-chunk", help="Number of frames per chunk to use for the VLM", type=int
    )
    opt_args.add_argument("--summarize-batch-size", help="Summarization Batch size", type=int)

    opt_args.add_argument("--rag-batch-size", help="RAG Batch Size", type=int)
    opt_args.add_argument("--rag-top-k", help="RAG top k", type=int)

    opt_args.add_argument(
        "--summarize-top-p",
        help="Top-P to use while generating from LLM for summarization",
        type=float,
    )
    opt_args.add_argument(
        "--summarize-temperature",
        help="Temperature to use while generating from LLM for summarization",
        type=float,
    )
    opt_args.add_argument(
        "--summarize-max-tokens",
        help="Max tokens to use while generating from LLM for summarization",
        type=int,
    )

    opt_args.add_argument(
        "--chat-top-p", help="Top-P to use while generating from LLM for QnA", type=float
    )
    opt_args.add_argument(
        "--chat-temperature",
        help="Temperature to use while generating from LLM for QnA",
        type=float,
    )
    opt_args.add_argument(
        "--chat-max-tokens", help="Max tokens to use while generating from LLM for QnA", type=int
    )

    opt_args.add_argument(
        "--notification-top-p",
        help="Top-P to use while generating from LLM for notification",
        type=float,
    )
    opt_args.add_argument(
        "--notification-temperature",
        help="Temperature to use while generating from LLM for notification",
        type=float,
    )
    opt_args.add_argument(
        "--notification-max-tokens",
        help="Max tokens to use while generating from LLM for notification",
        type=int,
    )

    opt_args.add_argument("--vlm-input-width", help="VLM Input Width", type=int)
    opt_args.add_argument("--vlm-input-height", help="VLM Input Height", type=int)
    opt_args.add_argument(
        "--enable-audio",
        help="Enable transcription of the audio stream in the media",
        action="store_true",
    )
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning for VLM captions generation",
        action="store_true",
    )
    opt_args.add_argument(
        "--collection-name",
        help="The name of the user managed milvus db collection to use for the summarization request",
        type=str,
    )
    opt_args.add_argument(
        "--custom-metadata",
        help=(
            "Custom metadata to be added to the summarization request. This is a JSON object "
            "with key-value pairs. Custom metadata is supported only with user managed milvus db collections."
        ),
        type=str,
    )
    opt_args.add_argument(
        "--delete-external-collection",
        help="Reset the user provided milvus db collection at the end of the summarization request",
        action="store_true",
    )
    add_common_args(summarize)

    generate_vlm_captions = subparsers.add_parser(
        "generate-vlm-captions",
        help="Generate VLM captions for an already added file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = generate_vlm_captions.add_argument_group("Mandatory Arguments")
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

    opt_args = generate_vlm_captions.add_argument_group("Optional Arguments")
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
        "--enable-cv-metadata",
        help="Enable CV metadata",
        action="store_true",
    )
    opt_args.add_argument(
        "--cv-pipeline-prompt",
        help=("Prompt used by CV pipeline."),
        type=str,
    )
    opt_args.add_argument(
        "--response-format",
        help="Format of the model output",
        choices=["json_object", "text"],
        default="text",
        type=str,
    )
    opt_args.add_argument(
        "--num-frames-per-chunk", help="Number of frames per chunk to use for the VLM", type=int
    )
    opt_args.add_argument("--vlm-input-width", help="VLM Input Width", type=int)
    opt_args.add_argument("--vlm-input-height", help="VLM Input Height", type=int)
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning for VLM captions generation",
        action="store_true",
    )
    add_common_args(generate_vlm_captions)

    add_live_stream = subparsers.add_parser(
        "add-live-stream",
        help="Add a live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = add_live_stream.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("live_stream_url", type=str, help="A Live Stream URL")
    mandatory_args.add_argument(
        "--description", help="Description of the live stream", type=str, required=True
    )

    opt_args = add_live_stream.add_argument_group("Optional Arguments")
    opt_args.add_argument("--username", help="Username to access the live stream", type=str)
    opt_args.add_argument("--password", help="Password to access the live stream", type=str)

    add_common_args(add_live_stream)

    list_live_streams = subparsers.add_parser(
        "list-live-streams",
        help="List all live streams",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(list_live_streams)

    delete_live_stream = subparsers.add_parser(
        "delete-live-stream",
        help="Delete a live stream from the VIA server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = delete_live_stream.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("video_id", type=str, help="ID of the live-stream to delete")
    add_common_args(delete_live_stream)

    add_alert = subparsers.add_parser(
        "add-alert",
        help="Add an alert",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = add_alert.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("--live-stream-id", type=str, help="Live Stream ID", required=True)
    mandatory_args.add_argument("--callback-url", type=str, help="Callback URL", required=True)
    mandatory_args.add_argument(
        "--events",
        type=str,
        help="Events to detect. Can be specified multiple times",
        required=True,
        action="append",
    )

    opt_args = add_alert.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--callback-json-template",
        help="Json Template to use while posting data to the callback URL."
        " Supported placeholders {streamId}, {alertId}, {ntpTimestamp}, {alertText}",
        type=str,
    )
    opt_args.add_argument(
        "--callback-token", help="Bearer token to use while posting to the callback URL", type=str
    )

    add_common_args(add_alert)

    list_alerts = subparsers.add_parser(
        "list-alerts",
        help="List all alerts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(list_alerts)

    delete_alert = subparsers.add_parser(
        "delete-alert",
        help="Delete an alert from the VIA server",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = delete_alert.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("alert_id", type=str, help="ID of the alert to delete")
    add_common_args(delete_alert)

    list_recent_alerts = subparsers.add_parser(
        "list-recent-alerts",
        help="List recent alerts",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    opt_args = list_recent_alerts.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--live-stream-id",
        help="Filter alerts by live stream ID",
        type=str,
    )
    add_common_args(list_recent_alerts)

    alert_callback_server = subparsers.add_parser(
        "alert-callback-server",
        help="Start a test server for VIA alert callbacks",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    opt_args = alert_callback_server.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--host",
        help="Host to server on",
        type=str,
        default="127.0.0.1",
    )
    opt_args.add_argument(
        "--port",
        help="Port to server on",
        type=int,
        default=8500,
    )

    add_common_args(alert_callback_server)

    list_models = subparsers.add_parser(
        "list-models",
        help="List all models",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(list_models)

    server_metrics = subparsers.add_parser(
        "server-metrics",
        help="Get VIA server metrics",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    add_common_args(server_metrics)

    server_health_check = subparsers.add_parser(
        "server-health-check",
        help="Check VIA server health",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    opt_args = server_health_check.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--liveness", help="Use liveness check instead of readiness (default)", action="store_true"
    )
    add_common_args(server_health_check)

    chat = subparsers.add_parser(
        "chat",
        help="Trigger chat/Q&A on an already added file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = chat.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument(
        "--id",
        required=True,
        action="append",
        type=str,
        help="ID of the file / live stream to trigger chat on",
    )
    mandatory_args.add_argument(
        "--model", required=True, type=str, help="The VLM model to use for summarizing"
    )
    mandatory_args.add_argument("--prompt", help="Prompt to use for VLM.", type=str)
    opt_args = chat.add_argument_group("Optional Arguments")
    opt_args.add_argument(
        "--stream", help="Stream the output using server side events", action="store_true"
    )
    opt_args.add_argument("--chunk-duration", help="Chunk duration in seconds", type=int)
    opt_args.add_argument(
        "--chunk-overlap-duration", help="Chunk overlap duration in seconds", type=int
    )
    opt_args.add_argument(
        "--summary-duration",
        help="Summarize every `summaryDuration` seconds of the video.",
        type=int,
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
        "--alert",
        help="Add an alert to be received as a server-sent event for live-streams."
        " Format '<alert_name>:<event1>,<event2>,...'. Can be specified multiple times",
        type=str,
        action="append",
    )
    opt_args.add_argument(
        "--response-format",
        help="Format of the model output",
        choices=["json_object", "text"],
        default="text",
        type=str,
    )
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning for VLM chat responses",
        action="store_true",
    )
    add_common_args(chat)

    review_alert = subparsers.add_parser(
        "review-alert",
        help="Review an external alert",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = review_alert.add_argument_group("Mandatory Arguments")
    mandatory_args.add_argument("--video-path", required=True, type=str, help="Path to video file")
    mandatory_args.add_argument(
        "--prompt", required=True, type=str, help="VLM prompt for alert review"
    )
    mandatory_args.add_argument("--sensor-id", required=True, type=str, help="Sensor identifier")
    mandatory_args.add_argument("--alert-type", required=True, type=str, help="Alert type")
    mandatory_args.add_argument(
        "--alert-description", required=True, type=str, help="Alert description"
    )
    mandatory_args.add_argument("--event-type", required=True, type=str, help="Event type")
    mandatory_args.add_argument(
        "--event-description", required=True, type=str, help="Event description"
    )

    opt_args = review_alert.add_argument_group("Optional Arguments")
    opt_args.add_argument("--version", type=str, default="1.0", help="Alert review API version")
    opt_args.add_argument(
        "--id", type=str, help="Unique request ID (auto-generated if not provided)"
    )
    opt_args.add_argument(
        "--timestamp", type=str, help="NTP timestamp (auto-generated if not provided)"
    )
    opt_args.add_argument(
        "--confidence", type=float, default=1.0, help="Confidence score (0.0-1.0)"
    )
    opt_args.add_argument("--cv-metadata-path", type=str, help="Path to CV metadata file")
    opt_args.add_argument("--stream-name", type=str, help="Stream name")
    opt_args.add_argument(
        "--do-verification",
        action="store_true",
        help="Enable verification of the alert",
    )
    opt_args.add_argument(
        "--alert-severity",
        type=str,
        choices=["LOW", "MEDIUM", "HIGH", "CRITICAL"],
        default="MEDIUM",
        help="Alert severity level",
    )
    opt_args.add_argument("--chunk-duration", type=int, default=0, help="Chunk duration in seconds")
    opt_args.add_argument(
        "--chunk-overlap-duration",
        type=int,
        default=0,
        help="Chunk overlap duration in seconds",
    )
    opt_args.add_argument(
        "--num-frames-per-chunk", type=int, default=0, help="Number of frames per chunk"
    )
    opt_args.add_argument(
        "--enable-caption", action="store_true", help="Enable detailed captioning"
    )
    opt_args.add_argument(
        "--cv-metadata-overlay", action="store_true", help="Enable CV metadata overlay"
    )
    opt_args.add_argument("--debug", action="store_true", help="Enable debug output in response")
    opt_args.add_argument(
        "--system-prompt",
        type=str,
        help="System prompt for the VLM",
    )
    opt_args.add_argument("--max-tokens", type=int, help="Maximum number of tokens to generate")
    opt_args.add_argument("--temperature", type=float, help="Sampling temperature (0.0-1.0)")
    opt_args.add_argument("--top-p", type=float, help="Top-p sampling parameter (0.0-1.0)")
    opt_args.add_argument("--top-k", type=float, help="Top-k sampling parameter")
    opt_args.add_argument("--seed", type=int, help="Random seed for generation")
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning for VLM alert review",
        action="store_true",
    )
    opt_args.add_argument(
        "--meta-labels", type=str, action="append", help="Metadata labels in format key:value"
    )
    opt_args.add_argument(
        "--start-time",
        type=float,
        help="Start time of the clip in the input videoin seconds (default: 0.0)",
    )
    opt_args.add_argument(
        "--end-time",
        type=float,
        help="End time of the clip in the input video in seconds (default: 0.0)",
    )

    add_common_args(review_alert)

    return parser


BASE_URL = ""


def get_api_url(path: str):
    return BASE_URL + path


def check_err_response(response: requests.Response, exit_on_error=False):
    if response.status_code >= 400:
        err_json = response.json()
        print(f"Request failed, code - {err_json['code']} message - {err_json['message']}")
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

    if args.print_curl_command:
        if "file" in files:
            files["file"] = (None, f"@{args.file}")
        print(
            f"""curl -i -X POST {get_api_url("/files")}"""
            + "".join([f" \\\n    -F '{k}={v[1]}'" for k, v in files.items()])
        )
        return

    result = requests.post(get_api_url("/files"), files=files)
    check_err_response(result, True)
    result_json = result.json()
    print(
        "File added - id: %s, filename %s, bytes %d, purpose %s, media_type %s"
        % (
            result_json["id"],
            result_json["filename"],
            result_json["bytes"],
            result_json["purpose"],
            result_json["media_type"],
        )
    )


def do_list_files(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/files?purpose=vision")}""")
        return
    result = requests.get(get_api_url("/files?purpose=vision"))
    check_err_response(result, True)
    term_width = shutil.get_terminal_size()[0]
    files_list = result.json()
    if not files_list["data"]:
        print("No files added to the server")
        return
    print(
        tabulate(
            [
                [file["id"], file["filename"], file["bytes"], file["media_type"], file["purpose"]]
                for file in files_list["data"]
            ],
            headers=["ID", "File Name", "Size", "Media Type", "Purpose"],
            tablefmt="simple_grid",
            maxcolwidths=[
                36,
                term_width - 36 - 10 - 10 - 7 - (3 * 5 + 1),
                10,
                10,
                7,
            ],
        )
    )


def do_get_file_info(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/files/" + args.file_id)}""")
        return
    result = requests.get(get_api_url("/files/" + args.file_id))
    check_err_response(result, True)
    result_json = result.json()
    print(
        "ID: %s\nFile name: %s\nSize: %d bytes\nPurpose: %s"
        % (result_json["id"], result_json["filename"], result_json["bytes"], result_json["purpose"])
    )


def do_get_file_content(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/files/" + args.file_id + "/content")}""")
        return
    result = requests.get(get_api_url("/files/" + args.file_id + "/content"), stream=True)
    check_err_response(result, True)

    file_size = int(result.headers.get("content-length", 0))
    bsize = 1024

    with tqdm(total=file_size, unit="B", unit_scale=True) as pb:
        with open(f"/tmp/via_{args.file_id}_content", "wb") as f:
            for data in result.iter_content(bsize):
                pb.update(len(data))
                f.write(data)
    print(f"File content written to /tmp/via_{args.file_id}_content")


def do_delete_file(args):
    if args.print_curl_command:
        print(f"""curl -i -X DELETE {get_api_url("/files/" + args.file_id)}""")
        return
    result = requests.delete(get_api_url("/files/" + args.file_id))
    check_err_response(result, True)
    result_json = result.json()
    print("File deleted - id %s, status %r" % (result_json["id"], result_json["deleted"]))


def do_summarize(args):
    req_json = {
        "id": args.id,
        "model": args.model,
        "response_format": {"type": args.response_format},
        "enable_chat": args.enable_chat,
        "enable_cv_metadata": args.enable_cv_metadata,
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
    if args.summary_duration is not None:
        req_json["summary_duration"] = args.summary_duration
    if args.summarize_batch_size is not None:
        req_json["summarize_batch_size"] = args.summarize_batch_size
    if args.rag_top_k is not None:
        req_json["rag_top_k"] = args.rag_top_k
    if args.rag_batch_size is not None:
        req_json["rag_batch_size"] = args.rag_batch_size

    if args.summarize_top_p is not None:
        req_json["summarize_top_p"] = args.summarize_top_p
    if args.summarize_temperature is not None:
        req_json["summarize_temperature"] = args.summarize_temperature
    if args.summarize_max_tokens is not None:
        req_json["summarize_max_tokens"] = args.summarize_max_tokens

    if args.chat_top_p is not None:
        req_json["chat_top_p"] = args.chat_top_p
    if args.chat_temperature is not None:
        req_json["chat_temperature"] = args.chat_temperature
    if args.chat_max_tokens is not None:
        req_json["chat_max_tokens"] = args.chat_max_tokens

    if args.notification_top_p is not None:
        req_json["notification_top_p"] = args.notification_top_p
    if args.notification_temperature is not None:
        req_json["notification_temperature"] = args.notification_temperature
    if args.notification_max_tokens is not None:
        req_json["notification_max_tokens"] = args.notification_max_tokens

    if args.prompt:
        req_json["prompt"] = args.prompt
    if args.system_prompt:
        req_json["system_prompt"] = args.system_prompt
    if args.caption_summarization_prompt:
        req_json["caption_summarization_prompt"] = args.caption_summarization_prompt
        if args.summary_aggregation_prompt:
            req_json["summary_aggregation_prompt"] = args.summary_aggregation_prompt
    if args.cv_pipeline_prompt:
        req_json["cv_pipeline_prompt"] = args.cv_pipeline_prompt
    if args.num_frames_per_chunk is not None:
        req_json["num_frames_per_chunk"] = args.num_frames_per_chunk
    if args.vlm_input_width is not None:
        req_json["vlm_input_width"] = args.vlm_input_width
    if args.vlm_input_height is not None:
        req_json["vlm_input_height"] = args.vlm_input_height
    if args.enable_audio is not None:
        req_json["enable_audio"] = args.enable_audio
    if args.enable_reasoning:
        req_json["enable_reasoning"] = args.enable_reasoning

    if args.alert:
        parsed_alerts = []
        for alert in args.alert:
            try:
                alert_name, events = [word.strip() for word in alert.split(":")]
                assert alert_name
                assert events

                parsed_events = [ev.strip() for ev in events.split(",") if ev.strip()]
                assert parsed_events
            except Exception:
                print(f"Failed to parse alert '{alert}'")
                exit(-1)
            parsed_alerts.append(
                {
                    "type": "alert",
                    "alert": {"name": alert_name, "events": parsed_events},
                }
            )
        if parsed_alerts:
            req_json["tools"] = parsed_alerts

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
    if args.collection_name is not None:
        req_json["collection_name"] = args.collection_name
    if args.custom_metadata is not None:
        req_json["custom_metadata"] = json.loads(args.custom_metadata)
    if args.delete_external_collection:
        req_json["delete_external_collection"] = True

    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/summarize")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(get_api_url("/summarize"), json=req_json, stream=args.stream)
    check_err_response(response, True)
    if args.stream:
        client = sseclient.SSEClient(response)
        first_response = True
        try:
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("Summarization Complete")
                    continue
                result = json.loads(data)
                if first_response:
                    print("Request ID:", result["id"])
                    print(
                        "Request Creation Time:",
                        datetime.utcfromtimestamp(result["created"]).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    print("Model:", result["model"])
                    print("----------------------------------------")
                    first_response = False
                print("Object:", result["object"])
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
                    start_time = format_ntp_timestamp(result["media_info"]["start_timestamp"])
                    end_time = format_ntp_timestamp(result["media_info"]["end_timestamp"])
                    print(f"Media time range: {start_time} - {end_time}")
                    print(
                        f"Full timestamps: {result['media_info']['start_timestamp']} to "
                        f"{result['media_info']['end_timestamp']}"
                    )
                if result["choices"]:
                    if result["choices"][0]["finish_reason"] == "stop":
                        print("Response:")
                        print(result["choices"][0]["message"]["content"])
                    if result["choices"][0]["finish_reason"] == "tool_calls":
                        print("Alert:")
                        alert = result["choices"][0]["message"]["tool_calls"][0]["alert"]
                        print("    Name:", alert["name"])
                        print("    Detected Event:", alert["detectedEvents"])
                        if "ntpTimestamp" in alert:
                            print("    NTP Time:", alert["ntpTimestamp"])
                        else:
                            print("    Time:", alert["offset"], "seconds")
                        print("    Details:", alert["details"])
                if result["usage"]:
                    print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
                    print(f"Processing Time: {result['usage']['query_processing_time']} seconds")
                print("----------------------------------------")
        except KeyboardInterrupt:
            print("User interrupted")
            response.close()
    else:
        result = response.json()
        print("Summarization finished")
        print("Request ID:", result["id"])
        print(
            "Request Creation Time:",
            datetime.utcfromtimestamp(result["created"]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        print("Model:", result["model"])
        print("Object:", result["object"])
        if result["media_info"]["type"] == "offset":
            print(
                "Media start offset: "
                + convert_seconds_to_string(result["media_info"]["start_offset"])
            )
            print(
                "Media end offset: " + convert_seconds_to_string(result["media_info"]["end_offset"])
            )
        elif result["media_info"]["type"] == "timestamp":
            start_time = format_ntp_timestamp(result["media_info"]["start_timestamp"])
            end_time = format_ntp_timestamp(result["media_info"]["end_timestamp"])
            print(f"Media time range: {start_time} - {end_time}")
            print(
                f"Full timestamps: {result['media_info']['start_timestamp']} to "
                f"{result['media_info']['end_timestamp']}"
            )
        print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
        print(f"Processing Time: {result['usage']['query_processing_time']} seconds")
        print("Response:")
        print(result["choices"][0]["message"]["content"])


def do_generate_vlm_captions(args):
    req_json = {
        "id": args.id,
        "model": args.model,
        "response_format": {"type": args.response_format},
        "enable_cv_metadata": args.enable_cv_metadata,
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
    if args.cv_pipeline_prompt:
        req_json["cv_pipeline_prompt"] = args.cv_pipeline_prompt
    if args.num_frames_per_chunk is not None:
        req_json["num_frames_per_chunk"] = args.num_frames_per_chunk
    if args.vlm_input_width is not None:
        req_json["vlm_input_width"] = args.vlm_input_width
    if args.vlm_input_height is not None:
        req_json["vlm_input_height"] = args.vlm_input_height
    if args.enable_reasoning:
        req_json["enable_reasoning"] = args.enable_reasoning

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
        print(f'curl -i -N -X POST {get_api_url("/generate_vlm_captions")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(
        get_api_url("/generate_vlm_captions"), json=req_json, stream=args.stream
    )
    check_err_response(response, True)
    if args.stream:
        client = sseclient.SSEClient(response)
        first_response = True
        try:
            for event in client.events():
                data = event.data.strip()
                if data == "[DONE]":
                    print("VLM Captions Generation Complete")
                    continue
                result = json.loads(data)
                if first_response:
                    print("Request ID:", result["id"])
                    print(
                        "Request Creation Time:",
                        datetime.utcfromtimestamp(result["created"]).strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    print("Model:", result["model"])
                    print(
                        "Note: VLM Captions generate raw chunk responses from the VLM model (not summaries)"
                    )
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
                    print("Raw VLM Caption Response:")
                    for chunk in result["chunk_responses"]:
                        start_time = chunk["start_time"]
                        end_time = chunk["end_time"]
                        if "T" in start_time:  # NTP timestamp format
                            start_time = format_ntp_timestamp(start_time)
                            end_time = format_ntp_timestamp(end_time)
                        print(f"[{start_time} - {end_time}] {chunk['content']}")

                        # Display reasoning if available
                        if chunk.get("reasoning_description"):
                            print(f"Reasoning: {chunk['reasoning_description']}")

                if result.get("usage"):
                    print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
                    print(f"Processing Time: {result['usage']['query_processing_time']} seconds")
                print("----------------------------------------")
        except KeyboardInterrupt:
            print("User interrupted")
            response.close()
    else:
        result = response.json()
        print("VLM Captions Generation finished")
        print("Request ID:", result["id"])
        print(
            "Request Creation Time:",
            datetime.utcfromtimestamp(result["created"]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        print("Model:", result["model"])
        print("Note: VLM Captions generate raw chunk responses from the VLM model (not summaries)")
        if result["media_info"]["type"] == "offset":
            print(
                "Media start offset: "
                + convert_seconds_to_string(result["media_info"]["start_offset"])
            )
            print(
                "Media end offset: " + convert_seconds_to_string(result["media_info"]["end_offset"])
            )
        print(f"Chunks processed: {result['usage']['total_chunks_processed']}")
        print(f"Processing Time: {result['usage']['query_processing_time']} seconds")

        # Display chunk responses in table format if available
        if "chunk_responses" in result and result["chunk_responses"]:
            print("\nRaw VLM Caption Responses (by chunk):")
            print("=" * 80)
            from tabulate import tabulate

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


def do_chat(args):
    req_json = {"id": args.id, "model": args.model}

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
    if args.enable_reasoning:
        req_json["enable_reasoning"] = args.enable_reasoning

    args.stream = True
    if args.stream:
        req_json["stream"] = True
        req_json["stream_options"] = {"include_usage": True}

    req_json["messages"] = [{"content": str(args.prompt), "role": "user"}]

    if args.print_curl_command:
        print(f'curl -i -N -X POST {get_api_url("/chat/completions")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(get_api_url("/chat/completions"), json=req_json, stream=args.stream)
    check_err_response(response, True)
    result = response.json()
    print("Response:")
    print(result["choices"][0]["message"]["content"])


def do_review_alert(args):
    import uuid
    from datetime import datetime, timezone

    # Generate ID and timestamp if not provided
    alert_id = args.id if args.id else str(uuid.uuid4())
    timestamp = (
        args.timestamp
        if args.timestamp
        else datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    # Parse meta labels if provided
    meta_labels = []
    if args.meta_labels:
        for label in args.meta_labels:
            if ":" in label:
                key, value = label.split(":", 1)
                meta_labels.append({"key": key.strip(), "value": value.strip()})
            else:
                print(f"Warning: Invalid meta label format '{label}'. Expected format: key:value")

    # Build VLM parameters
    vlm_params = {"prompt": args.prompt}

    if args.system_prompt:
        vlm_params["system_prompt"] = args.system_prompt

    if args.max_tokens is not None:
        vlm_params["max_tokens"] = args.max_tokens
    if args.temperature is not None:
        vlm_params["temperature"] = args.temperature
    if args.top_p is not None:
        vlm_params["top_p"] = args.top_p
    if args.top_k is not None:
        vlm_params["top_k"] = args.top_k
    if args.seed is not None:
        vlm_params["seed"] = args.seed

    # Build VSS parameters
    vss_params = {
        "vlm_params": vlm_params,
        "chunk_duration": args.chunk_duration,
        "chunk_overlap_duration": args.chunk_overlap_duration,
        "num_frames_per_chunk": args.num_frames_per_chunk,
        "cv_metadata_overlay": args.cv_metadata_overlay,
        "enable_reasoning": args.enable_reasoning,
        "debug": args.debug,
    }
    if args.do_verification:
        vss_params["do_verification"] = True

    # Build the complete request
    req_json = {
        "version": args.version,
        "id": alert_id,
        "@timestamp": timestamp,
        "sensor_id": args.sensor_id,
        "video_path": args.video_path,
        "confidence": args.confidence,
        "start_time": args.start_time if args.start_time is not None else 0.0,
        "end_time": args.end_time if args.end_time is not None else 0.0,
        "alert": {
            "severity": args.alert_severity,
            "status": "REVIEW_PENDING",
            "type": args.alert_type,
            "description": args.alert_description,
        },
        "event": {"type": args.event_type, "description": args.event_description},
        "vss_params": vss_params,
        "meta_labels": meta_labels,
    }
    if args.stream_name:
        req_json["stream_name"] = args.stream_name
    if args.cv_metadata_path:
        req_json["cv_metadata_path"] = args.cv_metadata_path

    if args.print_curl_command:
        print(f'curl -i -X POST {get_api_url("/reviewAlert")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    response = requests.post(get_api_url("/reviewAlert"), json=req_json)
    check_err_response(response, True)
    result = response.json()

    print("Alert review completed:")
    print(f"Request ID: {result['id']}")
    print(f"Review Status: {result['result']['status']}")
    print(f"Reviewed By: {result['result']['reviewed_by']}")
    print(f"Reviewed At: {result['result']['reviewed_at']}")
    verification_result = (
        result["result"]["verification_result"]
        if "verification_result" in result["result"]
        else "N/A"
    )
    print(f"Review Verification: {verification_result}")

    # Display reasoning if available
    if result["result"]["reasoning"]:
        print("\n" + "=" * 80)
        print("REASONING")
        print("=" * 80)
        print(result["result"]["reasoning"])
        print("=" * 80)

    if result["result"]["description"]:
        print(f"\nDescription: {result['result']['description']}")

    if result["result"]["error_string"]:
        print(f"Error: {result['result']['error_string']}")

    if result["result"].get("debug"):
        debug_info = result["result"]["debug"]
        print("\nDebug Information:")
        print(f"Selected Frame Timestamps: {debug_info['selected_frames_ts']}")


def do_add_live_stream(args):
    req_json = {
        "liveStreamUrl": args.live_stream_url,
    }
    if args.description:
        req_json["description"] = args.description
    if args.username:
        req_json["username"] = args.username
    if args.password:
        req_json["password"] = args.password

    if args.print_curl_command:
        print(f'curl -i -X POST {get_api_url("/live-stream")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    result = requests.post(get_api_url("/live-stream"), json=req_json)
    check_err_response(result, True)
    result_json = result.json()
    print(f"Live stream added - id: {result_json['id']}")


def do_list_live_streams(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/live-stream")}""")
        return
    result = requests.get(get_api_url("/live-stream"))
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
                    live_stream["chunk_duration"],
                    live_stream["chunk_overlap_duration"],
                    live_stream["summary_duration"],
                ]
                for live_stream in live_stream_list
            ],
            headers=[
                "ID",
                "URL",
                "Description",
                "Chunk\nDuration",
                "Chunk\nOverlap\nDuration",
                "Summary\nDuration",
            ],
            tablefmt="simple_grid",
            maxcolwidths=[36, 50, term_width - 36 - 50 - 8 - 8 - 8 - (1 + 3 * 6), 8, 8, 8],
        )
    )


def do_delete_live_stream(args):
    if args.print_curl_command:
        print(f"""curl -i -X DELETE {get_api_url("/live-stream/" + args.video_id)}""")
        return
    result = requests.delete(get_api_url("/live-stream/" + args.video_id))
    check_err_response(result, True)
    print("Live stream deleted")


def do_add_alert(args):
    req_json = {
        "liveStreamId": args.live_stream_id,
        "callback": args.callback_url,
        "events": args.events,
    }
    if args.callback_token:
        req_json["callbackToken"] = args.callback_token
    if args.callback_json_template:
        req_json["callbackJsonTemplate"] = args.callback_json_template

    if args.print_curl_command:
        print(f'curl -i -X POST {get_api_url("/alerts")} \\')
        print('    -H "Content-Type: application/json" \\')
        print(f"    --data \\\n'{json.dumps(req_json, indent=2)}'")
        return

    result = requests.post(get_api_url("/alerts"), json=req_json)
    check_err_response(result, True)
    result_json = result.json()
    print(f"Alert added - id: {result_json['id']}")


def do_list_alerts(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/alerts")}""")
        return
    result = requests.get(get_api_url("/alerts"))
    check_err_response(result, True)
    term_width = shutil.get_terminal_size()[0]
    alert_list = result.json()
    if not alert_list:
        print("No alerts added to the server")
        return
    print(
        tabulate(
            [
                [
                    alert["alertId"],
                    alert["liveStreamId"],
                    alert["events"],
                ]
                for alert in alert_list
            ],
            headers=[
                "Alert ID",
                "Live Stream ID",
                "Events",
            ],
            tablefmt="simple_grid",
            maxcolwidths=[36, 36, term_width - 36 - 36 - (1 + 3 * 3)],
        )
    )


def do_delete_alert(args):
    if args.print_curl_command:
        print(f"""curl -i -X DELETE {get_api_url("/alerts/" + args.alert_id)}""")
        return
    result = requests.delete(get_api_url("/alerts/" + args.alert_id))
    check_err_response(result, True)
    print("Alert deleted")


def do_list_recent_alerts(args):
    url = get_api_url("/alerts/recent")
    if args.live_stream_id:
        url += f"?live_stream_id={args.live_stream_id}"
    if args.print_curl_command:
        print(f"""curl -i -X GET {url}""")
        return

    result = requests.get(url)
    check_err_response(result, True)
    term_width = shutil.get_terminal_size()[0]
    alert_list = result.json()
    if not alert_list:
        print("No recent alerts found")
        return
    print(
        tabulate(
            [
                [
                    alert["alert_id"],
                    alert["live_stream_id"],
                    alert["detected_events"],
                    alert["ntp_timestamp"].split("T")[0]
                    + alert["ntp_timestamp"].split("T")[1].split(".")[0],
                    alert["alert_text"],
                ]
                for alert in alert_list
            ],
            headers=["Alert ID", "Live Stream ID", "Events", "Time (UTC)", "Alert Details"],
            tablefmt="simple_grid",
            maxcolwidths=[18, 18, 10, 20, term_width - 18 - 18 - 10 - 20 - (1 + 5 * 3)],
        )
    )


def do_alert_callback_server(args):
    app = FastAPI()

    @app.post("/via-alert-callback")
    async def print_alert(data: dict):
        print("Alert received:")
        print(json.dumps(data, indent=2))

    print("Server starting. Alert callback handler at path /via-alert-callback")

    uvicorn.run(app, host=args.host, port=args.port)


def do_list_models(args):
    if args.print_curl_command:
        print(f"""curl -i -X GET {get_api_url("/models")}""")
        return
    result = requests.get(get_api_url("/models"))
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
                    datetime.utcfromtimestamp(model["created"]).strftime("%Y-%m-%d %H:%M:%S"),
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
    result = requests.get(get_api_url("/metrics"))
    check_err_response(result, True)
    result = result.text
    print(result)


def do_server_health_check(args):
    health_check_type = "live" if args.liveness else "ready"
    url = get_api_url("/health/") + health_check_type
    if args.print_curl_command:
        print(f"""curl -i -X GET {url}""")
        return
    result = requests.get(url)
    check_err_response(result, True)
    print("VIA Server is " + health_check_type)


def main():
    global BASE_URL
    parser = get_parser()
    args = parser.parse_args()
    BASE_URL = args.backend
    if args.request == "add-file":
        do_add_file(args)
    if args.request == "list-files":
        do_list_files(args)
    if args.request == "file-info":
        do_get_file_info(args)
    if args.request == "file-content":
        do_get_file_content(args)
    if args.request == "delete-file":
        do_delete_file(args)
    if args.request == "summarize":
        do_summarize(args)
    if args.request == "generate-vlm-captions":
        do_generate_vlm_captions(args)
    if args.request == "add-live-stream":
        do_add_live_stream(args)
    if args.request == "list-live-streams":
        do_list_live_streams(args)
    if args.request == "delete-live-stream":
        do_delete_live_stream(args)
    if args.request == "add-alert":
        do_add_alert(args)
    if args.request == "list-alerts":
        do_list_alerts(args)
    if args.request == "delete-alert":
        do_delete_alert(args)
    if args.request == "list-recent-alerts":
        do_list_recent_alerts(args)
    if args.request == "alert-callback-server":
        do_alert_callback_server(args)
    if args.request == "list-models":
        do_list_models(args)
    if args.request == "server-metrics":
        do_server_metrics(args)
    if args.request == "server-health-check":
        do_server_health_check(args)
    if args.request == "chat":
        do_chat(args)
    if args.request == "review-alert":
        do_review_alert(args)


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print(f"Failed to connect to server {BASE_URL}")
        sys.exit(-1)

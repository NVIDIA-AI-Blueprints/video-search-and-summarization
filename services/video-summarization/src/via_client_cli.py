# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
import json
import os
import re
import shutil
import sys
from datetime import datetime

try:
    import requests
    import sseclient
    from tabulate import tabulate
    from tqdm import tqdm
except (ImportError, ModuleNotFoundError):
    print("Dependencies missing. Install using:")
    print("python3 -m pip install sseclient-py requests tabulate tqdm pyyaml")
    sys.exit(-1)

AWS_S3_URL_PATTERN = r"^s3://(?P<bucket>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*)/(?P<object>[^?\s]+)$"  # noqa: E501
AWS_S3_OBJECT_URL_PATTERN = r"""^https?://
        (?:
            (?P<bucket_vh>[a-z0-9.-]+)
            \.s3[.-](?P<region_vh>[a-z0-9-]+)\.amazonaws\.com/
            (?P<object_vh>.+)
        |
            s3[.-](?P<region_ps>[a-z0-9-]+)\.amazonaws\.com/
            (?P<bucket_ps>[a-z0-9.-]+)/(?P<object_ps>.+)
        )
        $
    """


def is_url(path: str) -> bool:
    """Check if the path is a URL (HTTP, HTTPS, or S3).

    Uses the same logic as asset_manager.py to detect URLs.

    Args:
        path: Path or URL to check

    Returns:
        True if path is a URL, False otherwise
    """
    if path.startswith("http://") or path.startswith("https://"):
        return True

    if re.match(AWS_S3_URL_PATTERN, path):
        return True

    if re.match(AWS_S3_OBJECT_URL_PATTERN, path, re.VERBOSE):
        return True

    return False


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


def add_dev_args(subparsers):

    if os.environ.get("VIA_DEV_API", "").lower() in ["true", "1"]:

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

        add_common_args(add_file)

        list_files = subparsers.add_parser(
            "list-files",
            help="List all files",
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
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

    add_dev_args(subparsers)

    summarize = subparsers.add_parser(
        "summarize",
        help="Trigger summary on an already added file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = summarize.add_argument_group(
        "Mandatory Arguments: Either --id or --url must be provided"
    )
    id_or_url = mandatory_args.add_mutually_exclusive_group(required=True)
    id_or_url.add_argument(
        "--id",
        action="append",
        type=str,
        help="ID of the file / live stream to trigger summary on",
    )
    id_or_url.add_argument(
        "--url",
        type=str,
        help="Direct URL to a video to summarize (HTTP/HTTPS/S3)",
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
        "--num-frames-per-chunk", help="Number of frames per chunk to use for the VLM", type=int
    )

    opt_args.add_argument("--vlm-input-width", help="VLM Input Width", type=int)
    opt_args.add_argument("--vlm-input-height", help="VLM Input Height", type=int)
    opt_args.add_argument(
        "--enable-audio",
        help="Enable transcription of the audio stream in the media",
        action="store_true",
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
    opt_args.add_argument(
        "--enable-vlm-structured-output",
        help="Enable structured JSON output from VLM",
        action="store_true",
    )
    opt_args.add_argument(
        "--disable-vlm-structured-output",
        help="Disable structured JSON output from VLM",
        action="store_true",
    )
    opt_args.add_argument(
        "--events",
        help="Comma-separated list of events to focus on",
        type=str,
    )
    opt_args.add_argument(
        "--objects-of-interest",
        help="Comma-separated list of objects to focus on",
        type=str,
    )
    opt_args.add_argument(
        "--scenario",
        help="Scenario description for video analysis",
        type=str,
    )
    opt_args.add_argument(
        "--schema",
        help="JSON schema string for structured output extraction",
        type=str,
    )
    opt_args.add_argument(
        "--batch-response-method",
        help="Method for batch response processing",
        type=str,
    )
    opt_args.add_argument(
        "--auto-generate-prompt",
        help="Enable automatic prompt generation based on schema and events",
        action="store_true",
    )
    opt_args.add_argument(
        "--time-metadata-keys",
        help="Comma-separated list of metadata keys containing time information",
        type=str,
    )
    opt_args.add_argument(
        "--override-vlm-prompt",
        help="Override the VLM prompt with the user supplied prompt",
        action="store_true",
    )
    opt_args.add_argument(
        "--enable-reasoning",
        help="Enable reasoning mode for the model",
        action="store_true",
    )
    add_common_args(summarize)

    generate_vlm_captions = subparsers.add_parser(
        "generate-vlm-captions",
        help="Generate VLM captions for an already added file / live stream",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mandatory_args = generate_vlm_captions.add_argument_group(
        "Mandatory Arguments: Either --id or --url must be provided"
    )
    mandatory_args.add_argument(
        "--id",
        action="append",
        type=str,
        help="ID of the file / live stream to generate VLM captions for",
    )
    mandatory_args.add_argument(
        "--url",
        type=str,
        help="URL of the video (HTTP/HTTPS/S3). Either --id or --url must be provided.",
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
    opt_args.add_argument("--vlm-input-width", help="VLM Input Width", type=int)
    opt_args.add_argument("--vlm-input-height", help="VLM Input Height", type=int)
    opt_args.add_argument(
        "--override-vlm-prompt",
        help="Override the VLM prompt with the user supplied prompt",
        action="store_true",
    )
    add_common_args(generate_vlm_captions)

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
    elif is_url(args.file):
        # Handle all types of URLs (HTTP, HTTPS, S3) by passing them as strings
        files = {"filename": (None, args.file)}
    else:
        files = {
            "file": open(args.file, "rb"),
        }
    files["purpose"] = (None, "vision")
    files["media_type"] = (None, "video")

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
        "model": args.model,
    }
    # Require one of --id or --url; attach whichever was provided
    if args.id:
        req_json["id"] = args.id
    if getattr(args, "url", None):
        req_json["url"] = args.url

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
    if args.vlm_input_width is not None:
        req_json["vlm_input_width"] = args.vlm_input_width
    if args.vlm_input_height is not None:
        req_json["vlm_input_height"] = args.vlm_input_height

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
    if args.custom_metadata is not None:
        req_json["custom_metadata"] = json.loads(args.custom_metadata)
    if args.delete_external_collection:
        req_json["delete_external_collection"] = True
    if args.enable_audio is not None:
        req_json["enable_audio"] = args.enable_audio

    if hasattr(args, "enable_vlm_structured_output") and args.enable_vlm_structured_output:
        req_json["enable_vlm_structured_output"] = True
    elif hasattr(args, "disable_vlm_structured_output") and args.disable_vlm_structured_output:
        req_json["enable_vlm_structured_output"] = False
    if hasattr(args, "events") and args.events:
        # Convert comma-separated string to list
        req_json["events"] = [e.strip() for e in args.events.split(",")]
    if hasattr(args, "objects_of_interest") and args.objects_of_interest:
        # Convert comma-separated string to list
        req_json["objects_of_interest"] = [o.strip() for o in args.objects_of_interest.split(",")]
    if hasattr(args, "scenario") and args.scenario:
        req_json["scenario"] = args.scenario
    if hasattr(args, "schema") and args.schema:
        req_json["schema"] = args.schema
    if hasattr(args, "batch_response_method") and args.batch_response_method:
        req_json["batch_response_method"] = args.batch_response_method
    if hasattr(args, "auto_generate_prompt") and args.auto_generate_prompt:
        req_json["auto_generate_prompt"] = True
    if hasattr(args, "time_metadata_keys") and args.time_metadata_keys:
        # Convert comma-separated string to list
        req_json["time_metadata_keys"] = [k.strip() for k in args.time_metadata_keys.split(",")]
    if hasattr(args, "override_vlm_prompt") and args.override_vlm_prompt:
        req_json["override_vlm_prompt"] = True
    if hasattr(args, "enable_reasoning") and args.enable_reasoning:
        req_json["enable_reasoning"] = True

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
        "model": args.model,
    }
    if args.id:
        req_json["id"] = args.id
    if getattr(args, "url", None):
        req_json["url"] = args.url

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
    if args.vlm_input_width is not None:
        req_json["vlm_input_width"] = args.vlm_input_width
    if args.vlm_input_height is not None:
        req_json["vlm_input_height"] = args.vlm_input_height
    if hasattr(args, "override_vlm_prompt") and args.override_vlm_prompt:
        req_json["override_vlm_prompt"] = True

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
    if args.request == "list-models":
        do_list_models(args)
    if args.request == "server-metrics":
        do_server_metrics(args)
    if args.request == "server-health-check":
        do_server_health_check(args)


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.ConnectionError:
        print(f"Failed to connect to server {BASE_URL}")
        sys.exit(-1)

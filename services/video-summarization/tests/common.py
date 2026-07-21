# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

import ast
import asyncio
import csv
import json
import multiprocessing
import os
import random
import threading
import time
from pathlib import Path

import requests
import sseclient

from via_server import ViaServer

# Path discovery for repo-relative paths
# This allows tests to work locally (after pip install -e .) and in Docker
TESTS_DIR = Path(__file__).parent
REPO_ROOT = TESTS_DIR.parent
SRC_DIR = REPO_ROOT / "src"
MODELS_DIR = SRC_DIR / "models"
SAMPLES_DIR = MODELS_DIR / "custom" / "samples"


class ViaTestServer:
    def __init__(
        self,
        server_args: str,
        port: int,
        ip="localhost",
        start_server=True,
        startup_timeout_sec: int = 30,
    ) -> None:
        self._ip = ip
        self._start_server = start_server
        self._server_args = server_args + f" --port {port} --log-level debug"
        self._port = port
        self._startup_timeout_sec = startup_timeout_sec

    def start_server(self):
        parser = ViaServer.get_argument_parser()
        args = parser.parse_args(self._server_args.split())
        self._server = ViaServer(args)

        def thread_func():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._server.run()
            loop.close()

        self._server_thread = threading.Thread(target=thread_func, daemon=True)
        self._server_thread.start()

        # Wait for server to start with timeout
        timeout = self._startup_timeout_sec
        start_time = time.time()
        while not self._server._server or not self._server._server.started:
            if time.time() - start_time > timeout:
                raise TimeoutError(
                    f"ViaTestServer failed to start within {timeout} seconds. "
                    "Check logs for initialization errors."
                )
            if not self._server_thread.is_alive():
                raise RuntimeError(
                    "ViaTestServer thread died before server started. "
                    "Check logs for initialization errors."
                )
            time.sleep(0.001)

        return self

    def stop_server(self):
        if self._server:
            print("stopping server")
            self._server._server.should_exit = True
            self._server_thread.join(timeout=30)

            # Ensure thread has stopped
            if self._server_thread.is_alive():
                main_thread = threading.main_thread()
                non_daemon = [
                    t.name
                    for t in threading.enumerate()
                    if t is not main_thread and not t.daemon and t.is_alive()
                ]
                print(f"Non-daemon threads still running: {non_daemon}")

            # Force-kill any remaining multiprocessing child processes
            for child in multiprocessing.active_children():
                child.kill()
                child.join(timeout=2)

            # Clean up server references
            self._server = None
            self._server_thread = None

            # Give OS time to release the port
            time.sleep(2)

    def __enter__(self):
        if self._start_server:
            return self.start_server()
        return

    def __exit__(self, type, value, tb):
        if self._start_server:
            self.stop_server()
        return

    def get(self, path: str) -> requests.models.Response:
        return requests.get(f"http://{self._ip}:{self._port}{path}")

    def post(self, path: str, **kwargs) -> requests.models.Response:
        return requests.post(f"http://{self._ip}:{self._port}{path}", **kwargs)

    def delete(self, path: str) -> requests.models.Response:
        return requests.delete(f"http://{self._ip}:{self._port}{path}")


def chat(
    question,
    t,
    video_id,
    model,
    chunk_size,
    temperature,
    top_p,
    top_k,
    max_new_tokens,
    seed,
):
    req_json = {
        "id": video_id,
        "model": model,
        "chunk_duration": chunk_size,
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_new_tokens,
        "top_p": top_p,
        "top_k": top_k,
        "stream": False,
        "stream_options": {"include_usage": False},
        "messages": [{"content": question, "role": "user"}],
    }

    resp = t.post("/chat/completions", json=req_json, stream=False)
    try:
        response = str(resp.json())
    except Exception:
        print("No JSON")
        return "ERROR: Server returned invalid JSON response"

    if resp.status_code != 200:
        print(f"ERROR: Server returned status code {resp.status_code}")
        try:
            error_details = resp.json()
            print(f"Error details: {error_details}")
            return f"ERROR: Server error {resp.status_code}: {error_details.get('message', 'Unknown error')}"
        except Exception:
            return f"ERROR: Server error {resp.status_code}: Unable to parse error details"

    data = ast.literal_eval(response)

    # Convert the data to a JSON-compatible format
    data_json = json.dumps(data)
    data = json.loads(data_json)
    choices = data["choices"]
    response_str = choices[0]["message"]["content"]
    return response_str


def get_response_table(responses):
    return (
        "<table><thead><th>Duration</th><th>Response</th></thead><tbody>"
        + "".join(
            [
                f'<tr><td>{convert_seconds_to_string(item["media_info"]["start_offset"])} '
                f'-> {convert_seconds_to_string(item["media_info"]["end_offset"])}</td>'
                f'<td>{item["choices"][0]["message"]["content"]}</td></tr>'
                for item in responses
            ]
        )
        + "</tbody></table>"
    )


def convert_seconds_to_string(seconds, need_hour=False, millisec=False):
    """
    Convert seconds to a human-readable time string format.

    Converts a numeric time value in seconds to a formatted string suitable
    for display in video timestamps, logs, or UI elements. The format adapts
    based on the magnitude of the time value and optional formatting flags.

    Args:
        seconds (float): The time value in seconds to convert. Can be fractional
            for sub-second precision.
        need_hour (bool, optional): If True, always include hours in the output
            format (HH:MM:SS) even if hours is zero. If False, hours are only
            included when the time value is >= 3600 seconds. Defaults to False.
        millisec (bool, optional): If True, append centiseconds (hundredths of
            a second) to the output string. Defaults to False.

    Returns:
        str: Formatted time string in one of the following formats:
            - "MM:SS" - when hours=0 and need_hour=False (e.g., "01:23")
            - "HH:MM:SS" - when hours>0 or need_hour=True (e.g., "01:02:34")
            - "MM:SS.CC" - with millisec=True (e.g., "01:23.45")
            - "HH:MM:SS.CC" - with hours and millisec=True (e.g., "01:02:34.56")

    Examples:
        >>> convert_seconds_to_string(90)
        '01:30'
        >>> convert_seconds_to_string(90, need_hour=True)
        '00:01:30'
        >>> convert_seconds_to_string(3661)
        '01:01:01'
        >>> convert_seconds_to_string(1.5, millisec=True)
        '00:01.50'
        >>> convert_seconds_to_string(3661.25, need_hour=True, millisec=True)
        '01:01:01.25'

    Note:
        The millisec parameter actually displays centiseconds (1/100 second),
        not milliseconds (1/1000 second). This is a historical naming artifact.
    """
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


def load_files(gt_file_name="groundtruth.txt", td_file_name="testdata.txt"):
    """
    Checks if the required CSV files exist in the given folder path and
    if all the Chunk_ID values in groundtruth.txt
    have corresponding entries in testdata.txt.

    Args:
        folder_path (str): The path to the folder containing the CSV files.

    Returns:
        dict: A dictionary containing the Chunk_ID, Expected Answer, and Answer values.
    """
    groundtruth_file = gt_file_name
    testdata_file = td_file_name

    # Check if the files exist
    if not os.path.exists(groundtruth_file) or not os.path.exists(testdata_file):
        raise FileNotFoundError("One or more required files not found")

    # Read the groundtruth file
    groundtruth_data = {}
    try:
        with open(groundtruth_file, "r") as groundtruth_csv:
            reader = csv.DictReader(groundtruth_csv)
            for row in reader:
                groundtruth_data[row["Chunk_ID"]] = row["Expected Answer"]
    except Exception as e:
        print(f"Error reading groundtruth file {groundtruth_file}: {e}")

    # Read the testdata file and check if all Chunk_ID values are present
    testdata_data = {}
    with open(testdata_file, "r") as testdata_csv:
        reader = csv.DictReader(testdata_csv)
        for row in reader:
            chunk_id = row["Chunk_ID"]
            testdata_data[chunk_id] = row["Answer"]
            if chunk_id not in groundtruth_data:
                print(
                    f"Error: Chunk_ID '{chunk_id}' in testdata.txt does not have"
                    " a corresponding entry in groundtruth.txt."
                )

    return {"groundtruth_data": groundtruth_data, "testdata_data": testdata_data}


def summarize(
    t,
    video_id,
    model,
    chunk_size,
    temperature,
    top_p,
    top_k,
    max_new_tokens,
    seed,
    summary_prompt=None,
    caption_summarization_prompt=None,
    summary_aggregation_prompt=None,
):
    req_json = {
        "id": video_id,
        "model": model,
        "chunk_duration": chunk_size,
        "temperature": temperature,
        "seed": seed,
        "max_tokens": max_new_tokens,
        "top_p": top_p,
        "top_k": top_k,
        "stream": True,
        "stream_options": {"include_usage": True},
        "summarize_batch_size": 4,
    }

    summarize_request_id = "unknown-" + str(random.randint(1, 1000000))

    if summary_prompt:
        req_json["prompt"] = summary_prompt
    if caption_summarization_prompt:
        req_json["caption_summarization_prompt"] = caption_summarization_prompt
    if summary_aggregation_prompt:
        req_json["summary_aggregation_prompt"] = summary_aggregation_prompt

    req_json["summarize"] = True

    resp = t.post("/summarize", json=req_json, stream=True)
    print("response is", str(resp))
    try:
        print("response is", str(resp.json()))
    except Exception:
        print("No JSON")

    assert resp.status_code == 200

    accumulated_responses = []
    client = sseclient.SSEClient(resp)
    for event in client.events():
        data = event.data.strip()

        if data == "[DONE]":
            continue
        response = json.loads(data)
        if response["id"]:
            summarize_request_id = response["id"]
        if response["choices"] and response["choices"][0]["finish_reason"] == "stop":
            accumulated_responses.append(response)

    if len(accumulated_responses) == 1:
        response_str = accumulated_responses[0]["choices"][0]["message"]["content"]
    elif len(accumulated_responses) > 1:
        response_str = get_response_table(accumulated_responses)
    else:
        response_str = ""

    print("summary response str is ", response_str)
    return response_str, summarize_request_id


def health_check(t):
    resp = t.get("/health/ready")
    print(f"response: {resp.status_code}")
    if resp.status_code != 200:
        print("Error: Server backend is not responding")
        return False
    return True


def generate_vlm_captions(t, req_json):
    resp = t.post("/generate_vlm_captions", json=req_json)
    assert resp.status_code == 200
    return resp.json()


MODEL_LIST = {
    "cosmos-reason2": ("cosmos-reason2", "git:https://huggingface.co/nvidia/Cosmos-Reason2-8B"),
    "cosmos-reason1": ("cosmos-reason1", "ngc:nim/nvidia/cosmos-reason-1-7b:1.1-fp8-dynamic"),
    # "vila-1.5": ("vila-1.5", "ngc:nim/nvidia/vila-1.5-40b:vila-yi-34b-siglip-stage3_1003_video_v8"),
    # "nvila": ("nvila", "ngc:nvidia/tao/nvila-highres:nvila-lite-15b-highres-lita"),
    "openai-compat": ("openai-compat", ""),
    "custom": ("", ""),
}

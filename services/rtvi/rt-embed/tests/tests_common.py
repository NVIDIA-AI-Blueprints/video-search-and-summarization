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

import asyncio
import copy
import csv
import json
import os
import random
import threading
import time

import requests
import sseclient

API_PREFIX = "/v1"

# Import RTSP stream helper
try:
    from .rtsp_stream_helper import RTSPStreamManager, cleanup_all_streams
except ImportError:
    RTSPStreamManager = None
    cleanup_all_streams = None


class ViaTestServer:
    def __init__(
        self,
        server_args: str,
        port: int,
        ip="localhost",
        start_server=True,
        server_module="server.rtvi_vlm_server",
    ) -> None:
        self._ip = ip
        self._start_server = start_server
        self._server_args = server_args + f" --port {port} --log-level debug"
        self._port = port
        self._server_module = server_module

    @staticmethod
    def _is_debug_enabled():
        """Check if debug output is enabled via RTVI_TEST_DEBUG environment variable."""
        return os.environ.get("RTVI_TEST_DEBUG", "").lower() in ("1", "true", "yes")

    @staticmethod
    def _get_max_wait_time():
        """Get server startup timeout from RTVI_TEST_SERVER_STARTUP_TIMEOUT environment variable."""
        return float(os.environ.get("RTVI_TEST_SERVER_STARTUP_TIMEOUT", "30.0"))

    def _debug_print(self, msg):
        """Print to stderr so pytest doesn't capture it (only if RTVI_TEST_DEBUG is enabled).

        Set RTVI_TEST_DEBUG=1 to enable debug output.
        """
        if self._is_debug_enabled():
            import sys

            sys.stderr.write(f"[ViaTestServer] {msg}\n")
            sys.stderr.flush()

    def start_server(self):
        # Dynamically import the server module
        import importlib

        # Configuration from environment variables
        enable_debug = self._is_debug_enabled()
        max_wait_time = self._get_max_wait_time()

        debug_print = self._debug_print  # Use instance method for consistency

        debug_print(f"Importing server module: {self._server_module}")
        server_mod = importlib.import_module(self._server_module)
        RTVIServer = server_mod.RTVIServer

        debug_print(f"Parsing server args: {self._server_args}")
        parser = RTVIServer.get_argument_parser()
        args = parser.parse_args(self._server_args.split())

        debug_print("Creating RTVIServer instance...")
        self._server = RTVIServer(args)
        debug_print("RTVIServer instance created")
        self._server_exception = None

        def thread_func():
            try:
                debug_print("Starting server thread...")
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._server.run()
                loop.close()
            except Exception as e:
                import traceback

                debug_print(f"Exception in server thread: {e}")
                debug_print(traceback.format_exc())
                self._server_exception = e
                raise

        self._server_thread = threading.Thread(target=thread_func, daemon=True)
        self._server_thread.start()
        debug_print("Server thread started, waiting for server to be ready...")

        # Wait for server to start with configurable timeout
        start_time = time.time()
        check_interval = 0.5
        last_status_time = start_time

        while not self._server._server or not self._server._server.started:
            current_time = time.time()
            elapsed = current_time - start_time

            # Print status every 5 seconds (only if debug enabled)
            if enable_debug and current_time - last_status_time >= 5.0:
                debug_print(f"Waiting for server to start... ({elapsed:.1f}s elapsed)")
                if self._server._server:
                    debug_print(f"  Server object exists: {self._server._server}")
                    started_flag = getattr(self._server._server, "started", "N/A")
                    debug_print(f"  Server started flag: {started_flag}")
                else:
                    debug_print("  Server object not yet created")
                last_status_time = current_time

            if self._server_exception:
                raise RuntimeError(
                    f"Server failed to start due to exception: {self._server_exception}"
                ) from self._server_exception
            if not self._server_thread.is_alive():
                raise RuntimeError("Server thread died during startup")
            if elapsed > max_wait_time:
                error_msg = (
                    f"Server failed to start within {max_wait_time} seconds. "
                    f"Elapsed: {elapsed:.1f}s. "
                )
                if self._server._server:
                    started_flag = getattr(self._server._server, "started", "N/A")
                    error_msg += f"Server object exists but started={started_flag}"
                else:
                    error_msg += "Server object not created yet."
                raise RuntimeError(error_msg)
            time.sleep(check_interval)

        debug_print(f"Server started successfully after {time.time() - start_time:.1f} seconds")
        return self

    def stop_server(self):
        if self._server:
            print("stopping server")
            self._server._server.should_exit = True
            self._server_thread.join()
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

    def delete(self, path: str, **kwargs) -> requests.models.Response:
        url = f"http://{self._ip}:{self._port}{path}"
        if "timeout" not in kwargs:
            kwargs["timeout"] = 5  # keep small; adjust as needed
        return requests.delete(url, **kwargs)


class TempEnv:
    def __init__(self, updated_env_vars: dict[str, str]):
        self._updated_env_vars = updated_env_vars

    def __enter__(self):
        self._original_env = copy.deepcopy(os.environ)
        os.environ.update(self._updated_env_vars)

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.environ.clear()
        os.environ.update(self._original_env)


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
    enable_chat=True,
    alert_tools=None,
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
        "enable_chat": enable_chat,
    }

    summarize_request_id = "unknown-" + str(random.randint(1, 1000000))

    if summary_prompt:
        req_json["prompt"] = summary_prompt
    if caption_summarization_prompt:
        req_json["caption_summarization_prompt"] = caption_summarization_prompt
    if summary_aggregation_prompt:
        req_json["summary_aggregation_prompt"] = summary_aggregation_prompt

    req_json["summarize"] = True
    req_json["enable_chat"] = enable_chat

    if alert_tools:
        req_json["tools"] = alert_tools

    resp = t.post("/summarize", json=req_json, stream=True)
    print("response is", str(resp))
    try:
        print("response is", str(resp.json()))
    except Exception:
        print("No JSON")

    assert resp.status_code == 200

    accumulated_responses = []
    past_alerts = []
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
        if response["choices"] and response["choices"][0]["finish_reason"] == "tool_calls":
            alert = response["choices"][0]["message"]["tool_calls"][0]["alert"]
            alert_str = (
                f"Alert Name: {alert['name']}\n"
                f"Detected Events: {', '.join(alert['detectedEvents'])}\n"
                f"NTP Time: {alert['ntpTimestamp']}\n"
                f"Details: {alert['details']}\n"
            )
            print("Got alert:", str(alert_str))
            past_alerts = past_alerts[int(len(past_alerts) / 99) :] + (
                [alert_str] if alert_str else []
            )

    if len(accumulated_responses) == 1:
        response_str = accumulated_responses[0]["choices"][0]["message"]["content"]
    elif len(accumulated_responses) > 1:
        response_str = get_response_table(accumulated_responses)
    else:
        response_str = ""

    print("summary response str is ", response_str)
    print("past_alerts", str(past_alerts))
    return response_str, summarize_request_id


def health_check(t):
    resp = t.get(f"{API_PREFIX}/ready")
    print(f"response: {resp.status_code}")
    if resp.status_code != 200:
        print("Error: Server backend is not responding")
        return False
    return True


def alert(t, req_json):
    """
    Execute alert verification for a test case

    Args:
        t: ViaTestServer instance
        req_json: JSON request body for the alert API


    Returns:
        dict: Result of the alert verification
    """
    resp = t.post("/reviewAlert", json=req_json)
    assert resp.status_code == 200
    return resp.json()


def generate_vlm_captions(t, req_json):
    resp = t.post("/generate_vlm_captions", json=req_json)
    assert resp.status_code == 200
    return resp.json()

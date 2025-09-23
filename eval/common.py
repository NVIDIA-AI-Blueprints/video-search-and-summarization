################################################################################
#  SPDX-FileCopyrightText: Copyright (c) 2024 NVIDIA CORPORATION & AFFILIATES.
#  All rights reserved.
#  SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
#  NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
#  property and proprietary rights in and to this material, related
#  documentation and any modifications thereto. Any use, reproduction,
#  disclosure or distribution of this material and related documentation
#  without an express license agreement from NVIDIA CORPORATION or
#  its affiliates is strictly prohibited.
################################################################################

import ast
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

from via_server import ViaServer


class ViaTestServer:

    def __init__(self, server_args: str, port: int, ip="localhost", start_server=True) -> None:
        self._ip = ip
        self._start_server = start_server
        self._server_args = server_args + f" --port {port} --log-level debug"
        self._port = port

    def start_server(self):
        parser = ViaServer.get_argument_parser()
        args = parser.parse_args(self._server_args.split())
        self._server = ViaServer(args)

        def thread_func():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._server.run()
            loop.close()

        self._server_thread = threading.Thread(target=thread_func)
        self._server_thread.start()
        while not self._server._server or not self._server._server.started:
            time.sleep(0.001)

        return self

    def stop_server(self):
        if self._server:
            print("stopping server")
            if self._server._server:
                self._server._server.should_exit = True
            if self._server_thread:
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

    def delete(self, path: str) -> requests.models.Response:
        return requests.delete(f"http://{self._ip}:{self._port}{path}")


class TempEnv:
    def __init__(self, updated_env_vars: dict[str, str]):
        self._updated_env_vars = updated_env_vars

    def __enter__(self):
        self._original_env = copy.deepcopy(os.environ)
        os.environ.update(self._updated_env_vars)

    def __exit__(self, exc_type, exc_val, exc_tb):
        os.environ.clear()
        os.environ.update(self._original_env)


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
    cv_pipeline_prompt=None,
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
        "enable_cv_metadata": True,
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
    resp = t.get("/health/ready")
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
    resp = t.post("/verifyAlert", json=req_json)
    assert resp.status_code == 200
    return resp.json()

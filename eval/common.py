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
import re
import threading
import time

import requests
import sseclient

from via_server import ViaServer


def sanitize_model_path(model_path):
    """
    Remove credentials (username:password@) from model paths to avoid exposing sensitive information.
    
    Args:
        model_path (str): Model path that may contain credentials
        
    Returns:
        str: Sanitized model path with credentials removed
        
    Examples:
        >>> sanitize_model_path("git:https://user:pass@huggingface.co/model")
        'git:https://huggingface.co/model'
        >>> sanitize_model_path("https://user:token@example.com/repo")
        'https://example.com/repo'
        >>> sanitize_model_path("ngc:nvidia/model:tag")
        'ngc:nvidia/model:tag'
    """
    if not model_path:
        return model_path
    
    # Pattern to match credentials in URLs: protocol://username:password@domain
    # This handles git:https://, https://, http://, etc.
    pattern = r'((?:git:)?https?://)([^:@]+:[^@]+@)'
    
    # Replace with just the protocol part
    sanitized = re.sub(pattern, r'\1', model_path)
    
    return sanitized


def sanitize_config(config):
    """
    Sanitize a configuration dictionary by removing credentials from model paths.
    
    Args:
        config (dict): Configuration dictionary that may contain model_path with credentials
        
    Returns:
        dict: Deep copy of config with sanitized model paths
    """
    sanitized = copy.deepcopy(config)
    
    # Sanitize VLM_Configurations.model_path if it exists
    if "VLM_Configurations" in sanitized:
        vlm_config = sanitized["VLM_Configurations"]
        if "model_path" in vlm_config and vlm_config["model_path"]:
            vlm_config["model_path"] = sanitize_model_path(vlm_config["model_path"])
    
    return sanitized


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
    resp = t.post("/reviewAlert", json=req_json)
    assert resp.status_code == 200
    return resp.json()


def generate_vlm_captions(t, req_json):
    resp = t.post("/generate_vlm_captions", json=req_json, stream=False)
    assert resp.status_code == 200
    return resp.json()


def build_vss_model_args(vlm_config, enable_ca_rag=False, ca_rag_config=None, 
                         guardrail_config=None, enable_cv=True, enable_audio=False):
    """
    Build VSS model arguments from VLM configuration.
    
    Common function used by both alert_eval.py and test_byov.py to avoid duplication.
    
    Args:
        vlm_config (dict): VLM configuration containing model type, path, etc.
        enable_ca_rag (bool): Whether to enable CA-RAG
        ca_rag_config (str): Path to CA-RAG config file
        guardrail_config (dict): Guardrail configuration
        enable_cv (bool): Whether to enable CV pipeline
        enable_audio (bool): Whether to enable audio processing
        
    Returns:
        str: Command-line arguments string for VSS server
    """
    import os
    
    model_args = ""
    
    # VLM model type configuration
    if vlm_config.get("model") == "nvila":
        model_args += " --vlm-model-type nvila"
        if vlm_config.get("model_path") is None:
            model_args += " --model-path ngc:nvidia/tao/nvila-highres:nvila-lite-15b-highres-lita"
        else:
            model_args += f" --model-path {vlm_config.get('model_path')}"
        if vlm_config.get("VLM_batch_size") is not None:
            model_args += f" --vlm-batch-size {vlm_config.get('VLM_batch_size')}"
            
    elif vlm_config.get("model") == "vila-1.5":
        model_args += " --vlm-model-type vila-1.5"
        if vlm_config.get("model_path") is not None:
            model_args += f" --model-path {vlm_config.get('model_path')}"
        else:
            model_args += " --model-path ngc:nim/nvidia/vila-1.5-40b:vila-yi-34b-siglip-stage3_1003_video_v8"
        if vlm_config.get("VLM_batch_size") is not None:
            model_args += f" --vlm-batch-size {vlm_config.get('VLM_batch_size')}"
            
    elif vlm_config.get("model") == "openai-compat":
        model_args += " --vlm-model-type openai-compat"
        
    elif vlm_config.get("model") in ("cosmos-reason1", "cosmos-reason2"):
        model_args += f" --vlm-model-type {vlm_config.get('model')}"
        if vlm_config.get("model_path") is not None:
            model_args += f" --model-path {vlm_config.get('model_path')}"
        os.environ["COSMOS_REASON1_USE_VLLM"] = "1"
        os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"
        os.environ["VLLM_USE_PYTORCH_CUDA_GRAPH"] = "0"
        os.environ["VLLM_DISABLE_CUSTOM_ALL_REDUCE"] = "1"
        
    elif vlm_config.get("model") == "custom":
        model_args += f" --model-path {vlm_config.get('model_path')}"
        
    elif vlm_config.get("model") in ("vllm-compatible", "vllm-compat"):
        model_args += " --vlm-model-type vllm-compatible"
        model_args += f" --model-path {vlm_config.get('model_path')}"
        
    elif vlm_config.get("model_path") is not None:
        # For models with model_path and unknown model type
        print(f"Unknown model type, forcing run by setting model path to {vlm_config.get('model_path')}")
        model_args += f" --model-path {vlm_config.get('model_path')}"
    else:
        raise ValueError(f"Invalid model type: {vlm_config.get('model')}")
    
    # Set OpenAI-compat environment variables
    if vlm_config.get("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME") is not None:
        os.environ["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"] = vlm_config["VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME"]
    if vlm_config.get("VIA_VLM_ENDPOINT") is not None:
        os.environ["VIA_VLM_ENDPOINT"] = vlm_config["VIA_VLM_ENDPOINT"]
    if vlm_config.get("AZURE_OPENAI_ENDPOINT") is not None:
        os.environ["AZURE_OPENAI_ENDPOINT"] = vlm_config["AZURE_OPENAI_ENDPOINT"]
    
    # Set VLM system prompt
    if vlm_config.get("VLM_SYSTEM_PROMPT") is not None:
        os.environ["VLM_SYSTEM_PROMPT"] = vlm_config["VLM_SYSTEM_PROMPT"]
    
    # Set VLM input resolution
    if vlm_config.get("VLM_INPUT_WIDTH") is not None:
        os.environ["VLM_INPUT_WIDTH"] = str(vlm_config["VLM_INPUT_WIDTH"])
    if vlm_config.get("VLM_INPUT_HEIGHT") is not None:
        os.environ["VLM_INPUT_HEIGHT"] = str(vlm_config["VLM_INPUT_HEIGHT"])
    
    # Frames per chunk
    if vlm_config.get("frames_per_chunk") is not None:
        model_args += f" --num-frames-per-chunk {vlm_config.get('frames_per_chunk')}"
    
    # CA-RAG configuration
    if enable_ca_rag and ca_rag_config:
        if os.path.exists(f"./eval/byov/{ca_rag_config}"):
            model_args += f" --ca-rag-config ./eval/byov/{ca_rag_config}"
        elif os.path.exists(ca_rag_config):
            model_args += f" --ca-rag-config {ca_rag_config}"
        else:
            model_args += " --disable-ca-rag"
    else:
        model_args += " --disable-ca-rag"
    
    # Guardrail configuration
    if guardrail_config and guardrail_config.get("enable", False):
        if guardrail_config.get("guardrail_config_file"):
            model_args += f" --guardrail-config {guardrail_config['guardrail_config_file']}"
    else:
        model_args += " --disable-guardrails"
    
    # CV pipeline
    if not enable_cv:
        model_args += " --disable-cv-pipeline"
    
    # Audio
    if enable_audio:
        model_args += " --enable-audio"
    
    return model_args


def create_eval_log_directory(req_ids, test_case_id, config_name, test_type="alert"):
    """
    Create directory and organize health eval logs for a test case.
    
    Common function to organize logs for both alert_eval and byov tests.
    
    Args:
        req_ids (str or list): Request ID(s) from the API calls. Can be a single string or list of strings.
        test_case_id (str): Unique test case identifier
        config_name (str): Configuration name
        test_type (str): Type of test ("alert" or "byov")
        
    Returns:
        str: Path to the created directory
    """
    import glob
    import shutil
    
    # Handle single req_id or list of req_ids
    if isinstance(req_ids, str):
        req_ids = [req_ids]
    elif req_ids is None:
        req_ids = []
    
    # Create the directory structure
    dir_path = f"logs/accuracy/{test_type}/{config_name}/{test_case_id}"
    print(f"Creating log directory: {dir_path}")
    os.makedirs(dir_path, exist_ok=True)
    
    # Files to copy (common logs)
    files_to_copy = [
        "/tmp/via-logs/via_engine.log",
        "/tmp/via-logs/via_ctx_rag.log",
    ]
    
    for file in files_to_copy:
        try:
            shutil.copy(file, dir_path)
        except FileNotFoundError:
            print(f"File not found: {file}; Skipping;")
        except IOError as e:
            print(f"Error copying file {file}: {e}; Skipping;")
    
    # Copy health eval logs for all request IDs
    # This includes: health summaries (JSON), GPU usage (CSV), plots (PNG)
    total_files_copied = 0
    for req_id in req_ids:
        wildcard_files = glob.glob(f"/tmp/via-logs/*{req_id}*")
        for file in wildcard_files:
            try:
                dest_file = os.path.join(dir_path, os.path.basename(file))
                shutil.copy(file, dest_file)
                file_type = "JSON" if file.endswith('.json') else "CSV" if file.endswith('.csv') else "PNG" if file.endswith('.png') else "other"
                print(f"Copied {file_type}: {os.path.basename(file)}")
                total_files_copied += 1
            except (FileNotFoundError, IOError) as e:
                print(f"Error copying file {file}: {e}")
    
    if total_files_copied > 0:
        print(f"Total files copied for {len(req_ids)} request(s): {total_files_copied}")
    else:
        print(f"Warning: No health eval files found for request IDs: {req_ids}")
    
    return dir_path

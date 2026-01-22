#  SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES.
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

import multiprocessing
import os
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil
import yaml
from dotenv import load_dotenv
from langchain.evaluation import load_evaluator
from langchain_openai import ChatOpenAI

from common import (
    ViaTestServer,
    alert,
    health_check,
    generate_vlm_captions,
    build_vss_model_args,
    create_eval_log_directory,
    sanitize_model_path,
    sanitize_config,
)
from scripts.create_alert_sheet import AccuracyMetrics, generate_results_summary

os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"
os.environ["VLLM_USE_PYTORCH_CUDA_GRAPH"] = "0"
os.environ["VLLM_DISABLE_CUSTOM_ALL_REDUCE"] = "1"
os.environ["ENABLE_VIA_HEALTH_EVAL"] = "true"

# Set multiprocessing start method to 'spawn' for CUDA compatibility
# This must be done before any other imports that might use multiprocessing

# Print current multiprocessing start method for debugging
print(f"Current multiprocessing start method: {multiprocessing.get_start_method(allow_none=True)}")


def get_vss_args(VSS_Config: dict):
    """
    Generate VSS (Video Scene Search) server arguments from configuration.

    Parses a VSS configuration dictionary and generates the appropriate command-line
    arguments for starting the VSS server with the specified VLM model, guardrails,
    and other parameters.

    Args:
        VSS_Config (dict): VSS configuration dictionary containing:
            - name (str): Configuration name
            - VLM_Configurations (dict): VLM model settings
            - Guardrail_Configurations (dict): Guardrail settings

    Returns:
        tuple: A tuple containing:
            - model_args (str): Command-line arguments for VSS server
            - model_name (str): Resolved model name for API calls
            - config_name (str): Name of the configuration

    Raises:
        ValueError: If an invalid model type is specified
    """
    # Get config name (should be set during config loading)
    config_name = VSS_Config.get("name", VSS_Config["VLM_Configurations"]["model"])
    vlm_config = VSS_Config["VLM_Configurations"]
    
    # Use common function to build model args
    model_args = build_vss_model_args(
        vlm_config=vlm_config,
        enable_ca_rag=False,  # Alerts don't use CA-RAG
        ca_rag_config=None,
        guardrail_config=VSS_Config.get("Guardrail_Configurations"),
        enable_cv=False,  # Alerts disable CV pipeline
        enable_audio=False
    )

    # Determine model name for API calls
    model_name = vlm_config["model"]
    if model_name == "openai-compat":
        model_name = vlm_config.get("VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME", "openai-compat")

    return model_args, model_name, config_name


@dataclass
class AlertTestCase:
    """
    Data class representing a complete test case for alert verification.

    Contains all necessary information to run both alert verification
    and chat-based VLM evaluations on a video clip.

    Attributes:
        id (str): Unique identifier for the test case
        clip_path (str): Path to the video clip file
        cv_metadata (str): Path to computer vision metadata (optional)
        prompts (List[str]): List of verification prompts/questions
        expected_answers (List[bool]): Expected boolean answers for prompts
        chat_prompts (List[str]): List of open-ended chat prompts
        chat_answers (List[str]): Expected answers for chat prompts
        expected_result (Optional[Dict[str, Any]]): Expected API result format
        iterations (int): Number of times to run each test
        config_name (str): Configuration name for this test case
    """

    id: str
    clip_path: str
    cv_metadata: str
    prompts: List[str]
    expected_answers: List[bool]  # True for "Yes", False for "No"
    chat_prompts: List[str]
    chat_answers: List[str] = None
    expected_result: Optional[Dict[str, Any]] = None
    iterations: int = 1
    config_name: str = ""


def _build_vlm_params(vlm_config_params, defaults):
    """
    Build VLM parameters from config, falling back to defaults.

    Args:
        vlm_config_params (dict, optional): VLM parameters from config
        defaults (dict): Default parameter values

    Returns:
        dict: Merged VLM parameters
    """
    if not vlm_config_params:
        return defaults

    params = defaults.copy()
    # Extract all supported parameters
    supported_params = [
        "prompt",
        "system_prompt",
        "temperature",
        "max_tokens",
        "top_p",
        "top_k",
        "seed",
        "enable_reasoning",
        "frames_per_chunk",
    ]
    for key in supported_params:
        if key in vlm_config_params:
            params[key] = vlm_config_params[key]

    return params


def create_request_json(
    test_case, prompt, prompt_index, vlm_params=None, do_verification=True
) -> Dict[str, Any]:
    """
    Create a JSON request payload for alert verification or chat queries.

    Constructs a properly formatted request JSON for the VIA alert API,
    including all necessary metadata, VLM parameters, and test case information.

    Args:
        test_case (AlertTestCase): Test case containing video path and metadata
        prompt (str): The verification prompt/question to ask
        prompt_index (int): Index of the prompt in the test case prompts list
        vlm_params (dict, optional): VLM parameters from config. Uses defaults if None.
        do_verification (bool): Whether this is a verification query (True) or chat query (False)

    Returns:
        Dict[str, Any]: Complete JSON request payload for alert API

    """
    alert_id = str(uuid.uuid4())

    # Build VLM parameters based on query type
    if do_verification:
        default_params = {
            "prompt": prompt,
            "system_prompt": "You are a helpful assistant. Answer the user's question based on the video.",
            "max_tokens": 200,
            "temperature": 0.3,
            "top_p": 0.3,
            "top_k": 40,
            "seed": 42,
        }
        severity = "MEDIUM"
        alert_type = "object_detection"
        chunk_duration = 0
    else:
        default_params = {
            "prompt": prompt,
            "system_prompt": "You are a helpful assistant. Answer the user's question based on the video.",
            "max_tokens": 200,
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "seed": 42,
        }
        severity = "LOW"
        alert_type = "chat_query"
        chunk_duration = 0

    built_vlm_params = _build_vlm_params(vlm_params, default_params)
    
    # Extract enable_reasoning from vlm_params (if present) and remove it
    # enable_reasoning should be at vss_params level, NOT inside vlm_params
    enable_reasoning = built_vlm_params.pop("enable_reasoning", False)

    num_frames_per_chunk = built_vlm_params.pop("frames_per_chunk", 0)

    req_json = {
        "version": "1.0",
        "id": alert_id,
        "@timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sensor_id": f"sensor_{test_case.id}",
        "video_path": test_case.clip_path,
        "confidence": 1.0,
        "alert": {
            "severity": severity,
            "status": "REVIEW_PENDING",
            "type": alert_type,
            "description": f"Query: {prompt}",
        },
        "event": {
            "type": "video_analysis",
            "description": f"Video analysis: {prompt}",
        },
        "vss_params": {
            "vlm_params": built_vlm_params,
            "chunk_duration": chunk_duration,
            "chunk_overlap_duration": 0,
            "num_frames_per_chunk": num_frames_per_chunk,
            "cv_metadata_overlay": False,
            "enable_reasoning": enable_reasoning,
            "debug": False,
            "do_verification": do_verification,
        },
        "meta_labels": [
            {"key": "prompt_index", "value": str(prompt_index)},
            {"key": "prompt_text", "value": prompt},
        ],
    }

    return req_json


def create_vlm_captions_request_json(
    video_id, prompt, model_name, vlm_params=None, stream=False, do_verification=False
) -> Dict[str, Any]:
    """
    Create a JSON request payload for VLM caption generation.

    Constructs a properly formatted request JSON similar to the format used in
    do_generate_vlm_captions from via_client_cli.py, focused on generating
    raw VLM captions without summarization.

    Args:
        video_id (str): The video ID returned from file upload
        prompt (str): The prompt for VLM caption generation
        model_name (str): The VLM model name to use
        vlm_params (dict, optional): VLM parameters from config. Uses defaults if None.
        stream (bool): Whether to stream the output using server-side events
        do_verification (bool): Whether this is a verification query (True) or chat query (False)

    Returns:
        Dict[str, Any]: Complete JSON request payload for generate_vlm_captions API

    """
    # Default VLM parameters based on query type
    if do_verification:
        default_params = {
            "prompt": prompt,
            "system_prompt": "You are a helpful assistant. Answer the user's question based on the video.",
            "temperature": 0.3,
            "top_p": 0.3,
            "top_k": 40,
            "seed": 42,
            "max_tokens": 200,
        }
    else:
        default_params = {
            "prompt": prompt,
            "system_prompt": "You are a helpful assistant. Answer the user's question based on the video.",
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 50,
            "seed": 42,
            "max_tokens": 200,
        }

    # Build final parameters by merging defaults with provided params
    built_params = _build_vlm_params(vlm_params, default_params)
    
    # Extract and remove enable_reasoning from built params (should be at request level, not in vlm_params)
    enable_reasoning = built_params.pop("enable_reasoning", False) if built_params else False

    num_frames_per_chunk = built_params.pop("frames_per_chunk", 0)

    # Start with base request structure
    req_json = {
        "id": [video_id],
        "model": model_name,
        "response_format": {"type": "text"},
        "enable_cv_metadata": False,
    }
    
    # Add optional model parameters
    if built_params is not None:
        req_json["temperature"] = built_params["temperature"]
        req_json["seed"] = built_params["seed"]
        req_json["top_p"] = built_params["top_p"]
        req_json["top_k"] = built_params["top_k"]
        req_json["max_tokens"] = built_params["max_tokens"]
        # Add prompts
        req_json["prompt"] = built_params["prompt"]
        req_json["system_prompt"] = built_params["system_prompt"]
    else:
        print("No VLM parameters provided, using default values in model.")
    req_json["chunk_duration"] = 0
    req_json["chunk_overlap_duration"] = 0

    req_json["num_frames_per_chunk"] = num_frames_per_chunk
    req_json["enable_reasoning"] = enable_reasoning


    return req_json


def create_alert_test_case(
    clip_path: str,
    prompts: List[str],
    expected_answers: List[bool],
    chat_prompts: List[str],
    chat_answers: List[str] = None,
    iterations: int = 1,
    test_id: str = None,
    cv_metadata: str = "",
    config_name: str = "",
):
    """
    Create an AlertTestCase instance for testing.

    Constructs a complete test case object containing all necessary information
    for running both alert verification and chat-based VLM evaluations.

    Args:
        clip_path (str): Path to the video clip file
        prompts (List[str]): List of alert verification prompts/questions
        expected_answers (List[bool]): Expected boolean answers for each prompt
        chat_prompts (List[str]): List of open-ended chat prompts for VLM evaluation
        chat_answers (List[str], optional): Expected answers for chat prompts
        iterations (int, optional): Number of times to run each test. Defaults to 1.
        test_id (str, optional): Custom test ID. Auto-generated if None.
        cv_metadata (str, optional): Computer vision metadata path. Defaults to "".
        config_name (str, optional): Configuration name. Defaults to "".

    Returns:
        AlertTestCase: Configured test case ready for execution

    Note:
        If test_id is None, it will be auto-generated based on the clip filename.
    """

    if test_id is None:
        test_id = f"alert_test_{Path(clip_path).stem}"

    return AlertTestCase(
        id=test_id,
        clip_path=clip_path,
        prompts=prompts,
        expected_answers=expected_answers,
        chat_prompts=chat_prompts,
        chat_answers=chat_answers,
        expected_result={"status": "success"},
        iterations=iterations,
        cv_metadata=cv_metadata,
        config_name=config_name,
    )


def process_single_result(raw_result, test_case, prompt, prompt_index):
    """
    Process a single alert verification result into standardized format.

    Extracts relevant information from the raw API response and formats it
    into a consistent structure for analysis and reporting.

    Args:
        raw_result (dict): Raw response from the alert verification API
        test_case (AlertTestCase): The test case that was executed
        prompt (str): The verification prompt that was used
        prompt_index (int): Index of the prompt in the test case

    Returns:
        dict: Processed result containing:
            - test_case_id: Unique test case identifier
            - config_name: Configuration name used
            - prompt: The verification prompt
            - ground_truth: Expected answer (boolean)
            - generated_answer: Model's answer (boolean)
            - status: Verification status from API
            - description: Model's description response
    """
    print("==PROCESSING SINGLE RESULT==")
    result = {}
    result["test_case_id"] = test_case.id
    result["config_name"] = test_case.config_name
    result["prompt"] = prompt
    result["ground_truth"] = test_case.expected_answers[prompt_index]

    # Handle new response format
    result["generated_answer"] = raw_result["result"]["verification_result"]
    result["description"] = raw_result["result"].get("description", "")
    result["status"] = raw_result["result"]["status"]

    return result


def process_chat_result(raw_result, test_case, chat_prompt, chat_index):
    """
    Process a single chat query result into standardized format.

    Extracts the description response from the raw API response for chat queries.

    Args:
        raw_result (dict): Raw response from the alert verification API
        test_case (AlertTestCase): The test case that was executed
        chat_prompt (str): The chat prompt that was used
        chat_index (int): Index of the chat prompt in the test case

    Returns:
        dict: Processed result containing:
            - test_case_id: Unique test case identifier
            - chat_prompt: The chat prompt
            - chat_result: Model's description response
            - chat_expected_answer: Expected answer for the chat prompt
    """
    print("==PROCESSING CHAT RESULT==")
    result = {}
    result["test_case_id"] = test_case.id
    result["chat_prompt"] = chat_prompt
    result["chat_expected_answer"] = (
        test_case.chat_answers[chat_index] if test_case.chat_answers else ""
    )

    # Handle response format from generate_vlm_captions API
    # The response contains chunk_responses with VLM outputs
    # Concatenate all chunk responses into a single result
    chunk_contents = [chunk["content"] for chunk in raw_result["chunk_responses"]]
    result["chat_result"] = " ".join(chunk_contents)

    print(result)

    return result


def process_result_list_for_one_config(
    results: List[Dict[str, Any]], config_name: str, chat_results: List[Dict[str, Any]], evaluator
):
    """
    Process and evaluate results for a specific configuration.

    Calculates accuracy metrics for alert verification results and evaluates
    chat responses using an LLM judge. Modifies the input lists in-place to
    add evaluation scores and metrics.

    Args:
        results (List[Dict[str, Any]]): List of alert verification results
        config_name (str): Name of the configuration being processed
        chat_results (List[Dict[str, Any]]): List of chat evaluation results
        evaluator: LangChain evaluator for scoring chat responses

    Returns:
        tuple: (results, chat_results, accuracy_metrics) with evaluation metrics

    Note:
        This function modifies the input lists in-place by adding:
        - chat_score and chat_reasoning to each chat_result
        Returns accuracy_metrics calculated for the entire configuration
    """
    print("==PROCESSING RESULT LIST FOR ONE CONFIG==")
    accuracy_metrics = AccuracyMetrics()
    for result in results:
        if result["generated_answer"] == result["ground_truth"]:
            if result["generated_answer"] is True:
                accuracy_metrics.true_positives += 1  # Correctly predicted positive
            else:
                accuracy_metrics.true_negatives += 1  # Correctly predicted negative
        elif result["generated_answer"] is True and result["ground_truth"] is False:
            accuracy_metrics.false_positives += 1  # Predicted positive, but was negative
        elif result["generated_answer"] is False and result["ground_truth"] is True:
            accuracy_metrics.false_negatives += 1  # Predicted negative, but was positive

    if chat_results:
        for chat_result in chat_results:
            # Evaluate chat responses using LLM judge
            try:
                chat_eval_result = evaluator.evaluate_strings(
                    prediction=chat_result["chat_result"],
                    reference=chat_result["chat_expected_answer"],
                    input=chat_result["chat_prompt"],
                )
                # Add evaluation results directly to the existing chat_result
                chat_result["config_name"] = config_name
                chat_result["chat_score"] = chat_eval_result["score"]
                chat_result["chat_reasoning"] = chat_eval_result["reasoning"]
            except Exception as e:
                print(f"Error evaluating chat result: {e}")
                print(f"  Prompt: {chat_result['chat_prompt']}")
                print(f"  VLM Response: {chat_result['chat_result'][:200] if len(chat_result['chat_result']) > 200 else chat_result['chat_result']}")
                print(f"  Expected: {chat_result['chat_expected_answer'][:200] if len(chat_result['chat_expected_answer']) > 200 else chat_result['chat_expected_answer']}")
                # Add default values so processing can continue
                chat_result["config_name"] = config_name
                chat_result["chat_score"] = 0
                chat_result["chat_reasoning"] = f"Evaluation failed: {str(e)}"

    return results, chat_results, accuracy_metrics


def evaluate_alerts(test_cases, config_name, model_args, model_name, vlm_params=None):
    """
    Execute alert and generate_vlm_captions verification tests for a specific configuration.

    Runs a complete test suite including alert verification and chat evaluations
    using a VIA server instance with the specified configuration. Processes all
    test cases and collects results for analysis.

    Args:
        test_cases (List[AlertTestCase]): List of test cases to execute
        config_name (str): Name of the configuration being tested
        model_args (str): Command-line arguments for starting VIA server
        model_name (str): Name of the VLM model for API calls
        vlm_params (dict, optional): VLM parameters from config

    Returns:
        tuple: (results, chat_results, accuracy_metrics) containing:
            - results (List[dict]): Alert verification results
            - chat_results (List[dict]): Chat evaluation results
            - accuracy_metrics (AccuracyMetrics): Calculated metrics for the configuration

    Note:
        This function starts a VIA server instance, runs all tests,
        processes results, and ensures proper cleanup.
    """
    print(f"Evaluating alert config: {config_name}")
    port = os.getenv("BACKEND_PORT")
    if not port:
        raise ValueError("BACKEND_PORT environment variable must be set")

    try:
        with ViaTestServer(
            f"{model_args} " "--log-level debug ",
            port,
        ) as server:

            if server is None:
                print("Error: ViaTestServer failed to start properly")
            # ping the server backend
            try:
                health_check(server)
            except Exception as e:
                print(f"Error: {e}")
                return

            print("Server backend is responding")
            
            registered_model_name = None
            # Get the registered model on the server
            try:
                models_resp = server.get("/models")
                if models_resp.status_code == 200:
                    models = models_resp.json()
                    available_models = [m['id'] for m in models.get('data', [])]
                    print(f"Available models on server: {available_models}")
                    if available_models:
                        registered_model_name = available_models[-1] # Use last model registered on the server
                        print(f"Registered model name: '{registered_model_name}'")
                else:
                    print(f"Failed to get models list: {models_resp.status_code}")
            except Exception as e:
                print(f"Error checking available models: {e}")
            
            results = []
            chat_results = []
            for x, test_case in enumerate(test_cases):
                print(
                    f"Processing test case {x+1}/{len(test_cases)}: {Path(test_case.clip_path).name}"
                )
                test_case.config_name = config_name
                # Store the original test case ID to prevent building on itself
                original_test_case_id = test_case.id
                base_test_case_id = f"{config_name}_{original_test_case_id}"
                
                # Track all request IDs for this test case to capture health eval logs
                test_case_request_ids = []

                # Upload video file for this test case
                try:
                    resp = server.post(
                        "/files",
                        files={
                            "filename": (None, test_case.clip_path),
                            "purpose": (None, "vision"),
                            "media_type": (None, "video"),
                        },
                    )
                    if resp.status_code == 200:
                        video_id = resp.json()["id"]
                        print(f"Uploaded video with ID: {video_id}")
                    else:
                        print(f"Failed to upload video: {resp.status_code}")
                        continue
                except Exception as e:
                    print(f"Error uploading video: {e}")
                    continue

                for prompt_index, prompt in enumerate(test_case.prompts):
                    print(
                        f"  Evaluating prompt {prompt_index+1}/{len(test_case.prompts)}: {prompt}"
                    )
                    req_json = create_request_json(
                        test_case, prompt, prompt_index, vlm_params, do_verification=True
                    )
                    print(f"****** req_json: {req_json} \n *******")
                    # Update video_path to use the uploaded video ID
                    # req_json["video_path"] = video_id

                    for i in range(test_case.iterations):
                        print(f"    Iteration {i+1}/{test_case.iterations}")
                        # Build test_case_id from the original base each time
                        if test_case.iterations > 1:
                            test_case_id = f"{base_test_case_id}_{i+1}"
                        else:
                            test_case_id = base_test_case_id
                        test_case.id = test_case_id
                        raw_result = alert(server, req_json)
                        
                        # Track request ID for health eval logs
                        if "id" in raw_result:
                            test_case_request_ids.append(raw_result["id"])
                        
                        try:
                            result = process_single_result(
                                raw_result, test_case, prompt, prompt_index
                            )
                            results.append(result)
                        except Exception as e:
                            print(f"    Error processing result: {e}")

                # Process chat_prompts for this test case (after all alert prompts)
                if hasattr(test_case, "chat_prompts") and test_case.chat_prompts:
                    print(
                        f"Processing {len(test_case.chat_prompts)} chat prompts for test case {test_case_id}"
                    )

                    # Process each chat prompt using the consolidated request function
                    for chat_idx, chat_prompt in enumerate(test_case.chat_prompts):
                        print(
                            f"  Processing chat prompt {chat_idx+1}/"
                            f"{len(test_case.chat_prompts)}: {chat_prompt}"
                        )

                        # Create chat request using consolidated function
                        chat_req_json = create_vlm_captions_request_json(
                            video_id, chat_prompt, registered_model_name, vlm_params, do_verification=False
                        )
                        print(f"****** chat_req_json: {chat_req_json} \n *******")

                        try:
                            chat_raw_result = generate_vlm_captions(server, chat_req_json)
                            
                            # Track request ID for health eval logs
                            if "id" in chat_raw_result:
                                test_case_request_ids.append(chat_raw_result["id"])
                            
                            chat_result = process_chat_result(
                                chat_raw_result, test_case, chat_prompt, chat_idx
                            )
                            chat_results.append(chat_result)
                        except Exception as e:
                            print(f"  Error processing chat prompt: {e}")
                            # Add a fallback result
                            chat_results.append(
                                {
                                    "test_case_id": test_case_id,
                                    "chat_prompt": chat_prompt,
                                    "chat_result": f"Error: {e}",
                                    "chat_expected_answer": (
                                        test_case.chat_answers[chat_idx]
                                        if test_case.chat_answers
                                        else ""
                                    ),
                                }
                            )

                # Create directory and capture health eval logs for this test case
                try:
                    # Use all collected request IDs from alert and chat API calls
                    log_dir = create_eval_log_directory(
                        req_ids=test_case_request_ids,
                        test_case_id=test_case_id,
                        config_name=config_name,
                        test_type="alert"
                    )
                    print(f"Health eval logs saved to: {log_dir} (captured {len(test_case_request_ids)} request IDs)")
                except Exception as e:
                    print(f"Warning: Failed to save health eval logs: {e}")
                
                # Restore original test case ID for potential reuse
                test_case.id = original_test_case_id

            # Process results after all test cases are complete
            results, chat_results, accuracy_metrics = process_result_list_for_one_config(
                results, config_name, chat_results, evaluator
            )
            return results, chat_results, accuracy_metrics

    except Exception as e:
        import traceback
        print(f"\n{'='*80}")
        print(f"ERROR in evaluate_alerts for config: {config_name}")
        print(f"{'='*80}")
        print(f"Exception type: {type(e).__name__}")
        print(f"Exception message: {e}")
        print(f"\nFull traceback:")
        traceback.print_exc()
        print(f"\nDebug info:")
        print(f"  - config_name: {config_name}")
        print(f"  - num test_cases: {len(test_cases)}")
        # Sanitize model_args to remove any credentials before printing
        sanitized_model_args = sanitize_model_path(model_args) if 'model_args' in locals() else 'Not set'
        print(f"  - model_args: {sanitized_model_args}")
        print(f"{'='*80}\n")
        return [], [], AccuracyMetrics()


if __name__ == "__main__":
    load_dotenv()
    os.environ["NVIDIA_VISIBLE_DEVICES"] = "0"

    # Ensure logs directory exists
    os.makedirs("logs/accuracy/alert", exist_ok=True)
    print("Created logs directory: logs/accuracy/alert")

    test_cases = []
    # Load alert config
    with open("eval/alerts/alert_config.yaml", "r") as f:
        alert_config = yaml.safe_load(f)

    if alert_config["clip_directory"] is not None:
        clip_directory = alert_config["clip_directory"]
        llm_model = alert_config["LLM_Judge_Model"]

    else:
        clip_directory = "clips"

    if alert_config["LLM_Judge_Model"] == "gpt-4o":
        llm = ChatOpenAI(
            model="gpt-4o",
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    elif alert_config["LLM_Judge_Model"] == "llama-3.1-70b-instruct":
        llm = ChatOpenAI(
            model="nvdev/meta/llama-3.1-70b-instruct",
            base_url="https://integrate.api.nvidia.com/v1",
            api_key=os.getenv("NVIDIA_API_KEY"),
        )
    elif os.getenv("OPENAI_API_KEY") is not None:
        llm = ChatOpenAI(
            model=alert_config["LLM_Judge_Model"],
            api_key=os.getenv("OPENAI_API_KEY"),
        )
    else:
        print(f"Invalid LLM model: {alert_config['LLM_Judge_Model']}")
        llm = None

    accuracy_criteria = {
        "accuracy": """Score 1: The answer is completely unrelated to the reference.
                Score 3: The answer has minor relevance but does not align with the reference.
                Score 5: The answer has moderate relevance but contains inaccuracies.
                Score 7: The answer aligns with the reference but has minor errors or omissions.
                Score 10: The answer is completely accurate and aligns perfectly with the reference."""
    }
    try:
        evaluator = load_evaluator("labeled_score_string", criteria=accuracy_criteria, llm=llm)
        if evaluator is None:
            raise ValueError("Failed to load evaluator")
    except Exception as e:
        print(f"Error: {e}")
        evaluator = None

    # create test cases
    for clip in alert_config["Clips"]:
        clip_file = clip["clip"]
        prompts_list = []
        expected_answers_list = []
        chat_prompts_list = []
        chat_answers_list = []
        for prompt in clip["alert_queries"]:
            prompts_list.append(prompt["query"])
            expected_answers_list.append(prompt["expected_verification"])
        # Handle chat_prompts if they exist, otherwise use empty list
        if "chat_queries" in clip and clip["chat_queries"]:
            for chat_prompt in clip["chat_queries"]:
                chat_prompts_list.append(chat_prompt["query"])
                chat_answers_list.append(chat_prompt["expected_answer"])

        cv_metadata = clip["cv_metadata"]
        # Construct full path to clip file (clips are in clips/ directory relative to via-engine root)
        clip_path = os.path.join(clip_directory, clip_file)

        test_case = create_alert_test_case(
            clip_path=clip_path,
            prompts=prompts_list,
            expected_answers=expected_answers_list,
            iterations=clip["iterations"],
            chat_prompts=chat_prompts_list if chat_prompts_list else [],
            chat_answers=chat_answers_list if chat_answers_list else [],
            cv_metadata=cv_metadata,
        )
        test_cases.append(test_case)

    # Run test suite and print results summary
    print("\nStarting alert verification performance test...")
    print(f"  - {len(test_cases)} test cases")

    total_prompts = sum(len(tc.prompts) for tc in test_cases)
    total_iterations = sum(tc.iterations for tc in test_cases)
    total_verifications = sum(len(tc.prompts) * tc.iterations for tc in test_cases)

    print(f"  - {total_iterations} total iterations")
    print(f"  - {total_prompts} unique prompts")
    print(f"  - {total_verifications} total verifications")

    configs = []
    configs_info = {}
    config_names_list = []
    config_name_counts = {}  # Track config name usage for unique indexing
    
    for i, VSS_Config in enumerate(alert_config["VSS_Configurations"]):
        # Check if config is wrapped in a dict with name as key (e.g., {'Config_1': {...}})
        # Wrapped config has a single key that is NOT "VLM_Configurations"
        if isinstance(VSS_Config, dict) and "VLM_Configurations" not in VSS_Config:
            # Config is wrapped - extract the name and actual config
            config_dict_key = list(VSS_Config.keys())[0]
            actual_config = VSS_Config[config_dict_key]
            config = actual_config
        else:
            # Config is already unwrapped or has VLM_Configurations at top level
            config = VSS_Config
        
        # Generate a descriptive unique name: model-name + last5chars + index
        model_name = config["VLM_Configurations"]["model"]
        model_path = config["VLM_Configurations"].get("model_path", "")
        
        # Extract meaningful identifier from model path
        if model_path:
            # Handle git URLs: extract last part after final /
            if "git:" in model_path or "huggingface.co" in model_path:
                # For git URLs, get the last part (e.g., "Cosmos-Reason2-8B-1208")
                path_parts = model_path.split('/')[-1]
                # Get last 5-10 chars depending on content
                if len(path_parts) >= 10:
                    path_suffix = path_parts[-10:]
                else:
                    path_suffix = path_parts[-5:] if len(path_parts) >= 5 else path_parts
            elif "ngc:" in model_path:
                # For NGC paths like "ngc:nvstaging/nim/cosmos-reason-2-8b:hf-v2"
                # Extract the tag/version after the last ":"
                if ":" in model_path.split('/')[-1]:
                    path_suffix = model_path.split(':')[-1]
                else:
                    path_suffix = model_path.split('/')[-1][-5:]
            else:
                # Default: last 5 characters of the path
                path_suffix = model_path.rstrip('/').split('/')[-1][-5:]
            
            # Clean up special characters for readability
            path_suffix = path_suffix.replace(':', '').replace('-', '_')
        else:
            # No model path specified
            path_suffix = "default"
        
        # Create base name
        base_name = f"{model_name}_{path_suffix}"
        
        # Add index if this base name has been used before
        if base_name in config_name_counts:
            config_name_counts[base_name] += 1
            unique_name = f"{base_name}_{config_name_counts[base_name]}"
        else:
            config_name_counts[base_name] = 1
            unique_name = base_name
        
        # Add the name field to the config
        config["name"] = unique_name
        configs.append(config)
        config_names_list.append(unique_name)
    
    print(f"Loaded {len(configs)} configurations: {config_names_list}")
    # Sanitize configs before printing to avoid exposing credentials
    sanitized_configs_for_print = [sanitize_config(cfg) for cfg in configs]
    print(f"configs: {sanitized_configs_for_print}")
    all_results = {}

    # Run test suite for all alerts each VSS configuration provided
    for i, VSS_Config in enumerate(configs):
        model_args, model_name, config_name = get_vss_args(VSS_Config)
        # Store in configs_info using the actual config_name from get_vss_args
        configs_info[config_name] = VSS_Config
        # Extract VLM parameters from config
        vlm_params = VSS_Config["VLM_Configurations"].get("vlmParams", None)

        if vlm_params is None:
            vlm_params = {}

        results, chat_results, accuracy_metrics = evaluate_alerts(
            test_cases, config_name, model_args, model_name, vlm_params
        )
        all_results[config_name] = {}
        all_results[config_name]["alerts"] = results
        all_results[config_name]["vlm_chat"] = chat_results
        all_results[config_name]["accuracy_metrics"] = accuracy_metrics
        print(f"Results for {config_name}: {len(results)} alert verification results")
        print(f"Chat results: {len(chat_results)} chat responses collected")

    # Sanitize configs_info before passing to Excel generation to avoid exposing credentials
    sanitized_configs_info = {name: sanitize_config(cfg) for name, cfg in configs_info.items()}
    output_file = "eval/alert_eval_results.xlsx"
    generate_results_summary(all_results, sanitized_configs_info, output_file=output_file)
    
    print("\n" + "="*80)
    print("All tests complete!")
    print("="*80)
    print(f"📊 Excel report written to: {output_file}")
    print(f"📁 Health eval logs saved to: logs/accuracy/alert/")
    print("="*80)

    # Wait a moment for any final operations
    time.sleep(1)

    # Force cleanup of any remaining threads and processes
    print("Cleaning up remaining threads and processes...")

    # Get current process
    current_process = psutil.Process()

    # Terminate any child processes
    for child in current_process.children(recursive=True):
        try:
            child.terminate()
            child.wait(timeout=5)
        except psutil.TimeoutExpired:
            child.kill()
        except psutil.NoSuchProcess:
            pass

    # List all active threads before cleanup
    print("Active threads before cleanup:")
    for thread in threading.enumerate():
        print(f"  - {thread.name} (daemon: {thread.daemon})")

    # Force cleanup of hanging threads by setting a short timeout
    # and then forcing exit
    print("Waiting for threads to finish...")
    time.sleep(2)  # Give threads a chance to finish naturally

    # Force exit to ensure all threads are terminated
    print("Script completed successfully, forcing exit...")
    os._exit(0)

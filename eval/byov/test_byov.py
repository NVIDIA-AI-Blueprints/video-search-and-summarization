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

"""
Pytest-based version of byov testing that runs each VLM configuration in separate processes
for better cleanup and memory management.
"""

import multiprocessing
import os
import sys
import time

import pytest

# Import after setting environment
from byov_parser import ByovParser
from byov_run_functions import (
    byov_create_dir_and_move_files,
    collect_responses,
    evaluate_qa,
    evaluate_summary,
)
from langchain.evaluation import load_evaluator
from langchain_openai import ChatOpenAI
from sse_starlette.sse import AppStatus

from common import ViaTestServer, chat, summarize, build_vss_model_args
from scripts.get_summary_qa_results_into_xlsx import (
    create_excel_report,
    process_results,
)

# Set multiprocessing environment variables
os.environ["VLLM_WORKER_MULTIPROC_METHOD"] = "spawn"
os.environ["VLLM_USE_PYTORCH_CUDA_GRAPH"] = "0"
os.environ["VLLM_DISABLE_CUSTOM_ALL_REDUCE"] = "1"
os.environ["ENABLE_VIA_HEALTH_EVAL"] = "true"

try:
    multiprocessing.set_start_method("spawn", force=True)
except RuntimeError:
    pass


# Get test configurations
def get_test_configs():
    """Load test configurations from the byov config file, grouped by unique config name"""
    config_file = os.getenv(
        "BYOV_CONFIG_FILE", "/opt/nvidia/via/via-engine/eval/byov/byov_config.yaml"
    )

    parser = ByovParser(config_file)
    videos = parser.get_videos()
    vss_configs = parser.get_vss_configs()

    # Create unique name for each config: model-name + path-suffix + index
    model_groups = {}
    config_name_counts = {}  # Track config name usage for unique indexing
    
    for i, vss_config in enumerate(vss_configs):
        vlm_name = vss_config["VLM_Configurations"]["model"]
        model_path = vss_config["VLM_Configurations"].get("model_path", "")
        
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
        base_name = f"{vlm_name}_{path_suffix}"
        
        # Add index if this base name has been used before
        if base_name in config_name_counts:
            config_name_counts[base_name] += 1
            unique_name = f"{base_name}_{config_name_counts[base_name]}"
        else:
            config_name_counts[base_name] = 1
            unique_name = base_name
        
        # Store config with unique name
        model_groups[unique_name] = {
            "vlm_config": vss_config,
            "config_name": unique_name,
            "videos": [],
        }
        
        # Add all videos for this config
        for video in videos:
            model_groups[unique_name]["videos"].append(video)

    return model_groups


def build_model_args(vss_config, video_config, ca_rag_config):
    """Build model arguments string based on VLM configuration"""
    # Extract the VLM configuration from the VSS config
    vlm_config = vss_config["VLM_Configurations"]
    guardrail_config = vss_config.get("Guardrail_Configurations", {})
    
    # Use common function to build model args
    model_args = build_vss_model_args(
        vlm_config=vlm_config,
        enable_ca_rag=(ca_rag_config is not None),
        ca_rag_config=ca_rag_config,
        guardrail_config=guardrail_config,
        enable_cv=video_config.get("enable_cv", True),
        enable_audio=video_config.get("enable_audio", False)
    )
    
    return model_args


def setup_llm_judge(judge_name):
    """Set up the LLM judge for evaluation"""
    if judge_name == "gpt-4o":
        openai_key = os.getenv("OPENAI_API_KEY")
        if openai_key:
            return ChatOpenAI(model="gpt-4o", api_key=openai_key)
        else:
            raise ValueError("OPENAI_API_KEY environment variable not set")
    elif judge_name == "llama-3.1-70b-instruct":
        nvidia_key = os.getenv("NVIDIA_API_KEY")
        if nvidia_key:
            return ChatOpenAI(
                model="nvdev/meta/llama-3.1-70b-instruct",
                base_url="https://integrate.api.nvidia.com/v1",
                api_key=nvidia_key,
            )
        else:
            raise ValueError("NVIDIA_API_KEY environment variable not set")
    else:
        return ChatOpenAI(
            model=judge_name,
            api_key=os.getenv("OPENAI_API_KEY"),
        )


# Parametrize tests to run each VLM config in separate process
@pytest.mark.parametrize("model_name", get_test_configs().keys())
def test_vlm_config(model_name):
    """
    Test a specific VLM configuration with all videos.
    Each VLM model gets its own server instance that processes all videos.
    """
    model_groups = get_test_configs()
    model_group = model_groups[model_name]
    vlm_config = model_group["vlm_config"]
    videos = model_group["videos"]

    print(f"\n=== Testing {model_name} with {len(videos)} videos ===")

    # Reset AppStatus to avoid errors
    AppStatus.should_exit_event = None

    # Get port from environment
    port = int(os.getenv("BACKEND_PORT", "8081"))

    # Build model arguments once for this VLM
    model_args = build_model_args(
        vlm_config, videos[0], vlm_config.get("CA_RAG_CONFIG")  # Use first video for config
    )

    print(f"Model args: {model_args}")

    # Set up evaluation with LLM judge
    parser = ByovParser(
        os.getenv("BYOV_CONFIG_FILE", "/opt/nvidia/via/via-engine/eval/byov/byov_config.yaml")
    )
    judge_name = parser.get_llm_judge()

    try:
        llm_judge = setup_llm_judge(judge_name)
        accuracy_criteria = {
            "accuracy": """Score 1: The answer is completely unrelated to the reference.
            Score 3: The answer has minor relevance but does not align with the reference.
            Score 5: The answer has moderate relevance but contains inaccuracies.
            Score 7: The answer aligns with the reference but has minor errors or omissions.
            Score 10: The answer is completely accurate and aligns perfectly with the reference."""
        }
        evaluator = load_evaluator(
            "labeled_score_string", criteria=accuracy_criteria, llm=llm_judge
        )
    except Exception as e:
        pytest.skip(f"Failed to setup evaluator: {e}")

    # Test the VLM configuration with all videos
    try:
        with ViaTestServer(f"{model_args} --log-level debug", port) as server:
            if server is None:
                pytest.fail("ViaTestServer failed to start properly")

            # Get VLM parameters
            vlm_cfg = vlm_config["VLM_Configurations"]
            temperature = vlm_cfg.get("temperature", 0.4)
            top_p = vlm_cfg.get("top_p", 1)
            top_k = vlm_cfg.get("top_k", 100)
            max_new_tokens = vlm_cfg.get("max_new_tokens", 100)
            seed = vlm_cfg.get("seed", 1)

            # Test server is up
            resp = server.get("/models")
            assert resp.status_code == 200, f"Server health check failed: {resp.status_code}"

            resp_json = resp.json()
            model = resp_json["data"][0]["id"]
            print(f"Using model: {model}")

            # Process each video with the same server instance
            for video_config in videos:
                print(f"\n--- Processing video: {video_config['video_id']} ---")

                # Get video file paths
                video_file_name = video_config["video_file_name"]

                # Find video file
                possible_paths = [
                    f"/opt/nvidia/via/streams/additional/{video_file_name}",
                    f"/opt/nvidia/via/streams/{video_file_name}",
                ]
                media_path = None
                for path in possible_paths:
                    if os.path.exists(path):
                        media_path = path
                        break

                if media_path is None:
                    print(f"Skipping video {video_file_name} - file not found")
                    continue

                # Verify ground truth files exist
                summary_gt_path = (
                    f"/opt/nvidia/via/via-engine/eval/byov/json_gts/{video_config['summary_gt']}"
                )
                qa_gt_path = (
                    f"/opt/nvidia/via/via-engine/eval/byov/json_gts/{video_config['qa_gt']}"
                )

                if not os.path.exists(summary_gt_path):
                    print(f"Skipping video {video_config['video_id']} - summary GT not found")
                    continue
                if not os.path.exists(qa_gt_path):
                    print(f"Skipping video {video_config['video_id']} - Q&A GT not found")
                    continue

                chunk_size = video_config["chunk_size"]

                # Upload video file
                media_path = os.path.abspath(media_path)
                resp = server.post(
                    "/files",
                    files={
                        "filename": (None, media_path),
                        "purpose": (None, "vision"),
                        "media_type": (None, "video"),
                    },
                )
                if resp.status_code != 200:
                    print(f"File upload failed for {video_config['video_id']}: {resp.status_code}")
                    continue

                resp_json = resp.json()
                server_video_id = resp_json["id"]
                print(f"Uploaded video with ID: {server_video_id}")

                try:
                    # Test summarization
                    print("=== Testing Summarization ===")
                    summary_prompt = video_config["prompts"]["caption"]
                    caption_summarization_prompt = video_config["prompts"]["caption_summarization"]
                    summary_aggregation_prompt = video_config["prompts"]["summary_aggregation"]

                    summary, summ_req_id = summarize(
                        server,
                        server_video_id,
                        model,
                        chunk_size,
                        temperature,
                        top_p,
                        top_k,
                        max_new_tokens,
                        seed,
                        summary_prompt,
                        caption_summarization_prompt,
                        summary_aggregation_prompt,
                        enable_chat=True,
                    )

                    print(f"Summary generated: {len(summary)} characters")
                    print(f"Summary (first 200 chars): {summary[:200]}...")
                    assert len(summary) > 0, "Summary should not be empty"

                    # Test chat and collect responses for evaluation
                    print("=== Testing Chat & Collecting Q&A Responses ===")
                    responses = collect_responses(
                        qa_gt_path,
                        chat,
                        server,
                        server_video_id,
                        model,
                        chunk_size,
                        temperature,
                        top_p,
                        top_k,
                        max_new_tokens,
                        seed,
                    )
                    print("=== Q&A Responses Collected ===")

                    # Verify we got responses
                    assert (
                        len(responses) > 0
                    ), "Should have collected Q&A responses from ground truth file"
                    print(f"Collected {len(responses)} Q&A responses from {qa_gt_path}")

                    # Evaluate summary and Q&A responses
                    print("=== Evaluating Summary ===")
                    summary_score, summary_reasoning = evaluate_summary(
                        summary,
                        evaluator,
                        summary_gt_path,
                        video_config["prompts"]["summary_aggregation"],
                    )
                    print(f"Summary Score: {summary_score}")
                    print(f"Summary Reasoning: {summary_reasoning}")

                    print("=== Evaluating Q&A Responses ===")
                    scores, reasonings, num_calls, avg_chat_score = evaluate_qa(
                        responses, evaluator, qa_gt_path
                    )
                    print(f"Average Chat Score: {avg_chat_score}")

                    # Store results for directory creation
                    test_data = {
                        "video_id": video_config["video_id"],
                        "VLM": model_name,
                        "enable_cv": video_config.get("enable_cv", True),
                        "enable_audio": video_config.get("enable_audio", False),
                        "num_frames_per_chunk": vlm_config["VLM_Configurations"].get(
                            "frames_per_chunk"
                        ),
                        "chunk_size": chunk_size,
                        "media_path": media_path,
                        "VLM_config": vlm_config["VLM_Configurations"],
                        "caption_prompt": video_config["prompts"]["caption"],
                        "caption_summarization_prompt": video_config["prompts"][
                            "caption_summarization"
                        ],
                        "summary_aggregation_prompt": video_config["prompts"][
                            "summary_aggregation"
                        ],
                        "summary_gt": summary_gt_path,
                        "qa_gt": qa_gt_path,
                        "dc_gt": None,  # No dense caption GT for now
                    }

                    # Create directory and move files
                    print("=== Creating Directory and Moving Files ===")
                    test_unique_name = byov_create_dir_and_move_files(
                        summ_req_id,
                        media_path,
                        chunk_size,
                        qa_gt_path,
                        None,  # DC_GT
                        summary_gt_path,
                        video_config["prompts"]["caption"],
                        video_config["prompts"]["summary_aggregation"],
                        evaluator,
                        test_data,
                        video_config["video_id"],
                        model_name,
                        summary_score,
                        avg_chat_score,
                    )
                    print(f"Test unique name: {test_unique_name}")
                    print("=== Directory and Files Created ===")

                    # Store results
                    global test_results
                    if "test_results" not in globals():
                        test_results = {}

                    test_results[f"{video_config['video_id']}_{model_name}"] = {
                        "summary_score": summary_score,
                        "summary_reasoning": summary_reasoning,
                        "scores": scores,
                        "reasonings": reasonings,
                        "num_calls": num_calls,
                        "avg_chat_score": avg_chat_score,
                        "test_unique_name": test_unique_name,
                    }

                finally:
                    # Clean up video file
                    try:
                        print("=== Starting Cleanup ===")
                        del_resp = server.delete("/files/" + server_video_id)
                        print(f"File deletion status: {del_resp.status_code}")
                        time.sleep(1)  # Wait for deletion to complete
                    except Exception as e:
                        print(f"Warning: Failed to cleanup video file {server_video_id}: {e}")

                print(f"--- Completed video: {video_config['video_id']} ---")

    except Exception as e:
        pytest.fail(f"Test failed with error: {e}")
    finally:
        # Minimal cleanup - just terminate child processes
        try:
            import psutil

            current_process = psutil.Process()
            for child in current_process.children(recursive=True):
                try:
                    child.terminate()
                except Exception:
                    pass
        except Exception:
            pass

    print(f"=== Test completed successfully for {model_name} with all videos ===")


@pytest.fixture(autouse=True, scope="function")
def test_cleanup():
    """Minimal automatic cleanup after each test"""
    yield
    # Let Python handle most cleanup naturally, just kill any hanging processes
    try:
        import psutil

        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except Exception:
        pass  # If psutil fails, just let the OS clean up when process dies


@pytest.fixture(autouse=True, scope="session")
def finish_tests():
    """Generate Excel report after all tests complete and clean up hanging processes.
    Now runs once per VLM model instead of per video."""
    yield  # This runs after all tests finish

    # Generate Excel report first
    try:
        import os

        print("=== Generating Excel Report (Session End) ===")
        output_path = "./eval/VSS_test_results.xlsx"

        # Process results from log directory
        video_data = process_results("/opt/nvidia/via/via-engine/logs/accuracy/")
        print(f"Found {len(video_data)} video results")

        # Get config file path
        config_file = os.getenv(
            "BYOV_CONFIG_FILE", "/opt/nvidia/via/via-engine/eval/byov/byov_config.yaml"
        )

        # Create Excel report
        create_excel_report(video_data, output_path, config_file)
        print(f"✅ Excel report created at {output_path}")
    except Exception as e:
        print(f"❌ Error generating Excel report: {e}")

    print("=== Testing complete, if program hangs please hit ctrl+c to exit ===")


if __name__ == "__main__":

    # Check for excel_only flag
    if len(sys.argv) > 1 and sys.argv[1] == "excel_only":
        print("=== Generating Excel Report Only ===")
        output_path = "./eval/VSS_test_results.xlsx"

        # Process results from log directory
        video_data = process_results("/opt/nvidia/via/via-engine/logs/accuracy/")
        print(f"Found {len(video_data)} video results")

        # Get config file path
        config_file = os.getenv("BYOV_CONFIG_FILE", "eval/byov/byov_config.yaml")

        # Create Excel report
        create_excel_report(video_data, output_path, config_file)
        print(f"Excel report created at {output_path}")
        sys.exit(0)

    # Show test configuration grouping
    print("=== BYOV Test Configuration ===")
    model_groups = get_test_configs()
    for model_name, group in model_groups.items():
        print(f"Model: {model_name}")
        print(f"  Videos: {len(group['videos'])}")
        for video in group["videos"]:
            print(f"    - {video['video_id']}")
        print()

    # Run the full test suite
    try:
        # Example: python -m pytest test_byov.py -n auto --dist=each --tx=popen//python=python3
        result = pytest.main([__file__, "-v", "-s", "--noconftest"])

        print("=== Generating Excel Report ===")
        output_path = "./eval/VSS_test_results.xlsx"

        # Process results from log directory
        video_data = process_results("/opt/nvidia/via/via-engine/logs/accuracy/")
        print(f"Found {len(video_data)} video results")

        # Get config file path
        config_file = os.getenv("BYOV_CONFIG_FILE", "eval/byov/byov_config.yaml")

        # Create Excel report
        create_excel_report(video_data, output_path, config_file)
        print(f"Excel report created at {output_path}")
    except Exception as e:
        print(f"Error generating Excel report: {e}")
    finally:
        print("=== Tests completed ===")

        # Just exit normally - let Python/OS handle cleanup
        sys.exit(result if "result" in locals() else 0)
